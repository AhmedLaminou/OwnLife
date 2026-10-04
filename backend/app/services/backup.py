"""Database backups. SQLite's online backup API copies a consistent snapshot
even while the app is writing — unlike copying the file, which can catch it
half-written. One backup a day is made automatically; the last 14 are kept."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from app.config import Settings

KEEP = 14


def sqlite_path(settings: Settings) -> Path | None:
    url = settings.effective_database_url
    if not url.startswith("sqlite:///"):
        return None
    return Path(url.removeprefix("sqlite:///"))


def list_backups(settings: Settings) -> list[Path]:
    d = settings.backups_dir
    return sorted(d.glob("ownlife-*.db"), reverse=True) if d.exists() else []


def make_backup(settings: Settings, label: str = "") -> Path:
    src = sqlite_path(settings)
    if src is None or not src.exists():
        raise RuntimeError("Backups are only supported for the SQLite database")
    settings.backups_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = settings.backups_dir / f"ownlife-{stamp}{('-' + label) if label else ''}.db"
    with sqlite3.connect(src) as source, sqlite3.connect(dst) as target:
        source.backup(target)
    for old in list_backups(settings)[KEEP:]:
        old.unlink(missing_ok=True)
    return dst


def backup_if_due(settings: Settings, max_age: timedelta = timedelta(hours=20)) -> Path | None:
    backups = list_backups(settings)
    if backups:
        age = datetime.now() - datetime.fromtimestamp(backups[0].stat().st_mtime)
        if age < max_age:
            return None
    src = sqlite_path(settings)
    if src is None or not src.exists():
        return None
    return make_backup(settings, "auto")
