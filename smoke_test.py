"""End-to-end smoke test for the Crewed MVP. Run: python smoke_test.py
Uses a throwaway DATA_DIR so it never touches real data."""

import os
import shutil
import tempfile
from datetime import date, timedelta

tmp = tempfile.mkdtemp(prefix="crewed_test_")
os.environ["DATA_DIR"] = tmp

from fastapi.testclient import TestClient  # noqa: E402

from main import app  # noqa: E402

passed = []


def check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    passed.append(condition)
    print(f"[{status}] {label}{(' — ' + detail) if detail and not condition else ''}")


tomorrow = (date.today() + timedelta(days=1)).isoformat()

client_http = TestClient(app, follow_redirects=True)
worker_http = TestClient(app, follow_redirects=True)
admin_http = TestClient(app, follow_redirects=True)

# Landing + auth pages
r = client_http.get("/")
check("Landing page renders", r.status_code == 200 and "Crewed" in r.text)
check("Login page renders", client_http.get("/login").status_code == 200)

# Client signup
r = client_http.post("/signup/client", data={
    "company_name": "Sunset Catering", "first_name": "Cara", "last_name": "Client",
    "email": "cara@sunset.test", "phone": "555-0100", "password": "password123",
})
check("Client signup lands on dashboard", r.status_code == 200 and "Sunset Catering" in r.text)

# Add a location (CA -> min wage 16.50 seeded)
r = client_http.post("/client/locations", data={
    "name": "Sunset HQ", "address1": "1 Beach Way", "address2": "",
    "city": "Los Angeles", "state": "CA", "zip": "90001",
})
check("Location added", r.status_code == 200 and "Sunset HQ" in r.text)

# Rate below CA min wage must be rejected
r = client_http.post("/client/positions", data={
    "position_id": "1", "pay_rate": "10.00", "requirements": "", "cert_ids": [],
})
check("Below-min-wage rate rejected", "must be at least" in r.text)

# Valid position with a required certification (cert id 1 = first seeded)
r = client_http.post("/client/positions", data={
    "position_id": "1", "pay_rate": "22.00", "requirements": "Black slacks",
    "cert_ids": ["1"],
})
check("Position added at $22.00", r.status_code == 200 and "22.00" in r.text)
check("Bill rate shows 55% markup ($34.10)", "34.10" in r.text)

# Post a shift
r = client_http.post("/client/shifts/new", data={
    "location_id": "1", "client_position_id": "1", "shift_date": tomorrow,
    "start_time": "16:00", "end_time": "22:00", "headcount": "1",
    "pay_rate": "22.00", "notes": "Ask for Maria",
})
check("Shift posted", r.status_code == 200 and "Shift posted" in r.text)

# Shift below min wage rejected
r = client_http.post("/client/shifts/new", data={
    "location_id": "1", "client_position_id": "1", "shift_date": tomorrow,
    "start_time": "16:00", "end_time": "22:00", "headcount": "1",
    "pay_rate": "12.00", "notes": "",
})
check("Shift below min wage rejected", "minimum wage" in r.text)

# Worker signup (pending status)
r = worker_http.post("/signup/employee", data={
    "first_name": "Wes", "last_name": "Worker", "email": "wes@worker.test",
    "phone": "555-0200", "city": "Los Angeles", "state": "CA", "zip": "90001",
    "password": "password123",
})
check("Worker signup lands on dashboard", r.status_code == 200 and "reviewed" in r.text)

# Worker adds position + cert
worker_http.post("/employee/profile/positions", data={"position_id": "1"})
r = worker_http.post("/employee/profile/certs", data={"certification_id": "1", "expires_on": ""})
check("Worker profile built", r.status_code == 200)

# Worker can see the shift but cannot apply while pending
r = worker_http.get("/employee/shifts")
check("Worker sees the open shift", "Sunset Catering" in r.text)
r = worker_http.post("/employee/shifts/1/apply")
check("Pending worker blocked from applying", "pending approval" in r.text)

