from datetime import date, datetime
import os
import uuid
import io

from fastapi import APIRouter, Depends, Form, Request, UploadFile, File
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from .. import models
from ..auth import require
from ..db import get_db
from ..storage import upload_file, get_presigned_url
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


_POSITION_KEY_MAP = {
    "cook": "Cook",
    "prep_cook": "Prep Cook",
    "dishwasher": "Dishwasher",
    "utility": "Utility",
    "server": "Server",
    "host": "Host",
    "runner": "Runner",
    "busser": "Busser",
    "bartender": "Bartender",
    "barback": "Barback",
    "cashier": "Cashier",
    "pastry": "Pastry",
    "baker": "Baker",
    "sushi": "Sushi",
    "concessions": "Concessions",
    "barista": "Barista",
    "valet": "Valet",
    "event_supervisor": "Event Supervisor",
    "sous_chef": "Sous Chef",
}
_POSITION_NAME_TO_KEY = {v: k for k, v in _POSITION_KEY_MAP.items()}

_POSITION_SYSTEM_PROMPT = (
    "You are a resume screener for a hospitality staffing agency.\n"
    "The user will send you a resume (as text or transcribed from a PDF/Word doc/image)\n"
    "together with self-reported experience text.\n"
    "Your job is to decide how qualified the candidate is for specific hospitality positions.\n"
    "Count experience at fine-dining or equivalent venues normally, but treat fast-food\n"
    "experience differently (see updated rules below).\n\n"
    "Target positions\n\n"
    "Evaluate the candidate for these positions:\n\n"
    "Cook\nPrep Cook\nDishwasher\nUtility\nServer\nHost\nRunner\nBusser\nBartender\n"
    "Barback\nCashier\nPastry\nBaker\nSushi\nConcessions\nBarista\nValet\nEvent Supervisor\nSous Chef\n\n"
    "Venue rules (VERY IMPORTANT)\n\n"
    "Fast-food or clearly quick-service chains (McDonald's, Burger King, Wendy's, Taco Bell, KFC, In-N-Out, "
    "Chick-fil-A, similar) DO qualify, BUT ONLY for Level 1 and ONLY if the role performed directly matches "
    "one of the target positions. Fast-food experience should never count toward Level 2 or Level 3.\n\n"
    "For Level 2 or Level 3 qualification count only experience at fine dining or equivalent hospitality "
    "venues: hotels, resorts, country clubs, upscale restaurants, steakhouses, chef-driven or white-tablecloth "
    "concepts, banquet/catering companies, convention centers, stadiums, arenas, large event venues, or "
    "corporate/contract dining for companies, universities, hospitals when clearly hospitality-related.\n\n"
    "Ignore non-hospitality jobs entirely (admin, warehouse, rideshare, retail, etc.).\n\n"
    "Special rule for Event Supervisor: requires a minimum of 3 years of management or supervisory experience "
    "in the hotel, food & beverage, or hospitality industry.\n\n"
    "Special rule for Sous Chef: requires a minimum of 3 years of qualifying experience. If less than 3 years, "
    "you MUST assign no_experience. 3-5 years = level_2; >5 years = level_3.\n\n"
    "Experience categorization:\n"
    "- level_1: less than 2 years combined qualifying experience (all fast-food always counts as level_1)\n"
    "- level_2: 2 to 5 years combined qualifying experience at non-fast-food venues\n"
    "- level_3: more than 5 years qualifying experience at non-fast-food venues\n"
    "- no_experience: neither qualifying nor fast-food experience for that role\n\n"
    "Output format — return valid JSON ONLY, no text outside the JSON:\n"
    '{"candidate_summary":{"hospitality_experience_overview":"","total_hospitality_years_estimate":0.0,'
    '"notable_venues":[],"notes_on_fast_food_or_non_qualifying_experience":""},'
    '"positions":{'
    '"cook":{"status":"no_experience","estimated_years":0.0,"confidence":0.0,"reasons":[]},'
    '"prep_cook":{"status":"no_experience","estimated_years":0.0,"confidence":0.0,"reasons":[]},'
    '"dishwasher":{"status":"no_experience","estimated_years":0.0,"confidence":0.0,"reasons":[]},'
    '"utility":{"status":"no_experience","estimated_years":0.0,"confidence":0.0,"reasons":[]},'
    '"server":{"status":"no_experience","estimated_years":0.0,"confidence":0.0,"reasons":[]},'
    '"host":{"status":"no_experience","estimated_years":0.0,"confidence":0.0,"reasons":[]},'
    '"runner":{"status":"no_experience","estimated_years":0.0,"confidence":0.0,"reasons":[]},'
    '"busser":{"status":"no_experience","estimated_years":0.0,"confidence":0.0,"reasons":[]},'
    '"bartender":{"status":"no_experience","estimated_years":0.0,"confidence":0.0,"reasons":[]},'
    '"barback":{"status":"no_experience","estimated_years":0.0,"confidence":0.0,"reasons":[]},'
    '"cashier":{"status":"no_experience","estimated_years":0.0,"confidence":0.0,"reasons":[]},'
    '"pastry":{"status":"no_experience","estimated_years":0.0,"confidence":0.0,"reasons":[]},'
    '"baker":{"status":"no_experience","estimated_years":0.0,"confidence":0.0,"reasons":[]},'
    '"sushi":{"status":"no_experience","estimated_years":0.0,"confidence":0.0,"reasons":[]},'
    '"concessions":{"status":"no_experience","estimated_years":0.0,"confidence":0.0,"reasons":[]},'
    '"barista":{"status":"no_experience","estimated_years":0.0,"confidence":0.0,"reasons":[]},'
    '"valet":{"status":"no_experience","estimated_years":0.0,"confidence":0.0,"reasons":[]},'
    '"event_supervisor":{"status":"no_experience","estimated_years":0.0,"confidence":0.0,"reasons":[]},'
    '"sous_chef":{"status":"no_experience","estimated_years":0.0,"confidence":0.0,"reasons":[]}'
    "}}"
)


