"""The time ledger: entries, the live timer, day and range statistics, quick-log."""

from __future__ import annotations

import datetime as dt

from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.db import utcnow
from app.deps import DB, CurrentProfile, CurrentUser
from app.models import Category, Goal, Habit, Person, PlanBlock, TimeEntry
from app.serializers import entry_out, plan_block_out
from app.services import records
from app.services.ledger import (
    KIND_ORDER,
    covered_seconds,
    effective_spans,
    entries_overlapping,
    overlap_notes,
    split_by_day,
)
from app.services.quicklog import parse_quick
from app.services.timeutil import local_instant, local_today, range_utc, tz_of

router = APIRouter(prefix="/api/time", tags=["time"])


class EntryIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    category_id: int | None = None
    started_at: datetime
    ended_at: datetime | None = None
    person_ids: list[int] = Field(default_factory=list)
    new_people: list[str] = Field(default_factory=list)
    location: str | None = Field(None, max_length=120)
    notes: str | None = None
    goal_id: int | None = None
    focus_rating: int | None = Field(None, ge=1, le=5)
    is_private: bool = False


class EntryPatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=300)
    category_id: int | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    person_ids: list[int] | None = None
    new_people: list[str] | None = None
    location: str | None = None
    notes: str | None = None
    goal_id: int | None = None
    focus_rating: int | None = Field(None, ge=1, le=5)
    is_private: bool | None = None


class TimerIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    category_id: int | None = None
    minutes_ago: int = Field(0, ge=0, le=720)  # "I started 20 minutes ago"


class QuickIn(BaseModel):
    text: str = Field(min_length=1, max_length=20000)
    date: dt.date | None = None
    commit: bool = False


