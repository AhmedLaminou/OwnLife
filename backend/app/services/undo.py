"""Undo for what the assistant and capture did.

Each action records what it needs to be reversed: the id it created, or the
values it replaced. Undo refuses (with a reason) rather than guess when the
record has been changed by hand since — it never removes your own edits.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai import rag
from app.models import (
    Goal,
    Habit,
    HabitLog,
    JournalEntry,
    LifeEvent,
    MediaItem,
    Person,
    PlanBlock,
    TimeEntry,
    Transaction,
    time_entry_people,
)
from app.services import filesync
from app.services.mdfile import clean_day_body
from app.services.records import running_timer


class UndoError(Exception):
    pass


def _own(db: Session, model, obj_id: int | None, user_id: int):
    obj = db.get(model, obj_id) if obj_id is not None else None
    if obj is None or obj.user_id != user_id:
        return None
    return obj


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def undo_journal_append(db: Session, user_id: int, cfg, info: dict) -> str:
    """Removes text that was appended to a page (and from the file)."""
    entry = _own(db, JournalEntry, info.get("id"), user_id)
    if entry is None:
        return "That page no longer exists."
    appended = clean_day_body(info.get("appended") or "")
    body = clean_day_body(entry.body or "")
    if info.get("created") and body == appended:
        filesync.remove_day(db, cfg, entry)
        rag.delete_source_chunks(db, user_id, "journal", entry.id)
        db.delete(entry)
        return "The page it created was removed."
    if not appended or not body.endswith(appended):
        raise UndoError("The page was edited since: remove that text by hand.")
    entry.body = body[: len(body) - len(appended)].rstrip()
    result = filesync.push_day(db, user_id, cfg, entry)
    rag.index_journal_entry(db, entry)
    if result["state"] == "conflict":
        return "Removed in OwnLife; your file changed meanwhile — resolve it in the Journal."
    return "The text was removed from the page."


def undo_action(db: Session, user_id: int, action: dict, cfg) -> str:
    kind = action.get("type")
    obj_id = action.get("id")
    if kind == "time_entry":
        e = _own(db, TimeEntry, obj_id, user_id)
        if e is None:
            return "Already gone."
        db.delete(e)
        return "Entry deleted."
    if kind == "timer_started":
        e = _own(db, TimeEntry, obj_id, user_id)
        if e is not None:
            db.delete(e)
        stopped = _own(db, TimeEntry, action.get("stopped_id"), user_id)
        if stopped is not None and running_timer(db, user_id) in (None, e):
            db.flush()
            stopped.ended_at = None  # the timer it had stopped runs again
            return "Timer removed; the previous one is running again."
        return "Timer removed."
    if kind in ("timer_stopped", "timer_paused"):
        e = _own(db, TimeEntry, obj_id, user_id)
        if e is None:
            return "Already gone."
        if running_timer(db, user_id) is not None:
            raise UndoError("Another timer is running: stop it first.")
        e.ended_at = None
        e.meta = {k: v for k, v in (e.meta or {}).items() if k != "paused"}
        return "The timer is running again."
    if kind == "timer_resumed":
        e = _own(db, TimeEntry, obj_id, user_id)
        if e is not None:
            db.delete(e)
        paused = _own(db, TimeEntry, action.get("paused_id"), user_id)
        if paused is not None:
            paused.meta = {**(paused.meta or {}), "paused": True}
        stopped = _own(db, TimeEntry, action.get("stopped_id"), user_id)
        if stopped is not None and running_timer(db, user_id) in (None, e):
            db.flush()
            stopped.ended_at = None
        return "The timer is paused again."
    if kind == "transaction":
        t = _own(db, Transaction, obj_id, user_id)
        if t is None:
            return "Already gone."
        db.delete(t)
        return "Transaction deleted."
    if kind == "habit_log":
        log = _own(db, HabitLog, obj_id, user_id)
        if log is not None:
            db.delete(log)
        habit = _own(db, Habit, action.get("habit_id"), user_id)
        for old in action.get("replaced") or []:
            if habit is not None:
                db.add(HabitLog(user_id=user_id, habit_id=habit.id, log_date=datetime.fromisoformat(old["date"]).date(),
                                status=old["status"], occurred_at=_dt(old.get("occurred_at")), note=old.get("note")))
        return "Habit log removed."
    if kind == "journal":
        return undo_journal_append(db, user_id, cfg, action)
    if kind in ("goal", "goal_updated"):
        g = _own(db, Goal, obj_id, user_id)
        before = action.get("before")
        if g is None:
            return "Already gone."
        if not before:
            raise UndoError("This change was made before undo existed.")
        g.progress, g.metric_current, g.status = before["progress"], before["metric_current"], before["status"]
        g.completed_at = _dt(before.get("completed_at"))
        return "Goal restored."
    if kind == "goal_created":
        g = _own(db, Goal, obj_id, user_id)
        if g is None:
            return "Already gone."
        if db.scalar(select(func.count()).select_from(Goal).where(Goal.parent_id == g.id)):
            raise UndoError("This goal has sub-goals now: delete it from the Goals page.")
        db.delete(g)
        return "Goal deleted."
    if kind == "life_event":
        ev = _own(db, LifeEvent, obj_id, user_id)
        if ev is None:
            return "Already gone."
        db.delete(ev)
        return "Life event deleted."
    if kind == "plan_block":
        b = _own(db, PlanBlock, obj_id, user_id)
        if b is None:
            return "Already gone."
        db.delete(b)
        return "Block removed from the plan."
    raise UndoError(f"Nothing to undo for “{kind}”.")


def undo_capture(db: Session, user_id: int, records: dict, cfg) -> dict:
    """Reverses a committed capture draft. Returns counts of what was removed."""
    removed = {"time_entries": 0, "transactions": 0, "habit_logs": 0, "media": 0, "people": 0}
    notes: list[str] = []
    for eid in records.get("time_entries") or []:
        e = _own(db, TimeEntry, eid, user_id)
        if e is not None:
            db.delete(e)
            removed["time_entries"] += 1
    for tid in records.get("transactions") or []:
        t = _own(db, Transaction, tid, user_id)
        if t is not None:
            db.delete(t)
            removed["transactions"] += 1
    for item in records.get("habit_logs") or []:
        undo_action(db, user_id, {"type": "habit_log", **item}, cfg)
        removed["habit_logs"] += 1
    for mid in records.get("media_created") or []:
        m = _own(db, MediaItem, mid, user_id)
        if m is not None:
            db.delete(m)
            removed["media"] += 1
    for upd in records.get("media_updated") or []:
        m = _own(db, MediaItem, upd.get("id"), user_id)
        if m is not None:
            m.status = upd.get("status_before") or m.status
    db.flush()
    for pid in records.get("people_created") or []:
        p = _own(db, Person, pid, user_id)
        if p is None:
            continue
        in_use = db.scalar(select(func.count()).select_from(time_entry_people).where(time_entry_people.c.person_id == pid))
        in_use = in_use or db.scalar(select(func.count()).select_from(Transaction).where(Transaction.person_id == pid))
        if not in_use:
            db.delete(p)
            removed["people"] += 1
    if records.get("journal"):
        try:
            notes.append(undo_journal_append(db, user_id, cfg, records["journal"]))
        except UndoError as e:
            notes.append(str(e))
    db.flush()
    return {"removed": removed, "notes": notes}
