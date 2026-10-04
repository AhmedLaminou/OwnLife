"""Embedding in the background. Saving a journal entry re-chunks it immediately
(so keyword search is current at once); the vectors follow a few seconds later
from this worker thread, without making the request wait for Ollama."""

from __future__ import annotations

import threading

from app.ai.embeddings import get_embedder
from app.ai.rag import embed_pending
from app.config import Settings
from app.db import SessionLocal

_lock = threading.Lock()
_status: dict[int, dict] = {}


def status(user_id: int) -> dict:
    with _lock:
        return dict(_status.get(user_id, {"state": "idle", "done": 0, "total": 0, "error": None}))


def schedule(user_id: int, settings: Settings) -> None:
    with _lock:
        st = _status.get(user_id)
        if st and st["state"] == "running":
            st["again"] = True  # something changed while running: do another pass
            return
        _status[user_id] = {"state": "running", "done": 0, "total": 0, "error": None, "again": False}
    threading.Thread(target=_run, args=(user_id, settings), daemon=True, name=f"embed-{user_id}").start()


def _set(user_id: int, **kw) -> None:
    with _lock:
        _status.setdefault(user_id, {}).update(kw)


def _run(user_id: int, settings: Settings) -> None:
    embedder = get_embedder(settings)
    if embedder is None:
        _set(user_id, state="disabled", error="Embeddings are disabled (EMBEDDINGS_PROVIDER=none)")
        return
    ok, message = embedder.available()
    if not ok:
        _set(user_id, state="unavailable", error=message)
        return
    try:
        while True:
            with SessionLocal() as db:
                embed_pending(db, user_id, embedder, on_progress=lambda d, t: _set(user_id, done=d, total=t))
            with _lock:
                again = _status[user_id].pop("again", False)
                _status[user_id]["again"] = False
            if not again:
                break
        _set(user_id, state="idle", error=None)
    except Exception as e:  # Ollama stopped mid-way: keyword search still works
        _set(user_id, state="error", error=f"{e.__class__.__name__}: {str(e)[:200]}")