_PICTURE_PROMPT = (
    "Evaluate this profile picture. Approve if ALL criteria are met:\n"
    "- Face forward (deny if turned 45°+ sideways or obscured)\n"
    "- Face close to camera and in focus\n"
    "- No sunglasses or masks (hats OK if eyes visible; prescription glasses are ALWAYS OK if eyes are visible through lenses)\n"
    "- No heavy beauty filters or AR distortions\n"
    "- No nudity, hate symbols, offensive gestures, or weapons\n\n"
    'Respond with JSON only:\n{"suitable": true|false, "reason": "polite explanation if false", "confidence": 0.0-1.0}'
)


def evaluate_profile_picture(filename: str) -> tuple[bool, str]:
    """Returns (approved, reason). reason='pending' signals fall-back to manual admin approval."""
    is_test = "crewed_test_" in os.environ.get("DATA_DIR", "")
    if is_test:
        return True, "test"

    api_key = os.environ.get("PROFILE_PICTURE_APPROVAL")
    if not api_key:
        return False, "pending"

    import json
    import logging

    try:
        from openai import OpenAI

        image_url = get_presigned_url("profile_pictures", filename, expiry=300)
        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": _PICTURE_PROMPT},
                    {"type": "image_url", "image_url": {"url": image_url, "detail": "low"}},
                ],
            }],
            response_format={"type": "json_object"},
            temperature=0.2,
        )
        content = response.choices[0].message.content or ""
        result = json.loads(content)
        return bool(result.get("suitable", False)), result.get("reason", "")
    except Exception as e:
        logging.exception("Profile picture AI evaluation failed")
        return False, f"ai_error: {e}"


