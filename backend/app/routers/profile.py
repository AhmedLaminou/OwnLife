"""The profile: birth date, horizon, targets, vocabulary, AI preferences."""

from __future__ import annotations

import re
from datetime import date
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.deps import DB, CurrentProfile, CurrentUser
from app.serializers import profile_out
from app.services.timeutil import parse_hhmm, tz_of


class GlossaryItem(BaseModel):
    term: str = Field(min_length=1, max_length=80)
    meaning: str = Field(default="", max_length=500)
    private: bool = False


class Redaction(BaseModel):
    pattern: str = Field(min_length=1, max_length=200)
    replace: str = Field(default="[private]", max_length=80)


class AiSettings(BaseModel):
    mode: Literal["cloud", "local", "cloud_then_local", "off"] | None = None
    models: list[str] | None = None


class ProfileIn(BaseModel):
    display_name: str | None = Field(None, min_length=1, max_length=120)
    birth_date: date | None = None
    life_expectancy_years: int | None = Field(None, ge=30, le=150)
    timezone: str | None = None
    currency: str | None = Field(None, min_length=3, max_length=8)
    awakening_date: date | None = None
    mission_title: str | None = Field(None, max_length=120)
    mission_text: str | None = Field(None, max_length=4000)
    focus_target_hours: float | None = Field(None, ge=0, le=20)
    stretch_focus_hours: float | None = Field(None, ge=0, le=20)
    sleep_target_hours: float | None = Field(None, ge=3, le=12)
    noise_budget_hours: float | None = Field(None, ge=0, le=12)
    wake_target: str | None = None
    bed_target: str | None = None
    glossary: list[GlossaryItem] | None = None
    redactions: list[Redaction] | None = None
    ai_settings: AiSettings | None = None


router = APIRouter(prefix="/api/profile", tags=["profile"])


@router.get("")
def get_profile(user: CurrentUser, profile: CurrentProfile) -> dict:
    return profile_out(profile, user)


@router.put("")
def update_profile(body: ProfileIn, user: CurrentUser, profile: CurrentProfile, db: DB) -> dict:
    data = body.model_dump(exclude_unset=True)
    if "display_name" in data:
        user.display_name = data.pop("display_name").strip()
    if "timezone" in data:
        if tz_of(data["timezone"]).key != data["timezone"]:
            raise HTTPException(422, f"Unknown time zone {data['timezone']!r} (e.g. Africa/Niamey)")
    if "currency" in data and data["currency"]:
        data["currency"] = data["currency"].upper()
    for key in ("wake_target", "bed_target"):
        if data.get(key):
            try:
                h, m = parse_hhmm(data[key])
            except ValueError as e:
                raise HTTPException(422, str(e)) from e
            data[key] = f"{h:02d}:{m:02d}"
    if "redactions" in data and data["redactions"] is not None:
        for r in data["redactions"]:
            try:
                re.compile(r["pattern"])
            except re.error as e:
                raise HTTPException(422, f"Invalid pattern {r['pattern']!r}: {e}") from e
    if "ai_settings" in data and data["ai_settings"] is not None:
        data["ai_settings"] = {k: v for k, v in data["ai_settings"].items() if v is not None}
    for key, value in data.items():
        setattr(profile, key, value)
    db.commit()
    return profile_out(profile, user)
