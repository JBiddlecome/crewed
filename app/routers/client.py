from collections import defaultdict
from datetime import date, datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from .. import models
from ..auth import require
from ..db import get_db
from ..helpers import (
    DETAIL_FIELDS,
    US_STATES,
    client_position_for,
    compute_bill_rate,
    details_map,
    effective_markup,
    location_day_for,
    min_wage_for_state,
    month_name,
    month_weeks,
    qualifies,
    refresh_shift_status,
    remove_employee_from_future_shifts,
    resolved_details,
)
from ..templating import flash, templates

router = APIRouter(prefix="/client")


def ctx(request, user, db, **extra):
    base = {"user": user, "company": user.company, "states": US_STATES}
    base.update(extra)
    return base


@router.get("")
def dashboard(
    request: Request,
    user: models.User = Depends(require("client")),
    db: Session = Depends(get_db),
):
    company = user.company
    today = date.today()
    upcoming = (
        db.query(models.Shift)
        .filter(
            models.Shift.client_id == company.id,
            models.Shift.shift_date >= today,
            models.Shift.status.in_(["open", "filled"]),
        )
        .order_by(models.Shift.shift_date, models.Shift.start_time)
        .limit(6)
        .all()
    )
    pending_requests = (
        db.query(models.Assignment)
        .join(models.Shift)
        .filter(
            models.Shift.client_id == company.id,
            models.Assignment.status == "requested",
            models.Shift.status.in_(["open", "filled"]),
        )
        .count()
    )
    submitted_timesheets = (
        db.query(models.Timesheet)
        .join(models.Assignment)
        .join(models.Shift)
        .filter(
            models.Shift.client_id == company.id,
            models.Timesheet.status == "submitted",
        )
        .count()
    )
    open_shifts = (
        db.query(models.Shift)
        .filter(
            models.Shift.client_id == company.id,
            models.Shift.status == "open",
            models.Shift.shift_date >= today,
        )
        .count()
    )
    onboarding = {
        "location": len(company.locations) > 0,
        "position": len(company.positions) > 0,
        "shift": len(company.shifts) > 0,
    }
    return templates.TemplateResponse(
        request,
        "client/dashboard.html",
        ctx(
            request,
            user,
            db,
            upcoming=upcoming,
            pending_requests=pending_requests,
            submitted_timesheets=submitted_timesheets,
            open_shifts=open_shifts,
            onboarding=onboarding,
            markup=effective_markup(db, company),
        ),
    )


# ---------- Locations ----------

@router.get("/locations")
def locations(
    request: Request,
    user: models.User = Depends(require("client")),
    db: Session = Depends(get_db),
):
    wages = {
        loc.id: min_wage_for_state(db, loc.state) for loc in user.company.locations
    }
    return templates.TemplateResponse(
        request, "client/locations.html", ctx(request, user, db, wages=wages)
    )


@router.post("/locations")
def add_location(
    request: Request,
    name: str = Form(...),
    address1: str = Form(...),
    address2: str = Form(""),
    city: str = Form(...),
    state: str = Form(...),
    zip: str = Form(...),
    parking: str = Form(""),
    check_in_location: str = Form(""),
    check_in_contact: str = Form(""),
    user: models.User = Depends(require("client")),
    db: Session = Depends(get_db),
):
    db.add(
        models.Location(
            client_id=user.client_id,
            name=name.strip(),
            address1=address1.strip(),
            address2=address2.strip(),
            city=city.strip(),
            state=state,
            zip=zip.strip(),
            parking=parking.strip() or None,
            check_in_location=check_in_location.strip() or None,
            check_in_contact=check_in_contact.strip() or None,
        )
    )
    db.commit()
    wage = min_wage_for_state(db, state)
    flash(request, f"Location added. Minimum wage in {state} is ${wage:.2f}/hr.")
    return RedirectResponse("/client/locations", status_code=303)


@router.get("/locations/{location_id}/edit")
def edit_location_form(
    location_id: int,
    request: Request,
    user: models.User = Depends(require("client")),
    db: Session = Depends(get_db),
):
    loc = db.get(models.Location, location_id)
    if not loc or loc.client_id != user.client_id:
        flash(request, "Location not found.", "error")
        return RedirectResponse("/client/locations", status_code=303)
    return templates.TemplateResponse(
        request, "client/location_edit.html", ctx(request, user, db, loc=loc)
    )


