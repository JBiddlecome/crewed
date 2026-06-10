from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from .. import models
from ..auth import require
from ..db import get_db
from ..helpers import (
    STATE_NAMES,
    effective_markup,
    get_setting,
    min_wage_for_state,
    remove_employee_from_future_shifts,
    set_setting,
)
from ..templating import flash, templates

router = APIRouter(prefix="/admin")


@router.get("")
def dashboard(
    request: Request,
    user: models.User = Depends(require("admin")),
    db: Session = Depends(get_db),
):
    today = date.today()
    stats = {
        "clients": db.query(models.ClientCompany).count(),
        "employees_active": db.query(models.User)
        .filter_by(role="employee", status="active")
        .count(),
        "employees_pending": db.query(models.User)
        .filter_by(role="employee", status="pending")
        .count(),
        "open_shifts": db.query(models.Shift)
        .filter(models.Shift.status == "open", models.Shift.shift_date >= today)
        .count(),
        "submitted_timesheets": db.query(models.Timesheet)
        .filter_by(status="submitted")
        .count(),
    }
    recent_shifts = (
        db.query(models.Shift)
        .order_by(models.Shift.created_at.desc())
        .limit(8)
        .all()
    )
    pending_employees = (
        db.query(models.User)
        .filter_by(role="employee", status="pending")
        .order_by(models.User.created_at.desc())
        .limit(8)
        .all()
    )
    return templates.TemplateResponse(
        request,
        "admin/dashboard.html",
        {
            "user": user,
            "stats": stats,
            "recent_shifts": recent_shifts,
            "pending_employees": pending_employees,
        },
    )


# ---------- Clients ----------

@router.get("/clients")
def clients(
    request: Request,
    user: models.User = Depends(require("admin")),
    db: Session = Depends(get_db),
):
    rows = db.query(models.ClientCompany).order_by(models.ClientCompany.name).all()
    return templates.TemplateResponse(
        request, "admin/clients.html", {"user": user, "clients": rows}
    )


@router.get("/clients/{client_id}")
def client_detail(
    client_id: int,
    request: Request,
    user: models.User = Depends(require("admin")),
    db: Session = Depends(get_db),
):
    company = db.get(models.ClientCompany, client_id)
    if not company:
        flash(request, "Client not found.", "error")
        return RedirectResponse("/admin/clients", status_code=303)
    shifts = (
        db.query(models.Shift)
        .filter_by(client_id=company.id)
        .order_by(models.Shift.shift_date.desc())
        .limit(15)
        .all()
    )
    alist_entries = db.query(models.AList).filter_by(client_id=company.id).all()
    blocklist_entries = db.query(models.BlockList).filter_by(client_id=company.id).all()
    employees = (
        db.query(models.User)
        .filter_by(role="employee", status="active")
        .order_by(models.User.first_name, models.User.last_name)
        .all()
    )
    return templates.TemplateResponse(
        request,
        "admin/client_detail.html",
        {
            "user": user,
            "company": company,
            "shifts": shifts,
            "markup": effective_markup(db, company),
            "global_markup": get_setting(db, "markup_percent", "55"),
            "wage": min_wage_for_state,
            "db": db,
            "alist": alist_entries,
            "blocklist": blocklist_entries,
            "employees": employees,
        },
    )


@router.post("/clients/{client_id}/markup")
def set_client_markup(
    client_id: int,
    request: Request,
    markup_override: str = Form(""),
    user: models.User = Depends(require("admin")),
    db: Session = Depends(get_db),
):
    company = db.get(models.ClientCompany, client_id)
    if not company:
        flash(request, "Client not found.", "error")
        return RedirectResponse("/admin/clients", status_code=303)
    if markup_override.strip() == "":
        company.markup_override = None
        flash(request, f"{company.name} now uses the global markup.")
    else:
        try:
            value = float(markup_override)
            if value < 0:
                raise ValueError
        except ValueError:
            flash(request, "Markup must be a non-negative number.", "error")
            return RedirectResponse(f"/admin/clients/{client_id}", status_code=303)
        company.markup_override = value
        flash(request, f"{company.name} markup set to {value:g}%.")
    db.commit()
    return RedirectResponse(f"/admin/clients/{client_id}", status_code=303)


# ---------- Employees ----------

@router.get("/employees")
def employees(
    request: Request,
    status: str = "",
    user: models.User = Depends(require("admin")),
    db: Session = Depends(get_db),
):
    q = db.query(models.User).filter_by(role="employee")
    if status in ("active", "pending", "disabled"):
        q = q.filter_by(status=status)
    rows = q.order_by(models.User.created_at.desc()).all()
    return templates.TemplateResponse(
        request,
        "admin/employees.html",
        {"user": user, "employees": rows, "filter_status": status},
    )


@router.post("/employees/{employee_id}/status")
def set_employee_status(
    employee_id: int,
    request: Request,
    new_status: str = Form(...),
    user: models.User = Depends(require("admin")),
    db: Session = Depends(get_db),
):
    emp = db.get(models.User, employee_id)
    if not emp or emp.role != "employee" or new_status not in ("active", "pending", "disabled"):
        flash(request, "Invalid request.", "error")
    else:
        emp.status = new_status
        db.commit()
        label = {"active": "approved", "pending": "set to pending", "disabled": "deactivated"}[new_status]
        flash(request, f"{emp.name} {label}.")
    return RedirectResponse("/admin/employees", status_code=303)


