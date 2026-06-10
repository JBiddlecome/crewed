from sqlalchemy import create_engine, inspect, text
from sqlalchemy.event import listens_for
from sqlalchemy.orm import declarative_base, sessionmaker

from .config import DB_PATH

engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False, "timeout": 30},
)

@listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
    except Exception:
        pass
    finally:
        cursor.close()

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
Base = declarative_base()

# Columns added after the initial release; create_all doesn't alter existing
# tables, so existing databases get them via ALTER TABLE here.
_ADDED_COLUMNS = {
    "location": {"parking": "TEXT", "check_in_location": "TEXT", "check_in_contact": "TEXT"},
    "shift": {"parking": "TEXT", "check_in_location": "TEXT", "check_in_contact": "TEXT"},
}


def ensure_schema():
    try:
        Base.metadata.create_all(engine)
    except Exception:
        pass
    try:
        inspector = inspect(engine)
        with engine.begin() as conn:
            for table, columns in _ADDED_COLUMNS.items():
                existing = {c["name"] for c in inspector.get_columns(table)}
                for name, ddl_type in columns.items():
                    if name not in existing:
                        try:
                            conn.execute(text(f'ALTER TABLE "{table}" ADD COLUMN {name} {ddl_type}'))
                        except Exception:
                            pass
    except Exception:
        pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
