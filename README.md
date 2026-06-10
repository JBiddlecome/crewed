# Crewed

On-demand hospitality staffing platform — MVP.

Clients create their own accounts, add locations, choose the positions they staff,
set their own pay rates (validated against the minimum wage at each location's
state), and post shifts. Bill rates are derived automatically from a preset
markup. Qualified employees (matching position + required certifications) browse
and apply for shifts; clients confirm crew and approve submitted timesheets.
Admins manage the catalogs, minimum wages, markup, and employee approvals.

## Run locally

On this machine, just run:

```
.\run.bat
```

(General case: `pip install -r requirements.txt` then `uvicorn main:app --reload` —
make sure `python`/`uvicorn` resolve to the interpreter where the deps are installed.)

Open http://127.0.0.1:8000

The SQLite database and secret key are created automatically in `./data/`
(override with the `DATA_DIR` env var).

**Default admin account** (created on first run):

- Email: `admin@crewed.app`
- Password: `CrewedAdmin1!`

Change this before going live.

## Try the flow

1. Sign up as a business (landing page → "I need staff"), add a location,
   pick positions and set rates, post a shift.
2. Sign up as a worker in a second browser/incognito window, add the matching
   position (+ any required certifications) to your profile.
3. Sign in as admin and approve the worker (Admin → Employees).
4. As the worker, apply to the shift; as the business, confirm them.
5. After the shift, the worker submits hours; the business approves the timesheet.

## Deploy on Render (later)

`render.yaml` is included. It runs the same app with a 1 GB persistent disk
mounted at `/var/data` (set as `DATA_DIR`), so the SQLite database survives
deploys and restarts.

## Stack

- Python / FastAPI, server-rendered Jinja2 templates, vanilla JS (no build step)
- SQLite via SQLAlchemy (file lives in `DATA_DIR`)
- Session cookie auth (signed), pbkdf2 password hashing
- Design system in `static/css/crewed.css` — palette in `Design_Schema.txt`

## Structure

```
main.py                 app entry (uvicorn main:app)
app/
  config.py             DATA_DIR / secret key
  db.py                 engine + session
  models.py             schema
  auth.py               hashing, session deps, role guards
  helpers.py            min wage, markup, eligibility, states
  seed.py               first-run data (admin, catalogs, state min wages)
  templating.py         Jinja env, flash messages, filters
  routers/
    public.py           landing, login, client/employee signup
    client.py           locations, positions+rates, shifts, crew, timesheets
    employee.py         profile, browse/apply, my shifts, submit hours
    admin.py            clients, employees, catalogs, min wage, settings
templates/              Jinja templates per area
static/                 css + js
```