# ---------- Catalogs ----------

@router.get("/positions")
def positions(
    request: Request,
    user: models.User = Depends(require("admin")),
    db: Session = Depends(get_db),
):
    rows = db.query(models.Position).order_by(models.Position.name).all()
    usage = {
        p.id: db.query(models.ClientPosition).filter_by(position_id=p.id).count()
        + db.query(models.Shift).filter_by(position_id=p.id).count()
        for p in rows
    }
    return templates.TemplateResponse(
        request, "admin/positions.html", {"user": user, "positions": rows, "usage": usage}
    )


@router.post("/positions")
def add_position(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    user: models.User = Depends(require("admin")),
    db: Session = Depends(get_db),
):
    name = name.strip()
    if not name:
        flash(request, "Name is required.", "error")
    elif db.query(models.Position).filter_by(name=name).first():
        flash(request, "That position already exists.", "error")
    else:
        db.add(models.Position(name=name, description=description.strip()))
        db.commit()
        flash(request, f"Position '{name}' added to the catalog.")
    return RedirectResponse("/admin/positions", status_code=303)


@router.post("/positions/{position_id}/delete")
def delete_position(
    position_id: int,
    request: Request,
    user: models.User = Depends(require("admin")),
    db: Session = Depends(get_db),
):
    p = db.get(models.Position, position_id)
    if not p:
        flash(request, "Position not found.", "error")
    elif (
        db.query(models.ClientPosition).filter_by(position_id=p.id).count()
        or db.query(models.Shift).filter_by(position_id=p.id).count()
    ):
        flash(request, "That position is in use and can't be deleted.", "error")
    else:
        db.query(models.EmployeePosition).filter_by(position_id=p.id).delete()
        db.delete(p)
        db.commit()
        flash(request, "Position deleted.")
    return RedirectResponse("/admin/positions", status_code=303)


@router.get("/certifications")
def certifications(
    request: Request,
    user: models.User = Depends(require("admin")),
    db: Session = Depends(get_db),
):
    rows = db.query(models.Certification).order_by(models.Certification.name).all()
    return templates.TemplateResponse(
        request, "admin/certifications.html", {"user": user, "certifications": rows}
    )


@router.post("/certifications")
def add_certification(
    request: Request,
    name: str = Form(...),
    user: models.User = Depends(require("admin")),
    db: Session = Depends(get_db),
):
    name = name.strip()
    if not name:
        flash(request, "Name is required.", "error")
    elif db.query(models.Certification).filter_by(name=name).first():
        flash(request, "That certification already exists.", "error")
    else:
        db.add(models.Certification(name=name))
        db.commit()
        flash(request, f"Certification '{name}' added.")
    return RedirectResponse("/admin/certifications", status_code=303)


# ---------- Minimum wage ----------

@router.get("/minwage")
def minwage(
    request: Request,
    user: models.User = Depends(require("admin")),
    db: Session = Depends(get_db),
):
    rows = db.query(models.MinWage).order_by(models.MinWage.state).all()
    return templates.TemplateResponse(
        request,
        "admin/minwage.html",
        {"user": user, "rows": rows, "state_names": STATE_NAMES},
    )


@router.post("/minwage/{row_id}")
def update_minwage(
    row_id: int,
    request: Request,
    rate: float = Form(...),
    user: models.User = Depends(require("admin")),
    db: Session = Depends(get_db),
):
    row = db.get(models.MinWage, row_id)
    if not row or rate <= 0:
        flash(request, "Invalid rate.", "error")
    else:
        row.rate = round(rate, 2)
        db.commit()
        flash(request, f"{STATE_NAMES.get(row.state, row.state)} minimum wage set to ${row.rate:.2f}.")
    return RedirectResponse("/admin/minwage", status_code=303)


# ---------- Settings ----------

@router.get("/settings")
def settings(
    request: Request,
    user: models.User = Depends(require("admin")),
    db: Session = Depends(get_db),
):
    return templates.TemplateResponse(
        request,
        "admin/settings.html",
        {"user": user, "markup": get_setting(db, "markup_percent", "55")},
    )


@router.post("/settings")
def save_settings(
    request: Request,
    markup_percent: float = Form(...),
    user: models.User = Depends(require("admin")),
    db: Session = Depends(get_db),
):
    if markup_percent < 0:
        flash(request, "Markup must be non-negative.", "error")
    else:
        set_setting(db, "markup_percent", f"{markup_percent:g}")
        db.commit()
        flash(request, f"Global markup set to {markup_percent:g}%.")
    return RedirectResponse("/admin/settings", status_code=303)


# ---------- Shifts ----------

