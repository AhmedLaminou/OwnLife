"""The scale of a life (weeks grid, projections) and the home dashboard."""

from __future__ import annotations

import datetime as dt

from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.db import utcnow
from app.deps import DB, CurrentProfile, CurrentUser
from app.models import CaptureDraft, Goal, Habit, JournalEntry, LifeChapter, LifeEvent
from app.serializers import chapter_out, entry_out, event_out, habit_out, iso
from app.services import records
from app.services.habits import habit_stats
from app.services.ledger import daily_breakdown
from app.services.life import add_years, life_overview, weekly_hours
from app.services.timeutil import local_today, tz_of

router = APIRouter(prefix="/api", tags=["life"])

Area = Literal["education", "family", "faith", "health", "work", "move", "travel", "achievement",
               "turning_point", "other"]


class EventIn(BaseModel):
    date: dt.date
    precision: Literal["day", "month", "year"] = "day"
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(None, max_length=4000)
    area: Area = "other"
    importance: int = Field(2, ge=1, le=3)
    is_private: bool = False


class EventPatch(BaseModel):
    date: dt.date | None = None
    precision: Literal["day", "month", "year"] | None = None
    title: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, max_length=4000)
    area: Area | None = None
    importance: int | None = Field(None, ge=1, le=3)
    is_private: bool | None = None


def _events(db, user_id: int) -> list[LifeEvent]:
    return list(db.scalars(
        select(LifeEvent).where(LifeEvent.user_id == user_id).order_by(LifeEvent.event_date, LifeEvent.id)
    ))


def _milestones(db, user_id: int, today) -> list[dict]:
    goals = db.scalars(
        select(Goal).where(Goal.user_id == user_id, Goal.target_date.is_not(None)).order_by(Goal.target_date)
    ).all()
    return [
        {"id": g.id, "title": g.title, "level": g.level, "status": g.status, "target_date": iso(g.target_date),
         "days_left": (g.target_date - today).days}
        for g in goals
    ]


@router.get("/life/overview")
def overview(user: CurrentUser, profile: CurrentProfile, db: DB) -> dict:
    today = local_today(tz_of(profile.timezone))
    data = life_overview(db, user.id, profile, utcnow())
    chapters = db.scalars(
        select(LifeChapter).where(LifeChapter.user_id == user.id).order_by(LifeChapter.start_date)
    ).all()
    data["chapters"] = [chapter_out(c) for c in chapters]
    data["events"] = [event_out(e) for e in _events(db, user.id)]
    data["milestones"] = _milestones(db, user.id, today)
    return data


@router.get("/life/events")
def list_events(user: CurrentUser, db: DB) -> list[dict]:
    return [event_out(e) for e in _events(db, user.id)]


@router.post("/life/events")
def create_event(body: EventIn, user: CurrentUser, db: DB) -> dict:
    data = body.model_dump()
    e = LifeEvent(user_id=user.id, event_date=data.pop("date"), **data)
    db.add(e)
    db.commit()
    return event_out(e)


def _own_event(db, user_id: int, event_id: int) -> LifeEvent:
    e = db.get(LifeEvent, event_id)
    if e is None or e.user_id != user_id:
        raise HTTPException(404, "Event not found")
    return e


@router.patch("/life/events/{event_id}")
def update_event(event_id: int, body: EventPatch, user: CurrentUser, db: DB) -> dict:
    e = _own_event(db, user.id, event_id)
    data = body.model_dump(exclude_unset=True)
    if "date" in data:
        e.event_date = data.pop("date")
    for k, v in data.items():
        setattr(e, k, v)
    db.commit()
    return event_out(e)


@router.delete("/life/events/{event_id}")
def delete_event(event_id: int, user: CurrentUser, db: DB) -> dict:
    db.delete(_own_event(db, user.id, event_id))
    db.commit()
    return {"ok": True}


@router.get("/life/weeks")
def weeks(user: CurrentUser, profile: CurrentProfile, db: DB) -> dict:
    return {"weeks": weekly_hours(db, user.id, profile, utcnow())}


@router.get("/dashboard")
def dashboard(user: CurrentUser, profile: CurrentProfile, db: DB) -> dict:
    tz = tz_of(profile.timezone)
    today = local_today(tz)
    now = utcnow()
    week = daily_breakdown(db, user.id, today - timedelta(days=6), today, tz, now)
    kinds_today = week.get(today, {})
    habits = []
    for h in db.scalars(select(Habit).where(Habit.user_id == user.id, Habit.archived.is_(False)).order_by(Habit.sort)):
        st = habit_stats(db, h, profile.timezone, now, days=14)
        habits.append({**habit_out(h), "today": st["today"], "current_streak": st["current_streak"],
                       "best_streak": st["best_streak"], "next_milestone": st.get("next_milestone"),
                       "recent": st["calendar"][-14:]})
    timer = records.running_timer(db, user.id)
    journal_today = db.scalar(
        select(func.count()).select_from(JournalEntry).where(
            JournalEntry.user_id == user.id, JournalEntry.entry_date == today, JournalEntry.visible())
    )
    pending = db.scalar(
        select(func.count()).select_from(CaptureDraft).where(CaptureDraft.user_id == user.id, CaptureDraft.status == "pending")
    )
    upcoming = [m for m in _milestones(db, user.id, today) if m["days_left"] >= 0 and m["status"] == "active"][:4]
    life = {}
    if profile.birth_date:
        days_alive = (today - profile.birth_date).days
        life = {
            "days_alive": days_alive,
            "weeks_alive": days_alive // 7,
            "total_weeks": (add_years(profile.birth_date, profile.life_expectancy_years) - profile.birth_date).days // 7,
            "age_years": round(days_alive / 365.2425, 2),
        }
    return {
        "today": today.isoformat(),
        "now": iso(now),
        "display_name": user.display_name,
        "awakening_day": (today - profile.awakening_date).days + 1 if profile.awakening_date else None,
        "mission_title": profile.mission_title,
        "kinds_today": kinds_today,
        "targets": {
            "focus_hours": profile.focus_target_hours,
            "stretch_hours": profile.stretch_focus_hours,
            "noise_budget_hours": profile.noise_budget_hours,
            "sleep_hours": profile.sleep_target_hours,
        },
        "week": [{"date": d.isoformat(), "kinds": k} for d, k in sorted(week.items())],
        "timer": entry_out(timer, now) if timer else None,
        "habits": habits,
        "journal_today": bool(journal_today),
        "pending_drafts": pending or 0,
        "milestones": upcoming,
        "life": life,
    }
