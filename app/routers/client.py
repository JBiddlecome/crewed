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
    log_timesheet_event,
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
    if not user.company:
        return templates.TemplateResponse(
            request,
            "client/pending_link.html",
            {"user": user},
        )
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
    if not company:
        flash(request, "Your account isn't linked to a company yet.", "warning")
        return RedirectResponse("/client", status_code=303)
    # Filter out any ClientPositions whose Position was deleted from the catalog
    company_positions = [cp for cp in company.positions if cp.position is not None]
    have = {cp.position_id for cp in company_positions}
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
            company_positions=company_positions,
            available=available,
            certifications=certifications,
            markup=markup,
            location_wages=location_wages,
            floor=floor,
            compute_bill_rate=compute_bill_rate,
            preset_rates_map={pr.position_id: pr for pr in company.preset_rates},
        ),
    )


@router.post("/positions")
def add_position(
    request: Request,
    position_id: int = Form(...),
    pay_rate: float = Form(0.0),
    requirements: str = Form(""),
    cert_ids: list[int] = Form([]),
    user: models.User = Depends(require("client")),
    db: Session = Depends(get_db),
):
    company = user.company
    
    if company.rate_setting == 'preset_rates':
        preset = db.query(models.ClientPresetRate).filter_by(client_id=company.id, position_id=position_id).first()
        if preset:
            pay_rate = preset.pay_rate_l1
        else:
            wages = [min_wage_for_state(db, loc.state) for loc in company.locations]
            pay_rate = min(wages) if wages else 7.25
    else:
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
    if user.company.rate_setting == 'preset_rates':
        flash(request, "Pay rates are preset by the admin and cannot be updated here.", "error")
    elif pay_rate < lowest:
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
    month_events = (
        db.query(models.Event)
        .filter(
            models.Event.client_id == user.client_id,
            models.Event.event_date >= grid_start,
            models.Event.event_date <= grid_end,
        )
        .order_by(models.Event.event_date)
        .all()
    )
    # {date: [events]}
    by_day = defaultdict(list)
    for e in month_events:
        by_day[e.event_date].append(e)

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


# ---------- Event view (all shifts within one event) ----------

@router.get("/days/{location_id}/{day}")
def day_redirect(
    location_id: int,
    day: str,
    request: Request,
    user: models.User = Depends(require("client")),
    db: Session = Depends(get_db),
):
    """Backward-compat redirect — old bookmarks still work."""
    try:
        d = date.fromisoformat(day)
    except ValueError:
        return RedirectResponse("/client/shifts", status_code=303)
    event = (
        db.query(models.Event)
        .filter_by(client_id=user.client_id, location_id=location_id, event_date=d)
        .first()
    )
    if event:
        return RedirectResponse(f"/client/events/{event.id}", status_code=301)
    flash(request, "Event not found.", "error")
    return RedirectResponse("/client/shifts", status_code=303)


@router.get("/events/{event_id}")
def event_view(
    event_id: int,
    request: Request,
    user: models.User = Depends(require("client")),
    db: Session = Depends(get_db),
):
    event = db.get(models.Event, event_id)
    if not event or event.client_id != user.client_id:
        flash(request, "Not found.", "error")
        return RedirectResponse("/client/shifts", status_code=303)
    day_shifts = sorted(event.shifts, key=lambda s: s.start_time)
    confirmed_crew = sorted(
        {a.employee for s in day_shifts for a in s.assignments if a.status == "confirmed"},
        key=lambda u: u.first_name,
    )
    return templates.TemplateResponse(
        request,
        "client/event.html",
        ctx(
            request,
            user,
            db,
            event=event,
            day_shifts=day_shifts,
            confirmed_crew=confirmed_crew,
            details=details_map(db, day_shifts),
        ),
    )