@router.post("/locations/{location_id}/edit")
def edit_location(
    location_id: int,
    request: Request,
    name: str = Form(...),
    address1: str = Form(...),
    address2: str = Form(""),
    city: str = Form(...),
    state: str = Form(...),
    zip: str = Form(...),
    parking: str = Form(""),
    check_in_location: str = Form(""),
    check_in_contact: str = Form(""),
    user: models.User = Depends(require("client")),
    db: Session = Depends(get_db),
):
    loc = db.get(models.Location, location_id)
    if not loc or loc.client_id != user.client_id:
        flash(request, "Location not found.", "error")
        return RedirectResponse("/client/locations", status_code=303)
    loc.name = name.strip()
    loc.address1 = address1.strip()
    loc.address2 = address2.strip()
    loc.city = city.strip()
    loc.state = state
    loc.zip = zip.strip()
    loc.parking = parking.strip() or None
    loc.check_in_location = check_in_location.strip() or None
    loc.check_in_contact = check_in_contact.strip() or None
    db.commit()
    flash(request, f"{loc.name} updated.")
    return RedirectResponse("/client/locations", status_code=303)


@router.post("/locations/{location_id}/delete")
def delete_location(
    location_id: int,
    request: Request,
    user: models.User = Depends(require("client")),
    db: Session = Depends(get_db),
):
    loc = db.get(models.Location, location_id)
    if not loc or loc.client_id != user.client_id:
        flash(request, "Location not found.", "error")
    elif db.query(models.Shift).filter_by(location_id=loc.id).count():
        flash(request, "That location has shifts attached and can't be removed.", "error")
    else:
        db.delete(loc)
        db.commit()
        flash(request, "Location removed.")
    return RedirectResponse("/client/locations", status_code=303)


# ---------- Positions & rates ----------

@router.get("/positions")
def positions(
    request: Request,
    user: models.User = Depends(require("client")),
    db: Session = Depends(get_db),
):
    company = user.company
    have = {cp.position_id for cp in company.positions}
    available = (
        db.query(models.Position)
        .filter(~models.Position.id.in_(have) if have else True)
        .order_by(models.Position.name)
        .all()
    )
    certifications = db.query(models.Certification).order_by(models.Certification.name).all()
    markup = effective_markup(db, company)
    location_wages = [
        (loc, min_wage_for_state(db, loc.state)) for loc in company.locations
    ]
    floor = max((w for _, w in location_wages), default=7.25)
    return templates.TemplateResponse(
        request,
        "client/positions.html",
        ctx(
            request,
            user,
            db,
            available=available,
            certifications=certifications,
            markup=markup,
            location_wages=location_wages,
            floor=floor,
            compute_bill_rate=compute_bill_rate,
        ),
    )


@router.post("/positions")
def add_position(
    request: Request,
    position_id: int = Form(...),
    pay_rate: float = Form(...),
    requirements: str = Form(""),
    cert_ids: list[int] = Form([]),
    user: models.User = Depends(require("client")),
    db: Session = Depends(get_db),
):
    company = user.company
    wages = [min_wage_for_state(db, loc.state) for loc in company.locations]
    lowest = min(wages) if wages else 7.25
    if pay_rate < lowest:
        flash(
            request,
            f"Pay rate must be at least ${lowest:.2f}/hr (the minimum wage across your locations).",
            "error",
        )
        return RedirectResponse("/client/positions", status_code=303)
    if db.query(models.ClientPosition).filter_by(
        client_id=company.id, position_id=position_id
    ).first():
        flash(request, "You already offer that position.", "error")
        return RedirectResponse("/client/positions", status_code=303)

    cp = models.ClientPosition(
        client_id=company.id,
        position_id=position_id,
        pay_rate=round(pay_rate, 2),
        requirements=requirements.strip(),
    )
    db.add(cp)
    db.flush()
    for cert_id in cert_ids:
        db.add(models.ClientPositionCert(client_position_id=cp.id, certification_id=cert_id))
    db.commit()
    flash(request, "Position added.")
    return RedirectResponse("/client/positions", status_code=303)


