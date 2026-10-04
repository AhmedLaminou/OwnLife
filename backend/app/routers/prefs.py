"""Preferences that are not part of the profile proper: prayer times and
reminders. Stored in profile.prefs, one section each, with defaults in code."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from app.db import utcnow
from app.deps import DB, CurrentProfile, CurrentUser
from app.services import notify, prayer, reminders
from app.services.activitywatch import get_state
from app.services.timeutil import local_today, tz_of

router = APIRouter(prefix="/api", tags=["prefs"])


class PrayerPrefs(BaseModel):
    city: str = Field("Niamey", max_length=80)
    latitude: float = Field(13.5116, ge=-90, le=90)
    longitude: float = Field(2.1254, ge=-180, le=180)
    method: Literal["MWL", "ISNA", "Egypt", "Makkah", "Karachi", "France"] = "MWL"
    asr: Literal["standard", "hanafi"] = "standard"
    offsets: dict[Literal["fajr", "sunrise", "dhuhr", "asr", "maghrib", "isha"], int] = Field(default_factory=dict)
    align_planner: bool = True


class ReminderPrefs(BaseModel):
    enabled: bool = True
    capture: bool = True
    capture_before_bed_minutes: int = Field(30, ge=5, le=180)
    bedtime: bool = True
    bedtime_text: str = Field("Bedtime. Screen off, phone in another room.", max_length=200)
    morning: bool = True
    auto_review: bool = True
    timer_nudge_minutes: int = Field(120, ge=0, le=720)


def _section(profile, name: str) -> dict:
    return (profile.prefs or {}).get(name) or {}


def _save(db, profile, name: str, value: dict) -> None:
    profile.prefs = {**(profile.prefs or {}), name: value}  # a new dict: the JSON column sees the change
    db.commit()


@router.get("/prefs")
def get_prefs(user: CurrentUser, profile: CurrentProfile) -> dict:  # noqa: ARG001
    return {
        "prayer": prayer.settings_with_defaults(_section(profile, "prayer")),
        "reminders": reminders.settings_with_defaults(_section(profile, "reminders")),
        "prayer_methods": [{"id": k, "name": v["name"]} for k, v in prayer.METHODS.items()],
        "notifications_available": notify.available(),
    }


@router.put("/prefs/prayer")
def put_prayer(body: PrayerPrefs, user: CurrentUser, profile: CurrentProfile, db: DB) -> dict:  # noqa: ARG001
    _save(db, profile, "prayer", body.model_dump())
    return prayer.settings_with_defaults(_section(profile, "prayer"))


@router.put("/prefs/reminders")
def put_reminders(body: ReminderPrefs, user: CurrentUser, profile: CurrentProfile, db: DB) -> dict:  # noqa: ARG001
    _save(db, profile, "reminders", body.model_dump())
    return reminders.settings_with_defaults(_section(profile, "reminders"))


# ---------------------------------------------------------------- prayer times
@router.get("/prayer/{day}")
def prayer_day(day: date, user: CurrentUser, profile: CurrentProfile) -> dict:  # noqa: ARG001
    tz = tz_of(profile.timezone)
    info = prayer.times_for(day, _section(profile, "prayer"), tz)
    if day == local_today(tz):
        info["next"] = prayer.next_prayer(datetime.now(tz), _section(profile, "prayer"), tz)
    return info


# ---------------------------------------------------------------- reminders
@router.get("/reminders")
def reminders_status(user: CurrentUser, profile: CurrentProfile, db: DB) -> dict:
    state = get_state(db, user.id, "reminders")
    db.commit()
    return {
        "today": reminders.status(db, profile, state.cursor, utcnow()),
        "last_error": state.last_error,
        "available": notify.available(),
    }


@router.post("/reminders/test")
async def reminders_test(user: CurrentUser) -> dict:  # noqa: ARG001
    shown, detail = await run_in_threadpool(
        notify.send, "OwnLife", "Notifications work. The evening reminders will look like this.", None, None
    )
    return {"shown": shown, "detail": detail}
