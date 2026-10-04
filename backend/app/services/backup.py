"""Database backups. SQLite's online backup API copies a consistent snapshot
even while the app is writing — unlike copying the file, which can catch it
half-written. One backup a day is made automatically; the last 14 are kept.

A backup on the same disk does not survive the disk: BACKUP_MIRROR_DIR names a
second place (a USB stick, a share, a synced folder) where the newest backup is
copied as soon as that place is reachable; the last 8 copies are kept there."""

from __future__ import annotations

import os
import shutil
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from app.config import Settings

KEEP = 14
MIRROR_KEEP = 8


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


def _copies(target: Path) -> list[Path]:
    return sorted(target.glob("ownlife-*.db"), reverse=True) if target.exists() else []


def mirror_latest(settings: Settings) -> Path | None:
    """Copies the newest backup to the mirror folder if it is not there yet.
    None when there is nothing to do, or when the place is not reachable (the
    stick is not plugged in): the next pass tries again."""
    target = settings.backup_mirror_dir
    latest = list_backups(settings)[:1]
    if target is None or not latest:
        return None
    if not target.exists():
        if not target.parent.exists():
            return None
        target.mkdir(parents=True, exist_ok=True)
    dst = target / latest[0].name
    if dst.exists():
        return None
    part = dst.with_name(dst.name + ".part")
    shutil.copyfile(latest[0], part)
    os.replace(part, dst)  # a half-copied file never looks like a backup
    for old in _copies(target)[MIRROR_KEEP:]:
        old.unlink(missing_ok=True)
    return dst


def mirror_status(settings: Settings) -> dict:
    target = settings.backup_mirror_dir
    if target is None:
        return {"dir": None, "reachable": False, "copies": [], "behind": False}
    reachable = target.exists() or target.parent.exists()
    copies = _copies(target)
    latest = list_backups(settings)[:1]
    return {
        "dir": str(target),
        "reachable": reachable,
        "copies": [{"name": p.name, "bytes": p.stat().st_size,
                    "created": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")}
                   for p in copies],
        # the newest backup is not there yet (copied within the hour once reachable)
        "behind": bool(latest) and not (target / latest[0].name).exists(),
    }