# Admin approves the worker
r = admin_http.post("/login", data={"email": "admin@crewed.app", "password": "CrewedAdmin1!"})
check("Admin login", r.status_code == 200)
r = admin_http.post("/admin/employees/3/status", data={"new_status": "active"})
check("Admin approves worker", "approved" in r.text)
check("Admin pages render", all(
    admin_http.get(p).status_code == 200
    for p in ["/admin", "/admin/clients", "/admin/clients/1", "/admin/employees",
              "/admin/positions", "/admin/certifications", "/admin/minwage",
              "/admin/settings", "/admin/shifts"]
))

# Worker applies
r = worker_http.post("/employee/shifts/1/apply")
check("Worker applies to shift", "Applied to" in r.text)

# Client confirms
r = client_http.get("/client/shifts/1")
check("Client sees applicant", "Wes Worker" in r.text)
r = client_http.post("/client/assignments/1/confirm")
check("Client confirms worker", "confirmed for the shift" in r.text)
r = client_http.get("/client/shifts/1")
check("Shift now filled", "filled" in r.text.lower())

# Worker submits timesheet (shift is tomorrow; needs_timesheet requires date <= today,
# so submit directly — the route allows pending timesheets any time)
r = worker_http.post("/employee/timesheets/1/submit", data={
    "start_time": "16:00", "end_time": "22:30", "meal_start_time": "19:00", "meal_end_time": "19:30",
})
check("Timesheet submitted (6.00 hrs)", "6.00 hours" in r.text)

# Client approves timesheet with adjustment (dispute)
r = client_http.post("/client/timesheets/1/approve", data={
    "start_time": "16:00", "end_time": "22:00", "meal_start_time": "19:00", "meal_end_time": "19:30", "dispute_reason": "Client sent home early"
})
check("Client approves timesheet with adjustment (5.50 hrs billing)", "approved" in r.text and "Hours adjusted for billing" in r.text)

# Employee history shows both hours
r = worker_http.get("/employee/myshifts")
check("Employee history shows dispute warning", "Adjusted for billing" in r.text)

# Admin dashboard shows the dispute
r = admin_http.get("/admin")
check("Admin dashboard shows disputed timesheet", "Client sent home early" in r.text and "Wes Worker" in r.text)


# ---- Location details / calendar / day view / messaging ----

# Client sets location default details via edit page
r = client_http.post("/client/locations/1/edit", data={
    "name": "Sunset HQ", "address1": "1 Beach Way", "address2": "",
    "city": "Los Angeles", "state": "CA", "zip": "90001",
    "parking": "Lot B off 5th St", "check_in_location": "Loading dock",
    "check_in_contact": "Maria (Event Manager)",
})
check("Location defaults saved", "updated" in r.text and "Lot B" in r.text)

# Calendar view shows the location chip for the shift date
r = client_http.get("/client/shifts")
check("Calendar renders with location chip", "Sunset HQ" in r.text and "staffed" in r.text)

# Day view shows shifts + inherited details
day_url = f"/client/days/1/{tomorrow}"
r = client_http.get(day_url)
check("Day view lists the shift", "Maria (Event Manager)" in r.text or "Loading dock" in r.text)

# Per-date override applies to the date
r = client_http.post(day_url + "/details", data={
    "parking": "Valet only today", "check_in_location": "Main lobby",
    "check_in_contact": "James (Banquet Captain)",
})
check("Date details saved", "apply to every shift" in r.text)

# Worker sees the date-level details on My Shifts
r = worker_http.get("/employee/myshifts")
check("Worker sees date details", "Main lobby" in r.text and "James (Banquet Captain)" in r.text)

# Shift-level override beats the date override
r = client_http.post("/client/shifts/1/details", data={
    "parking": "", "check_in_location": "Stage door",
    "check_in_contact": "", "notes": "Ask for Maria",
})
check("Shift override saved", "Shift details updated" in r.text)
r = worker_http.get("/employee/myshifts")
check("Worker sees shift override + inherited contact",
      "Stage door" in r.text and "James (Banquet Captain)" in r.text)

# Message all crew for the date/location
r = client_http.post(day_url + "/message", data={"body": "Start moved up 30 minutes."})
check("Day message sent to crew", "Message sent to 1 crew member" in r.text)

