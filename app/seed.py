"""Idempotent first-run seed: admin account, position/cert catalogs,
state minimum wages (editable defaults — verify before production), settings."""

from .auth import hash_password
from .db import SessionLocal
from .models import Certification, MinWage, Position, Setting, User

ADMIN_EMAIL = "admin@crewed.app"
ADMIN_PASSWORD = "CrewedAdmin1!"

POSITIONS = [
    "Server", "Bartender", "Barback", "Busser", "Food Runner",
    "Host", "Prep Cook", "Dishwasher", "Barista", "Event Captain",
    "Concession Worker", "Housekeeper",
]

CERTIFICATIONS = [
    "Food Handler Card",
    "ServSafe Manager",
    "Alcohol Service (RBS/TIPS)",
    "CPR / First Aid",
]

# Seeded defaults (approx. current state minimums; admins should verify and
# can edit every value under Admin -> Minimum Wage).
MIN_WAGES = {
    "AL": 7.25, "AK": 11.91, "AZ": 14.70, "AR": 11.00, "CA": 16.50, "CO": 14.81,
    "CT": 16.35, "DE": 15.00, "DC": 17.50, "FL": 13.00, "GA": 7.25, "HI": 14.00,
    "ID": 7.25, "IL": 15.00, "IN": 7.25, "IA": 7.25, "KS": 7.25, "KY": 7.25,
    "LA": 7.25, "ME": 14.65, "MD": 15.00, "MA": 15.00, "MI": 12.48, "MN": 11.13,
    "MS": 7.25, "MO": 13.75, "MT": 10.55, "NE": 13.50, "NV": 12.00, "NH": 7.25,
    "NJ": 15.49, "NM": 12.00, "NY": 16.50, "NC": 7.25, "ND": 7.25, "OH": 10.70,
    "OK": 7.25, "OR": 15.05, "PA": 7.25, "RI": 15.00, "SC": 7.25, "SD": 11.50,
    "TN": 7.25, "TX": 7.25, "UT": 7.25, "VT": 14.01, "VA": 12.41, "WA": 16.66,
    "WV": 8.75, "WI": 7.25, "WY": 7.25,
}


def run():
    db = SessionLocal()
    try:
        if not db.query(User).filter_by(role="admin").first():
            try:
                db.add(
                    User(
                        email=ADMIN_EMAIL,
                        password_hash=hash_password(ADMIN_PASSWORD),
                        first_name="Crewed",
                        last_name="Admin",
                        role="admin",
                        status="active",
                    )
                )
                db.commit()
                print(f"[seed] Created admin account: {ADMIN_EMAIL} / {ADMIN_PASSWORD}")
            except Exception:
                db.rollback()

        try:
            existing_positions = {p.name for p in db.query(Position).all()}
            for name in POSITIONS:
                if name not in existing_positions:
                    db.add(Position(name=name))
            db.commit()
        except Exception:
            db.rollback()

        try:
            existing_certs = {c.name for c in db.query(Certification).all()}
            for name in CERTIFICATIONS:
                if name not in existing_certs:
                    db.add(Certification(name=name))
            db.commit()
        except Exception:
            db.rollback()

        try:
            existing_states = {m.state for m in db.query(MinWage).all()}
            for state, rate in MIN_WAGES.items():
                if state not in existing_states:
                    db.add(MinWage(state=state, rate=rate))
            db.commit()
        except Exception:
            db.rollback()

        try:
            if not db.get(Setting, "markup_percent"):
                db.add(Setting(key="markup_percent", value="55"))
                db.commit()
        except Exception:
            db.rollback()
    except Exception:
        db.rollback()
    finally:
        db.close()
