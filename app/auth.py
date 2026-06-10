import hashlib
import hmac
import secrets

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from .db import get_db
from .models import User

PBKDF2_ITERATIONS = 260_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt.encode(), PBKDF2_ITERATIONS
    ).hex()
    return f"pbkdf2${PBKDF2_ITERATIONS}${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iterations, salt, digest = stored.split("$")
        check = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), salt.encode(), int(iterations)
        ).hex()
        return hmac.compare_digest(check, digest)
    except (ValueError, AttributeError):
        return False


class LoginRequired(Exception):
    pass


def get_current_user(request: Request, db: Session = Depends(get_db)):
    uid = request.session.get("uid")
    if uid:
        user = db.get(User, uid)
        if user and user.status != "disabled":
            return user
    return None


def require(role: str):
    def dependency(request: Request, db: Session = Depends(get_db)) -> User:
        user = get_current_user(request, db)
        if user is None or user.role != role:
            raise LoginRequired()
        return user

    return dependency


def role_home(user: User) -> str:
    return {"admin": "/admin", "client": "/client", "employee": "/employee"}.get(user.role, "/")
