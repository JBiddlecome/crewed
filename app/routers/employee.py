from datetime import date, datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from .. import models
from ..auth import require
from ..db import get_db
from ..helpers import (
    US_STATES,
    details_map,
    qualifies,
    refresh_shift_status,
    resolved_details,
    unread_count,
)
from ..templating import flash, templates

router = APIRouter(prefix="/employee")


@router.get("")
def dashboard(
    request: Request,
    user: models.User = Depends(require("employee")),
    db: Session = Depends(get_db),
):
    today = date.today()
    my_assignments = (
        db.query(models.Assignment)
        .join(models.Shift)
        .filter(models.Assignment.employee_id == user.id)
        .all()
    )
    next_shift = None
    confirmed_upcoming = sorted(
        (
            a
            for a in my_assignments
            if a.status == "confirmed" and a.shift.shift_date >= today
        ),
        key=lambda a: (a.shift.shift_date, a.shift.start_time),
    )
    if confirmed_upcoming:
        next_shift = confirmed_upcoming[0]
    pending = sum(1 for a in my_assignments if a.status == "requested")
    needs_timesheet = sum(
        1
        for a in my_assignments
        if a.status == "confirmed"
        and a.shift.shift_date <= today
        and a.timesheet
        and a.timesheet.status == "pending"
    )
    open_count = (
        db.query(models.Shift)
        .filter(models.Shift.status == "open", models.Shift.shift_date >= today)
        .count()
    )
    return templates.TemplateResponse(
        request,
        "employee/dashboard.html",
        {
            "user": user,
            "next_shift": next_shift,
            "next_details": resolved_details(db, next_shift.shift) if next_shift else None,
            "pending": pending,
            "needs_timesheet": needs_timesheet,
            "open_count": open_count,
            "confirmed_count": len(confirmed_upcoming),
            "profile_ready": bool(user.positions),
            "unread": unread_count(db, user),
        },
    )


# ---------- Profile ----------

@router.get("/profile")
def profile(
    request: Request,
    user: models.User = Depends(require("employee")),
    db: Session = Depends(get_db),
):
    have_positions = {p.position_id for p in user.positions}
    available_positions = (
        db.query(models.Position)
        .filter(~models.Position.id.in_(have_positions) if have_positions else True)
        .order_by(models.Position.name)
        .all()
    )
    certifications = db.query(models.Certification).order_by(models.Certification.name).all()
    return templates.TemplateResponse(
        request,
        "employee/profile.html",
        {
            "user": user,
            "available_positions": available_positions,
            "certifications": certifications,
            "states": US_STATES,
            "today": date.today(),
            "unread": unread_count(db, user),
        },
    )


@router.post("/profile/info")
def update_info(
    request: Request,
    phone: str = Form(""),
    city: str = Form(""),
    state: str = Form(""),
    zip: str = Form(""),
    user: models.User = Depends(require("employee")),
    db: Session = Depends(get_db),
):
    db_user = db.get(models.User, user.id)
    db_user.phone = phone.strip()
    db_user.city = city.strip()
    db_user.state = state
    db_user.zip = zip.strip()
    db.commit()
    flash(request, "Profile updated.")
    return RedirectResponse("/employee/profile", status_code=303)


@router.post("/profile/positions")
def add_position(
    request: Request,
    position_id: int = Form(...),
    user: models.User = Depends(require("employee")),
    db: Session = Depends(get_db),
):
    exists = (
        db.query(models.EmployeePosition)
        .filter_by(user_id=user.id, position_id=position_id)
        .first()
    )
    if not exists:
        db.add(models.EmployeePosition(user_id=user.id, position_id=position_id))
        db.commit()
        flash(request, "Position added to your profile.")
    return RedirectResponse("/employee/profile", status_code=303)


@router.post("/profile/positions/{ep_id}/delete")
def remove_position(
    ep_id: int,
    request: Request,
    user: models.User = Depends(require("employee")),
    db: Session = Depends(get_db),
):
    ep = db.get(models.EmployeePosition, ep_id)
    if ep and ep.user_id == user.id:
        db.delete(ep)
        db.commit()
        flash(request, "Position removed.")
    return RedirectResponse("/employee/profile", status_code=303)


