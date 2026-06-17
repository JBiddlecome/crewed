from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from .config import DATABASE_URL

engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=300)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
Base = declarative_base()


def _add_column_if_missing(conn, table: str, column: str, col_type: str):
    """Add a column to an existing table if it doesn't already exist."""
    dialect = engine.dialect.name
    quoted = f'"{table}"'
    if dialect == "sqlite":
        rows = conn.execute(
            __import__("sqlalchemy").text(f"PRAGMA table_info({table})")
        ).fetchall()
        existing = {r[1] for r in rows}
        if column not in existing:
            conn.execute(__import__("sqlalchemy").text(f"ALTER TABLE {quoted} ADD COLUMN {column} {col_type}"))
    else:
        # PostgreSQL / others support IF NOT EXISTS; DATETIME is not a valid PG type
        pg_type = col_type.replace("DATETIME", "TIMESTAMP")
        conn.execute(
            __import__("sqlalchemy").text(
                f"ALTER TABLE {quoted} ADD COLUMN IF NOT EXISTS {column} {pg_type}"
            )
        )


_SEED_POSITIONS = [
    "Cook", "Prep Cook", "Dishwasher", "Utility", "Server", "Host", "Runner",
    "Busser", "Bartender", "Barback", "Cashier", "Pastry", "Baker", "Sushi",
    "Concessions", "Barista", "Valet", "Event Supervisor", "Sous Chef",
]


def _seed_positions(conn):
    from sqlalchemy import text
    for name in _SEED_POSITIONS:
        conn.execute(
            text("INSERT INTO position (name) SELECT :name WHERE NOT EXISTS (SELECT 1 FROM position WHERE name = :name)"),
            {"name": name},
        )


def _migrate_shift_events(conn):
    """Create Event rows for any shifts that pre-date the Event model."""
    from sqlalchemy import text
    result = conn.execute(text("SELECT COUNT(*) FROM shift WHERE event_id IS NULL")).scalar()
    if result == 0:
        return
    rows = conn.execute(text("""
        SELECT DISTINCT s.client_id, s.location_id, s.shift_date,
               l.name, l.address1, l.address2, l.city, l.state, l.zip,
               l.parking, l.check_in_location, l.check_in_contact
        FROM shift s
        JOIN location l ON l.id = s.location_id
        WHERE s.event_id IS NULL
    """)).fetchall()
    for row in rows:
        client_id, location_id, shift_date = row[0], row[1], row[2]
        name = row[3]
        address1, address2, city, state, zip_ = row[4], row[5], row[6], row[7], row[8]
        parking, check_in_location, check_in_contact = row[9], row[10], row[11]
        try:
            ld = conn.execute(text(
                "SELECT parking, check_in_location, check_in_contact "
                "FROM location_day WHERE location_id=:lid AND date=:date"
            ), {"lid": location_id, "date": shift_date}).fetchone()
            if ld:
                parking = ld[0] or parking
                check_in_location = ld[1] or check_in_location
                check_in_contact = ld[2] or check_in_contact
        except Exception:
            pass
        existing = conn.execute(text(
            "SELECT id FROM event WHERE client_id=:cid AND location_id=:lid AND event_date=:date"
        ), {"cid": client_id, "lid": location_id, "date": shift_date}).fetchone()
        if existing:
            event_id = existing[0]
        else:
            insert_sql = """
                INSERT INTO event
                  (client_id, location_id, event_date, name, address1, address2,
                   city, state, zip, parking, check_in_location, check_in_contact,
                   status, created_at)
                VALUES
                  (:client_id, :location_id, :event_date, :name, :address1, :address2,
                   :city, :state, :zip, :parking, :check_in_location, :check_in_contact,
                   'active', CURRENT_TIMESTAMP)
            """
            params = {
                "client_id": client_id, "location_id": location_id, "event_date": shift_date,
                "name": name or "", "address1": address1 or "", "address2": address2 or "",
                "city": city or "", "state": state or "", "zip": zip_ or "",
                "parking": parking, "check_in_location": check_in_location,
                "check_in_contact": check_in_contact,
            }
            if engine.dialect.name == "sqlite":
                conn.execute(text(insert_sql), params)
                event_id = conn.execute(text("SELECT last_insert_rowid()")).scalar()
            else:
                event_id = conn.execute(text(insert_sql + " RETURNING id"), params).scalar()
        conn.execute(text("""
            UPDATE shift SET event_id=:eid
            WHERE client_id=:cid AND location_id=:lid AND shift_date=:date AND event_id IS NULL
        """), {"eid": event_id, "cid": client_id, "lid": location_id, "date": shift_date})


