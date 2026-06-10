from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from .. import models
from ..auth import get_current_user, hash_password, role_home, verify_password
from ..db import get_db
from ..helpers import US_STATES
from ..templating import flash, templates

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
    company_name: str = Form(...),
    first_name: str = Form(...),
    last_name: str = Form(...),
    email: str = Form(...),
    phone: str = Form(""),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    email = email.strip().lower()
    if len(password) < 8:
        flash(request, "Password must be at least 8 characters.", "error")
        return RedirectResponse("/signup/client", status_code=303)
    if db.query(models.User).filter_by(email=email).first():
        flash(request, "An account with that email already exists.", "error")
        return RedirectResponse("/signup/client", status_code=303)

    company = models.ClientCompany(name=company_name.strip(), phone=phone.strip())
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
    db.add(user)
    db.commit()
    request.session["uid"] = user.id
    flash(request, f"Welcome to Crewed, {company.name}! Add a location to get started.")
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
    db.add(user)
    db.commit()
    request.session["uid"] = user.id
    flash(
        request,
        "Welcome to Crewed! Build your profile while our team reviews your account.",
    )
    return RedirectResponse("/employee", status_code=303)