# Direct message to one employee on the shift
r = client_http.post("/client/assignments/1/message", data={"body": "Bring your bar kit."})
check("Direct message sent", "Message sent to Wes Worker" in r.text)

# Worker has unread badge, then inbox shows both and marks read
r = worker_http.get("/employee")
check("Unread count on worker dashboard", ">2</div>" in r.text)
r = worker_http.get("/employee/notifications")
check("Inbox shows both messages",
      "Start moved up 30 minutes." in r.text and "Bring your bar kit." in r.text)
r = worker_http.get("/employee/notifications")
check("Messages marked read on view", "nav-badge\">new" not in r.text)

# Worker pages all render
check("Worker pages render", all(
    worker_http.get(p).status_code == 200
    for p in ["/employee", "/employee/profile", "/employee/shifts", "/employee/myshifts"]
))
check("Client pages render", all(
    client_http.get(p).status_code == 200
    for p in ["/client", "/client/locations", "/client/locations/1/edit",
              "/client/positions", "/client/shifts", "/client/shifts/new",
              "/client/shifts/1", "/client/timesheets", day_url]
))

# ---- A-List & Block List Tests ----
# Client pages for A-List & Block List render
r = client_http.get("/client/crew")
check("Crew management page renders", r.status_code == 200 and "A-List" in r.text)

# Add worker to A-List
r = client_http.post("/client/crew/alist", data={"employee_id": "3", "location_id": "0", "notes": "Top tier"})
check("Worker added to A-List", r.status_code == 200 and "Top tier" in r.text)

# Post another future shift for next week (next week's date)
next_week = (date.today() + timedelta(days=7)).isoformat()
r = client_http.post("/client/shifts/new", data={
    "location_id": "1", "client_position_id": "1", "shift_date": next_week,
    "start_time": "12:00", "end_time": "18:00", "headcount": "1",
    "pay_rate": "22.00", "notes": "Future shift for block test",
})
check("Future shift posted", r.status_code == 200 and "Shift posted" in r.text)

# Worker applies to future shift (Shift ID 2)
r = worker_http.post("/employee/shifts/2/apply")
check("Worker applies to future shift", "Applied to" in r.text)

# Client confirms worker for future shift
r = client_http.post("/client/assignments/2/confirm")
check("Client confirms worker for future shift", "confirmed" in r.text)

# Add worker to Block List (this should cancel their confirmed future shift)
r = client_http.post("/client/crew/blocklist", data={"employee_id": "3", "location_id": "0", "reason": "No longer welcome"})
check("Worker blocked", r.status_code == 200 and "No longer welcome" in r.text)

# Check that the future shift assignment status is now cancelled
r = worker_http.get("/employee/myshifts")
check("Future assignment auto-cancelled by block list", "cancelled" in r.text.lower())

# Worker tries to apply to a third shift (Shift ID 3)
r = client_http.post("/client/shifts/new", data={
    "location_id": "1", "client_position_id": "1", "shift_date": next_week,
    "start_time": "18:00", "end_time": "22:00", "headcount": "1",
    "pay_rate": "22.00", "notes": "Third shift",
})
check("Third shift posted", r.status_code == 200)

r = worker_http.post("/employee/shifts/3/apply")
check("Blocked worker prevented from applying", "block list" in r.text.lower())

# Remove from Block List (unblock)
r = client_http.post("/client/crew/blocklist/1/delete")
check("Worker unblocked", r.status_code == 200)

# ---- Admin Crew Management Tests ----
# Admin views the client detail page (should show the current A-List entry 'Top tier')
r = admin_http.get("/admin/clients/1")
check("Admin views client detail page with crew lists", r.status_code == 200 and "Top tier" in r.text and "Add to A-List" in r.text)

# Admin blocks the worker
r = admin_http.post("/admin/clients/1/crew/blocklist", data={"employee_id": "3", "location_id": "0", "reason": "Blocked by admin"})
check("Admin blocks worker", r.status_code == 200 and "Blocked by admin" in r.text)