@router.get("/shifts")
def shifts(
    request: Request,
    user: models.User = Depends(require("admin")),
    db: Session = Depends(get_db),
):
    today = date.today()
    upcoming = (
        db.query(models.Shift)
        .filter(models.Shift.shift_date >= today)
        .order_by(models.Shift.shift_date, models.Shift.start_time)
        .all()
    )
    past = (
        db.query(models.Shift)
        .filter(models.Shift.shift_date < today)
        .order_by(models.Shift.shift_date.desc())
        .limit(30)
        .all()
    )
    return templates.TemplateResponse(
        request,
        "admin/shifts.html",
        {"user": user, "upcoming": upcoming, "past": past},
    )


# ---------- Client Crew Lists (Admin) ----------

@router.post("/clients/{client_id}/crew/alist")
def admin_add_to_alist(
    client_id: int,
    request: Request,
    employee_id: int = Form(...),
    location_id: int = Form(0),
    notes: str = Form(""),
    user: models.User = Depends(require("admin")),
    db: Session = Depends(get_db),
):
    company = db.get(models.ClientCompany, client_id)
    if not company:
        flash(request, "Client not found.", "error")
        return RedirectResponse("/admin/clients", status_code=303)

    emp = db.query(models.User).filter_by(id=employee_id, role="employee", status="active").first()
    if not emp:
        flash(request, "Employee not found or inactive.", "error")
        return RedirectResponse(f"/admin/clients/{client_id}", status_code=303)

    loc_id = location_id if location_id > 0 else None
    if loc_id:
        loc = db.get(models.Location, loc_id)
        if not loc or loc.client_id != company.id:
            flash(request, "Invalid location.", "error")
            return RedirectResponse(f"/admin/clients/{client_id}", status_code=303)

    exists = (
        db.query(models.AList)
        .filter_by(employee_id=employee_id, client_id=company.id, location_id=loc_id)
        .first()
    )
    if exists:
        flash(request, f"{emp.name} is already on the A-List for this location.", "warning")
    else:
        entry = models.AList(
            employee_id=employee_id,
            client_id=company.id,
            location_id=loc_id,
            notes=notes.strip() or None,
        )
        db.add(entry)
        db.commit()
        flash(request, f"Added {emp.name} to A-List.")
    return RedirectResponse(f"/admin/clients/{client_id}", status_code=303)


@router.post("/clients/{client_id}/crew/alist/{entry_id}/delete")
def admin_delete_from_alist(
    client_id: int,
    entry_id: int,
    request: Request,
    user: models.User = Depends(require("admin")),
    db: Session = Depends(get_db),
):
    entry = db.get(models.AList, entry_id)
    if not entry or entry.client_id != client_id:
        flash(request, "Entry not found.", "error")
    else:
        name = entry.employee.name
        db.delete(entry)
        db.commit()
        flash(request, f"Removed {name} from A-List.")
    return RedirectResponse(f"/admin/clients/{client_id}", status_code=303)


@router.post("/clients/{client_id}/crew/blocklist")
def admin_add_to_blocklist(
    client_id: int,
    request: Request,
    employee_id: int = Form(...),
    location_id: int = Form(0),
    reason: str = Form(""),
    user: models.User = Depends(require("admin")),
    db: Session = Depends(get_db),
):
    company = db.get(models.ClientCompany, client_id)
    if not company:
        flash(request, "Client not found.", "error")
        return RedirectResponse("/admin/clients", status_code=303)

    emp = db.query(models.User).filter_by(id=employee_id, role="employee", status="active").first()
    if not emp:
        flash(request, "Employee not found or inactive.", "error")
        return RedirectResponse(f"/admin/clients/{client_id}", status_code=303)

    loc_id = location_id if location_id > 0 else None
    if loc_id:
        loc = db.get(models.Location, loc_id)
        if not loc or loc.client_id != company.id:
            flash(request, "Invalid location.", "error")
            return RedirectResponse(f"/admin/clients/{client_id}", status_code=303)

    exists = (
        db.query(models.BlockList)
        .filter_by(employee_id=employee_id, client_id=company.id, location_id=loc_id)
        .first()
    )
    if exists:
        flash(request, f"{emp.name} is already blocked for this location.", "warning")
    else:
        entry = models.BlockList(
            employee_id=employee_id,
            client_id=company.id,
            location_id=loc_id,
            reason=reason.strip() or None,
        )
        db.add(entry)
        db.commit()

        remove_employee_from_future_shifts(db, employee_id, company.id, loc_id)
        db.commit()

        flash(request, f"Blocked {emp.name} and cancelled any future shifts.")
    return RedirectResponse(f"/admin/clients/{client_id}", status_code=303)


@router.post("/clients/{client_id}/crew/blocklist/{entry_id}/delete")
def admin_delete_from_blocklist(
    client_id: int,
    entry_id: int,
    request: Request,
    user: models.User = Depends(require("admin")),
    db: Session = Depends(get_db),
):
    entry = db.get(models.BlockList, entry_id)
    if not entry or entry.client_id != client_id:
        flash(request, "Entry not found.", "error")
    else:
        name = entry.employee.name
        db.delete(entry)
        db.commit()
        flash(request, f"Removed {name} from Block List.")
    return RedirectResponse(f"/admin/clients/{client_id}", status_code=303)
