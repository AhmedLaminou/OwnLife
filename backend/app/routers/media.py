"""What is being watched and read: media items, and YouTube history."""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Literal

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.config import get_settings
from app.db import utcnow
from app.deps import DB, CurrentProfile, CurrentUser
from app.ai.llm import effective_mode
from app.models import Category, ClassificationRule, MediaItem, WatchEvent
from app.serializers import category_out, iso, media_out
from app.services import videosort, watchlive
from app.services.rules import Activity, load_ruleset
from app.services.timeutil import local_today, range_utc, tz_of
from app.services.youtube import (
    HistoryFile,
    lookup_status,
    parse_history,
    rebuild_sessions,
    schedule_channel_lookup,
    store_watch_events,
    watched,
)

router = APIRouter(prefix="/api/media", tags=["media"])

Kind = Literal["book", "course", "series", "anime", "manga", "movie", "video", "channel", "podcast", "article", "music"]
Status = Literal["want", "in_progress", "done", "dropped", "none"]
MAX_TAKEOUT_BYTES = 200 * 1024 * 1024


class MediaIn(BaseModel):
    kind: Kind
    title: str = Field(min_length=1, max_length=300)
    creator: str | None = Field(None, max_length=200)
    url: str | None = Field(None, max_length=500)
    category_id: int | None = None
    status: Status = "want"
    progress_current: float | None = None
    progress_total: float | None = None
    progress_unit: str | None = Field(None, max_length=20)
    rating: int | None = Field(None, ge=1, le=5)
    notes: str | None = None


class MediaPatch(BaseModel):
    kind: Kind | None = None
    title: str | None = Field(None, min_length=1, max_length=300)
    creator: str | None = Field(None, max_length=200)
    url: str | None = Field(None, max_length=500)
    category_id: int | None = None
    status: Status | None = None
    progress_current: float | None = None
    progress_total: float | None = None
    progress_unit: str | None = Field(None, max_length=20)
    rating: int | None = Field(None, ge=1, le=5)
    notes: str | None = None


def _own(db, user_id: int, item_id: int) -> MediaItem:
    m = db.get(MediaItem, item_id)
    if m is None or m.user_id != user_id:
        raise HTTPException(404, "Item not found")
    return m


@router.get("")
def list_media(
    user: CurrentUser,
    db: DB,
    kind: str | None = None,
    status: str | None = None,
    q: str | None = None,
    include_videos: bool = False,
) -> list[dict]:
    query = select(MediaItem).where(MediaItem.user_id == user.id)
    if kind:
        query = query.where(MediaItem.kind == kind)
    elif not include_videos:
        query = query.where(MediaItem.kind != "video")  # single videos are a lookup cache
    if status:
        query = query.where(MediaItem.status == status)
    rows = db.scalars(query.order_by(MediaItem.updated_at.desc())).all()
    if q:
        rows = [m for m in rows if q.casefold() in f"{m.title} {m.creator or ''}".casefold()]
    return [media_out(m) for m in rows]


@router.post("")
def create_media(body: MediaIn, user: CurrentUser, db: DB) -> dict:
    m = MediaItem(user_id=user.id, **body.model_dump())
    db.add(m)
    db.commit()
    return media_out(m)


@router.patch("/{item_id}")
def update_media(item_id: int, body: MediaPatch, user: CurrentUser, db: DB) -> dict:
    m = _own(db, user.id, item_id)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(m, k, v)
    db.commit()
    return media_out(m)


@router.delete("/{item_id}")
def delete_media(item_id: int, user: CurrentUser, db: DB) -> dict:
    db.delete(_own(db, user.id, item_id))
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- YouTube
def _nothing_watched(parsed: HistoryFile) -> str:
    """Why a history file gave no video, and what to do about it."""
    if parsed.source == "chrome_history":
        return "This Chrome history has no YouTube video in it."
    found = parsed.ignored
    if found and set(found) == {"searches"}:
        return (
            "This is your YouTube search history. Import the other file of the same folder: the videos you "
            "watched (watch-history.json — in a French export, the one about « vidéos regardées »)."
        )
    if found:
        listed = ", ".join(f"{n} {kind}" for kind, n in found.most_common())
        return (
            f"No video watched in this file, only {listed}. YouTube is not keeping the videos you watch: turn on "
            "“Include the videos you watch on YouTube” (myactivity.google.com → YouTube History), then export "
            "again. Until then, Chrome's history (Takeout → Chrome → History.json) gives the recent weeks."
        )
    return "No watched videos found. In Google Takeout, export YouTube history as JSON."


