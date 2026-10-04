"""ActivityWatch: status and sync."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool

from app.config import get_settings
from app.db import SessionLocal
from app.deps import DB, CurrentUser
from app.serializers import iso
from app.services import activitywatch as aw
from app.services.rules import load_ruleset

router = APIRouter(prefix="/api/integrations", tags=["integrations"])


@router.get("/activitywatch")
def aw_status(user: CurrentUser, db: DB) -> dict:
    s = get_settings()
    state = aw.get_state(db, user.id)
    db.commit()
    client = aw.AWClient(s.activitywatch_url)
    info, buckets, error = None, [], None
    try:
        info = client.info()
        buckets = [
            {"id": b, "type": v.get("type"), "last_updated": v.get("last_updated")}
            for b, v in client.buckets().items()
        ]
    except aw.ActivityWatchError as e:
        error = str(e)
    finally:
        client.close()
    return {
        "url": s.activitywatch_url,
        "reachable": info is not None,
        "info": info,
        "buckets": buckets,
        "has_web_watcher": any(b["id"].startswith("aw-watcher-web") for b in buckets),
        "error": error,
        "last_synced_at": iso(state.last_synced_at),
        "last_error": state.last_error,
        "cursor": state.cursor,
        "autosync_minutes": s.activitywatch_autosync_minutes,
    }


def run_sync(user_id: int) -> dict:
    s = get_settings()
    client = aw.AWClient(s.activitywatch_url)
    try:
        with SessionLocal() as db:
            try:
                result = aw.sync(db, user_id, client, load_ruleset(db, user_id), oembed=s.youtube_oembed)
            except aw.ActivityWatchError:
                db.commit()  # keep last_error
                raise
            db.commit()
            return result
    finally:
        client.close()


@router.post("/activitywatch/sync")
async def aw_sync(user: CurrentUser) -> dict:
    try:
        return await run_in_threadpool(run_sync, user.id)
    except aw.ActivityWatchError as e:
        raise HTTPException(502, str(e)) from e
