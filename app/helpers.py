import calendar as _calendar
from datetime import date

from sqlalchemy.orm import Session

from . import models

FEDERAL_MIN_WAGE = 7.25

US_STATES = [
    ("AL", "Alabama"), ("AK", "Alaska"), ("AZ", "Arizona"), ("AR", "Arkansas"),
    ("CA", "California"), ("CO", "Colorado"), ("CT", "Connecticut"), ("DE", "Delaware"),
    ("DC", "District of Columbia"), ("FL", "Florida"), ("GA", "Georgia"), ("HI", "Hawaii"),
    ("ID", "Idaho"), ("IL", "Illinois"), ("IN", "Indiana"), ("IA", "Iowa"),
    ("KS", "Kansas"), ("KY", "Kentucky"), ("LA", "Louisiana"), ("ME", "Maine"),
    ("MD", "Maryland"), ("MA", "Massachusetts"), ("MI", "Michigan"), ("MN", "Minnesota"),
    ("MS", "Mississippi"), ("MO", "Missouri"), ("MT", "Montana"), ("NE", "Nebraska"),
    ("NV", "Nevada"), ("NH", "New Hampshire"), ("NJ", "New Jersey"), ("NM", "New Mexico"),
    ("NY", "New York"), ("NC", "North Carolina"), ("ND", "North Dakota"), ("OH", "Ohio"),
    ("OK", "Oklahoma"), ("OR", "Oregon"), ("PA", "Pennsylvania"), ("RI", "Rhode Island"),
    ("SC", "South Carolina"), ("SD", "South Dakota"), ("TN", "Tennessee"), ("TX", "Texas"),
    ("UT", "Utah"), ("VT", "Vermont"), ("VA", "Virginia"), ("WA", "Washington"),
    ("WV", "West Virginia"), ("WI", "Wisconsin"), ("WY", "Wyoming"),
]

STATE_NAMES = dict(US_STATES)


def get_setting(db: Session, key: str, default: str = None) -> str:
    row = db.get(models.Setting, key)
    return row.value if row else default


def set_setting(db: Session, key: str, value: str):
    row = db.get(models.Setting, key)
    if row:
        row.value = value
    else:
        db.add(models.Setting(key=key, value=value))


def effective_markup(db: Session, company: models.ClientCompany) -> float:
    """Markup percent applied on top of pay rate to produce the bill rate."""
    if company is not None and company.markup_override is not None:
        return company.markup_override
    return float(get_setting(db, "markup_percent", "55"))


def compute_bill_rate(pay_rate: float, markup_percent: float) -> float:
    return round(pay_rate * (1 + markup_percent / 100), 2)


def min_wage_for_state(db: Session, state: str) -> float:
    row = db.query(models.MinWage).filter_by(state=state).first()
    return row.rate if row else FEDERAL_MIN_WAGE


def qualifies(db: Session, user: models.User, shift: models.Shift):
    """Whether an employee can work a shift. Returns (ok, reasons)."""
    reasons = []
    position_ids = {p.position_id for p in user.positions}
    if shift.position_id not in position_ids:
        reasons.append(f"{shift.position.name} is not on your profile")

    client_position = (
        db.query(models.ClientPosition)
        .filter_by(client_id=shift.client_id, position_id=shift.position_id)
        .first()
    )
    if client_position and client_position.certs:
        today = date.today()
        held = {
            c.certification_id
            for c in user.certifications
            if c.expires_on is None or c.expires_on >= today
        }
        missing = [
            c.certification.name
            for c in client_position.certs
            if c.certification_id not in held
        ]
        if missing:
            reasons.append("Missing certification: " + ", ".join(missing))
    return (not reasons, reasons)


def refresh_shift_status(db: Session, shift: models.Shift):
    if shift.status in ("cancelled", "completed"):
        return
    db.flush()  # session has autoflush off; the count below must see pending status changes
    confirmed = (
        db.query(models.Assignment)
        .filter_by(shift_id=shift.id, status="confirmed")
        .count()
    )
    shift.status = "filled" if confirmed >= shift.headcount else "open"


def client_position_for(db: Session, shift: models.Shift):
    return (
        db.query(models.ClientPosition)
        .filter_by(client_id=shift.client_id, position_id=shift.position_id)
        .first()
    )


DETAIL_FIELDS = ("parking", "check_in_location", "check_in_contact")


def location_day_for(db: Session, location_id: int, day: date):
    return (
        db.query(models.LocationDay)
        .filter_by(location_id=location_id, date=day)
        .first()
    )


def resolved_details(db: Session, shift: models.Shift) -> dict:
    """Effective parking/check-in details: shift override → that date's
    location-day override → location defaults."""
    day = location_day_for(db, shift.location_id, shift.shift_date)
    out = {}
    for field in DETAIL_FIELDS:
        out[field] = (
            getattr(shift, field, None)
            or (getattr(day, field, None) if day else None)
            or getattr(shift.location, field, None)
        )
    return out


def details_map(db: Session, shifts) -> dict:
    """{shift.id: resolved details} for a list of shifts."""
    return {s.id: resolved_details(db, s) for s in shifts}


def month_weeks(year: int, month: int):
    """Calendar weeks (Sun–Sat) of date objects covering the month."""
    return _calendar.Calendar(firstweekday=6).monthdatescalendar(year, month)


def month_name(month: int) -> str:
    return _calendar.month_name[month]


def unread_count(db: Session, user: models.User) -> int:
    return (
        db.query(models.Message)
        .filter_by(recipient_id=user.id, read_at=None)
        .count()
    )
