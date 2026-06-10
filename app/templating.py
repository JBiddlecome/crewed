from fastapi import Request
from fastapi.templating import Jinja2Templates

from .config import BASE_DIR

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def flash(request: Request, message: str, category: str = "success"):
    request.session.setdefault("_flash", []).append(
        {"message": message, "category": category}
    )


def get_flashed(request: Request):
    return request.session.pop("_flash", [])


def money(value):
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return "—"


def dt(value):
    """'Wed, Jun 10' — portable (no %-d on Windows)."""
    try:
        return f"{value.strftime('%a, %b')} {value.day}"
    except (AttributeError, ValueError):
        return value


templates.env.globals["get_flashed"] = get_flashed
templates.env.filters["money"] = money
templates.env.filters["dt"] = dt

import json as _json


def _from_json(value):
    try:
        return _json.loads(value)
    except Exception:
        return {}


templates.env.filters["from_json"] = _from_json
