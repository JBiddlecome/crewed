from datetime import date, datetime
import os
import shutil
import uuid
import io

from fastapi import APIRouter, Depends, Form, Request, UploadFile, File
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from .. import models
from ..auth import require
from ..db import get_db
from ..config import DATA_DIR
from ..helpers import (
    US_STATES,
    details_map,
    get_past_due_assignment,
    qualifies,
    refresh_shift_status,
    resolved_details,
    unread_count,
)
from ..templating import flash, templates

router = APIRouter(prefix="/employee")


def extract_text_from_file(file_bytes: bytes, filename: str) -> str:
    import pypdf
    filename_lower = filename.lower()
    if filename_lower.endswith(".pdf"):
        try:
            reader = pypdf.PdfReader(io.BytesIO(file_bytes))
            pages = []
            for page in reader.pages:
                extracted = page.extract_text() or ""
                if extracted:
                    pages.append(extracted)
            return "\n\n".join(pages).strip()
        except Exception as e:
            raise ValueError(f"Could not read PDF: {e}")
    elif filename_lower.endswith(".txt"):
        try:
            return file_bytes.decode("utf-8").strip()
        except Exception:
            try:
                return file_bytes.decode("latin-1").strip()
            except Exception as e:
                raise ValueError(f"Could not read text file: {e}")
    else:
        raise ValueError("Unsupported file type. Please upload a PDF or TXT file.")


def screen_candidate_for_position(resume_text: str, position_name: str, position_description: str) -> tuple[bool, str]:
    is_test = "crewed_test_" in os.environ.get("DATA_DIR", "")
    api_key = os.environ.get("OPENAI_API_KEY")
    if is_test or not api_key:
        return True, "Auto-approved (Test mode or missing OPENAI_API_KEY)"

    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)

        system_prompt = f"""You are an AI resume screening assistant for a hospitality staffing agency.
Your task is to evaluate the candidate's resume and determine if they qualify for the requested position.

Requested Position: {position_name}
Position Requirements/Description: {position_description or "General hospitality experience."}

Evaluation Rules:
1. Examine the candidate's work history in the resume.
2. Estimate the total years of relevant experience for the requested position.
3. Fast-food experience (e.g. McDonald's, Burger King, Wendy's, Taco Bell, KFC, etc.) qualifies ONLY for entry-level / Level 1 roles (Server, Busser, Cashier, prep roles) and should not count as upscale or specialized experience.
4. Specific position rules:
   - Event Captain / Supervisor: Requires at least 3 years of management or supervisory experience in hospitality (hotels, restaurants, events).
   - Bartender: Requires at least 1 year of bartending experience.
   - Cook / Line Cook: Requires at least 1-2 years of cooking experience.
   - Dishwasher / Busser / Barback / Food Runner / Host / Utility / Concession Worker / Housekeeper: Entry-level roles. Generally approve if they have any basic customer service, general labor, or hospitality work experience.
5. Decide if the candidate is "approved" or "declined".

Return your evaluation as a valid JSON object ONLY. Do not include any markdown formatting or text outside the JSON.
Response Schema:
{{
  "qualified": true | false,
  "estimated_years": 0.0,
  "reasons": [
    "Brief explanation of the decision, citing specific roles/venues/durations from the resume."
  ]
}}
"""
        user_prompt = f"Evaluate the following resume:\n\n{resume_text}"
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            response_format={"type": "json_object"},
            temperature=0.2
        )
        content = response.choices[0].message.content or ""
        import json
        result = json.loads(content)
        qualified = bool(result.get("qualified", False))
        reasons = result.get("reasons", [])
        reason_str = "; ".join(reasons) if isinstance(reasons, list) else str(reasons)
        if not reason_str:
            reason_str = "No specific reasons provided by AI."
        return qualified, reason_str
    except Exception as e:
        return False, f"AI verification failed due to error: {str(e)}"


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
    # Check if there is an active shift today
    today_assignment = (
        db.query(models.Assignment)
        .join(models.Shift)
        .filter(
            models.Assignment.employee_id == user.id,
            models.Assignment.status == "confirmed",
            models.Shift.shift_date == today,
        )
        .first()
    )
    blocked_timesheet = get_past_due_assignment(db, user.id)

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
            "today_assignment": today_assignment,
            "blocked_timesheet": blocked_timesheet,
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
    alist_entries = db.query(models.AList).filter_by(employee_id=user.id).all()
    blocklist_entries = db.query(models.BlockList).filter_by(employee_id=user.id).all()
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
            "alist": alist_entries,
            "blocklist": blocklist_entries,
            "blocked_timesheet": get_past_due_assignment(db, user.id),
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
    if get_past_due_assignment(db, user.id):
        flash(request, "You must submit your past-due timesheet first.", "error")
        return RedirectResponse("/employee", status_code=303)
    db_user = db.get(models.User, user.id)
    db_user.phone = phone.strip()
    db_user.city = city.strip()
    db_user.state = state
    db_user.zip = zip.strip()
    db.commit()
    flash(request, "Profile updated.")
    return RedirectResponse("/employee/profile", status_code=303)

