import secrets
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, Form, Request, BackgroundTasks
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from .. import models
from ..auth import get_current_user, hash_password, role_home, verify_password
from ..db import get_db
from ..helpers import US_STATES
from ..templating import flash, templates
from ..email import send_confirmation_email, send_password_reset_email

router = APIRouter()


@router.get("/")
def landing(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if user:
        return RedirectResponse(role_home(user), status_code=303)
    return templates.TemplateResponse(request, "landing.html", {"user": None})


@router.get("/login")
def login_form(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if user:
        return RedirectResponse(role_home(user), status_code=303)
    return templates.TemplateResponse(request, "login.html", {"user": None})


@router.post("/login")
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    next: str = Form(""),
    db: Session = Depends(get_db),
):
    user = db.query(models.User).filter_by(email=email.strip().lower()).first()
    if not user or not verify_password(password, user.password_hash):
        flash(request, "Invalid email or password.", "error")
        return RedirectResponse("/login", status_code=303)
    if user.status == "disabled":
        flash(request, "This account has been disabled.", "error")
        return RedirectResponse("/login", status_code=303)
    request.session["uid"] = user.id
    target = next if next.startswith("/") else role_home(user)
    return RedirectResponse(target, status_code=303)


@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)


@router.get("/signup/client")
def signup_client_form(request: Request):
    return templates.TemplateResponse(
        request, "signup_client.html", {"user": None, "states": US_STATES}
    )


@router.post("/signup/client")
def signup_client(
    request: Request,
    has_company_account: str = Form("no"),
    company_name: str = Form(""),
    first_name: str = Form(...),
    last_name: str = Form(...),
    email: str = Form(...),
    phone: str = Form(""),
    password: str = Form(...),
    background_tasks: BackgroundTasks = None,
    db: Session = Depends(get_db),
):
    email = email.strip().lower()
    if len(password) < 8:
        flash(request, "Password must be at least 8 characters.", "error")
        return RedirectResponse("/signup/client", status_code=303)
    if db.query(models.User).filter_by(email=email).first():
        flash(request, "An account with that email already exists.", "error")
        return RedirectResponse("/signup/client", status_code=303)

    if has_company_account == "yes":
        # Create login only — admin will link them to the right company
        user = models.User(
            email=email,
            password_hash=hash_password(password),
            first_name=first_name.strip(),
            last_name=last_name.strip(),
            role="client",
            status="active",
            client_id=None,
            phone=phone.strip(),
        )
        user.confirmation_token = secrets.token_urlsafe(32)
        db.add(user)
        db.commit()
        if background_tasks:
            send_confirmation_email(background_tasks, request, user.email, user.confirmation_token)
        request.session["uid"] = user.id
        flash(request, "Account created! An admin will link you to your company account shortly. Please check your email to confirm your address.")
        return RedirectResponse("/client", status_code=303)

    # New company signup — pending admin approval before placing shifts
    name = company_name.strip()
    if not name:
        flash(request, "Company name is required.", "error")
        return RedirectResponse("/signup/client", status_code=303)
    company = models.ClientCompany(name=name, phone=phone.strip(), portal_approved=False)
    db.add(company)
    db.flush()
    user = models.User(
        email=email,
        password_hash=hash_password(password),
        first_name=first_name.strip(),
        last_name=last_name.strip(),
        role="client",
        status="active",
        client_id=company.id,
        phone=phone.strip(),
    )
    user.confirmation_token = secrets.token_urlsafe(32)
    db.add(user)
    db.commit()
    if background_tasks:
        send_confirmation_email(background_tasks, request, user.email, user.confirmation_token)
    request.session["uid"] = user.id
    flash(request, f"Welcome to Crewed, {company.name}! Set up your account while we review and activate it. Please check your email to confirm your address.")
    return RedirectResponse("/client", status_code=303)


@router.get("/signup/employee")
def signup_employee_form(request: Request):
    return templates.TemplateResponse(
        request, "signup_employee.html", {"user": None, "states": US_STATES}
    )