@router.post("/youtube/takeout")
async def import_takeout(
    user: CurrentUser, profile: CurrentProfile, db: DB, file: UploadFile = File(...)
) -> dict:
    """A YouTube history (Takeout or My Activity, JSON or HTML) or a Chrome history."""
    raw = await file.read(MAX_TAKEOUT_BYTES + 1)
    if len(raw) > MAX_TAKEOUT_BYTES:
        raise HTTPException(413, "File too large (200 MB max)")
    tz = tz_of(profile.timezone)
    try:
        parsed = parse_history(raw, file.filename or "watch-history.json", tz)
    except ValueError as e:
        raise HTTPException(422, f"Could not read this file: {e}") from e
    events = parsed.events
    if not events:
        raise HTTPException(422, _nothing_watched(parsed))
    new = store_watch_events(db, user.id, events, parsed.source)
    first = min(e.occurred_at for e in events)
    last = max(e.occurred_at for e in events)
    blocks = rebuild_sessions(db, user.id, load_ruleset(db, user.id), first, last)
    db.commit()
    # Chrome's lines have no channel: look them up now that they are stored.
    lookup = parsed.source == "chrome_history" and schedule_channel_lookup(user.id, get_settings().youtube_oembed)
    return {
        "source": parsed.source,
        "events_in_file": len(events),
        "new_events": new,
        "skipped": parsed.skipped,
        "ignored": dict(parsed.ignored),
        "from": iso(first),
        "to": iso(last),
        "estimated_blocks": blocks,
        "channel_lookup": lookup,
    }


@router.get("/youtube/summary")
def youtube_summary(
    user: CurrentUser,
    profile: CurrentProfile,
    db: DB,
    start: date | None = None,
    end: date | None = None,
    limit: int = Query(40, le=500),
) -> dict:
    """Per channel: videos opened, estimated hours, and the category its rule gives."""
    tz = tz_of(profile.timezone)
    end = end or local_today(tz)
    start = start or end - timedelta(days=365)
    lo, hi = range_utc(start, end, tz)
    rows = watched(db, user.id, lo, hi)  # measured minutes, or estimated from the history files
    rules = load_ruleset(db, user.id)
    cats = {c.id: c for c in db.scalars(select(Category).where(Category.user_id == user.id))}
    channels: dict[str, dict] = {}
    hours_of_day = [0.0] * 24
    months: dict[str, float] = {}
    by_kind: dict[str, float] = {}
    for ev, secs, _ in rows:
        name = ev.channel or "Unknown channel"
        row = channels.get(name)
        if row is None:
            cat_id = rules.classify(Activity(title=ev.title, channel=ev.channel, url=ev.url))
            row = channels[name] = {"channel": name, "videos": 0, "seconds": 0.0, "category_id": cat_id,
                                    "last_watched": None}
        row["videos"] += 1
        row["seconds"] += secs
        row["last_watched"] = iso(ev.occurred_at)
        local = ev.occurred_at.astimezone(tz)
        hours_of_day[local.hour] += secs / 3600
        months[local.strftime("%Y-%m")] = months.get(local.strftime("%Y-%m"), 0.0) + secs / 3600
        kind = cats[row["category_id"]].kind if row["category_id"] in cats else "uncategorized"
        by_kind[kind] = by_kind.get(kind, 0.0) + secs / 3600
    top = sorted(channels.values(), key=lambda r: -r["seconds"])
    for r in top:
        c = cats.get(r["category_id"])
        r["category"] = category_out(c) if c else None
        r["hours"] = round(r.pop("seconds") / 3600, 2)
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "videos": len({(ev.video_id, ev.occurred_at.date()) for ev, _, _ in rows}),
        "measured_hours": round(sum(secs for _, secs, measured in rows if measured) / 3600, 1),
        "channels_count": len(channels),
        "estimated_hours": round(sum(r["hours"] for r in top), 1),
        "hours_by_kind": {k: round(v, 1) for k, v in by_kind.items()},
        "hours_of_day": [round(h, 2) for h in hours_of_day],
        "hours_by_month": [{"month": m, "hours": round(h, 1)} for m, h in sorted(months.items())],
        "channels": top[:limit],
        "unclassified_channels": sum(1 for r in top if r["category"] is None),
    }


@router.get("/youtube/events")
def youtube_events(
    user: CurrentUser, db: DB, channel: str | None = None, limit: int = Query(100, le=1000)
) -> list[dict]:
    q = select(WatchEvent).where(WatchEvent.user_id == user.id)
    if channel:
        q = q.where(WatchEvent.channel == channel)
    rows = db.scalars(q.order_by(WatchEvent.occurred_at.desc()).limit(limit)).all()
    return [
        {"id": r.id, "occurred_at": iso(r.occurred_at), "title": r.title, "channel": r.channel,
         "url": r.url, "source": r.source}
        for r in rows
    ]


@router.get("/youtube/stats")
def youtube_stats(user: CurrentUser, db: DB) -> dict:
    total = db.scalar(select(func.count()).select_from(WatchEvent).where(WatchEvent.user_id == user.id)) or 0
    first = db.scalar(select(func.min(WatchEvent.occurred_at)).where(WatchEvent.user_id == user.id))
    last = db.scalar(select(func.max(WatchEvent.occurred_at)).where(WatchEvent.user_id == user.id))
    return {"events": total, "first": iso(first), "last": iso(last), "now": iso(utcnow()),
            "channel_lookup": lookup_status(user.id)}


