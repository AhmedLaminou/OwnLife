"""Brings the database schema up to date. Runs automatically at startup."""

from __future__ import annotations

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine

from app.config import BACKEND_DIR


def _config(url: str) -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    cfg.attributes["configure_logger"] = False
    return cfg


def pending_upgrade(url: str) -> bool:
    """True when an existing database is behind the code's schema — the moment
    to take a backup before migrating. A brand-new database is not "pending"."""
    head = ScriptDirectory.from_config(_config(url)).get_current_head()
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            current = MigrationContext.configure(conn).get_current_revision()
    finally:
        engine.dispose()
    return current is not None and current != head


def upgrade_database(url: str) -> None:
    command.upgrade(_config(url), "head")
