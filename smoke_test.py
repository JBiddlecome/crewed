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
    "start_time": "16:00", "end_time": "22:30", "break_minutes": "30",
})
check("Timesheet submitted (6.00 hrs)", "6.00 hours" in r.text)

# Client approves timesheet
r = client_http.post("/client/timesheets/1/approve")
check("Client approves timesheet", "Timesheet approved" in r.text)

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

# Auth guard: logged-out user is redirected
anon = TestClient(app, follow_redirects=False)
r = anon.get("/client/shifts")
check("Auth guard redirects anonymous users", r.status_code == 303)

print()
total, ok = len(passed), sum(passed)
print(f"{ok}/{total} checks passed")
shutil.rmtree(tmp, ignore_errors=True)
raise SystemExit(0 if ok == total else 1)