@router.post("/events/{event_id}/details")
def save_event_details(
    event_id: int,
    request: Request,
    name: str = Form(""),
    address1: str = Form(""),
    address2: str = Form(""),
    city: str = Form(""),
    state: str = Form(""),
    zip: str = Form(""),
    parking: str = Form(""),
    check_in_location: str = Form(""),
    check_in_contact: str = Form(""),
    notes: str = Form(""),
    user: models.User = Depends(require("client")),
    db: Session = Depends(get_db),
):
    event = db.get(models.Event, event_id)
    if not event or event.client_id != user.client_id:
        flash(request, "Not found.", "error")
        return RedirectResponse("/client/shifts", status_code=303)
    event.name = name.strip() or event.name
    event.address1 = address1.strip() or event.address1
    event.address2 = address2.strip()
    event.city = city.strip() or event.city
    event.state = state or event.state
    event.zip = zip.strip() or event.zip
    event.parking = parking.strip() or None
    event.check_in_location = check_in_location.strip() or None
    event.check_in_contact = check_in_contact.strip() or None
    event.notes = notes.strip() or None
    db.commit()
    flash(request, f"Event details saved for {event.name} on {event.event_date.strftime('%b %d')}.")
    return RedirectResponse(f"/client/events/{event.id}", status_code=303)


@router.post("/events/{event_id}/message")
def message_event_crew(
    event_id: int,
    request: Request,
    body: str = Form(...),
    user: models.User = Depends(require("client")),
    db: Session = Depends(get_db),
):
    event = db.get(models.Event, event_id)
    if not event or event.client_id != user.client_id:
        flash(request, "Not found.", "error")
        return RedirectResponse("/client/shifts", status_code=303)
    body = body.strip()
    if not body:
        flash(request, "Message can't be empty.", "error")
        return RedirectResponse(f"/client/events/{event.id}", status_code=303)
    assignments = (
        db.query(models.Assignment)
        .join(models.Shift)
        .filter(
            models.Shift.event_id == event.id,
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
                location_id=event.location_id,
                context_date=event.event_date,
            )
        )
    db.commit()
    if recipients:
        flash(request, f"Message sent to {len(recipients)} crew member{'s' if len(recipients) != 1 else ''}.")
    else:
        flash(request, "No confirmed crew on this date yet — nothing sent.", "warning")
    return RedirectResponse(f"/client/events/{event.id}", status_code=303)