@router.post("/positions/{cp_id}/update")
def update_position(
    cp_id: int,
    request: Request,
    pay_rate: float = Form(...),
    user: models.User = Depends(require("client")),
    db: Session = Depends(get_db),
):
    cp = db.get(models.ClientPosition, cp_id)
    if not cp or cp.client_id != user.client_id:
        flash(request, "Position not found.", "error")
        return RedirectResponse("/client/positions", status_code=303)
    wages = [min_wage_for_state(db, loc.state) for loc in user.company.locations]
    lowest = min(wages) if wages else 7.25
    if pay_rate < lowest:
        flash(request, f"Pay rate must be at least ${lowest:.2f}/hr.", "error")
    else:
        cp.pay_rate = round(pay_rate, 2)
        db.commit()
        flash(request, f"{cp.position.name} rate updated.")
    return RedirectResponse("/client/positions", status_code=303)


@router.post("/positions/{cp_id}/delete")
def delete_position(
    cp_id: int,
    request: Request,
    user: models.User = Depends(require("client")),
    db: Session = Depends(get_db),
):
    cp = db.get(models.ClientPosition, cp_id)
    if not cp or cp.client_id != user.client_id:
        flash(request, "Position not found.", "error")
    else:
        db.delete(cp)
        db.commit()
        flash(request, "Position removed.")
    return RedirectResponse("/client/positions", status_code=303)


# ---------- Shifts ----------

@router.get("/shifts")
def shifts(
    request: Request,
    year: int = 0,
    month: int = 0,
    user: models.User = Depends(require("client")),
    db: Session = Depends(get_db),
):
    today = date.today()
    year = year or today.year
    month = month or today.month
    if month < 1:
        month, year = 12, year - 1
    elif month > 12:
        month, year = 1, year + 1

    weeks = month_weeks(year, month)
    grid_start, grid_end = weeks[0][0], weeks[-1][-1]
    month_shifts = (
        db.query(models.Shift)
        .filter(
            models.Shift.client_id == user.client_id,
            models.Shift.shift_date >= grid_start,
            models.Shift.shift_date <= grid_end,
        )
        .order_by(models.Shift.start_time)
        .all()
    )
    # {date: {location: [shifts]}}
    by_day = defaultdict(lambda: defaultdict(list))
    for s in month_shifts:
        by_day[s.shift_date][s.location].append(s)

    return templates.TemplateResponse(
        request,
        "client/shifts.html",
        ctx(
            request,
            user,
            db,
            weeks=weeks,
            by_day=by_day,
            today=today,
            cal_year=year,
            cal_month=month,
            cal_title=f"{month_name(month)} {year}",
            prev_q=f"?year={year if month > 1 else year - 1}&month={month - 1 if month > 1 else 12}",
            next_q=f"?year={year if month < 12 else year + 1}&month={month + 1 if month < 12 else 1}",
        ),
    )


# ---------- Location-day view (all shifts at a location on a date) ----------

@router.get("/days/{location_id}/{day}")
def day_view(
    location_id: int,
    day: str,
    request: Request,
    user: models.User = Depends(require("client")),
    db: Session = Depends(get_db),
):
    loc = db.get(models.Location, location_id)
    try:
        d = date.fromisoformat(day)
    except ValueError:
        loc = None
    if not loc or loc.client_id != user.client_id:
        flash(request, "Not found.", "error")
        return RedirectResponse("/client/shifts", status_code=303)

    day_shifts = (
        db.query(models.Shift)
        .filter_by(client_id=user.client_id, location_id=loc.id, shift_date=d)
        .order_by(models.Shift.start_time)
        .all()
    )
    day_row = location_day_for(db, loc.id, d)
    # Prefill the day-details form: day override if set, else location defaults
    day_details = {
        f: (getattr(day_row, f, None) if day_row else None) or getattr(loc, f, None) or ""
        for f in DETAIL_FIELDS
    }
    confirmed_crew = sorted(
        {
            a.employee
            for s in day_shifts
            for a in s.assignments
            if a.status == "confirmed"
        },
        key=lambda u: u.first_name,
    )
    return templates.TemplateResponse(
        request,
        "client/day.html",
        ctx(
            request,
            user,
            db,
            loc=loc,
            d=d,
            day_shifts=day_shifts,
            day_details=day_details,
            has_day_override=day_row is not None,
            details=details_map(db, day_shifts),
            confirmed_crew=confirmed_crew,
        ),
    )