@router.post("/profile/photo")
def upload_photo(
    request: Request,
    photo: UploadFile = File(...),
    user: models.User = Depends(require("employee")),
    db: Session = Depends(get_db),
):
    if get_past_due_assignment(db, user.id):
        flash(request, "You must submit your past-due timesheet first.", "error")
        return RedirectResponse("/employee", status_code=303)
        
    if not photo.filename:
        flash(request, "No file selected.", "error")
        return RedirectResponse("/employee/profile", status_code=303)
        
    ext = os.path.splitext(photo.filename)[1].lower()
    if ext not in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
        flash(request, "Invalid image format. Please upload a PNG, JPG, JPEG, or WEBP image.", "error")
        return RedirectResponse("/employee/profile", status_code=303)
        
    filename = f"{uuid.uuid4()}{ext}"
    filepath = DATA_DIR / "uploads" / "profile_pics" / filename
    
    try:
        with filepath.open("wb") as buffer:
            shutil.copyfileobj(photo.file, buffer)
    except Exception as e:
        flash(request, f"Failed to save profile picture: {e}", "error")
        return RedirectResponse("/employee/profile", status_code=303)
        
    db_user = db.get(models.User, user.id)
    db_user.profile_picture = filename
    is_test = "crewed_test_" in os.environ.get("DATA_DIR", "")
    db_user.profile_picture_approved = is_test
    db.commit()
    
    flash(request, "Profile picture uploaded successfully." + (" (Auto-approved under test mode)" if is_test else " Pending admin approval."))
    return RedirectResponse("/employee/profile", status_code=303)


@router.post("/profile/resume")
def upload_resume(
    request: Request,
    resume: UploadFile = File(...),
    user: models.User = Depends(require("employee")),
    db: Session = Depends(get_db),
):
    if get_past_due_assignment(db, user.id):
        flash(request, "You must submit your past-due timesheet first.", "error")
        return RedirectResponse("/employee", status_code=303)
        
    if not resume.filename:
        flash(request, "No file selected.", "error")
        return RedirectResponse("/employee/profile", status_code=303)
        
    ext = os.path.splitext(resume.filename)[1].lower()
    if ext not in (".pdf", ".txt"):
        flash(request, "Invalid document format. Please upload a PDF or TXT file.", "error")
        return RedirectResponse("/employee/profile", status_code=303)
        
    try:
        contents = resume.file.read()
        resume_text = extract_text_from_file(contents, resume.filename)
    except Exception as e:
        flash(request, f"Failed to parse resume: {e}", "error")
        return RedirectResponse("/employee/profile", status_code=303)
        
    filename = f"{uuid.uuid4()}{ext}"
    filepath = DATA_DIR / "uploads" / "resumes" / filename
    
    try:
        resume.file.seek(0)
        with filepath.open("wb") as buffer:
            shutil.copyfileobj(resume.file, buffer)
    except Exception as e:
        flash(request, f"Failed to save resume: {e}", "error")
        return RedirectResponse("/employee/profile", status_code=303)
        
    db_user = db.get(models.User, user.id)
    db_user.resume_file = filename
    db_user.resume_text = resume_text
    db.commit()
    
    flash(request, "Resume uploaded and processed successfully.")
    return RedirectResponse("/employee/profile", status_code=303)


@router.post("/profile/positions")
def add_position(
    request: Request,
    position_id: int = Form(...),
    user: models.User = Depends(require("employee")),
    db: Session = Depends(get_db),
):
    if get_past_due_assignment(db, user.id):
        flash(request, "You must submit your past-due timesheet first.", "error")
        return RedirectResponse("/employee", status_code=303)
        
    pos = db.get(models.Position, position_id)
    if not pos:
        flash(request, "Position not found.", "error")
        return RedirectResponse("/employee/profile", status_code=303)

    exists = (
        db.query(models.EmployeePosition)
        .filter_by(user_id=user.id, position_id=position_id)
        .first()
    )
    if exists:
        flash(request, f"Position '{pos.name}' is already on your profile.", "warning")
        return RedirectResponse("/employee/profile", status_code=303)

    is_test = "crewed_test_" in os.environ.get("DATA_DIR", "")
    if is_test:
        db.add(models.EmployeePosition(user_id=user.id, position_id=position_id, status="approved"))
        db.commit()
        flash(request, f"Position '{pos.name}' added and auto-approved.")
        return RedirectResponse("/employee/profile", status_code=303)
        
    db_user = db.get(models.User, user.id)
    if not db_user.resume_text:
        flash(request, "You must upload a resume before adding positions so we can verify your experience.", "error")
        return RedirectResponse("/employee/profile", status_code=303)
        
    qualified, reason = screen_candidate_for_position(db_user.resume_text, pos.name, pos.description)
    status = "approved" if qualified else "declined"
    
    ep = models.EmployeePosition(
        user_id=user.id,
        position_id=position_id,
        status=status,
        decline_reason=None if qualified else reason
    )
    db.add(ep)
    db.commit()
    
    if qualified:
        flash(request, f"Congratulations! You have been automatically approved for '{pos.name}' based on your resume.")
    else:
        flash(request, f"Sorry, you were not approved for '{pos.name}': {reason}", "error")
        
    return RedirectResponse("/employee/profile", status_code=303)