@router.post("/profile/certs")
def add_cert(
    request: Request,
    certification_id: int = Form(...),
    expires_on: str = Form(""),
    user: models.User = Depends(require("employee")),
    db: Session = Depends(get_db),
):
    expiry = None
    if expires_on:
        try:
            expiry = date.fromisoformat(expires_on)
        except ValueError:
            flash(request, "Invalid expiration date.", "error")
            return RedirectResponse("/employee/profile", status_code=303)
    db.add(
        models.EmployeeCert(
            user_id=user.id, certification_id=certification_id, expires_on=expiry
        )
    )
    db.commit()
    flash(request, "Certification added.")
    return RedirectResponse("/employee/profile", status_code=303)


@router.post("/profile/certs/{ec_id}/delete")
def remove_cert(
    ec_id: int,
    request: Request,
    user: models.User = Depends(require("employee")),
    db: Session = Depends(get_db),
):
    ec = db.get(models.EmployeeCert, ec_id)
    if ec and ec.user_id == user.id:
        db.delete(ec)
        db.commit()
        flash(request, "Certification removed.")
    return RedirectResponse("/employee/profile", status_code=303)


# ---------- Browse & apply ----------

@router.get("/shifts")
def browse(
    request: Request,
    user: models.User = Depends(require("employee")),
    db: Session = Depends(get_db),
):
    today = date.today()
    taken = {
        a.shift_id
        for a in db.query(models.Assignment).filter_by(employee_id=user.id).all()
    }
    open_shifts = (
        db.query(models.Shift)
        .filter(models.Shift.status == "open", models.Shift.shift_date >= today)
        .order_by(models.Shift.shift_date, models.Shift.start_time)
        .all()
    )
    rows = []
    for shift in open_shifts:
        if shift.id in taken:
            continue
        ok, reasons = qualifies(db, user, shift)
        rows.append((shift, ok, reasons))
    eligible = [r for r in rows if r[1]]
    other = [r for r in rows if not r[1]]
    return templates.TemplateResponse(
        request,
        "employee/browse.html",
        {
            "user": user,
            "eligible": eligible,
            "other": other,
            "details": details_map(db, [r[0] for r in rows]),
            "unread": unread_count(db, user),
        },
    )


@router.post("/shifts/{shift_id}/apply")
def apply(
    shift_id: int,
    request: Request,
    user: models.User = Depends(require("employee")),
    db: Session = Depends(get_db),
):
    if user.status != "active":
        flash(request, "Your account is pending approval — you can apply once it's activated.", "warning")
        return RedirectResponse("/employee/shifts", status_code=303)
    shift = db.get(models.Shift, shift_id)
    if not shift or shift.status != "open" or shift.shift_date < date.today():
        flash(request, "That shift is no longer open.", "error")
        return RedirectResponse("/employee/shifts", status_code=303)
    if db.query(models.Assignment).filter_by(shift_id=shift.id, employee_id=user.id).first():
        flash(request, "You've already responded to that shift.", "warning")
        return RedirectResponse("/employee/shifts", status_code=303)
    ok, reasons = qualifies(db, user, shift)
    if not ok:
        flash(request, "You don't meet the requirements: " + "; ".join(reasons), "error")
        return RedirectResponse("/employee/shifts", status_code=303)
    db.add(models.Assignment(shift_id=shift.id, employee_id=user.id))
    db.commit()
    flash(request, f"Applied to {shift.position.name} at {shift.location.name}. You'll be notified when confirmed.")
    return RedirectResponse("/employee/myshifts", status_code=303)


# ---------- My shifts & timesheets ----------

