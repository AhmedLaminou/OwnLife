"""Database engine, session factory and the shared declarative base.

SQLite is configured for durability over raw speed: WAL journaling plus
synchronous=FULL means a committed entry survives a crash or a power cut.
The write volume of one person's life is tiny, so the extra fsync costs nothing.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone

from sqlalchemy import DateTime, create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.types import TypeDecorator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator):
    """Stores naive UTC in the database and always hands back aware UTC datetimes.

    SQLite has no time zone type. Converting at the boundary means the rest of the
    code never compares a naive datetime with an aware one.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect):  # noqa: ARG002
        if value is None:
            return None
        if value.tzinfo is None:
            return value  # naive values are UTC by convention
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect):  # noqa: ARG002
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc)


class Base(DeclarativeBase):
    pass


SessionLocal = sessionmaker(autoflush=False, expire_on_commit=False)
_engine: Engine | None = None


def _sqlite_pragmas(dbapi_conn, _record) -> None:
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=FULL")
    cur.execute("PRAGMA busy_timeout=5000")
    cur.close()


def init_engine(url: str) -> Engine:
    """(Re)binds the session factory to `url`. Called once at startup, and by tests."""
    global _engine
    if _engine is not None:
        _engine.dispose()
    is_sqlite = url.startswith("sqlite")
    engine = create_engine(
        url, connect_args={"check_same_thread": False} if is_sqlite else {}
    )
    if is_sqlite:
        event.listen(engine, "connect", _sqlite_pragmas)
    SessionLocal.configure(bind=engine)
    _engine = engine
    return engine


def get_engine() -> Engine:
    if _engine is None:
        raise RuntimeError("init_engine() has not been called")
    return _engine


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