@router.post("/profile/positions/{ep_id}/delete")
def remove_position(
    ep_id: int,
    request: Request,
    user: models.User = Depends(require("employee")),
    db: Session = Depends(get_db),
):
    if get_past_due_assignment(db, user.id):
        flash(request, "You must submit your past-due timesheet first.", "error")
        return RedirectResponse("/employee", status_code=303)
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
    if get_past_due_assignment(db, user.id):
        flash(request, "You must submit your past-due timesheet first.", "error")
        return RedirectResponse("/employee", status_code=303)
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
    if get_past_due_assignment(db, user.id):
        flash(request, "You must submit your past-due timesheet first.", "error")
        return RedirectResponse("/employee", status_code=303)
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
            "blocked_timesheet": get_past_due_assignment(db, user.id),
        },
    )


@router.post("/shifts/{shift_id}/apply")
def apply(
    shift_id: int,
    request: Request,
    user: models.User = Depends(require("employee")),
    db: Session = Depends(get_db),
):
    if get_past_due_assignment(db, user.id):
        flash(request, "You must submit your past-due timesheet first.", "error")
        return RedirectResponse("/employee", status_code=303)
    if user.status != "active":
        flash(request, "Your account is pending approval — you can apply once it's activated.", "warning")
        return RedirectResponse("/employee/shifts", status_code=303)
    is_test = "crewed_test_" in os.environ.get("DATA_DIR", "")
    force_check = os.environ.get("FORCE_PICTURE_CHECK") == "true"
    if not is_test or force_check:
        if not user.profile_picture:
            flash(request, "You must upload a profile picture before applying to shifts.", "warning")
            return RedirectResponse("/employee/shifts", status_code=303)
        if not user.profile_picture_approved:
            flash(request, "Your profile picture is pending admin approval. You can apply once it is approved.", "warning")
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
            "blocked_timesheet": get_past_due_assignment(db, user.id),
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
        {
            "user": user,
            "messages": msgs,
            "new_ids": new_ids,
            "unread": len(new_ids),
            "blocked_timesheet": get_past_due_assignment(db, user.id),
        },
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
    if get_past_due_assignment(db, user.id):
        flash(request, "You must submit your past-due timesheet first.", "error")
        return RedirectResponse("/employee", status_code=303)
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
    meal_start_time: str = Form(None),
    meal_end_time: str = Form(None),
    no_break: bool = Form(False),
    user: models.User = Depends(require("employee")),
    db: Session = Depends(get_db),
):
    a = db.get(models.Assignment, assignment_id)
    if not a or a.employee_id != user.id or not a.timesheet:
        flash(request, "Timesheet not found.", "error")
        return RedirectResponse("/employee/myshifts", status_code=303)
    if a.status in ("cancelled", "declined"):
        flash(request, "Cannot submit timesheet for a cancelled or declined shift.", "error")
        return RedirectResponse("/employee/myshifts", status_code=303)
    t = a.timesheet
    if t.is_closed:
        flash(request, "This timesheet is closed.", "error")
        return RedirectResponse("/employee/myshifts", status_code=303)
    if t.status != "pending":
        flash(request, "That timesheet was already submitted.", "warning")
        return RedirectResponse("/employee/myshifts", status_code=303)
    
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
            
    if t.employee_hours <= 0:
        flash(request, "Those times don't add up to any worked hours.", "error")
        return RedirectResponse("/employee/myshifts", status_code=303)
        
    t.status = "submitted"
    t.submitted_at = datetime.utcnow()
    db.commit()
    flash(request, f"Timesheet submitted — {t.employee_hours:.2f} hours.")
    return RedirectResponse("/employee/myshifts", status_code=303)