# Admin removes the worker from Block List (Block list entry ID 1, since the table was empty and ID was reused)
r = admin_http.post("/admin/clients/1/crew/blocklist/1/delete")
check("Admin unblocks worker", r.status_code == 200 and "Removed Wes Worker from Block List" in r.text)

# ---- Timesheet Locking & Closeout, Past-Due Hard Block, and Timeclock Tests ----

# 1. Post a shift for tomorrow first, so the worker can apply to it
tomorrow_str = (date.today() + timedelta(days=2)).isoformat()
r = client_http.post("/client/shifts/new", data={
    "location_id": "1", "client_position_id": "1", "shift_date": tomorrow_str,
    "start_time": "09:00", "end_time": "17:00", "headcount": "1",
    "pay_rate": "22.00", "notes": "Past-due shift test",
})
check("Past-due shift posted (originally for tomorrow)", r.status_code == 200)

# Worker applies to it (Shift ID 4)
r = worker_http.post("/employee/shifts/4/apply")
check("Worker applies to shift", "Applied to" in r.text)

# Client confirms worker for it (Assignment ID 3, since we cancelled 2)
r = client_http.post("/client/assignments/3/confirm")
check("Client confirms worker", "confirmed" in r.text)

# Now, update the shift date programmatically in the database to yesterday!
from app.db import SessionLocal
from app import models
db_sess = SessionLocal()
db_sess.query(models.Shift).filter_by(id=4).update({"shift_date": date.today() - timedelta(days=1)})
db_sess.commit()
db_sess.close()


# Since the shift is in the past and status is pending, worker must be blocked
r = worker_http.get("/employee")
check("Worker blocked by past-due timesheet modal", "hard-block-overlay" in r.text and "Submit Missing Timesheet" in r.text)

# Worker tries to update profile while blocked (POST request)
r = worker_http.post("/employee/profile/info", data={"phone": "123-4567"})
check("Worker POST profile action rejected while blocked", "past-due timesheet" in r.text)

# Worker submits yesterday's timesheet to clear the block (using meal times)
r = worker_http.post("/employee/timesheets/3/submit", data={
    "start_time": "09:00", "end_time": "17:00", "meal_start_time": "12:00", "meal_end_time": "12:30",
})
check("Worker submits past-due timesheet", "submitted" in r.text)

# Worker is no longer blocked
r = worker_http.get("/employee")
check("Worker no longer blocked after submitting", "hard-block-overlay" not in r.text)

# Client approves yesterday's timesheet (Timesheet ID 3)
r = client_http.post("/client/timesheets/3/approve", data={
    "start_time": "09:00", "end_time": "17:00", "meal_start_time": "12:00", "meal_end_time": "12:30",
})
check("Client approves yesterday's timesheet", "approved" in r.text)

# Admin views and edits yesterday's timesheet (Timesheet ID 3) to adjust pay and billing hours
r = admin_http.post("/admin/timesheets/3/edit", data={
    "start_time": "09:00", "end_time": "17:00", "meal_start_time": "12:00", "meal_end_time": "12:30",
    "billing_start_time": "09:00", "billing_end_time": "16:30", "billing_meal_start_time": "12:00", "billing_meal_end_time": "12:30",
    "is_disputed": "true", "dispute_reason": "Admin adjustment",
})
check("Admin successfully overrides timesheet hours", r.status_code == 200)

# Admin closes out the timesheet (locks it)
r = admin_http.post("/admin/timesheets/3/close")
check("Admin closes timesheet", "closed (locked)" in r.text)

# Worker tries to edit closed timesheet (POST /employee/timesheets/3/edit)
r = worker_http.post("/employee/timesheets/3/edit", data={
    "start_time": "09:00", "end_time": "17:00", "meal_start_time": "12:00", "meal_end_time": "12:30",
})
check("Worker edit blocked on closed timesheet", "timesheet is closed" in r.text)

# Client tries to edit closed timesheet (POST /client/timesheets/3/edit)
r = client_http.post("/client/timesheets/3/edit", data={
    "start_time": "09:00", "end_time": "17:00", "meal_start_time": "12:00", "meal_end_time": "12:30",
})
check("Client edit blocked on closed timesheet", "timesheet is closed" in r.text)

