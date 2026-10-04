"""Health, backups and export. Your data is yours: the export is complete JSON."""

from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from sqlalchemy import select

from app import __version__
from app.config import get_settings
from app.db import utcnow
from app.deps import DB, CurrentProfile, CurrentUser
from app.models import (
    CaptureDraft,
    Category,
    ChatMessage,
    ChatThread,
    ClassificationRule,
    Goal,
    Habit,
    HabitLog,
    JournalEntry,
    LifeChapter,
    LifeEvent,
    MediaItem,
    Note,
    Person,
    PlanBlock,
    PlanTemplate,
    TimeEntry,
    Transaction,
    WatchEvent,
)
from app.serializers import (
    category_out,
    chapter_out,
    draft_out,
    entry_out,
    event_out,
    habit_out,
    iso,
    journal_out,
    media_out,
    message_out,
    note_out,
    person_out,
    plan_block_out,
    profile_out,
    rule_out,
    template_out,
    thread_out,
    tx_out,
)
from app.services.backup import list_backups, make_backup, mirror_latest, mirror_status

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/health")
def health() -> dict:
    return {"status": "ok", "version": __version__}


@router.post("/backup")
def backup(user: CurrentUser) -> dict:  # noqa: ARG001
    settings = get_settings()
    try:
        path = make_backup(settings, "manual")
    except RuntimeError as e:
        raise HTTPException(400, str(e)) from e
    try:
        copy = mirror_latest(settings)
    except OSError as e:  # the second place refused the copy: the local backup stands
        copy, error = None, str(e)
    else:
        error = None
    return {"path": str(path), "bytes": path.stat().st_size, "mirror": str(copy) if copy else None,
            "mirror_error": error}


@router.get("/backup-mirror")
def backup_mirror(user: CurrentUser) -> dict:  # noqa: ARG001
    """Where the second copy of the backups goes, and what is there."""
    return mirror_status(get_settings())


@router.get("/backups")
def backups(user: CurrentUser) -> list[dict]:  # noqa: ARG001
    return [
        {"name": p.name, "bytes": p.stat().st_size,
         "created": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")}
        for p in list_backups(get_settings())
    ]


def _seed_path():
    return get_settings().data_dir / "personal_seed.json"


def _single_account(db) -> bool:
    from sqlalchemy import func

    from app.models import User

    return (db.scalar(select(func.count()).select_from(User)) or 0) == 1


@router.get("/personal-seed")
def personal_seed_status(user: CurrentUser, db: DB) -> dict:  # noqa: ARG001
    """The personal seed (backend/data/personal_seed.json) pre-fills chapters, goals,
    habits, people, rules, events. It is offered only while one account exists:
    it describes one person."""
    p = _seed_path()
    return {"exists": p.exists(), "allowed": p.exists() and _single_account(db), "path": str(p)}


@router.post("/personal-seed")
def apply_seed(user: CurrentUser, db: DB) -> dict:
    from app.seed.personal import apply_personal_seed, load_seed

    p = _seed_path()
    if not p.exists():
        raise HTTPException(404, "No personal seed file")
    if not _single_account(db):
        raise HTTPException(403, "The personal seed is only applied while a single account exists")
    try:
        report = apply_personal_seed(db, user, load_seed(p))
    except ValueError as e:
        db.rollback()
        raise HTTPException(422, str(e)) from e
    db.commit()
    return {"applied": report}


@router.get("/export")
def export(user: CurrentUser, profile: CurrentProfile, db: DB) -> Response:
    uid = user.id
    now = utcnow()

    def rows(model):
        return db.scalars(select(model).where(model.user_id == uid)).all()

    habits = rows(Habit)
    data = {
        "exported_at": iso(now),
        "app": f"OwnLife {__version__}",
        "profile": profile_out(profile, user),
        "categories": [category_out(c) for c in rows(Category)],
        "rules": [rule_out(r) for r in rows(ClassificationRule)],
        "time_entries": [entry_out(e, now) for e in rows(TimeEntry)],
        "journal": [journal_out(j) for j in rows(JournalEntry)],
        "notes": [note_out(n) for n in rows(Note)],
        "goals": [
            {"id": g.id, "parent_id": g.parent_id, "title": g.title, "level": g.level, "status": g.status,
             "description": g.description, "start_date": iso(g.start_date), "target_date": iso(g.target_date),
             "progress_mode": g.progress_mode, "progress": g.progress, "metric_unit": g.metric_unit,
             "metric_start": g.metric_start, "metric_target": g.metric_target, "metric_current": g.metric_current,
             "hours_target": g.hours_target, "category_ids": [c.id for c in g.categories]}
            for g in rows(Goal)
        ],
        "chapters": [chapter_out(c) for c in rows(LifeChapter)],
        "life_events": [event_out(e) for e in rows(LifeEvent)],
        "habits": [habit_out(h) for h in habits],
        "habit_logs": [
            {"habit_id": log.habit_id, "date": iso(log.log_date), "status": log.status,
             "occurred_at": iso(log.occurred_at), "note": log.note, "value": log.value}
            for log in rows(HabitLog)
        ],
        "plan_blocks": [plan_block_out(b) for b in rows(PlanBlock)],
        "plan_templates": [template_out(t) for t in rows(PlanTemplate)],
        "media": [media_out(m) for m in rows(MediaItem)],
        "watch_events": [
            {"occurred_at": iso(w.occurred_at), "title": w.title, "channel": w.channel, "url": w.url,
             "source": w.source}
            for w in rows(WatchEvent)
        ],
        "transactions": [tx_out(t) for t in rows(Transaction)],
        "people": [person_out(p) for p in rows(Person)],
        "chat_threads": [
            {**thread_out(t), "messages": [message_out(m) for m in db.scalars(
                select(ChatMessage).where(ChatMessage.thread_id == t.id).order_by(ChatMessage.id))]}
            for t in rows(ChatThread)
        ],
        "capture_drafts": [draft_out(d) for d in rows(CaptureDraft)],
    }
    filename = f"ownlife-export-{now:%Y%m%d-%H%M}.json"
    return Response(
        json.dumps(data, ensure_ascii=False, indent=1),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