# ---------------------------------------------------------------- sorting the videos the extension measured
class SortPrefsIn(BaseModel):
    strict: bool | None = None
    sort: bool | None = None


class VideoCategoryIn(BaseModel):
    category_id: int
    whole_channel: bool = False  # a rule for the channel instead of this video alone


def _video_row(v: videosort.Video, cat: Category | None = None) -> dict:
    return {"video_id": v.video_id, "title": v.title, "channel": v.channel, "seconds": round(v.seconds),
            "last": iso(v.last), "url": f"https://www.youtube.com/watch?v={v.video_id}",
            "category": category_out(cat) if cat else None}


@router.get("/youtube/sorting")
def youtube_sorting(user: CurrentUser, profile: CurrentProfile, db: DB, days: int = Query(14, ge=1, le=90)) -> dict:
    """The videos of the last days that nothing places (with the model's guess,
    when it had one), and those the model sorted — to confirm or correct."""
    rules = load_ruleset(db, user.id)
    seen = videosort.verdicts(db, user.id)
    cats = {c.id: c for c in db.scalars(select(Category).where(Category.user_id == user.id))}
    to_sort, by_model = [], []
    for v in sorted(videosort.watched_videos(db, user.id, utcnow() - timedelta(days=days)).values(),
                    key=lambda v: v.last or utcnow(), reverse=True):
        act = Activity(title=v.title, channel=v.channel, url=f"https://www.youtube.com/watch?v={v.video_id}")
        if v.video_id in rules.yours or rules.by_rules(act) is not None:
            continue
        verdict = seen.get(v.video_id)
        if v.video_id in rules.model:
            by_model.append(_video_row(v, cats.get(rules.model[v.video_id])))
        else:
            guess = cats.get(verdict.category_id) if verdict is not None and verdict.sorted_by == "guess" else None
            to_sort.append({**_video_row(v, guess), "asked": verdict is not None})
    prefs = watchlive.settings_with_defaults((profile.prefs or {}).get("youtube"))
    return {"prefs": {"strict": prefs["strict"], "sort": prefs["sort"]},
            "ai": effective_mode(get_settings(), profile) != "off",
            "model": get_settings().ollama_chat_model, "status": videosort.status(user.id),
            "unsorted_category": watchlive.UNSORTED, "to_sort": to_sort, "by_model": by_model}


@router.put("/youtube/sorting/prefs")
def youtube_sorting_prefs(body: SortPrefsIn, user: CurrentUser, profile: CurrentProfile, db: DB) -> dict:
    old = watchlive.settings_with_defaults((profile.prefs or {}).get("youtube"))
    new = {**old, **body.model_dump(exclude_none=True)}
    profile.prefs = {**(profile.prefs or {}), "youtube": new}
    db.commit()
    refiled = 0
    if new["strict"] != old["strict"]:  # the videos nothing sorted change category
        with watchlive.LOCK:
            refiled = watchlive.rebuild(db, user.id, load_ruleset(db, user.id))
            db.commit()
    if new["sort"] and not old["sort"]:
        videosort.schedule(get_settings(), user.id, force=True)
    return {"prefs": {"strict": new["strict"], "sort": new["sort"]}, "refiled": refiled}


@router.post("/youtube/sorting/run")
def youtube_sorting_run(user: CurrentUser, profile: CurrentProfile) -> dict:
    if effective_mode(get_settings(), profile) == "off":
        raise HTTPException(400, "AI is off (Settings → AI): there is no model to sort the videos.")
    return {"running": videosort.schedule(get_settings(), user.id, force=True)}


@router.post("/youtube/videos/{video_id}/category")
def set_video_category(video_id: str, body: VideoCategoryIn, user: CurrentUser, db: DB) -> dict:
    """You sort a video: this one alone (it then wins over every rule), or its
    whole channel (a rule, which also applies to the history and to new videos)."""
    cat = db.get(Category, body.category_id)
    if cat is None or cat.user_id != user.id:
        raise HTTPException(404, "Category not found")
    v = videosort.watched_videos(db, user.id, utcnow() - timedelta(days=3650)).get(video_id)
    if v is None:
        raise HTTPException(404, "The extension has not measured this video")
    videos = {video_id}
    if body.whole_channel:
        if not v.channel:
            raise HTTPException(400, "This video's channel is unknown: sort the video alone")
        db.add(ClassificationRule(user_id=user.id, field="channel", pattern=f"^{re.escape(v.channel)}$", is_regex=True,
                                  category_id=cat.id, note="From the Watching page"))
        videos |= set(db.scalars(select(WatchEvent.video_id).where(
            WatchEvent.user_id == user.id, WatchEvent.source == watchlive.SOURCE, WatchEvent.channel == v.channel)))
        item = videosort.verdicts(db, user.id).get(video_id)
        if item is not None and item.sorted_by == "you":  # the rule now speaks for the whole channel
            item.sorted_by = None
    else:
        videosort.remember(db, user.id, v, cat.id, "you")
    db.commit()
    return {"ok": True, "refiled": videosort.refile(db, user.id, videos, unsorted_too=False)}