@router.get("/shifts/new")
def new_shift_form(
    request: Request,
    user: models.User = Depends(require("client")),
    db: Session = Depends(get_db),
):
    company = user.company
    if not company.portal_approved:
        return templates.TemplateResponse(
            request,
            "client/shift_new.html",
            ctx(request, user, db, not_approved=True, location_wages={}, markup=0, today=date.today().isoformat()),
        )
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
            not_approved=False,
            preset_rates_map={pr.position_id: pr for pr in company.preset_rates},
            compute_bill_rate=compute_bill_rate,
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
    required_level: int = Form(1),
    notes: str = Form(""),
    user: models.User = Depends(require("client")),
    db: Session = Depends(get_db),
):
    company = user.company
    if not company.portal_approved:
        flash(request, "Your account must be activated before you can place shifts.", "error")
        return RedirectResponse("/client/shifts/new", status_code=303)
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

    if company.rate_setting == 'preset_rates':
        preset = db.query(models.ClientPresetRate).filter_by(client_id=company.id, position_id=cp.position_id).first()
        if preset:
            if required_level == 1:
                pay_rate = preset.pay_rate_l1
                bill_rate = preset.bill_rate_l1
            elif required_level == 2:
                pay_rate = preset.pay_rate_l2
                bill_rate = preset.bill_rate_l2
            else:
                pay_rate = preset.pay_rate_l3
                bill_rate = preset.bill_rate_l3
        else:
            if required_level == 1:
                pay_rate = cp.position.default_pay_rate_l1
                bill_rate = cp.position.default_bill_rate_l1
            elif required_level == 2:
                pay_rate = cp.position.default_pay_rate_l2
                bill_rate = cp.position.default_bill_rate_l2
            else:
                pay_rate = cp.position.default_pay_rate_l3
                bill_rate = cp.position.default_bill_rate_l3
            
            preset = models.ClientPresetRate(
                client_id=company.id,
                position_id=cp.position_id,
                pay_rate_l1=cp.position.default_pay_rate_l1, bill_rate_l1=cp.position.default_bill_rate_l1, markup_l1=cp.position.default_markup_l1,
                pay_rate_l2=cp.position.default_pay_rate_l2, bill_rate_l2=cp.position.default_bill_rate_l2, markup_l2=cp.position.default_markup_l2,
                pay_rate_l3=cp.position.default_pay_rate_l3, bill_rate_l3=cp.position.default_bill_rate_l3, markup_l3=cp.position.default_markup_l3
            )
            db.add(preset)
            db.flush()
            db.add(models.ClientPresetRateHistory(
                preset_rate=preset,
                pay_rate_l1=cp.position.default_pay_rate_l1, bill_rate_l1=cp.position.default_bill_rate_l1, markup_l1=cp.position.default_markup_l1,
                pay_rate_l2=cp.position.default_pay_rate_l2, bill_rate_l2=cp.position.default_bill_rate_l2, markup_l2=cp.position.default_markup_l2,
                pay_rate_l3=cp.position.default_pay_rate_l3, bill_rate_l3=cp.position.default_bill_rate_l3, markup_l3=cp.position.default_markup_l3,
                changed_by=None
            ))
    else:
        wage = min_wage_for_state(db, location.state)
        if pay_rate < wage:
            flash(
                request,
                f"Pay rate must be at least ${wage:.2f}/hr — the minimum wage in {location.state}.",
                "error",
            )
            return RedirectResponse("/client/shifts/new", status_code=303)
        markup = effective_markup(db, company)
        bill_rate = compute_bill_rate(pay_rate, markup)

    # Find or create the event folder for this location + date
    event = (
        db.query(models.Event)
        .filter_by(client_id=company.id, location_id=location.id, event_date=parsed_date)
        .first()
    )
    if not event:
        event = models.Event(
            client_id=company.id,
            location_id=location.id,
            event_date=parsed_date,
            name=location.name,
            address1=location.address1,
            address2=location.address2 or "",
            city=location.city,
            state=location.state,
            zip=location.zip,
            parking=location.parking,
            check_in_location=location.check_in_location,
            check_in_contact=location.check_in_contact,
        )
        db.add(event)
        db.flush()

    shift = models.Shift(
        client_id=company.id,
        location_id=location.id,
        event_id=event.id,
        position_id=cp.position_id,
        shift_date=parsed_date,
        start_time=start_time,
        end_time=end_time,
        headcount=max(headcount, 1),
        pay_rate=round(pay_rate, 2),
        bill_rate=round(bill_rate, 2),
        notes=notes.strip(),
        required_level=max(1, min(3, required_level)),
        status="open",
    )
    db.add(shift)
    db.commit()
    flash(request, f"Shift posted — {cp.position.name} on {parsed_date.strftime('%b %d')}. Qualified crew can now apply.")
    return RedirectResponse(f"/client/events/{event.id}", status_code=303)


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
        new_ts = models.Timesheet(assignment_id=a.id)
        db.add(new_ts)
        db.flush()
        log_timesheet_event(
            db, new_ts.id, "created", user.id, "client",
            f"Timesheet created — {a.employee.name} confirmed for shift on {a.shift.shift_date}",
        )
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
        .filter(models.Shift.client_id == user.client_id, models.Timesheet.deleted_at == None)
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
    start_time: str = Form(...),
    end_time: str = Form(...),
    meal_start_time: str = Form(None),
    meal_end_time: str = Form(None),
    no_break: bool = Form(False),
    dispute_reason: str = Form(""),
    user: models.User = Depends(require("client")),
    db: Session = Depends(get_db),
):
    t = db.get(models.Timesheet, timesheet_id)
    if not t or t.assignment.shift.client_id != user.client_id:
        flash(request, "Timesheet not found.", "error")
    elif t.is_closed:
        flash(request, "This timesheet is closed.", "error")
    elif t.status != "submitted":
        flash(request, "Only submitted timesheets can be approved.", "warning")
    else:
        # Check if the client modified any times compared to employee submission
        meal_changed = False
        if no_break:
            if t.meal_start_time is not None or t.meal_end_time is not None:
                meal_changed = True
        else:
            if meal_start_time != t.meal_start_time or meal_end_time != t.meal_end_time:
                meal_changed = True
        
        edited = (
            start_time != t.start_time or
            end_time != t.end_time or
            meal_changed
        )
        if edited:
            t.billing_start_time = start_time
            t.billing_end_time = end_time
            if no_break:
                t.billing_meal_start_time = None
                t.billing_meal_end_time = None
                t.billing_break_minutes = 0
            else:
                t.billing_meal_start_time = meal_start_time or None
                t.billing_meal_end_time = meal_end_time or None
                if t.billing_meal_start_time and t.billing_meal_end_time:
                    t.billing_break_minutes = models.minutes_between(t.billing_meal_start_time, t.billing_meal_end_time)
                else:
                    t.billing_break_minutes = 0
            t.is_disputed = True
            t.dispute_reason = dispute_reason.strip() or None

        t.status = "approved"
        t.approved_at = datetime.utcnow()
        t.approved_by = user.id

        if edited:
            reason_note = f" Reason: {dispute_reason.strip()}" if dispute_reason.strip() else ""
            log_timesheet_event(
                db, t.id, "client_adjusted", user.id, "client",
                f"Approved with billing adjustment: {start_time}–{end_time}"
                + (f" (meal {meal_start_time}–{meal_end_time})" if not no_break and meal_start_time else " (no break)")
                + f" · {t.billing_hours:.2f} hrs billed." + reason_note,
            )
        else:
            log_timesheet_event(
                db, t.id, "client_approved", user.id, "client",
                f"Approved as submitted · {t.employee_hours:.2f} hrs.",
            )

        db.commit()

        msg = f"Timesheet approved — {t.hours:.2f} hours for {t.assignment.employee.name}."
        if edited:
            msg += " (Hours adjusted for billing)"
        flash(request, msg)
    return RedirectResponse("/client/timesheets", status_code=303)


