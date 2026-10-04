"""Habits: to build (ticked or evaluated from data) and to quit (streaks, urges)."""

from __future__ import annotations

import datetime as dt

from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select

from app.db import utcnow
from app.deps import DB, CurrentProfile, CurrentUser
from app.models import CATEGORY_KINDS, Habit, HabitLog
from app.serializers import habit_out, iso
from app.services import records
from app.services.habits import habit_stats
from app.services.timeutil import local_instant, local_today, tz_of

router = APIRouter(prefix="/api/habits", tags=["habits"])


def _validate_rule(rule: dict[str, Any] | None) -> dict[str, Any] | None:
    if rule is None:
        return None
    t = rule.get("type")
    if t == "journal_written":
        return {"type": t}
    if t in ("kind_hours_min", "kind_hours_max"):
        if rule.get("kind") not in CATEGORY_KINDS:
            raise ValueError(f"rule.kind must be one of {', '.join(CATEGORY_KINDS)}")
        hours = float(rule.get("hours", -1))
        if not 0 <= hours <= 24:
            raise ValueError("rule.hours must be between 0 and 24")
        return {"type": t, "kind": rule["kind"], "hours": hours}
    raise ValueError("rule.type must be kind_hours_min, kind_hours_max or journal_written")


class HabitIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["build", "quit"] = "build"
    description: str | None = None
    target_per_week: int = Field(7, ge=1, le=7)
    rule: dict[str, Any] | None = None
    start_date: date | None = None
    is_private: bool = False
    color: str | None = Field(None, max_length=9)
    icon: str | None = Field(None, max_length=40)

    _rule = field_validator("rule")(_validate_rule)


class HabitPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    description: str | None = None
    target_per_week: int | None = Field(None, ge=1, le=7)
    rule: dict[str, Any] | None = None
    start_date: date | None = None
    is_private: bool | None = None
    color: str | None = Field(None, max_length=9)
    icon: str | None = Field(None, max_length=40)
    archived: bool | None = None
    sort: int | None = None

    _rule = field_validator("rule")(_validate_rule)


class LogIn(BaseModel):
    date: dt.date | None = None
    status: Literal["done", "missed", "skip", "urge", "relapse"]
    time: str | None = None
    note: str | None = Field(None, max_length=2000)
    value: float | None = None


def _own(db, user_id: int, habit_id: int) -> Habit:
    h = db.get(Habit, habit_id)
    if h is None or h.user_id != user_id:
        raise HTTPException(404, "Habit not found")
    return h


@router.get("")
def list_habits(
    user: CurrentUser, profile: CurrentProfile, db: DB, include_archived: bool = False, days: int = Query(120, le=730)
) -> list[dict]:
    q = select(Habit).where(Habit.user_id == user.id)
    if not include_archived:
        q = q.where(Habit.archived.is_(False))
    now = utcnow()
    return [
        {**habit_out(h), "stats": habit_stats(db, h, profile.timezone, now, days=days)}
        for h in db.scalars(q.order_by(Habit.sort, Habit.id))
    ]


@router.post("")
def create_habit(body: HabitIn, user: CurrentUser, profile: CurrentProfile, db: DB) -> dict:
    top = db.scalar(select(func.max(Habit.sort)).where(Habit.user_id == user.id)) or 0
    data = body.model_dump()
    data["start_date"] = data["start_date"] or local_today(tz_of(profile.timezone))
    if data["kind"] == "quit":
        data["rule"] = None
    h = Habit(user_id=user.id, sort=top + 1, **data)
    db.add(h)
    db.commit()
    return {**habit_out(h), "stats": habit_stats(db, h, profile.timezone, utcnow())}


@router.patch("/{habit_id}")
def update_habit(habit_id: int, body: HabitPatch, user: CurrentUser, profile: CurrentProfile, db: DB) -> dict:
    h = _own(db, user.id, habit_id)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(h, k, v)
    db.commit()
    return {**habit_out(h), "stats": habit_stats(db, h, profile.timezone, utcnow())}


@router.delete("/{habit_id}")
def delete_habit(habit_id: int, user: CurrentUser, db: DB) -> dict:
    db.delete(_own(db, user.id, habit_id))
    db.commit()
    return {"ok": True}


@router.post("/{habit_id}/log")
def log(habit_id: int, body: LogIn, user: CurrentUser, profile: CurrentProfile, db: DB) -> dict:
    h = _own(db, user.id, habit_id)
    tz = tz_of(profile.timezone)
    d = body.date or local_today(tz)
    try:
        when = local_instant(d, body.time, tz) if body.time else (utcnow() if body.date is None else None)
        records.log_habit(db, h, d, body.status, occurred_at=when, note=body.note, value=body.value)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    db.commit()
    return {**habit_out(h), "stats": habit_stats(db, h, profile.timezone, utcnow())}


@router.get("/{habit_id}/logs")
def logs(habit_id: int, user: CurrentUser, db: DB, limit: int = Query(200, le=2000)) -> list[dict]:
    _own(db, user.id, habit_id)
    rows = db.scalars(
        select(HabitLog).where(HabitLog.habit_id == habit_id).order_by(HabitLog.log_date.desc(), HabitLog.id.desc()).limit(limit)
    ).all()
    return [
        {"id": r.id, "date": iso(r.log_date), "status": r.status, "occurred_at": iso(r.occurred_at),
         "note": r.note, "value": r.value}
        for r in rows
    ]


@router.delete("/logs/{log_id}")
def delete_log(log_id: int, user: CurrentUser, db: DB) -> dict:
    r = db.get(HabitLog, log_id)
    if r is None or r.user_id != user.id:
        raise HTTPException(404, "Log not found")
    db.delete(r)
    db.commit()
    return {"ok": True}
