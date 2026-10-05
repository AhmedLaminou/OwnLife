"""Ideas found in the journal's thought sections, and the essays they go to."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.ai.embeddings import get_embedder
from app.ai.llm import effective_mode
from app.config import get_settings
from app.deps import DB, CurrentProfile, CurrentUser
from app.models import Idea, JournalEntry, Note
from app.serializers import idea_out, iso
from app.services import filesync, journal_ideas
from app.services.activitywatch import get_state
from app.services.timeutil import local_today, tz_of
from app.services.undo import UndoError

router = APIRouter(prefix="/api/ideas", tags=["ideas"])


class ReadIn(BaseModel):
    all: bool = False  # read every page again, not only the changed ones


class IdeasPrefs(BaseModel):
    scan: bool = False


class IdeaPatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=200)
    statement: str | None = Field(None, min_length=1, max_length=4000)
    domain: Literal[journal_ideas.DOMAINS] | None = None  # type: ignore[valid-type]
    note_id: int | None = None
    new_essay: str | None = Field(None, max_length=200)


class PlaceIn(BaseModel):
    note_id: int | None = None
    new_title: str | None = Field(None, max_length=200)
    text: str | None = Field(None, max_length=20000)  # what goes into the essay; your passage when left out


class StatusIn(BaseModel):
    status: Literal["new", "kept", "dismissed"]


def _own(db, user_id: int, idea_id: int) -> Idea:
    i = db.get(Idea, idea_id)
    if i is None or i.user_id != user_id:
        raise HTTPException(404, "Idea not found")
    return i


def _notes(db, user_id: int) -> dict[int, Note]:
    return {n.id: n for n in db.scalars(select(Note).where(Note.user_id == user_id))}


@router.get("")
def list_ideas(user: CurrentUser, profile: CurrentProfile, db: DB, status: str | None = None) -> dict:
    q = select(Idea).where(Idea.user_id == user.id)
    if status:
        q = q.where(Idea.status == status)
    rows = db.scalars(q.order_by(Idea.entry_date.desc(), Idea.id)).all()
    notes = _notes(db, user.id)
    counts = dict(db.execute(select(Idea.status, func.count()).where(Idea.user_id == user.id).group_by(Idea.status)).all())
    state = get_state(db, user.id, journal_ideas.PROVIDER)
    cursor = state.cursor or {}
    waiting = journal_ideas.changed_entries(db, user.id, dict(cursor.get("days") or {}))
    pages = db.scalars(select(JournalEntry).where(JournalEntry.user_id == user.id, JournalEntry.visible())).all()
    thought_chars = sum(len(s) for e in pages for s in journal_ideas.thought_sections(e.body))
    journal_chars = sum(len(e.body or "") for e in pages)
    s = get_settings()
    out = {
        "ideas": [idea_out(i, notes) for i in rows],
        "counts": {k: counts.get(k, 0) for k in ("new", "kept", "placed", "dismissed")},
        "essays": [{"id": n.id, "title": n.title, "kind": n.kind, "words": len((n.body or "").split()),
                    "is_private": n.is_private} for n in journal_ideas.destinations(db, user.id)],
        "prefs": journal_ideas.settings_with_defaults((profile.prefs or {}).get("ideas")),
        "scan": journal_ideas.status(user.id),
        "last_scan": iso(state.last_synced_at),
        "last_error": state.last_error,
        "pages_waiting": len(waiting),
        "thought_share": round(thought_chars / journal_chars, 2) if journal_chars else 0.0,
        "online": bool(effective_mode(s, profile) != "off" and journal_ideas.online_models(s, profile)),
        "models": [c.label for c in journal_ideas.online_models(s, profile)],
    }
    db.commit()
    return out


@router.post("/scan")
def scan_ideas(body: ReadIn, user: CurrentUser, profile: CurrentProfile) -> dict:
    s = get_settings()
    if effective_mode(s, profile) == "off" or not journal_ideas.online_models(s, profile):
        raise HTTPException(400, "Reading ideas needs an online model: an OpenRouter key and an AI mode that uses it.")
    journal_ideas.schedule(s, user.id, rescan_all=body.all)
    return journal_ideas.status(user.id)


@router.put("/prefs")
def ideas_prefs(body: IdeasPrefs, profile: CurrentProfile, db: DB) -> dict:
    profile.prefs = {**(profile.prefs or {}), "ideas": body.model_dump()}
    db.commit()
    return journal_ideas.settings_with_defaults(profile.prefs["ideas"])


@router.get("/essays")
async def essays_with_passages(user: CurrentUser) -> list[dict]:
    """For each essay, the journal passages closest in meaning (local embeddings)."""
    uid = user.id

    def work() -> list[dict]:
        from app.db import SessionLocal

        embedder = get_embedder(get_settings())
        with SessionLocal() as db:
            out = []
            for n in journal_ideas.destinations(db, uid):
                passages: list[dict] = []
                error = None if embedder is not None else "Search by meaning is off (EMBEDDINGS_PROVIDER): no passages."
                if embedder is not None:
                    try:
                        passages = journal_ideas.essay_passages(db, uid, embedder, n)
                    except Exception:  # Ollama not running: the rest of the page still works
                        error = "The local search model (Ollama) is not answering."
                out.append({"id": n.id, "title": n.title, "words": len((n.body or "").split()), "passages": passages,
                            "error": error})
            return out

    return await run_in_threadpool(work)


@router.patch("/{idea_id}")
def update_idea(idea_id: int, body: IdeaPatch, user: CurrentUser, db: DB) -> dict:
    i = _own(db, user.id, idea_id)
    data = body.model_dump(exclude_unset=True)
    if data.get("note_id") is not None:
        n = db.get(Note, data["note_id"])
        if n is None or n.user_id != user.id:
            raise HTTPException(404, "Essay not found")
        data["new_essay"] = None
    for k, v in data.items():
        setattr(i, k, v.strip() if isinstance(v, str) else v)
    db.commit()
    return idea_out(i, _notes(db, user.id))


@router.post("/{idea_id}/status")
def set_status(idea_id: int, body: StatusIn, user: CurrentUser, db: DB) -> dict:
    i = _own(db, user.id, idea_id)
    if i.status == "placed":
        raise HTTPException(409, "This idea is in an essay: undo that first.")
    i.status = body.status
    db.commit()
    return idea_out(i, _notes(db, user.id))


@router.post("/{idea_id}/place")
def place_idea(idea_id: int, body: PlaceIn, user: CurrentUser, profile: CurrentProfile, db: DB) -> dict:
    i = _own(db, user.id, idea_id)
    with filesync.LOCK:
        try:
            r = journal_ideas.place(db, user.id, i, filesync.config(get_settings()), note_id=body.note_id,
                                    new_title=body.new_title, text=body.text, today=local_today(tz_of(profile.timezone)))
        except journal_ideas.PlaceError as e:
            raise HTTPException(409, str(e)) from e
        except filesync.SyncError as e:
            db.rollback()
            raise HTTPException(409, f"The essay's file refused it: {e}") from e
        db.commit()
    return {**r, "idea": idea_out(i, _notes(db, user.id))}


@router.post("/{idea_id}/unplace")
def unplace_idea(idea_id: int, user: CurrentUser, profile: CurrentProfile, db: DB) -> dict:
    with filesync.LOCK:
        try:
            message = journal_ideas.unplace(db, user.id, idea_id, filesync.config(get_settings()),
                                            today=local_today(tz_of(profile.timezone)))
        except UndoError as e:
            raise HTTPException(409, str(e)) from e
        db.commit()
    return {"ok": True, "message": message, "idea": idea_out(_own(db, user.id, idea_id), _notes(db, user.id))}