@router.post("/days/{location_id}/{day}/details")
def save_day_details(
    location_id: int,
    day: str,
    request: Request,
    parking: str = Form(""),
    check_in_location: str = Form(""),
    check_in_contact: str = Form(""),
    user: models.User = Depends(require("client")),
    db: Session = Depends(get_db),
):
    loc = db.get(models.Location, location_id)
    d = date.fromisoformat(day)
    if not loc or loc.client_id != user.client_id:
        flash(request, "Not found.", "error")
        return RedirectResponse("/client/shifts", status_code=303)
    row = location_day_for(db, loc.id, d)
    if not row:
        row = models.LocationDay(location_id=loc.id, date=d)
        db.add(row)
    row.parking = parking.strip() or None
    row.check_in_location = check_in_location.strip() or None
    row.check_in_contact = check_in_contact.strip() or None
    db.commit()
    flash(request, f"Details saved for {loc.name} — they apply to every shift on this date.")
    return RedirectResponse(f"/client/days/{loc.id}/{day}", status_code=303)


@router.post("/days/{location_id}/{day}/message")
def message_day_crew(
    location_id: int,
    day: str,
    request: Request,
    body: str = Form(...),
    user: models.User = Depends(require("client")),
    db: Session = Depends(get_db),
):
    loc = db.get(models.Location, location_id)
    d = date.fromisoformat(day)
    if not loc or loc.client_id != user.client_id:
        flash(request, "Not found.", "error")
        return RedirectResponse("/client/shifts", status_code=303)
    body = body.strip()
    if not body:
        flash(request, "Message can't be empty.", "error")
        return RedirectResponse(f"/client/days/{loc.id}/{day}", status_code=303)
    assignments = (
        db.query(models.Assignment)
        .join(models.Shift)
        .filter(
            models.Shift.location_id == loc.id,
            models.Shift.shift_date == d,
            models.Assignment.status == "confirmed",
        )
        .all()
    )
    recipients = {a.employee_id for a in assignments}
    for rid in recipients:
        db.add(
            models.Message(
                sender_id=user.id,
                recipient_id=rid,
                body=body,
                location_id=loc.id,
                context_date=d,
            )
        )
    db.commit()
    if recipients:
        flash(request, f"Message sent to {len(recipients)} crew member{'s' if len(recipients) != 1 else ''}.")
    else:
        flash(request, "No confirmed crew on this date yet — nothing sent.", "warning")
    return RedirectResponse(f"/client/days/{loc.id}/{day}", status_code=303)


@router.get("/shifts/new")
def new_shift_form(
    request: Request,
    user: models.User = Depends(require("client")),
    db: Session = Depends(get_db),
):
    company = user.company
    if not company.locations or not company.positions:
        flash(request, "Add at least one location and one position first.", "warning")
        return RedirectResponse("/client/positions" if company.locations else "/client/locations", status_code=303)
    location_wages = {loc.id: min_wage_for_state(db, loc.state) for loc in company.locations}
    return templates.TemplateResponse(
        request,
        "client/shift_new.html",
        ctx(
            request,
            user,
            db,
            location_wages=location_wages,
            markup=effective_markup(db, company),
            today=date.today().isoformat(),
        ),
    )