# 2. Timeclock functionality test
# Post a shift for today
today_str = date.today().isoformat()
r = client_http.post("/client/shifts/new", data={
    "location_id": "1", "client_position_id": "1", "shift_date": today_str,
    "start_time": "10:00", "end_time": "18:00", "headcount": "1",
    "pay_rate": "22.00", "notes": "Timeclock shift test",
})
check("Today's shift posted", r.status_code == 200)

# Worker applies
r = worker_http.post("/employee/shifts/5/apply")
check("Worker applies to today's shift", r.status_code == 200)

# Client confirms
r = client_http.post("/client/assignments/4/confirm")
check("Client confirms worker for today", r.status_code == 200)

# Worker views dashboard today, should see the active timeclock
r = worker_http.get("/employee")
check("Worker dashboard shows today's timeclock", "Today's Timeclock" in r.text and "Clock In" in r.text)

# Worker clocks in (Timesheet ID 4)
r = worker_http.post("/employee/timeclock/4/event", data={"event_type": "clock_in", "event_time": "10:05"})
check("Worker clocks in successfully", "Clocked in at 10:05" in r.text)

# Worker starts break
r = worker_http.post("/employee/timeclock/4/event", data={"event_type": "meal_start", "event_time": "13:00"})
check("Worker starts meal break", "Meal break started at 13:00" in r.text)

# Worker ends break
r = worker_http.post("/employee/timeclock/4/event", data={"event_type": "meal_end", "event_time": "13:30"})
check("Worker ends meal break", "Meal break ended at 13:30" in r.text)

# Worker clocks out (timesheet is submitted)
r = worker_http.post("/employee/timeclock/4/event", data={"event_type": "clock_out", "event_time": "18:05"})
check("Worker clocks out successfully", "Clocked out at 18:05" in r.text and "submitted" in r.text)


# Auth guard: logged-out user is redirected
anon = TestClient(app, follow_redirects=False)
r = anon.get("/client/shifts")
check("Auth guard redirects anonymous users", r.status_code == 303)


# --- Profile Picture & Resume / AI Position Verification Integration Tests ---
import io
from app.db import SessionLocal
import app.models as models
import app.routers.employee as employee_router

# 1. Test profile picture check restriction when FORCE_PICTURE_CHECK is active
os.environ["FORCE_PICTURE_CHECK"] = "true"
next_week = (date.today() + timedelta(days=7)).isoformat()

# Post a new open shift (will be shift 6)
r = client_http.post("/client/shifts/new", data={
    "location_id": "1", "client_position_id": "1", "shift_date": next_week,
    "start_time": "16:00", "end_time": "22:00", "headcount": "1",
    "pay_rate": "22.00", "notes": "Profile pic test",
})
check("Shift 6 posted for profile pic check", r.status_code == 200)

# Worker tries to apply to shift 6 without profile picture uploaded
r = worker_http.post("/employee/shifts/6/apply")
check("Worker without profile picture blocked from applying", "must upload a profile picture" in r.text)

# Worker uploads profile picture
photo_data = b"fake-jpeg-photo-content"
r = worker_http.post("/employee/profile/photo", files={"photo": ("test_pic.jpg", io.BytesIO(photo_data), "image/jpeg")})
check("Profile picture uploaded successfully", "uploaded successfully" in r.text)

# Manually set user's profile picture approval to false to simulate pending status
db_sess = SessionLocal()
wes = db_sess.query(models.User).filter_by(email="wes@worker.test").first()
wes.profile_picture_approved = False
db_sess.commit()

# Worker tries to apply to shift 6 with pending profile picture approval
r = worker_http.post("/employee/shifts/6/apply")
check("Worker with pending profile picture blocked from applying", "pending admin approval" in r.text)

# Admin approves the photo
r = admin_http.post("/admin/employees/3/approve_photo")
check("Admin approves profile picture", "Approved profile picture for Wes" in r.text)