def ensure_schema():
    Base.metadata.create_all(engine)
    # Add columns introduced after initial table creation
    _new_user_columns = [
        ("gender", "VARCHAR"),
        ("rehire_date", "DATE"),
        ("concierge_date", "DATE"),
        ("payroll_id", "VARCHAR"),
        ("ssn", "VARCHAR"),
        ("interview_notes", "TEXT"),
        ("background_check_date", "DATE"),
        ("background_check_status", "VARCHAR"),
        ("email_confirmed", "BOOLEAN DEFAULT FALSE"),
        ("confirmation_token", "VARCHAR"),
        ("reset_token", "VARCHAR"),
        ("reset_token_expires", "DATETIME"),
    ]
    with engine.begin() as conn:
        for col, col_type in _new_user_columns:
            _add_column_if_missing(conn, "user", col, col_type)
        _add_column_if_missing(conn, "user", "profile_picture_declined", "BOOLEAN DEFAULT FALSE")
        _add_column_if_missing(conn, "client_company", "portal_approved", "BOOLEAN DEFAULT TRUE")
        _add_column_if_missing(conn, "client_company", "rate_setting", "VARCHAR DEFAULT 'client_controlled'")
        _add_column_if_missing(conn, "position", "default_pay_rate_l1", "FLOAT DEFAULT 0")
        _add_column_if_missing(conn, "position", "default_bill_rate_l1", "FLOAT DEFAULT 0")
        _add_column_if_missing(conn, "position", "default_markup_l1", "FLOAT DEFAULT 0")
        _add_column_if_missing(conn, "position", "default_pay_rate_l2", "FLOAT DEFAULT 0")
        _add_column_if_missing(conn, "position", "default_bill_rate_l2", "FLOAT DEFAULT 0")
        _add_column_if_missing(conn, "position", "default_markup_l2", "FLOAT DEFAULT 0")
        _add_column_if_missing(conn, "position", "default_pay_rate_l3", "FLOAT DEFAULT 0")
        _add_column_if_missing(conn, "position", "default_bill_rate_l3", "FLOAT DEFAULT 0")
        _add_column_if_missing(conn, "position", "default_markup_l3", "FLOAT DEFAULT 0")
        _add_column_if_missing(conn, "client_preset_rate", "pay_rate_l1", "FLOAT DEFAULT 0")
        _add_column_if_missing(conn, "client_preset_rate", "bill_rate_l1", "FLOAT DEFAULT 0")
        _add_column_if_missing(conn, "client_preset_rate", "markup_l1", "FLOAT DEFAULT 0")
        _add_column_if_missing(conn, "client_preset_rate", "pay_rate_l2", "FLOAT DEFAULT 0")
        _add_column_if_missing(conn, "client_preset_rate", "bill_rate_l2", "FLOAT DEFAULT 0")
        _add_column_if_missing(conn, "client_preset_rate", "markup_l2", "FLOAT DEFAULT 0")
        _add_column_if_missing(conn, "client_preset_rate", "pay_rate_l3", "FLOAT DEFAULT 0")
        _add_column_if_missing(conn, "client_preset_rate", "bill_rate_l3", "FLOAT DEFAULT 0")
        _add_column_if_missing(conn, "client_preset_rate", "markup_l3", "FLOAT DEFAULT 0")
        _add_column_if_missing(conn, "client_preset_rate_history", "pay_rate_l1", "FLOAT DEFAULT 0")
        _add_column_if_missing(conn, "client_preset_rate_history", "bill_rate_l1", "FLOAT DEFAULT 0")
        _add_column_if_missing(conn, "client_preset_rate_history", "markup_l1", "FLOAT DEFAULT 0")
        _add_column_if_missing(conn, "client_preset_rate_history", "pay_rate_l2", "FLOAT DEFAULT 0")
        _add_column_if_missing(conn, "client_preset_rate_history", "bill_rate_l2", "FLOAT DEFAULT 0")
        _add_column_if_missing(conn, "client_preset_rate_history", "markup_l2", "FLOAT DEFAULT 0")
        _add_column_if_missing(conn, "client_preset_rate_history", "pay_rate_l3", "FLOAT DEFAULT 0")
        _add_column_if_missing(conn, "client_preset_rate_history", "bill_rate_l3", "FLOAT DEFAULT 0")
        _add_column_if_missing(conn, "client_preset_rate_history", "markup_l3", "FLOAT DEFAULT 0")
        _add_column_if_missing(conn, "employee_position", "level", "INTEGER DEFAULT 2")
        _add_column_if_missing(conn, "shift", "required_level", "INTEGER DEFAULT 1")
        _add_column_if_missing(conn, "shift", "event_id", "INTEGER")
        _add_column_if_missing(conn, "timesheet", "approved_by", "INTEGER")
        _add_column_if_missing(conn, "timesheet", "deleted_at", "DATETIME")
        _add_column_if_missing(conn, "timesheet", "deleted_by", "INTEGER")
        _seed_positions(conn)
        _migrate_shift_events(conn)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
