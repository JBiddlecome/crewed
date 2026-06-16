from dotenv import load_dotenv
load_dotenv()

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app import seed
from app.auth import LoginRequired
from app.config import BASE_DIR, DATA_DIR, SECRET_KEY
from app.db import ensure_schema
from app.routers import admin, client, employee, public, recruiting

app = FastAPI(title="Crewed")

app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, max_age=60 * 60 * 24 * 14)
# Ensure upload directories exist
(DATA_DIR / "uploads" / "profile_pics").mkdir(parents=True, exist_ok=True)
(DATA_DIR / "uploads" / "resumes").mkdir(parents=True, exist_ok=True)
(DATA_DIR / "uploads" / "onboarding_pdfs").mkdir(parents=True, exist_ok=True)
(DATA_DIR / "uploads" / "onboarding_completed").mkdir(parents=True, exist_ok=True)

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
app.mount("/uploads", StaticFiles(directory=str(DATA_DIR / "uploads")), name="uploads")

ensure_schema()
seed.run()


@app.exception_handler(LoginRequired)
def login_required_handler(request: Request, exc: LoginRequired):
    return RedirectResponse("/login", status_code=303)


app.include_router(public.router)
app.include_router(admin.router)
app.include_router(recruiting.router)
app.include_router(client.router)
app.include_router(employee.router)
