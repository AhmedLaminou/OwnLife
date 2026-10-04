"""The written memory: journal days, notes, the two-way file sync, and search."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.ai import rag
from app.ai.embeddings import get_embedder
from app.config import get_settings
from app.db import utcnow
from app.deps import DB, CurrentProfile, CurrentUser
from app.models import Chunk, JournalEntry, Note
from app.serializers import iso, journal_out, note_out
from app.services import filesync, indexer
from app.services.filesync import SyncError
from app.services.journal_import import extract_tags, import_virtual_memory
from app.services.mdfile import clean_day_body, clean_note_body, sha
from app.services.timeutil import local_today, tz_of

router = APIRouter(prefix="/api", tags=["memory"])

MAX_IMPORT_BYTES = 20 * 1024 * 1024


class JournalIn(BaseModel):
    entry_date: date
    body: str = Field(default="", max_length=500_000)
    title: str | None = Field(None, max_length=200)
    day_number: int | None = None
    tags: list[str] | None = None
    mood: int | None = Field(None, ge=-2, le=2)
    is_private: bool = False


class JournalPatch(BaseModel):
    entry_date: date | None = None
    body: str | None = Field(None, max_length=500_000)
    title: str | None = Field(None, max_length=200)
    day_number: int | None = None
    tags: list[str] | None = None
    mood: int | None = Field(None, ge=-2, le=2)
    is_private: bool | None = None
    # body_hash of the version the editor opened: a save based on an older
    # version is refused instead of silently overwriting newer text.
    expected_hash: str | None = None


class NoteIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(default="", max_length=500_000)
    kind: str = Field(default="note", max_length=30)
    tags: list[str] | None = None
    is_private: bool = False


class NotePatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=200)
    body: str | None = Field(None, max_length=500_000)
    kind: str | None = Field(None, max_length=30)
    tags: list[str] | None = None
    is_private: bool | None = None
    expected_hash: str | None = None


class ResolveIn(BaseModel):
    keep: Literal["file", "app", "text"]
    text: str | None = Field(None, max_length=500_000)


class TextImportIn(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_IMPORT_BYTES)


def _own(db, model, obj_id: int, user_id: int):
    obj = db.get(model, obj_id)
    if obj is None or obj.user_id != user_id:
        raise HTTPException(404, "Not found")
    return obj


def _cfg():
    return filesync.config(get_settings())


def _stale(current_body: str, expected: str | None) -> None:
    if expected and expected != sha(current_body or ""):
        raise HTTPException(
            409,
            "This page changed since you opened it (probably in your editor). "
            "Your text is still in the editor: copy it, reopen the page, and merge.",
        )


def _after_write(db, user_id: int) -> None:
    db.commit()
    indexer.schedule(user_id, get_settings())
    w = filesync.watcher()
    if w is not None:
        w.poke()  # its next look at the files sees OwnLife's own write: a no-op sync


# ---------------------------------------------------------------- journal
@router.get("/journal")
def list_journal(
    user: CurrentUser,
    db: DB,
    limit: int = Query(60, le=1000),
    offset: int = 0,
    tag: str | None = None,
    start: date | None = None,
    end: date | None = None,
) -> dict:
    q = select(JournalEntry).where(JournalEntry.user_id == user.id, JournalEntry.visible())
    if start:
        q = q.where(JournalEntry.entry_date >= start)
    if end:
        q = q.where(JournalEntry.entry_date <= end)
    rows = db.scalars(q.order_by(JournalEntry.entry_date.desc(), JournalEntry.id.desc())).all()
    if tag:
        rows = [r for r in rows if any(t.casefold() == tag.casefold() for t in (r.tags or []))]
    return {"total": len(rows), "items": [journal_out(r, with_body=False) for r in rows[offset : offset + limit]]}


@router.get("/journal/tags")
def journal_tags(user: CurrentUser, db: DB) -> list[dict]:
    counts: dict[str, int] = {}
    for tags in db.scalars(select(JournalEntry.tags).where(JournalEntry.user_id == user.id, JournalEntry.visible())):
        for t in tags or []:
            counts[t] = counts.get(t, 0) + 1
    return [{"tag": t, "count": c} for t, c in sorted(counts.items(), key=lambda kv: -kv[1])]


@router.get("/journal/date/{day}")
def journal_by_date(day: date, user: CurrentUser, db: DB) -> list[dict]:
    rows = db.scalars(
        select(JournalEntry).where(
            JournalEntry.user_id == user.id, JournalEntry.entry_date == day, JournalEntry.visible()
        )
    ).all()
    return [journal_out(r) for r in rows]


@router.get("/journal/{entry_id}")
def get_journal(entry_id: int, user: CurrentUser, db: DB) -> dict:
    entry = _own(db, JournalEntry, entry_id, user.id)
    out = journal_out(entry)
    ids = list(db.scalars(
        select(JournalEntry.id).where(JournalEntry.user_id == user.id, JournalEntry.visible())
        .order_by(JournalEntry.entry_date, JournalEntry.id)
    ))
    i = ids.index(entry.id) if entry.id in ids else -1
    out["prev_id"] = ids[i - 1] if i > 0 else None
    out["next_id"] = ids[i + 1] if 0 <= i < len(ids) - 1 else None
    return out


@router.post("/journal")
def create_journal(body: JournalIn, user: CurrentUser, profile: CurrentProfile, db: DB) -> dict:
    data = body.model_dump()
    data["body"] = clean_day_body(body.body)
    if data["tags"] is None:
        data["tags"] = extract_tags(data["body"])
    with filesync.LOCK:
        if db.scalar(select(JournalEntry.id).where(
            JournalEntry.user_id == user.id, JournalEntry.entry_date == body.entry_date, JournalEntry.visible()
        )):
            raise HTTPException(409, f"{body.entry_date:%d/%m/%Y} already has a page: open it and add to it")
        if data["day_number"] is None:
            data["day_number"] = filesync.infer_day_number(db, user.id, profile, body.entry_date)
        entry = JournalEntry(user_id=user.id, source="manual", **data)
        db.add(entry)
        db.flush()
        try:
            sync = filesync.push_day(db, user.id, _cfg(), entry)
        except SyncError as e:
            db.rollback()
            raise HTTPException(422, str(e)) from e
        rag.index_journal_entry(db, entry)
        _after_write(db, user.id)
    return {**journal_out(entry), "sync": sync}


@router.patch("/journal/{entry_id}")
def update_journal(entry_id: int, body: JournalPatch, user: CurrentUser, db: DB) -> dict:
    data = body.model_dump(exclude_unset=True)
    expected = data.pop("expected_hash", None)
    with filesync.LOCK:
        entry = _own(db, JournalEntry, entry_id, user.id)
        db.refresh(entry)  # the watcher may have just updated it
        _stale(entry.body, expected)
        file_date = entry.entry_date
        text_changed = "body" in data and clean_day_body(data["body"]) != entry.body
        if "body" in data:
            data["body"] = clean_day_body(data["body"])
            if "tags" not in data:
                data["tags"] = sorted(set(entry.tags or []) | set(extract_tags(data["body"])))
        moved = "entry_date" in data and data["entry_date"] != entry.entry_date
        renumbered = "day_number" in data and data["day_number"] != entry.day_number
        for k, v in data.items():
            setattr(entry, k, v)
        sync = None
        if text_changed or moved or renumbered:
            try:
                sync = filesync.push_day(db, user.id, _cfg(), entry, file_date=file_date)
            except SyncError as e:
                db.rollback()
                raise HTTPException(422, str(e)) from e
        rag.index_journal_entry(db, entry)
        _after_write(db, user.id)
    return {**journal_out(entry), "sync": sync}


@router.delete("/journal/{entry_id}")
def delete_journal(entry_id: int, user: CurrentUser, db: DB) -> dict:
    with filesync.LOCK:
        entry = _own(db, JournalEntry, entry_id, user.id)
        try:
            filesync.remove_day(db, _cfg(), entry)
        except SyncError as e:
            raise HTTPException(409, str(e)) from e
        rag.delete_source_chunks(db, user.id, "journal", entry.id)
        db.delete(entry)
        _after_write(db, user.id)
    return {"ok": True}


@router.post("/journal/{entry_id}/resolve")
def resolve_journal(entry_id: int, body: ResolveIn, user: CurrentUser, profile: CurrentProfile, db: DB) -> dict:
    with filesync.LOCK:
        entry = _own(db, JournalEntry, entry_id, user.id)
        try:
            result = filesync.resolve(db, user.id, _cfg(), entry, body.keep, body.text,
                                      local_today(tz_of(profile.timezone)))
        except SyncError as e:
            raise HTTPException(422, str(e)) from e
        if result["state"] == "deleted":
            rag.delete_source_chunks(db, user.id, "journal", entry.id)
            db.delete(entry)
            _after_write(db, user.id)
            return {"deleted": True}
        rag.index_journal_entry(db, entry)
        _after_write(db, user.id)
    return {**journal_out(entry), "sync": result}


@router.post("/journal/{entry_id}/restore")
def restore_journal(entry_id: int, user: CurrentUser, db: DB) -> dict:
    """Puts a day that vanished from the file back into it."""
    with filesync.LOCK:
        entry = _own(db, JournalEntry, entry_id, user.id)
        if entry.sync_state != "missing":
            raise HTTPException(409, "This page is not missing from the file")
        entry.sync_state, entry.file_path, entry.source, entry.source_hash = "local", None, "manual", None
        try:
            sync = filesync.push_day(db, user.id, _cfg(), entry)
        except SyncError as e:
            db.rollback()
            raise HTTPException(422, str(e)) from e
        rag.index_journal_entry(db, entry)
        _after_write(db, user.id)
    return {**journal_out(entry), "sync": sync}


def _refuse_when_synced() -> None:
    if _cfg() is not None:
        raise HTTPException(
            400,
            "Your journal file is synced live: write in it directly (or here), there is nothing to import. "
            "A one-off import could overwrite newer text in the file.",
        )


def _after_import(db, user_id: int, entry_ids: list[int]) -> None:
    for eid in entry_ids:
        rag.index_journal_entry(db, db.get(JournalEntry, eid))
    db.commit()
    if entry_ids:
        indexer.schedule(user_id, get_settings())


@router.post("/journal/import")
async def import_journal_file(
    user: CurrentUser, profile: CurrentProfile, db: DB, file: UploadFile = File(...)
) -> dict:
    _refuse_when_synced()
    raw = await file.read(MAX_IMPORT_BYTES + 1)
    if len(raw) > MAX_IMPORT_BYTES:
        raise HTTPException(413, "File too large (20 MB max)")
    report = import_virtual_memory(db, user.id, profile, raw.decode("utf-8", errors="replace"))
    _after_import(db, user.id, report.changed_entry_ids)
    return report.as_dict()


@router.post("/journal/import-text")
def import_journal_text(body: TextImportIn, user: CurrentUser, profile: CurrentProfile, db: DB) -> dict:
    _refuse_when_synced()
    report = import_virtual_memory(db, user.id, profile, body.text)
    _after_import(db, user.id, report.changed_entry_ids)
    return report.as_dict()


# ---------------------------------------------------------------- the sync itself
def _sync_now(db, user_id: int) -> dict:
    w = filesync.watcher()
    if w is not None and w.cfg is not None and w.running:
        report = w.run_once("manual")
        if report is None:
            raise HTTPException(500, w.last_error or "The sync failed")
        return report.as_dict()
    cfg = _cfg()
    if cfg is None:
        raise HTTPException(400, "JOURNAL_SYNC_PATH and IMPORT_ROOT must be set in backend/.env")
    from app.models import User

    try:
        report = filesync.sync_all(db, db.get(User, user_id), cfg)
    except SyncError as e:
        raise HTTPException(403, str(e)) from e
    filesync.reindex(db, report)
    db.commit()
    indexer.schedule(user_id, get_settings())
    return report.as_dict()


@router.post("/sync/run")
def sync_run(user: CurrentUser, db: DB) -> dict:
    return _sync_now(db, user.id)


@router.get("/sync")
def sync_status(user: CurrentUser, profile: CurrentProfile, db: DB) -> dict:
    s = get_settings()
    cfg = _cfg()
    w = filesync.watcher()
    today = local_today(tz_of(profile.timezone))

    def rows(model, state):
        return db.scalars(select(model).where(model.user_id == user.id, model.sync_state == state)).all()

    def day_item(e: JournalEntry) -> dict:
        return {"type": "journal", "id": e.id, "label": f"Day {e.day_number}" if e.day_number else "Page",
                "date": iso(e.entry_date), "file_removed": e.conflict_body is None}

    def note_item(n: Note) -> dict:
        return {"type": "note", "id": n.id, "label": n.title, "date": None, "file_removed": n.conflict_body is None}

    files = []
    if cfg is not None:
        for p in cfg.journal_files():
            days = db.scalar(select(func.count()).select_from(JournalEntry).where(
                JournalEntry.user_id == user.id, JournalEntry.file_path == str(p), JournalEntry.visible()))
            files.append({"kind": "journal", "path": str(p), "name": p.name, "days": days or 0})
        notes = db.scalar(select(func.count()).select_from(Note).where(
            Note.user_id == user.id, Note.source_path.is_not(None), Note.visible()))
        for d in cfg.notes_dirs(today):
            files.append({"kind": "notes", "path": str(d), "name": d.name, "notes": notes or 0})
    return {
        "configured": cfg is not None,
        "pattern": str(s.journal_sync_path) if s.journal_sync_path else None,
        "yearly": bool(cfg and cfg.yearly),
        "root": str(s.import_root) if s.import_root else None,
        "writeback": bool(cfg and cfg.writeback),
        "watching": bool(w and w.running),
        "history_dir": str(cfg.history_dir) if cfg else None,
        "next_day_file": str(cfg.journal_file_for(today)) if cfg else None,
        "files": files,
        **(w.status() if w else {"revision": 0, "last_sync_at": None, "last_change_at": None,
                                 "last_report": None, "last_error": None, "running": False}),
        "conflicts": [day_item(e) for e in rows(JournalEntry, "conflict")] + [note_item(n) for n in rows(Note, "conflict")],
        "missing": [day_item(e) for e in rows(JournalEntry, "missing")] + [note_item(n) for n in rows(Note, "missing")],
        "pending": [day_item(e) for e in rows(JournalEntry, "pending")] + [note_item(n) for n in rows(Note, "pending")],
        "now": iso(utcnow()),
    }


# ---------------------------------------------------------------- notes
@router.get("/notes")
def list_notes(user: CurrentUser, db: DB) -> list[dict]:
    rows = db.scalars(
        select(Note).where(Note.user_id == user.id, Note.visible()).order_by(Note.updated_at.desc())
    ).all()
    return [note_out(n, with_body=False) for n in rows]


@router.get("/notes/{note_id}")
def get_note(note_id: int, user: CurrentUser, db: DB) -> dict:
    return note_out(_own(db, Note, note_id, user.id))


@router.post("/notes")
def create_note(body: NoteIn, user: CurrentUser, profile: CurrentProfile, db: DB) -> dict:
    data = body.model_dump()
    data["body"] = clean_note_body(body.body)
    if data["tags"] is None:
        data["tags"] = extract_tags(data["body"])
    with filesync.LOCK:
        note = Note(user_id=user.id, **data)
        db.add(note)
        db.flush()
        try:
            sync = filesync.push_note(db, _cfg(), note, local_today(tz_of(profile.timezone)))
        except SyncError as e:
            db.rollback()
            raise HTTPException(422, str(e)) from e
        rag.index_note(db, note)
        _after_write(db, user.id)
    return {**note_out(note), "sync": sync}


@router.patch("/notes/{note_id}")
def update_note(note_id: int, body: NotePatch, user: CurrentUser, profile: CurrentProfile, db: DB) -> dict:
    data = body.model_dump(exclude_unset=True)
    expected = data.pop("expected_hash", None)
    with filesync.LOCK:
        note = _own(db, Note, note_id, user.id)
        db.refresh(note)
        _stale(note.body, expected)
        text_changed = "body" in data and clean_note_body(data["body"]) != note.body
        if "body" in data:
            data["body"] = clean_note_body(data["body"])
            if "tags" not in data:
                data["tags"] = extract_tags(data["body"])
        for k, v in data.items():
            setattr(note, k, v)
        sync = None
        if text_changed:
            try:
                sync = filesync.push_note(db, _cfg(), note, local_today(tz_of(profile.timezone)))
            except SyncError as e:
                db.rollback()
                raise HTTPException(422, str(e)) from e
        rag.index_note(db, note)
        _after_write(db, user.id)
    return {**note_out(note), "sync": sync}


@router.delete("/notes/{note_id}")
def delete_note(note_id: int, user: CurrentUser, db: DB) -> dict:
    with filesync.LOCK:
        note = _own(db, Note, note_id, user.id)
        try:
            copy = filesync.remove_note_file(_cfg(), note) if note.sync_state != "missing" else None
        except SyncError as e:
            raise HTTPException(409, str(e)) from e
        rag.delete_source_chunks(db, user.id, "note", note.id)
        db.delete(note)
        _after_write(db, user.id)
    return {"ok": True, "file_copy": copy}


@router.post("/notes/{note_id}/resolve")
def resolve_note(note_id: int, body: ResolveIn, user: CurrentUser, profile: CurrentProfile, db: DB) -> dict:
    with filesync.LOCK:
        note = _own(db, Note, note_id, user.id)
        try:
            result = filesync.resolve(db, user.id, _cfg(), note, body.keep, body.text,
                                      local_today(tz_of(profile.timezone)))
        except SyncError as e:
            raise HTTPException(422, str(e)) from e
        if result["state"] == "deleted":
            rag.delete_source_chunks(db, user.id, "note", note.id)
            db.delete(note)
            _after_write(db, user.id)
            return {"deleted": True}
        rag.index_note(db, note)
        _after_write(db, user.id)
    return {**note_out(note), "sync": result}


# ---------------------------------------------------------------- search
@router.get("/search")
def search(
    user: CurrentUser,
    db: DB,
    q: str = Query(..., min_length=1, max_length=500),
    k: int = Query(12, ge=1, le=50),
) -> dict:
    hits, mode = rag.search(db, user.id, q, get_embedder(get_settings()), k=k, include_private=True)
    return {"query": q, "mode": mode, "hits": [h.as_dict() for h in hits]}


@router.get("/search/status")
def search_status(user: CurrentUser, db: DB) -> dict:
    s = get_settings()
    total = db.scalar(select(func.count()).select_from(Chunk).where(Chunk.user_id == user.id)) or 0
    embedder = get_embedder(s)
    embedded = 0
    available, message = False, "disabled"
    if embedder is not None:
        embedded = db.scalar(
            select(func.count()).select_from(Chunk).where(
                Chunk.user_id == user.id, Chunk.embedding_model == embedder.name
            )
        ) or 0
        available, message = embedder.available()
    return {
        "chunks": total,
        "embedded": embedded,
        "embedder": {"model": embedder.name if embedder else None, "available": available, "message": message},
        "indexing": indexer.status(user.id),
    }


@router.post("/search/reindex")
def reindex(user: CurrentUser, db: DB) -> dict:
    n = rag.reindex_all(db, user.id)
    db.commit()
    indexer.schedule(user.id, get_settings())
    return {"chunks": n}
