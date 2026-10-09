"""Blocking noise on YouTube.

The extension asks before a video plays, and every minute while it plays:
"this video — may it play?" The answer comes from the same classification as
the ledger (your rules, your sortings, the local model's), and from today's
noise, counted from every source as for the noise alert:

    a channel you blocked                    → blocked, every day
    a noise category you always block        → blocked, every day
    a noise category, once today's noise     → blocked until midnight
      reaches the limit (2 hours by default)    (your local midnight)
    anything else (learning, unsorted…)      → plays

A video nothing has sorted yet plays: the local model sorts it within a minute
or two, and the next check blocks it if it turns out to be noise. When OwnLife
is not running, nothing is blocked (the extension cannot ask).
"""

from __future__ import annotations

import unicodedata
from datetime import datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Category, Profile
from app.services import watchlive
from app.services.reminders import noise_today
from app.services.rules import Activity, RuleSet
from app.services.timeutil import tz_of

DEFAULTS = {
    "enabled": False,
    "limit_minutes": 120,
    "always": [],  # noise categories blocked all day, every day (ids)
    "never": [],  # noise categories never blocked (ids)
    "channels": [],  # channels blocked all day, every day (names)
}


def settings_with_defaults(prefs: dict | None) -> dict:
    return {**DEFAULTS, **(prefs or {})}


def prefs_of(profile: Profile) -> dict:
    return settings_with_defaults((profile.prefs or {}).get("youtube_block"))


def channel_key(name: str | None) -> str:
    """'Daily  Planet News' and 'daily planet news' are one channel; accents aside."""
    s = unicodedata.normalize("NFKD", name or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return " ".join(s.casefold().split())


def next_midnight(profile: Profile, now: datetime) -> datetime:
    tz = tz_of(profile.timezone)
    local = now.astimezone(tz)
    return datetime.combine(local.date() + timedelta(days=1), time(0), tzinfo=tz)


def noise_categories(db: Session, user_id: int) -> list[Category]:
    """The categories blocking can apply to: noise, minus "not sorted yet"."""
    return list(db.scalars(select(Category).where(
        Category.user_id == user_id, Category.kind == "noise", Category.archived.is_(False),
        Category.name != watchlive.UNSORTED).order_by(Category.sort, Category.id)))


def status(db: Session, user_id: int, profile: Profile, now: datetime) -> dict:
    """Today's noise against the limit, and whether blocking is on now."""
    prefs = prefs_of(profile)
    noise = noise_today(db, user_id, profile, now)
    limit = int(prefs["limit_minutes"]) * 60
    return {"enabled": bool(prefs["enabled"]), "noise_seconds": noise["seconds"], "limit_seconds": limit,
            "active": bool(prefs["enabled"]) and noise["seconds"] >= limit,
            "until": next_midnight(profile, now).isoformat()}


def decide(db: Session, user_id: int, profile: Profile, ruleset: RuleSet, video: dict, now: datetime) -> dict:
    """May this video play? video: video_id, title, channel, url."""
    prefs = prefs_of(profile)
    st = status(db, user_id, profile, now)
    channel = (video.get("channel") or "").strip() or None
    cat_id = ruleset.classify(Activity(title=video.get("title"), channel=channel, url=video.get("url")))
    cat = db.get(Category, cat_id) if cat_id else None
    out = {**st, "blocked": False, "reason": None, "channel": channel,  # a private category is never named
           "category": {"id": cat.id, "name": None if cat.is_private else cat.name, "kind": cat.kind} if cat else None}
    if not prefs["enabled"]:
        return out
    blocked_channels = {channel_key(c) for c in prefs["channels"]}
    if channel and channel_key(channel) in blocked_channels:
        return {**out, "blocked": True, "reason": "channel"}
    if cat is None or cat.kind != "noise" or cat.name == watchlive.UNSORTED or cat.id in prefs["never"]:
        return out
    if cat.id in prefs["always"]:
        return {**out, "blocked": True, "reason": "category"}
    if st["active"]:
        return {**out, "blocked": True, "reason": "limit"}
    return out
