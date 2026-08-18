"""SQLAlchemy database setup."""

from collections.abc import Generator

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def _make_engine():
    settings = get_settings()
    url = settings.database_url
    connect_args = {}
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        connect_args["timeout"] = 30
    engine = create_engine(url, connect_args=connect_args, future=True)

    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def set_sqlite_pragma(dbapi_connection, connection_record):  # noqa: ARG001
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.close()

    return engine


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create tables (dev/test). Prefer Alembic in production."""
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)

    # SQLite create_all does not ADD columns to existing tables
    inspector = inspect(engine)
    alterations = {
        "collector_runs": [("criteria", "JSON")],
        "market_snapshots": [("criteria_hash", "VARCHAR(32)")],
        "search_profiles": [
            ("hunt_key", "VARCHAR(64) DEFAULT 'fortuner-4x4'"),
            ("make", "VARCHAR(64) DEFAULT 'Toyota'"),
            ("model", "VARCHAR(64) DEFAULT 'Fortuner'"),
            ("required_fuel", "VARCHAR(32)"),
        ],
    }
    with engine.begin() as conn:
        for table, cols in alterations.items():
            if table not in inspector.get_table_names():
                continue
            existing = {c["name"] for c in inspector.get_columns(table)}
            for name, coltype in cols:
                if name in existing:
                    continue
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {coltype}"))
