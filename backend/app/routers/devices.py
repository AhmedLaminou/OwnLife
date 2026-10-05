"""Keys for devices and tools other than the browser session — today the
OwnLife YouTube extension — and the endpoints they call.

A key is shown once, stored only as a SHA-256, limited to its scopes, and can be
revoked at any time. It cannot read your journal or change anything else."""

from __future__ import annotations

import secrets
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal, utcnow
from app.deps import DB, CurrentProfile, CurrentUser
from app.models import ApiToken, Profile, User, WatchEvent
from app.security import hash_token
from app.serializers import iso
from app.services import reminders, videosort, watchlive
from app.services.reminders import noise_today
from app.services.rules import load_ruleset
from app.services.timeutil import local_today, range_utc, tz_of

router = APIRouter(prefix="/api", tags=["devices"])

Scope = Literal["youtube"]


class TokenIn(BaseModel):
    name: str = Field("YouTube extension", min_length=1, max_length=80)
    scopes: list[Scope] = Field(default_factory=lambda: ["youtube"])


class Heartbeat(BaseModel):
    video_id: str = Field(max_length=32)
    ts: str = Field(max_length=40)
    title: str | None = Field(None, max_length=300)
    channel: str | None = Field(None, max_length=200)
    url: str | None = Field(None, max_length=500)


class IngestIn(BaseModel):
    heartbeats: list[Heartbeat] = Field(max_length=2000)
    client: str | None = Field(None, max_length=80)


def token_out(t: ApiToken) -> dict:
    return {"id": t.id, "name": t.name, "prefix": t.prefix, "scopes": t.scopes or [],
            "created_at": iso(t.created_at), "last_used_at": iso(t.last_used_at)}


# ---------------------------------------------------------------- managing keys (browser session)
@router.get("/tokens")
def list_tokens(user: CurrentUser, db: DB) -> list[dict]:
    return [token_out(t) for t in db.scalars(select(ApiToken).where(ApiToken.user_id == user.id).order_by(ApiToken.id))]


@router.post("/tokens")
def create_token(body: TokenIn, user: CurrentUser, db: DB) -> dict:
    raw = "ol_" + secrets.token_urlsafe(32)
    t = ApiToken(user_id=user.id, name=body.name, token_hash=hash_token(raw), prefix=raw[:10], scopes=list(body.scopes))
    db.add(t)
    db.commit()
    return {**token_out(t), "token": raw}


@router.delete("/tokens/{token_id}")
def revoke_token(token_id: int, user: CurrentUser, db: DB) -> dict:
    t = db.get(ApiToken, token_id)
    if t is None or t.user_id != user.id:
        raise HTTPException(404, "Key not found")
    db.delete(t)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- called by the extension (key)
def token_user(scope: str):
    def dependency(db: DB, authorization: Annotated[str | None, Header()] = None) -> User:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(401, "Missing key: paste an OwnLife key in the extension's options")
        t = db.scalar(select(ApiToken).where(ApiToken.token_hash == hash_token(authorization[7:].strip())))
        if t is None:
            raise HTTPException(401, "Unknown or revoked key")
        if scope not in (t.scopes or []):
            raise HTTPException(403, f"This key is not allowed to {scope}")
        user = db.get(User, t.user_id)
        if user is None or not user.is_active:
            raise HTTPException(401, "Account disabled")
        now = utcnow()
        if t.last_used_at is None or (now - t.last_used_at).total_seconds() > 60:
            t.last_used_at = now
            db.commit()
        return user

    return dependency


YoutubeUser = Annotated[User, Depends(token_user("youtube"))]


def _today(db, user_id: int) -> dict:
    """What the extension shows: today's YouTube by channel, and today's noise
    (from every source) against the budget, for the badge on its icon."""
    profile = db.get(Profile, user_id)
    tz = tz_of(profile.timezone)
    lo, hi = range_utc(local_today(tz), local_today(tz), tz)
    warn = int(reminders.settings_with_defaults((profile.prefs or {}).get("reminders"))["noise_warn_minutes"] or 0)
    return {**watchlive.today_summary(db, user_id, lo, hi),
            "noise": {**noise_today(db, user_id, profile, utcnow()), "warn_seconds": warn * 60}}


@router.get("/ingest/ping")
def ingest_ping(user: YoutubeUser, db: DB) -> dict:
    return {"ok": True, "name": user.display_name, "today": _today(db, user.id)}


@router.post("/ingest/youtube")
async def ingest_youtube(body: IngestIn, user: YoutubeUser) -> dict:
    uid = user.id
    beats = [h.model_dump() for h in body.heartbeats]

    def work() -> dict:
        settings = get_settings()
        with SessionLocal() as db:
            with watchlive.LOCK:
                result = watchlive.ingest(db, uid, beats, load_ruleset(db, uid), oembed=settings.youtube_oembed)
                db.commit()
            videosort.schedule(settings, uid)  # videos no rule places: the local model sorts them
            return {**result, "today": _today(db, uid)}

    return await run_in_threadpool(work)


@router.get("/ingest/youtube/recent")
def recent_segments(user: CurrentUser, profile: CurrentProfile, db: DB) -> dict:
    """For the Watching page: today's measured minutes, as the extension saw them."""
    tz = tz_of(profile.timezone)
    lo, hi = range_utc(local_today(tz), local_today(tz), tz)
    rows = db.scalars(select(WatchEvent).where(
        WatchEvent.user_id == user.id, WatchEvent.source == watchlive.SOURCE,
        WatchEvent.occurred_at >= lo, WatchEvent.occurred_at < hi,
    ).order_by(WatchEvent.occurred_at.desc())).all()
    last = db.scalar(select(WatchEvent.ended_at).where(
        WatchEvent.user_id == user.id, WatchEvent.source == watchlive.SOURCE).order_by(WatchEvent.ended_at.desc()).limit(1))
    return {
        "today": _today(db, user.id),
        "last_seen": iso(last),
        "segments": [{"title": r.title, "channel": r.channel, "start": iso(r.occurred_at), "end": iso(r.ended_at),
                      "seconds": r.seconds, "url": r.url} for r in rows[:50]],
    }