@router.post("/shifts/new")
def create_shift(
    request: Request,
    location_id: int = Form(...),
    client_position_id: int = Form(...),
    shift_date: str = Form(...),
    start_time: str = Form(...),
    end_time: str = Form(...),
    headcount: int = Form(1),
    pay_rate: float = Form(...),
    notes: str = Form(""),
    user: models.User = Depends(require("client")),
    db: Session = Depends(get_db),
):
    company = user.company
    location = db.get(models.Location, location_id)
    cp = db.get(models.ClientPosition, client_position_id)
    if not location or location.client_id != company.id or not cp or cp.client_id != company.id:
        flash(request, "Invalid location or position.", "error")
        return RedirectResponse("/client/shifts/new", status_code=303)
    try:
        parsed_date = date.fromisoformat(shift_date)
    except ValueError:
        flash(request, "Invalid date.", "error")
        return RedirectResponse("/client/shifts/new", status_code=303)
    if parsed_date < date.today():
        flash(request, "Shift date can't be in the past.", "error")
        return RedirectResponse("/client/shifts/new", status_code=303)

    wage = min_wage_for_state(db, location.state)
    if pay_rate < wage:
        flash(
            request,
            f"Pay rate must be at least ${wage:.2f}/hr — the minimum wage in {location.state}.",
            "error",
        )
        return RedirectResponse("/client/shifts/new", status_code=303)

    markup = effective_markup(db, company)
    shift = models.Shift(
        client_id=company.id,
        location_id=location.id,
        position_id=cp.position_id,
        shift_date=parsed_date,
        start_time=start_time,
        end_time=end_time,
        headcount=max(headcount, 1),
        pay_rate=round(pay_rate, 2),
        bill_rate=compute_bill_rate(pay_rate, markup),
        notes=notes.strip(),
        status="open",
    )
    db.add(shift)
    db.commit()
    flash(request, f"Shift posted — {cp.position.name} on {parsed_date.strftime('%b %d')}. Qualified crew can now apply.")
    return RedirectResponse(f"/client/days/{location.id}/{parsed_date.isoformat()}", status_code=303)


@router.get("/shifts/{shift_id}")
def shift_detail(
    shift_id: int,
    request: Request,
    user: models.User = Depends(require("client")),
    db: Session = Depends(get_db),
):
    shift = db.get(models.Shift, shift_id)
    if not shift or shift.client_id != user.client_id:
        flash(request, "Shift not found.", "error")
        return RedirectResponse("/client/shifts", status_code=303)
    applicants = [
        (a, qualifies(db, a.employee, shift))
        for a in shift.assignments
        if a.status in ("requested", "confirmed")
    ]
    cp = client_position_for(db, shift)
    return templates.TemplateResponse(
        request,
        "client/shift_detail.html",
        ctx(
            request,
            user,
            db,
            shift=shift,
            applicants=applicants,
            cp=cp,
            today=date.today(),
            details=resolved_details(db, shift),
        ),
    )


@router.post("/shifts/{shift_id}/details")
def save_shift_details(
    shift_id: int,
    request: Request,
    parking: str = Form(""),
    check_in_location: str = Form(""),
    check_in_contact: str = Form(""),
    notes: str = Form(""),
    user: models.User = Depends(require("client")),
    db: Session = Depends(get_db),
):
    shift = db.get(models.Shift, shift_id)
    if not shift or shift.client_id != user.client_id:
        flash(request, "Shift not found.", "error")
        return RedirectResponse("/client/shifts", status_code=303)
    shift.parking = parking.strip() or None
    shift.check_in_location = check_in_location.strip() or None
    shift.check_in_contact = check_in_contact.strip() or None
    shift.notes = notes.strip()
    db.commit()
    flash(request, "Shift details updated. Blank fields inherit the date/location details.")
    return RedirectResponse(f"/client/shifts/{shift.id}", status_code=303)


@router.post("/assignments/{assignment_id}/message")
def message_employee(
    assignment_id: int,
    request: Request,
    body: str = Form(...),
    user: models.User = Depends(require("client")),
    db: Session = Depends(get_db),
):
    a = db.get(models.Assignment, assignment_id)
    if not a or a.shift.client_id != user.client_id:
        flash(request, "Not found.", "error")
        return RedirectResponse("/client/shifts", status_code=303)
    body = body.strip()
    if not body:
        flash(request, "Message can't be empty.", "error")
    else:
        db.add(
            models.Message(
                sender_id=user.id,
                recipient_id=a.employee_id,
                body=body,
                shift_id=a.shift_id,
                location_id=a.shift.location_id,
                context_date=a.shift.shift_date,
            )
        )
        db.commit()
        flash(request, f"Message sent to {a.employee.name}.")
    return RedirectResponse(f"/client/shifts/{a.shift_id}", status_code=303)


