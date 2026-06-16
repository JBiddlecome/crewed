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
        # PostgreSQL / others support IF NOT EXISTS
        conn.execute(
            __import__("sqlalchemy").text(
                f"ALTER TABLE {quoted} ADD COLUMN IF NOT EXISTS {column} {col_type}"
            )
        )


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
    ]
    with engine.begin() as conn:
        for col, col_type in _new_user_columns:
            _add_column_if_missing(conn, "user", col, col_type)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