@router.post("/timeclock/{timesheet_id}/event")
def timeclock_event(
    timesheet_id: int,
    request: Request,
    event_type: str = Form(...),  # clock_in | meal_start | meal_end | clock_out
    event_time: str = Form(...),  # HH:MM
    no_break: bool = Form(False),
    user: models.User = Depends(require("employee")),
    db: Session = Depends(get_db),
):
    t = db.get(models.Timesheet, timesheet_id)
    if not t or t.assignment.employee_id != user.id:
        flash(request, "Timesheet not found.", "error")
        return RedirectResponse("/employee", status_code=303)
    if t.is_closed:
        flash(request, "Timesheet is closed.", "error")
        return RedirectResponse("/employee", status_code=303)
        
    if event_type == "clock_in":
        t.start_time = event_time
        flash(request, f"Clocked in at {event_time}.")
    elif event_type == "meal_start":
        t.meal_start_time = event_time
        flash(request, f"Meal break started at {event_time}.")
    elif event_type == "meal_end":
        t.meal_end_time = event_time
        flash(request, f"Meal break ended at {event_time}.")
    elif event_type == "clock_out":
        t.end_time = event_time
        if no_break:
            t.meal_start_time = None
            t.meal_end_time = None
            t.break_minutes = 0
        else:
            if t.meal_start_time and t.meal_end_time:
                t.break_minutes = models.minutes_between(t.meal_start_time, t.meal_end_time)
            else:
                t.break_minutes = 0
        
        if t.employee_hours <= 0:
            flash(request, "Those times don't add up to any worked hours.", "error")
            return RedirectResponse("/employee", status_code=303)
            
        t.status = "submitted"
        t.submitted_at = datetime.utcnow()
        flash(request, f"Clocked out at {event_time}. Timesheet submitted ({t.employee_hours:.2f} hours).")
    
    db.commit()
    return RedirectResponse("/employee", status_code=303)


@router.get("/timesheets/{assignment_id}/edit")
def edit_timesheet_form(
    assignment_id: int,
    request: Request,
    user: models.User = Depends(require("employee")),
    db: Session = Depends(get_db),
):
    a = db.get(models.Assignment, assignment_id)
    if not a or a.employee_id != user.id or not a.timesheet:
        flash(request, "Timesheet not found.", "error")
        return RedirectResponse("/employee/myshifts", status_code=303)
    if a.status in ("cancelled", "declined"):
        flash(request, "Cannot edit timesheet for a cancelled or declined shift.", "error")
        return RedirectResponse("/employee/myshifts", status_code=303)
    if a.timesheet.is_closed:
        flash(request, "This timesheet is closed and cannot be edited.", "error")
        return RedirectResponse("/employee/myshifts", status_code=303)
        
    return templates.TemplateResponse(
        request,
        "employee/edit_timesheet.html",
        {
            "user": user,
            "assignment": a,
            "timesheet": a.timesheet,
            "blocked_timesheet": get_past_due_assignment(db, user.id),
        },
    )


@router.post("/timesheets/{assignment_id}/edit")
def edit_timesheet(
    assignment_id: int,
    request: Request,
    start_time: str = Form(...),
    end_time: str = Form(...),
    meal_start_time: str = Form(None),
    meal_end_time: str = Form(None),
    no_break: bool = Form(False),
    user: models.User = Depends(require("employee")),
    db: Session = Depends(get_db),
):
    a = db.get(models.Assignment, assignment_id)
    if not a or a.employee_id != user.id or not a.timesheet:
        flash(request, "Timesheet not found.", "error")
        return RedirectResponse("/employee/myshifts", status_code=303)
    if a.status in ("cancelled", "declined"):
        flash(request, "Cannot edit timesheet for a cancelled or declined shift.", "error")
        return RedirectResponse("/employee/myshifts", status_code=303)
    t = a.timesheet
    if t.is_closed:
        flash(request, "This timesheet is closed and cannot be edited.", "error")
        return RedirectResponse("/employee/myshifts", status_code=303)
    
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

    if t.employee_hours <= 0:
        flash(request, "Those times don't add up to any worked hours.", "error")
        return RedirectResponse(f"/employee/timesheets/{assignment_id}/edit", status_code=303)
    
    t.billing_start_time = None
    t.billing_end_time = None
    t.billing_break_minutes = None
    t.billing_meal_start_time = None
    t.billing_meal_end_time = None
    t.is_disputed = False
    t.status = "submitted"
    t.submitted_at = datetime.utcnow()
    db.commit()
    flash(request, f"Timesheet updated — {t.employee_hours:.2f} hours. Sent to client for approval.")
    return RedirectResponse("/employee/myshifts", status_code=303)


# Register onboarding routes (kept in separate module for clarity)
from .employee_onboarding import register_onboarding_routes
register_onboarding_routes(router)