@router.post("/shifts/{shift_id}/cancel")
def cancel_shift(
    shift_id: int,
    request: Request,
    user: models.User = Depends(require("client")),
    db: Session = Depends(get_db),
):
    shift = db.get(models.Shift, shift_id)
    if not shift or shift.client_id != user.client_id:
        flash(request, "Shift not found.", "error")
        return RedirectResponse("/client/shifts", status_code=303)
    shift.status = "cancelled"
    for a in shift.assignments:
        if a.status in ("requested", "confirmed"):
            a.status = "cancelled"
    db.commit()
    flash(request, "Shift cancelled and crew released.")
    return RedirectResponse("/client/shifts", status_code=303)


@router.post("/assignments/{assignment_id}/confirm")
def confirm_assignment(
    assignment_id: int,
    request: Request,
    user: models.User = Depends(require("client")),
    db: Session = Depends(get_db),
):
    a = db.get(models.Assignment, assignment_id)
    if not a or a.shift.client_id != user.client_id:
        flash(request, "Request not found.", "error")
        return RedirectResponse("/client/shifts", status_code=303)
    if a.shift.confirmed_count >= a.shift.headcount:
        flash(request, "This shift is already fully staffed.", "warning")
        return RedirectResponse(f"/client/shifts/{a.shift_id}", status_code=303)
    a.status = "confirmed"
    a.confirmed_at = datetime.utcnow()
    if not a.timesheet:
        db.add(models.Timesheet(assignment_id=a.id))
    refresh_shift_status(db, a.shift)
    db.commit()
    flash(request, f"{a.employee.name} confirmed for the shift.")
    return RedirectResponse(f"/client/shifts/{a.shift_id}", status_code=303)


@router.post("/assignments/{assignment_id}/decline")
def decline_assignment(
    assignment_id: int,
    request: Request,
    user: models.User = Depends(require("client")),
    db: Session = Depends(get_db),
):
    a = db.get(models.Assignment, assignment_id)
    if not a or a.shift.client_id != user.client_id:
        flash(request, "Request not found.", "error")
        return RedirectResponse("/client/shifts", status_code=303)
    a.status = "declined"
    refresh_shift_status(db, a.shift)
    db.commit()
    flash(request, "Request declined.")
    return RedirectResponse(f"/client/shifts/{a.shift_id}", status_code=303)


# ---------- Timesheets ----------

@router.get("/timesheets")
def timesheets(
    request: Request,
    user: models.User = Depends(require("client")),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(models.Timesheet)
        .join(models.Assignment)
        .join(models.Shift)
        .filter(models.Shift.client_id == user.client_id)
        .order_by(models.Timesheet.status.desc(), models.Timesheet.id.desc())
        .all()
    )
    submitted = [t for t in rows if t.status == "submitted"]
    approved = [t for t in rows if t.status == "approved"]
    return templates.TemplateResponse(
        request,
        "client/timesheets.html",
        ctx(request, user, db, submitted=submitted, approved=approved),
    )


@router.post("/timesheets/{timesheet_id}/approve")
def approve_timesheet(
    timesheet_id: int,
    request: Request,
    user: models.User = Depends(require("client")),
    db: Session = Depends(get_db),
):
    t = db.get(models.Timesheet, timesheet_id)
    if not t or t.assignment.shift.client_id != user.client_id:
        flash(request, "Timesheet not found.", "error")
    elif t.status != "submitted":
        flash(request, "Only submitted timesheets can be approved.", "warning")
    else:
        t.status = "approved"
        t.approved_at = datetime.utcnow()
        db.commit()
        flash(request, f"Timesheet approved — {t.hours:.2f} hours for {t.assignment.employee.name}.")
    return RedirectResponse("/client/timesheets", status_code=303)


# ---------- A-List & Block List (Crew Management) ----------

@router.get("/crew")
def crew_management(
    request: Request,
    user: models.User = Depends(require("client")),
    db: Session = Depends(get_db),
):
    company = user.company
    alist_entries = (
        db.query(models.AList)
        .filter_by(client_id=company.id)
        .all()
    )
    blocklist_entries = (
        db.query(models.BlockList)
        .filter_by(client_id=company.id)
        .all()
    )
    # Fetch active employees for dropdown
    employees = (
        db.query(models.User)
        .filter_by(role="employee", status="active")
        .order_by(models.User.first_name, models.User.last_name)
        .all()
    )
    return templates.TemplateResponse(
        request,
        "client/crew.html",
        ctx(
            request,
            user,
            db,
            alist=alist_entries,
            blocklist=blocklist_entries,
            employees=employees,
        ),
    )