@router.post("/signup/employee")
def signup_employee(
    request: Request,
    first_name: str = Form(...),
    last_name: str = Form(...),
    email: str = Form(...),
    phone: str = Form(""),
    city: str = Form(""),
    state: str = Form(""),
    zip: str = Form(""),
    password: str = Form(...),
    background_tasks: BackgroundTasks = None,
    db: Session = Depends(get_db),
):
    email = email.strip().lower()
    if len(password) < 8:
        flash(request, "Password must be at least 8 characters.", "error")
        return RedirectResponse("/signup/employee", status_code=303)
    if db.query(models.User).filter_by(email=email).first():
        flash(request, "An account with that email already exists.", "error")
        return RedirectResponse("/signup/employee", status_code=303)

    user = models.User(
        email=email,
        password_hash=hash_password(password),
        first_name=first_name.strip(),
        last_name=last_name.strip(),
        role="employee",
        status="pending",
        phone=phone.strip(),
        city=city.strip(),
        state=state,
        zip=zip.strip(),
    )
    user.confirmation_token = secrets.token_urlsafe(32)
    db.add(user)
    db.commit()
    if background_tasks:
        send_confirmation_email(background_tasks, request, user.email, user.confirmation_token)
    request.session["uid"] = user.id
    flash(
        request,
        "Welcome to Crewed! Build your profile while our team reviews your account. Please check your email to confirm your address.",
    )
    return RedirectResponse("/employee", status_code=303)

@router.get("/confirm-email")
def confirm_email(request: Request, token: str, db: Session = Depends(get_db)):
    user = db.query(models.User).filter_by(confirmation_token=token).first()
    if not user:
        flash(request, "Invalid or expired confirmation link.", "error")
        return RedirectResponse("/", status_code=303)
    user.email_confirmed = True
    user.confirmation_token = None
    db.commit()
    flash(request, "Email successfully confirmed!", "success")
    if "uid" in request.session:
        return RedirectResponse(role_home(user), status_code=303)
    return RedirectResponse("/login", status_code=303)

@router.get("/forgot-password")
def forgot_password_form(request: Request):
    return templates.TemplateResponse(request, "forgot_password.html", {"user": None})

@router.post("/forgot-password")
def forgot_password(
    request: Request,
    email: str = Form(...),
    background_tasks: BackgroundTasks = None,
    db: Session = Depends(get_db),
):
    user = db.query(models.User).filter_by(email=email.strip().lower()).first()
    if user:
        user.reset_token = secrets.token_urlsafe(32)
        user.reset_token_expires = datetime.utcnow() + timedelta(hours=1)
        db.commit()
        if background_tasks:
            send_password_reset_email(background_tasks, request, user.email, user.reset_token)
    flash(request, "If an account exists with that email, a password reset link has been sent.", "success")
    return RedirectResponse("/login", status_code=303)

@router.get("/reset-password")
def reset_password_form(request: Request, token: str, db: Session = Depends(get_db)):
    user = db.query(models.User).filter_by(reset_token=token).first()
    if not user or not user.reset_token_expires or user.reset_token_expires < datetime.utcnow():
        flash(request, "Invalid or expired password reset link.", "error")
        return RedirectResponse("/forgot-password", status_code=303)
    return templates.TemplateResponse(request, "reset_password.html", {"user": None, "token": token})

@router.post("/reset-password")
def reset_password(
    request: Request,
    token: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    if len(password) < 8:
        flash(request, "Password must be at least 8 characters.", "error")
        return RedirectResponse(f"/reset-password?token={token}", status_code=303)
    
    user = db.query(models.User).filter_by(reset_token=token).first()
    if not user or not user.reset_token_expires or user.reset_token_expires < datetime.utcnow():
        flash(request, "Invalid or expired password reset link.", "error")
        return RedirectResponse("/forgot-password", status_code=303)
    
    user.password_hash = hash_password(password)
    user.reset_token = None
    user.reset_token_expires = None
    db.commit()
    flash(request, "Your password has been reset. You can now log in.", "success")
    return RedirectResponse("/login", status_code=303)