@router.post("/timesheets/{timesheet_id}/edit")
def edit_timesheet(
    timesheet_id: int,
    request: Request,
    start_time: str = Form(...),
    end_time: str = Form(...),
    meal_start_time: str = Form(None),
    meal_end_time: str = Form(None),
    no_break: bool = Form(False),
    dispute_reason: str = Form(""),
    user: models.User = Depends(require("client")),
    db: Session = Depends(get_db),
):
    t = db.get(models.Timesheet, timesheet_id)
    if not t or t.assignment.shift.client_id != user.client_id:
        flash(request, "Timesheet not found.", "error")
        return RedirectResponse("/client/timesheets", status_code=303)
    if t.is_closed:
        flash(request, "This timesheet is closed and cannot be edited.", "error")
        return RedirectResponse("/client/timesheets", status_code=303)
        
    meal_changed = False
    if no_break:
        if t.meal_start_time is not None or t.meal_end_time is not None:
            meal_changed = True
    else:
        if meal_start_time != t.meal_start_time or meal_end_time != t.meal_end_time:
            meal_changed = True
            
    edited = (
        start_time != t.start_time or
        end_time != t.end_time or
        meal_changed
    )
    if edited:
        t.billing_start_time = start_time
        t.billing_end_time = end_time
        if no_break:
            t.billing_meal_start_time = None
            t.billing_meal_end_time = None
            t.billing_break_minutes = 0
        else:
            t.billing_meal_start_time = meal_start_time or None
            t.billing_meal_end_time = meal_end_time or None
            if t.billing_meal_start_time and t.billing_meal_end_time:
                t.billing_break_minutes = models.minutes_between(t.billing_meal_start_time, t.billing_meal_end_time)
            else:
                t.billing_break_minutes = 0
        t.is_disputed = True
        t.dispute_reason = dispute_reason.strip() or None
        reason_note = f" Reason: {dispute_reason.strip()}" if dispute_reason.strip() else ""
        log_timesheet_event(
            db, t.id, "client_billing_edit", user.id, "client",
            f"Billing updated to {start_time}–{end_time}"
            + (f" (meal {meal_start_time}–{meal_end_time})" if not no_break and meal_start_time else " (no break)")
            + f" · {t.billing_hours:.2f} hrs." + reason_note,
        )
    else:
        t.billing_start_time = None
        t.billing_end_time = None
        t.billing_break_minutes = None
        t.billing_meal_start_time = None
        t.billing_meal_end_time = None
        t.is_disputed = False
        t.dispute_reason = None
        log_timesheet_event(
            db, t.id, "client_billing_edit", user.id, "client",
            "Billing reset to match claimed times.",
        )

    db.commit()
    flash(request, f"Timesheet updated — {t.billing_hours:.2f} hours billed.")
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