@router.post("/crew/alist")
def add_to_alist(
    request: Request,
    employee_id: int = Form(...),
    location_id: int = Form(0),
    notes: str = Form(""),
    user: models.User = Depends(require("client")),
    db: Session = Depends(get_db),
):
    company = user.company
    # Validate employee exists and is active
    emp = db.query(models.User).filter_by(id=employee_id, role="employee", status="active").first()
    if not emp:
        flash(request, "Employee not found or inactive.", "error")
        return RedirectResponse("/client/crew", status_code=303)

    loc_id = location_id if location_id > 0 else None
    if loc_id:
        loc = db.get(models.Location, loc_id)
        if not loc or loc.client_id != company.id:
            flash(request, "Invalid location.", "error")
            return RedirectResponse("/client/crew", status_code=303)

    # Check duplicate
    exists = (
        db.query(models.AList)
        .filter_by(employee_id=employee_id, client_id=company.id, location_id=loc_id)
        .first()
    )
    if exists:
        flash(request, f"{emp.name} is already on the A-List for this location/client.", "warning")
    else:
        entry = models.AList(
            employee_id=employee_id,
            client_id=company.id,
            location_id=loc_id,
            notes=notes.strip() or None,
        )
        db.add(entry)
        db.commit()
        loc_name = loc.name if loc_id else "Client-wide"
        flash(request, f"Added {emp.name} to A-List ({loc_name}).")
    return RedirectResponse("/client/crew", status_code=303)


@router.post("/crew/alist/{entry_id}/delete")
def delete_from_alist(
    entry_id: int,
    request: Request,
    user: models.User = Depends(require("client")),
    db: Session = Depends(get_db),
):
    entry = db.get(models.AList, entry_id)
    if not entry or entry.client_id != user.client_id:
        flash(request, "Entry not found.", "error")
    else:
        name = entry.employee.name
        db.delete(entry)
        db.commit()
        flash(request, f"Removed {name} from A-List.")
    return RedirectResponse("/client/crew", status_code=303)


@router.post("/crew/blocklist")
def add_to_blocklist(
    request: Request,
    employee_id: int = Form(...),
    location_id: int = Form(0),
    reason: str = Form(""),
    user: models.User = Depends(require("client")),
    db: Session = Depends(get_db),
):
    company = user.company
    emp = db.query(models.User).filter_by(id=employee_id, role="employee", status="active").first()
    if not emp:
        flash(request, "Employee not found or inactive.", "error")
        return RedirectResponse("/client/crew", status_code=303)

    loc_id = location_id if location_id > 0 else None
    if loc_id:
        loc = db.get(models.Location, loc_id)
        if not loc or loc.client_id != company.id:
            flash(request, "Invalid location.", "error")
            return RedirectResponse("/client/crew", status_code=303)

    # Check duplicate
    exists = (
        db.query(models.BlockList)
        .filter_by(employee_id=employee_id, client_id=company.id, location_id=loc_id)
        .first()
    )
    if exists:
        flash(request, f"{emp.name} is already blocked for this location/client.", "warning")
    else:
        entry = models.BlockList(
            employee_id=employee_id,
            client_id=company.id,
            location_id=loc_id,
            reason=reason.strip() or None,
        )
        db.add(entry)
        db.commit()

        # Remove from any future shifts at this client/location
        remove_employee_from_future_shifts(db, employee_id, company.id, loc_id)
        db.commit()

        loc_name = loc.name if loc_id else "Client-wide"
        flash(request, f"Blocked {emp.name} ({loc_name}) and removed from any future shifts.")
    return RedirectResponse("/client/crew", status_code=303)


@router.post("/crew/blocklist/{entry_id}/delete")
def delete_from_blocklist(
    entry_id: int,
    request: Request,
    user: models.User = Depends(require("client")),
    db: Session = Depends(get_db),
):
    entry = db.get(models.BlockList, entry_id)
    if not entry or entry.client_id != user.client_id:
        flash(request, "Entry not found.", "error")
    else:
        name = entry.employee.name
        db.delete(entry)
        db.commit()
        flash(request, f"Removed {name} from Block List.")
    return RedirectResponse("/client/crew", status_code=303)
