"""Re-filing imported activity after the rules changed: ActivityWatch blocks,
window-tracker blocks, YouTube history (rebuilt) and measured YouTube
(re-attached). Entries whose category you set by hand are left alone. Commits."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import TimeEntry, WatchEvent
from app.services import watchlive
from app.services.rules import Activity, load_ruleset
from app.services.youtube import HISTORY_SOURCES, rebuild_sessions


def reapply_rules(db: Session, user_id: int) -> dict:
    rules = load_ruleset(db, user_id)
    changed = 0
    for e in db.scalars(select(TimeEntry).where(
            TimeEntry.user_id == user_id, TimeEntry.source == "activitywatch", TimeEntry.category_locked.is_(False))):
        meta = e.meta or {}
        titles = meta.get("titles") or [None]
        new = rules.classify(Activity(title=titles[0], channel=meta.get("channel"), app=meta.get("app"),
                                      url=f"https://{meta['domain']}/" if meta.get("domain") else None))
        if new != e.category_id:
            e.category_id = new
            changed += 1
    windows = 0
    for e in db.scalars(select(TimeEntry).where(
            TimeEntry.user_id == user_id, TimeEntry.source == "window", TimeEntry.category_locked.is_(False))):
        meta = e.meta or {}  # the block's main window: its most seen title and program
        app = next(iter(meta.get("apps") or {}), None)
        new = rules.classify(Activity(title=(meta.get("titles") or [None])[0], app=app))
        if new != e.category_id:
            e.category_id = new
            windows += 1
    rebuilt = 0
    history = WatchEvent.source.in_(HISTORY_SOURCES)
    first = db.scalar(select(func.min(WatchEvent.occurred_at)).where(WatchEvent.user_id == user_id, history))
    last = db.scalar(select(func.max(WatchEvent.occurred_at)).where(WatchEvent.user_id == user_id, history))
    if first and last:
        rebuilt = rebuild_sessions(db, user_id, rules, first, last)
    with watchlive.LOCK:  # committed inside the lock: an upload never sees half of it
        measured = watchlive.rebuild(db, user_id, rules)
        db.commit()
    return {"activitywatch_reclassified": changed, "window_blocks_reclassified": windows,
            "youtube_blocks_rebuilt": rebuilt, "extension_segments_refiled": measured}
