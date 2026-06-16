"""
Auth-protected routes that generate temporary presigned URLs for private R2 files.
"""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from .. import models
from ..auth import get_current_user
from ..auth import LoginRequired
from ..db import get_db
from ..storage import get_presigned_url

router = APIRouter(prefix="/file")

_FOLDER_MAP = {
    "profile-pic": "profile_pictures",
    "resume": "resumes",
    "onboarding": "onboarding",
    "certificate": "certificates",
    "id": "ids",
}


def _require_login(request: Request, db: Session = Depends(get_db)) -> models.User:
    user = get_current_user(request, db)
    if user is None:
        raise LoginRequired()
    return user


@router.get("/{folder}/{filename:path}")
def serve_file(
    folder: str,
    filename: str,
    user: models.User = Depends(_require_login),
):
    folder_key = _FOLDER_MAP.get(folder)
    if not folder_key:
        return RedirectResponse("/", status_code=303)
    url = get_presigned_url(folder_key, filename)
    return RedirectResponse(url, status_code=302)