# Worker applies to shift 6 successfully now
r = worker_http.post("/employee/shifts/6/apply")
check("Worker with approved profile picture can apply", "Applied to" in r.text)

# 2. Test Resume upload and AI position verification logic
# Save original DATA_DIR and monkeypatch screen_candidate_for_position
orig_env_data_dir = os.environ.get("DATA_DIR")
os.environ["DATA_DIR"] = "/tmp/real_run_to_bypass_is_test"  # Bypasses is_test so it runs screen_candidate_for_position

orig_screen = employee_router.screen_candidate_for_position
employee_router.screen_candidate_for_position = lambda r, p, d: (False, "Candidate lacks 3 years of event supervision experience.")

# Worker tries to add position 12 (Event Captain) without resume
r = worker_http.post("/employee/profile/positions", data={"position_id": "12"})
check("Worker without resume blocked from adding position", "must upload a resume" in r.text)

# Worker uploads resume
resume_data = b"Wes Worker resume content."
r = worker_http.post("/employee/profile/resume", files={"resume": ("resume.txt", io.BytesIO(resume_data), "text/plain")})
check("Resume uploaded successfully", "Resume uploaded and processed successfully" in r.text)

# Worker adds position 12, AI screens it and declines it
r = worker_http.post("/employee/profile/positions", data={"position_id": "12"})
check("Worker screened and declined by AI position approver", "lacks 3 years of event supervision" in r.text)

# Find the employee_position record id from db
ep = db_sess.query(models.EmployeePosition).filter_by(user_id=3, position_id=12).first()
check("Employee position created as declined with reason", ep is not None and ep.status == "declined")

# Admin manually overrides and approves the position
r = admin_http.post(f"/admin/employee-position/{ep.id}/approve")
check("Admin manually overrides and approves position", "Manually approved Event Captain for Wes" in r.text)

# Verify database status is updated
db_sess.refresh(ep)
check("Employee position status is approved in db", ep.status == "approved")

db_sess.close()

# ---- Official W-4 / I-9 PDF generation ----

from app.pdf_forms import fill_i9, fill_w4  # noqa: E402

_employer = {"name": "Crewed Staffing LLC", "address": "100 Main St, LA, CA", "ein": "12-3456789"}
_w4_pdf = fill_w4(
    {
        "first_name": "Jane", "last_name": "Doe", "ssn": "123-45-6789",
        "address": "456 Oak Ave", "city_state_zip": "Los Angeles, CA 90001",
        "filing_status": "Married", "multiple_jobs": "check_box",
        "qualifying_children": "2", "other_dependents": "1",
        "other_income": "1200", "deductions": "0", "extra_withholding": "50",
        "signature": "Jane Doe", "sign_date": "2026-06-10",
    },
    _employer,
)
check("W-4 PDF generates", _w4_pdf[:5] == b"%PDF-" and len(_w4_pdf) > 50_000)

_i9_pdf = fill_i9(
    {
        "first_name": "Jane", "middle_initial": "Q", "last_name": "Doe",
        "address": "456 Oak Ave", "apt": "2B", "city": "Los Angeles",
        "state": "CA", "zip": "90001", "dob": "1990-03-15", "ssn": "123-45-6789",
        "email": "jane@example.com", "phone": "555-0100",
        "citizenship_status": "work_authorized", "alien_reg_num": "A123456789",
        "work_auth_expiry": "2027-09-30", "foreign_passport": "P9876543 (Canada)",
        "signature": "Jane Doe", "sign_date": "2026-06-10",
        "doc_list_a": "Employment Authorization Document (Form I-766)",
    },
    _employer,
)
check("I-9 PDF generates", _i9_pdf[:5] == b"%PDF-" and len(_i9_pdf) > 100_000)

# Restore environment and monkeypatch
os.environ["DATA_DIR"] = orig_env_data_dir
employee_router.screen_candidate_for_position = orig_screen
os.environ["FORCE_PICTURE_CHECK"] = "false"


print()
total, ok = len(passed), sum(passed)
print(f"{ok}/{total} checks passed")
shutil.rmtree(tmp, ignore_errors=True)
raise SystemExit(0 if ok == total else 1)