def _aware(dt: datetime, tz) -> datetime:
    """A datetime without offset is read as local time in the profile's zone."""
    return dt.replace(tzinfo=tz).astimezone(timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _check_refs(db, user_id: int, category_id: int | None, goal_id: int | None) -> None:
    if category_id is not None:
        c = db.get(Category, category_id)
        if c is None or c.user_id != user_id:
            raise HTTPException(422, "Unknown category")
    if goal_id is not None:
        g = db.get(Goal, goal_id)
        if g is None or g.user_id != user_id:
            raise HTTPException(422, "Unknown goal")


def _people(db, user_id: int, ids: list[int], names: list[str]) -> list[Person]:
    found = [p for p in (db.get(Person, i) for i in ids) if p is not None and p.user_id == user_id]
    return found + [p for p in records.resolve_people(db, user_id, names) if p not in found]


def _own_entry(db, user_id: int, entry_id: int) -> TimeEntry:
    e = db.get(TimeEntry, entry_id)
    if e is None or e.user_id != user_id:
        raise HTTPException(404, "Entry not found")
    return e


@router.get("/entries")
def list_entries(
    user: CurrentUser,
    profile: CurrentProfile,
    db: DB,
    start: date | None = None,
    end: date | None = None,
    category_id: int | None = None,
    source: str | None = None,
    q: str | None = None,
    limit: int = Query(500, le=5000),
) -> list[dict]:
    tz = tz_of(profile.timezone)
    today = local_today(tz)
    start = start or today - timedelta(days=6)
    end = end or today
    lo, hi = range_utc(start, end, tz)
    now = utcnow()
    out = []
    for e in entries_overlapping(db, user.id, lo, hi):
        if category_id is not None and e.category_id != category_id:
            continue
        if source and e.source != source:
            continue
        if q and q.casefold() not in e.title.casefold():
            continue
        out.append(entry_out(e, now))
    return out[-limit:]


@router.post("/entries")
def create_entry(body: EntryIn, user: CurrentUser, profile: CurrentProfile, db: DB) -> dict:
    tz = tz_of(profile.timezone)
    _check_refs(db, user.id, body.category_id, body.goal_id)
    try:
        e = records.create_time_entry(
            db,
            user.id,
            title=body.title,
            category_id=body.category_id,
            started_at=_aware(body.started_at, tz),
            ended_at=_aware(body.ended_at, tz) if body.ended_at else None,
            source="manual",
            people=_people(db, user.id, body.person_ids, body.new_people),
            location=body.location,
            notes=body.notes,
            goal_id=body.goal_id,
            focus_rating=body.focus_rating,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    e.is_private = body.is_private
    db.commit()
    return entry_out(e, utcnow())


@router.patch("/entries/{entry_id}")
def update_entry(entry_id: int, body: EntryPatch, user: CurrentUser, profile: CurrentProfile, db: DB) -> dict:
    tz = tz_of(profile.timezone)
    e = _own_entry(db, user.id, entry_id)
    data = body.model_dump(exclude_unset=True)
    _check_refs(db, user.id, data.get("category_id"), data.get("goal_id"))
    if "category_id" in data:
        e.category_id = data.pop("category_id")
        e.category_locked = True
    if "started_at" in data and data["started_at"] is not None:
        e.started_at = _aware(data.pop("started_at"), tz)
    if "ended_at" in data:
        value = data.pop("ended_at")
        e.ended_at = _aware(value, tz) if value is not None else None
    if e.ended_at is not None and e.ended_at <= e.started_at:
        raise HTTPException(422, "The end must be after the start")
    if "person_ids" in data or "new_people" in data:
        e.people = _people(db, user.id, data.pop("person_ids", None) or [p.id for p in e.people],
                           data.pop("new_people", None) or [])
    for k, v in data.items():
        setattr(e, k, v)
    db.commit()
    db.refresh(e)
    return entry_out(e, utcnow())


@router.delete("/entries/{entry_id}")
def delete_entry(entry_id: int, user: CurrentUser, db: DB) -> dict:
    db.delete(_own_entry(db, user.id, entry_id))
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- timer
@router.get("/timer")
def get_timer(user: CurrentUser, db: DB) -> dict | None:
    e = records.running_timer(db, user.id)
    return entry_out(e, utcnow()) if e else None


@router.post("/timer/start")
def timer_start(body: TimerIn, user: CurrentUser, db: DB) -> dict:
    """Starts now, or `minutes_ago` back; a timer running meanwhile stops at that moment."""
    _check_refs(db, user.id, body.category_id, None)
    now = utcnow()
    e = records.start_timer(db, user.id, body.title, body.category_id, now - timedelta(minutes=body.minutes_ago))
    db.commit()
    return entry_out(e, now)


AGO = Query(0, ge=0, le=720, description="It happened this many minutes ago")


@router.post("/timer/stop")
def timer_stop(user: CurrentUser, db: DB, minutes_ago: int = AGO) -> dict | None:
    now = utcnow()
    e = records.stop_timer(db, user.id, now - timedelta(minutes=minutes_ago))
    db.commit()
    return _with_session(db, user.id, e, now) if e else None


def _with_session(db, user_id: int, e: TimeEntry, now: datetime) -> dict:
    return {**entry_out(e, now), "session_seconds": records.session_seconds(db, user_id, e, now)}


@router.get("/timer/state")
def timer_state(user: CurrentUser, db: DB) -> dict:
    """The running timer and the paused ones, with the time of their whole session."""
    now = utcnow()
    running = records.running_timer(db, user.id)
    return {
        "running": _with_session(db, user.id, running, now) if running else None,
        "paused": [_with_session(db, user.id, e, now) for e in records.paused_timers(db, user.id, now)[:5]],
    }


@router.post("/timer/pause")
def timer_pause(user: CurrentUser, db: DB, minutes_ago: int = AGO) -> dict:
    now = utcnow()
    e = records.pause_timer(db, user.id, now - timedelta(minutes=minutes_ago))
    if e is None:
        raise HTTPException(409, "No timer is running")
    db.commit()
    return _with_session(db, user.id, e, now)


class ResumeIn(BaseModel):
    entry_id: int | None = None


@router.post("/timer/resume")
def timer_resume(body: ResumeIn, user: CurrentUser, db: DB) -> dict:
    now = utcnow()
    e = records.resume_timer(db, user.id, now, body.entry_id)
    if e is None:
        raise HTTPException(409, "No paused timer to resume")
    db.commit()
    return _with_session(db, user.id, e, now)


@router.post("/timer/finish/{entry_id}")
def timer_finish(entry_id: int, user: CurrentUser, db: DB) -> dict:
    """A paused timer you will not resume: it stays in the ledger as it is."""
    now = utcnow()
    e = records.finish_paused(db, user.id, entry_id, now)
    if e is None:
        raise HTTPException(404, "No paused timer with this id")
    db.commit()
    return _with_session(db, user.id, e, now)


# ---------------------------------------------------------------- views
def _totals(entries: list[TimeEntry], lo: datetime, hi: datetime, now: datetime) -> tuple[dict, list[dict]]:
    by_kind: dict[str, float] = {}
    by_cat: dict[int | None, dict] = {}
    for e, a, b in effective_spans(entries, now):
        secs = max(0.0, (min(b, hi) - max(a, lo)).total_seconds())
        kind = e.category.kind if e.category else "uncategorized"
        by_kind[kind] = by_kind.get(kind, 0.0) + secs
        row = by_cat.setdefault(
            e.category_id,
            {"category_id": e.category_id, "name": e.category.name if e.category else "Uncategorised",
             "kind": kind, "seconds": 0.0},
        )
        row["seconds"] += secs
    return by_kind, sorted(by_cat.values(), key=lambda r: -r["seconds"])


@router.get("/day/{day}")
def day_view(day: date, user: CurrentUser, profile: CurrentProfile, db: DB) -> dict:
    tz = tz_of(profile.timezone)
    lo, hi = range_utc(day, day, tz)
    now = utcnow()
    entries = entries_overlapping(db, user.id, lo, hi)
    by_kind, by_cat = _totals(entries, lo, hi, now)
    elapsed_end = min(hi, now) if day == local_today(tz) else hi
    covered = covered_seconds(entries, lo, elapsed_end, now)
    blocks = db.scalars(
        select(PlanBlock).where(PlanBlock.user_id == user.id, PlanBlock.plan_date == day).order_by(PlanBlock.start_minute)
    ).all()
    counted: dict[int, float] = {}
    for e, a, b in effective_spans(entries, now):
        counted[e.id] = counted.get(e.id, 0.0) + max(0.0, (min(b, hi) - max(a, lo)).total_seconds())
    return {
        "date": day.isoformat(),
        "entries": [
            {**entry_out(e, now),
             "counted_seconds": counted.get(e.id, 0.0),
             "window_seconds": max(0.0, (min(e.ended_at or now, hi) - max(e.started_at, lo)).total_seconds())}
            for e in entries
        ],
        "overlaps": overlap_notes(entries, now),
        "plan_blocks": [plan_block_out(b) for b in blocks],
        "totals_by_kind": by_kind,
        "totals_by_category": by_cat,
        "covered_seconds": covered,
        "untracked_seconds": max(0.0, (elapsed_end - lo).total_seconds() - covered),
        "targets": {
            "focus_hours": profile.focus_target_hours,
            "stretch_hours": profile.stretch_focus_hours,
            "noise_budget_hours": profile.noise_budget_hours,
            "sleep_hours": profile.sleep_target_hours,
        },
    }


@router.get("/stats")
def stats(
    user: CurrentUser,
    profile: CurrentProfile,
    db: DB,
    start: date | None = None,
    end: date | None = None,
) -> dict:
    tz = tz_of(profile.timezone)
    today = local_today(tz)
    end = end or today
    start = start or end - timedelta(days=29)
    if (end - start).days > 3660:
        raise HTTPException(422, "Ranges are limited to ten years")
    lo, hi = range_utc(start, end, tz)
    now = utcnow()
    entries = entries_overlapping(db, user.id, lo, hi)
    days = split_by_day(entries, start, end, tz, now)
    by_kind, by_cat = _totals(entries, lo, hi, now)
    titles: dict[str, float] = {}
    for e, a, b in effective_spans(entries, now):
        if e.is_private:
            continue
        secs = max(0.0, (min(b, hi) - max(a, lo)).total_seconds())
        titles[e.title] = titles.get(e.title, 0.0) + secs
    tracked = [d for d, k in days.items() if sum(k.values()) >= 7200]
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "kinds": [*KIND_ORDER, "uncategorized"],
        "days": [{"date": d.isoformat(), "kinds": k, "total": sum(k.values())} for d, k in sorted(days.items())],
        "totals_by_kind": by_kind,
        "totals_by_category": by_cat,
        "top_titles": [{"title": t, "seconds": s} for t, s in sorted(titles.items(), key=lambda kv: -kv[1])[:15]],
        "tracked_days": len(tracked),
        "avg_by_kind_per_tracked_day": {
            k: sum(days[d].get(k, 0.0) for d in tracked) / len(tracked) for k in by_kind
        } if tracked else {},
        "targets": {"focus_hours": profile.focus_target_hours, "noise_budget_hours": profile.noise_budget_hours},
    }


# ---------------------------------------------------------------- quick-log
@router.post("/quick")
def quick_log(body: QuickIn, user: CurrentUser, profile: CurrentProfile, db: DB) -> dict:
    tz = tz_of(profile.timezone)
    day = body.date or local_today(tz)
    cats = list(db.scalars(select(Category).where(Category.user_id == user.id, Category.archived.is_(False))))
    habits = list(db.scalars(select(Habit).where(Habit.user_id == user.id, Habit.archived.is_(False))))
    people = list(db.scalars(select(Person).where(Person.user_id == user.id)))
    parsed = parse_quick(body.text, day, cats, habits, people)
    result = {
        "time_entries": parsed.time_entries,
        "transactions": parsed.transactions,
        "habit_logs": parsed.habit_logs,
        "errors": parsed.errors,
        "committed": False,
    }
    if not body.commit:
        return result
    for t in parsed.time_entries:
        s = local_instant(day, t["start"], tz)
        e = local_instant(day, t["end"], tz)
        if e <= s:
            e = local_instant(day, t["end"], tz, day_offset=1)
        people_objs = [p for p in (db.get(Person, i) for i in t["person_ids"]) if p]
        people_objs += records.resolve_people(db, user.id, t["new_people"])
        records.create_time_entry(
            db, user.id, title=t["title"], category_id=t["category_id"], started_at=s, ended_at=e,
            source="quick", people=people_objs, location=t["location"],
        )
    for tx in parsed.transactions:
        records.create_transaction(
            db, user.id, profile.currency, occurred_on=day, direction=tx["direction"],
            amount=tx["amount"], item=tx["item"], category=tx["category"], source="quick",
        )
    for h in parsed.habit_logs:
        habit = db.get(Habit, h["habit_id"])
        try:
            records.log_habit(db, habit, day, h["status"])
        except ValueError as exc:
            result["errors"].append(str(exc))
    db.commit()
    result["committed"] = True
    return result