@router.get("/myshifts")
def my_shifts(
    request: Request,
    user: models.User = Depends(require("employee")),
    db: Session = Depends(get_db),
):
    today = date.today()
    assignments = (
        db.query(models.Assignment)
        .join(models.Shift)
        .filter(models.Assignment.employee_id == user.id)
        .order_by(models.Shift.shift_date.desc())
        .all()
    )
    requested = [a for a in assignments if a.status == "requested"]
    upcoming = [
        a for a in assignments if a.status == "confirmed" and a.shift.shift_date >= today
    ]
    upcoming.sort(key=lambda a: (a.shift.shift_date, a.shift.start_time))
    needs_timesheet = [
        a
        for a in assignments
        if a.status == "confirmed"
        and a.shift.shift_date <= today
        and a.timesheet
        and a.timesheet.status == "pending"
    ]
    history = [
        a
        for a in assignments
        if a.status in ("declined", "cancelled")
        or (a.timesheet and a.timesheet.status in ("submitted", "approved"))
    ]
    return templates.TemplateResponse(
        request,
        "employee/myshifts.html",
        {
            "user": user,
            "requested": requested,
            "upcoming": upcoming,
            "needs_timesheet": needs_timesheet,
            "history": history,
            "details": details_map(db, [a.shift for a in upcoming + requested]),
            "unread": unread_count(db, user),
        },
    )


# ---------- Notifications ----------

@router.get("/notifications")
def notifications(
    request: Request,
    user: models.User = Depends(require("employee")),
    db: Session = Depends(get_db),
):
    msgs = (
        db.query(models.Message)
        .filter_by(recipient_id=user.id)
        .order_by(models.Message.created_at.desc())
        .limit(100)
        .all()
    )
    new_ids = {m.id for m in msgs if m.read_at is None}
    response = templates.TemplateResponse(
        request,
        "employee/notifications.html",
        {"user": user, "messages": msgs, "new_ids": new_ids, "unread": len(new_ids)},
    )
    # Viewing the inbox marks everything read
    now = datetime.utcnow()
    for m in msgs:
        if m.read_at is None:
            m.read_at = now
    db.commit()
    return response


@router.post("/assignments/{assignment_id}/cancel")
def cancel(
    assignment_id: int,
    request: Request,
    user: models.User = Depends(require("employee")),
    db: Session = Depends(get_db),
):
    a = db.get(models.Assignment, assignment_id)
    if not a or a.employee_id != user.id:
        flash(request, "Shift not found.", "error")
        return RedirectResponse("/employee/myshifts", status_code=303)
    if a.status not in ("requested", "confirmed"):
        flash(request, "That request can't be cancelled.", "warning")
        return RedirectResponse("/employee/myshifts", status_code=303)
    a.status = "cancelled"
    refresh_shift_status(db, a.shift)
    db.commit()
    flash(request, "You've been removed from the shift.")
    return RedirectResponse("/employee/myshifts", status_code=303)


@router.post("/timesheets/{assignment_id}/submit")
def submit_timesheet(
    assignment_id: int,
    request: Request,
    start_time: str = Form(...),
    end_time: str = Form(...),
    break_minutes: int = Form(0),
    user: models.User = Depends(require("employee")),
    db: Session = Depends(get_db),
):
    a = db.get(models.Assignment, assignment_id)
    if not a or a.employee_id != user.id or not a.timesheet:
        flash(request, "Timesheet not found.", "error")
        return RedirectResponse("/employee/myshifts", status_code=303)
    if a.timesheet.status != "pending":
        flash(request, "That timesheet was already submitted.", "warning")
        return RedirectResponse("/employee/myshifts", status_code=303)
    t = a.timesheet
    t.start_time = start_time
    t.end_time = end_time
    t.break_minutes = max(break_minutes, 0)
    if t.hours <= 0:
        flash(request, "Those times don't add up to any worked hours.", "error")
        return RedirectResponse("/employee/myshifts", status_code=303)
    t.status = "submitted"
    t.submitted_at = datetime.utcnow()
    db.commit()
    flash(request, f"Timesheet submitted — {t.hours:.2f} hours.")
    return RedirectResponse("/employee/myshifts", status_code=303)