def screen_candidate_for_position(resume_text: str, position_name: str, experience_text: str = "") -> tuple[bool, str]:
    """Returns (approved, reason). Approved positions are always set to level 2 by default."""
    is_test = "crewed_test_" in os.environ.get("DATA_DIR", "")
    api_key = os.environ.get("POSITION_REQUESTS")
    if is_test or not api_key:
        return True, "Auto-approved (test mode or missing POSITION_REQUESTS key)"

    position_key = _POSITION_NAME_TO_KEY.get(position_name)
    if not position_key:
        return False, f"Unknown position: {position_name}"

    try:
        from openai import OpenAI
        import json
        client = OpenAI(api_key=api_key)

        user_message = (
            "Resume and experience information for evaluation. Return only the JSON schema provided.\n\n"
            f"User provided experience:\n{experience_text or 'Not provided'}\n\n"
            f"Resume text:\n{resume_text}"
        )

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": _POSITION_SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
        )

        content = response.choices[0].message.content or ""
        result = json.loads(content)

        pos_result = result.get("positions", {}).get(position_key, {})
        status = pos_result.get("status", "no_experience")
        reasons = pos_result.get("reasons", [])
        reason_str = "; ".join(reasons) if isinstance(reasons, list) else str(reasons)
        if not reason_str:
            reason_str = "No specific reasons provided by AI."

        if status == "no_experience":
            return False, reason_str
        return True, reason_str

    except Exception as e:
        return False, f"AI verification failed: {str(e)}"


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

    try:
        upload_file(photo.file.read(), "profile_pictures", filename)
    except Exception as e:
        flash(request, f"Failed to save profile picture: {e}", "error")
        return RedirectResponse("/employee/profile", status_code=303)

    db_user = db.get(models.User, user.id)
    db_user.profile_picture = filename
    approved, reason = evaluate_profile_picture(filename)
    db_user.profile_picture_approved = approved
    db_user.profile_picture_declined = not approved and reason not in ("pending",) and not reason.startswith("ai_error:")
    db.commit()

    if approved:
        is_test = "crewed_test_" in os.environ.get("DATA_DIR", "")
        flash(request, "Profile picture uploaded successfully." + (" (Auto-approved under test mode)" if is_test else ""))
    elif reason == "pending":
        flash(request, "Profile picture uploaded successfully. Pending admin approval.")
    elif reason.startswith("ai_error:"):
        flash(request, f"Profile picture could not be evaluated ({reason}). Pending admin approval.", "warning")
    else:
        flash(request, "Please upload a clear passport style photo looking directly into the camera with no filters or anything covering your face.", "error")
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

    try:
        resume.file.seek(0)
        upload_file(resume.file.read(), "resumes", filename)
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
        db.add(models.EmployeePosition(user_id=user.id, position_id=position_id, status="approved", level=2))
        db.commit()
        flash(request, f"Position '{pos.name}' added and auto-approved (Level 2).")
        return RedirectResponse("/employee/profile", status_code=303)
        
    db_user = db.get(models.User, user.id)
    if not db_user.resume_text:
        flash(request, "You must upload a resume before adding positions so we can verify your experience.", "error")
        return RedirectResponse("/employee/profile", status_code=303)
        
    qualified, reason = screen_candidate_for_position(db_user.resume_text, pos.name)
    status = "approved" if qualified else "declined"

    ep = models.EmployeePosition(
        user_id=user.id,
        position_id=position_id,
        status=status,
        level=2,
        decline_reason=None if qualified else reason,
    )
    db.add(ep)
    db.commit()

    if qualified:
        flash(request, f"Congratulations! You have been approved for '{pos.name}' (Level 2) based on your resume.")
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
    approved_position_ids = {
        p.position_id for p in user.positions if p.status == "approved"
    }
    open_shifts = (
        db.query(models.Shift)
        .filter(
            models.Shift.status == "open",
            models.Shift.shift_date >= today,
            models.Shift.position_id.in_(approved_position_ids) if approved_position_ids else False,
        )
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

