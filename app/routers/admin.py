from datetime import date, datetime

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
    disputed_timesheets = (
        db.query(models.Timesheet)
        .filter_by(is_disputed=True)
        .order_by(models.Timesheet.approved_at.desc())
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
            "disputed_timesheets": disputed_timesheets,
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

def group_shifts_by_client(shifts_list, reverse_dates=False):
    grouped = {}
    for s in shifts_list:
        client = s.company
        if client.id not in grouped:
            grouped[client.id] = {"client": client, "dates": {}}
        if s.shift_date not in grouped[client.id]["dates"]:
            grouped[client.id]["dates"][s.shift_date] = []
        grouped[client.id]["dates"][s.shift_date].append(s)
    
    sorted_grouped = []
    for cid in sorted(grouped.keys(), key=lambda c_id: grouped[c_id]["client"].name.lower()):
        c_data = grouped[cid]
        sorted_dates = []
        total_count = 0
        for d in sorted(c_data["dates"].keys(), reverse=reverse_dates):
            shifts = c_data["dates"][d]
            total_count += len(shifts)
            sorted_dates.append({
                "date": d,
                "shifts": shifts
            })
        sorted_grouped.append({
            "client": c_data["client"],
            "dates": sorted_dates,
            "total_count": total_count
        })
    return sorted_grouped


def group_shifts_by_location(shifts_list, reverse_dates=False):
    grouped = {}
    for s in shifts_list:
        location = s.location
        if location.id not in grouped:
            grouped[location.id] = {"location": location, "dates": {}}
        if s.shift_date not in grouped[location.id]["dates"]:
            grouped[location.id]["dates"][s.shift_date] = []
        grouped[location.id]["dates"][s.shift_date].append(s)
        
    sorted_grouped = []
    for lid in sorted(grouped.keys(), key=lambda l_id: (grouped[l_id]["location"].company.name.lower(), grouped[l_id]["location"].name.lower())):
        l_data = grouped[lid]
        sorted_dates = []
        total_count = 0
        for d in sorted(l_data["dates"].keys(), reverse=reverse_dates):
            shifts = l_data["dates"][d]
            total_count += len(shifts)
            sorted_dates.append({
                "date": d,
                "shifts": shifts
            })
        sorted_grouped.append({
            "location": l_data["location"],
            "dates": sorted_dates,
            "total_count": total_count
        })
    return sorted_grouped


@router.get("/shifts")
def shifts(
    request: Request,
    view: str = "client",
    user: models.User = Depends(require("admin")),
    db: Session = Depends(get_db),
):
    today = date.today()
    upcoming_raw = (
        db.query(models.Shift)
        .filter(models.Shift.shift_date >= today)
        .order_by(models.Shift.shift_date, models.Shift.start_time)
        .all()
    )
    past_raw = (
        db.query(models.Shift)
        .filter(models.Shift.shift_date < today)
        .order_by(models.Shift.shift_date.desc())
        .limit(30)
        .all()
    )
    
    if view == "location":
        upcoming = group_shifts_by_location(upcoming_raw)
        past = group_shifts_by_location(past_raw, reverse_dates=True)
    else:
        upcoming = group_shifts_by_client(upcoming_raw)
        past = group_shifts_by_client(past_raw, reverse_dates=True)
        
    return templates.TemplateResponse(
        request,
        "admin/shifts.html",
        {"user": user, "upcoming": upcoming, "past": past, "view": view},
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


# ---------- Timesheets ----------

@router.get("/timesheets")
def admin_timesheets(
    request: Request,
    user: models.User = Depends(require("admin")),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(models.Timesheet)
        .join(models.Assignment)
        .join(models.Shift)
        .order_by(models.Timesheet.id.desc())
        .all()
    )
    return templates.TemplateResponse(
        request,
        "admin/timesheets.html",
        {"user": user, "timesheets": rows},
    )


@router.post("/timesheets/{timesheet_id}/close")
def admin_close_timesheet(
    timesheet_id: int,
    request: Request,
    user: models.User = Depends(require("admin")),
    db: Session = Depends(get_db),
):
    t = db.get(models.Timesheet, timesheet_id)
    if not t:
        flash(request, "Timesheet not found.", "error")
        return RedirectResponse("/admin/timesheets", status_code=303)
    t.is_closed = not t.is_closed
    db.commit()
    status_str = "closed (locked)" if t.is_closed else "re-opened (unlocked)"
    flash(request, f"Timesheet for {t.assignment.employee.name} has been {status_str}.")
    return RedirectResponse("/admin/timesheets", status_code=303)


@router.post("/timesheets/{timesheet_id}/edit")
def admin_edit_timesheet(
    timesheet_id: int,
    request: Request,
    start_time: str = Form(...),
    end_time: str = Form(...),
    meal_start_time: str = Form(None),
    meal_end_time: str = Form(None),
    no_break: bool = Form(False),
    billing_start_time: str = Form(None),
    billing_end_time: str = Form(None),
    billing_meal_start_time: str = Form(None),
    billing_meal_end_time: str = Form(None),
    billing_no_break: bool = Form(False),
    is_disputed: bool = Form(False),
    dispute_reason: str = Form(""),
    user: models.User = Depends(require("admin")),
    db: Session = Depends(get_db),
):
    t = db.get(models.Timesheet, timesheet_id)
    if not t:
        flash(request, "Timesheet not found.", "error")
        return RedirectResponse("/admin/timesheets", status_code=303)
        
    # Update employee side
    t.start_time = start_time
    t.end_time = end_time
    if no_break:
        t.meal_start_time = None
        t.meal_end_time = None
        t.break_minutes = 0
    else:
        t.meal_start_time = meal_start_time or None
        t.meal_end_time = meal_end_time or None
        if t.meal_start_time and t.meal_end_time:
            t.break_minutes = models.minutes_between(t.meal_start_time, t.meal_end_time)
        else:
            t.break_minutes = 0

    # Update billing side
    t.billing_start_time = billing_start_time or None
    t.billing_end_time = billing_end_time or None
    if billing_no_break:
        t.billing_meal_start_time = None
        t.billing_meal_end_time = None
        t.billing_break_minutes = 0
    else:
        t.billing_meal_start_time = billing_meal_start_time or None
        t.billing_meal_end_time = billing_meal_end_time or None
        if t.billing_meal_start_time and t.billing_meal_end_time:
            t.billing_break_minutes = models.minutes_between(t.billing_meal_start_time, t.billing_meal_end_time)
        else:
            t.billing_break_minutes = None

    t.is_disputed = is_disputed
    t.dispute_reason = dispute_reason.strip() or None
    
    # If edited, ensure status is approved
    t.status = "approved"
    if not t.approved_at:
        t.approved_at = datetime.utcnow()
        
    db.commit()
    flash(request, f"Timesheet updated successfully.")
    return RedirectResponse("/admin/timesheets", status_code=303)


@router.post("/employees/{employee_id}/approve_photo")
def admin_approve_photo(
    employee_id: int,
    request: Request,
    user: models.User = Depends(require("admin")),
    db: Session = Depends(get_db),
):
    emp = db.get(models.User, employee_id)
    if not emp or emp.role != "employee":
        flash(request, "Employee not found.", "error")
    else:
        emp.profile_picture_approved = True
        db.commit()
        flash(request, f"Approved profile picture for {emp.name}.")
    return RedirectResponse("/admin/employees", status_code=303)


@router.post("/employees/{employee_id}/reject_photo")
def admin_reject_photo(
    employee_id: int,
    request: Request,
    user: models.User = Depends(require("admin")),
    db: Session = Depends(get_db),
):
    emp = db.get(models.User, employee_id)
    if not emp or emp.role != "employee":
        flash(request, "Employee not found.", "error")
    else:
        from ..config import DATA_DIR
        import os
        if emp.profile_picture:
            filepath = DATA_DIR / "uploads" / "profile_pics" / emp.profile_picture
            if filepath.exists():
                try:
                    os.remove(filepath)
                except Exception:
                    pass
        emp.profile_picture = None
        emp.profile_picture_approved = False
        db.commit()
        flash(request, f"Rejected and removed profile picture for {emp.name}.")
    return RedirectResponse("/admin/employees", status_code=303)


@router.post("/employee-position/{ep_id}/approve")
def admin_approve_position(
    ep_id: int,
    request: Request,
    user: models.User = Depends(require("admin")),
    db: Session = Depends(get_db),
):
    ep = db.get(models.EmployeePosition, ep_id)
    if not ep:
        flash(request, "Employee position record not found.", "error")
    else:
        ep.status = "approved"
        ep.decline_reason = None
        db.commit()
        flash(request, f"Manually approved {ep.position.name} for {ep.employee.name}.")
    return RedirectResponse("/admin/employees", status_code=303)


@router.post("/employee-position/{ep_id}/decline")
def admin_decline_position(
    ep_id: int,
    request: Request,
    decline_reason: str = Form("Manually declined by admin."),
    user: models.User = Depends(require("admin")),
    db: Session = Depends(get_db),
):
    ep = db.get(models.EmployeePosition, ep_id)
    if not ep:
        flash(request, "Employee position record not found.", "error")
    else:
        ep.status = "declined"
        ep.decline_reason = decline_reason.strip() or "Manually declined by admin."
        db.commit()
        flash(request, f"Manually declined {ep.position.name} for {ep.employee.name}.")
    return RedirectResponse("/admin/employees", status_code=303)

