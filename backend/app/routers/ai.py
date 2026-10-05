"""The AI assistant: status, capture drafts, streaming chat, daily review."""

from __future__ import annotations

import datetime as dt

from datetime import date, datetime

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.ai import rag
from app.ai.agent import build_graph, message_to_row, rows_to_messages, sse, stream_turn, text_of
from app.ai.capture import CommitDraft, commit, extract, normalize
from app.ai.embeddings import get_embedder
from app.ai.llm import AIUnavailableError, chat_models, compose_each, describe_error, effective_mode
from app.ai.prompts import chat_system_prompt
from app.ai.review import day_facts, facts_text, write_review
from app.ai.tools import LOCAL_TOOLS, ToolContext, build_tools, known_people, today_summary
from app.config import get_settings
from app.db import SessionLocal, utcnow
from app.deps import DB, CurrentProfile, CurrentUser
from app.models import CaptureDraft, Category, ChatMessage, ChatThread, DayReview, Habit, Profile
from app.serializers import draft_out, iso, message_out, thread_out
from app.services import filesync, journal_time
from app.services.activitywatch import get_state
from app.services.privacy import redact
from app.services.undo import UndoError, undo_action, undo_capture
from app.services.timeutil import day_start_utc, local_today, tz_of

router = APIRouter(prefix="/api/ai", tags=["ai"])

HISTORY_MESSAGES = 30


class CaptureIn(BaseModel):
    text: str = Field(min_length=3, max_length=30000)
    date: dt.date | None = None


class CommitIn(BaseModel):
    draft: CommitDraft


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    thread_id: int | None = None


class ThreadPatch(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class JournalTimePrefs(BaseModel):
    scan: bool = True


class ReadIn(BaseModel):
    all: bool = False  # read every page again, not only the changed ones


# ---------------------------------------------------------------- status
@router.get("/status")
def ai_status(user: CurrentUser, profile: CurrentProfile) -> dict:  # noqa: ARG001
    s = get_settings()
    ollama = {"reachable": False, "chat_model": False, "embed_model": False}
    try:
        r = httpx.get(f"{s.ollama_base_url.rstrip('/')}/api/tags", timeout=2)
        names = {m.get("name", "") for m in r.json().get("models", [])}
        ollama = {
            "reachable": True,
            "chat_model": s.ollama_chat_model in names or f"{s.ollama_chat_model}:latest" in names,
            "embed_model": s.ollama_embed_model in names or f"{s.ollama_embed_model}:latest" in names,
        }
    except (httpx.HTTPError, ValueError):
        pass
    choices = chat_models(s, profile)
    return {
        "mode": effective_mode(s, profile),
        "chain": [c.label for c in choices],
        "cloud_first": bool(choices and choices[0].is_cloud),
        "has_openrouter_key": bool(s.openrouter_api_key and s.openrouter_api_key.get_secret_value()),
        "openrouter_models": (profile.ai_settings or {}).get("models") or s.openrouter_model_list,
        "ollama": {**ollama, "chat_model_name": s.ollama_chat_model, "embed_model_name": s.ollama_embed_model},
        "free_tier": "Free OpenRouter models: 20 requests/minute, 50/day (1000/day after $10 of credits).",
    }


# ---------------------------------------------------------------- capture
@router.post("/capture")
async def capture(body: CaptureIn, user: CurrentUser, profile: CurrentProfile, db: DB) -> dict:
    s = get_settings()
    tz = tz_of(profile.timezone)
    d = body.date or local_today(tz)
    categories = list(db.scalars(select(Category).where(Category.user_id == user.id, Category.archived.is_(False))))
    habits = list(db.scalars(select(Habit).where(Habit.user_id == user.id, Habit.archived.is_(False))))
    people = known_people(db, user.id)
    try:
        extraction, model = await run_in_threadpool(extract, s, profile, categories, habits, people, body.text, d)
    except AIUnavailableError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # provider errors: give the person something actionable
        raise HTTPException(502, describe_error(e)) from e
    draft = normalize(extraction, d, categories, habits, people)
    row = CaptureDraft(user_id=user.id, capture_date=d, input_text=body.text, draft=draft, model=model)
    db.add(row)
    db.commit()
    return draft_out(row)


@router.get("/journal-time")
def journal_time_state(user: CurrentUser, profile: CurrentProfile, db: DB) -> dict:
    """Reading time blocks from the journal: preferences and the reader's state."""
    state = get_state(db, user.id, journal_time.PROVIDER)
    waiting = journal_time.changed_entries(db, user.id, dict((state.cursor or {}).get("days") or {}))
    db.commit()
    return {
        "prefs": journal_time.settings_with_defaults((profile.prefs or {}).get("journal_time")),
        "scan": journal_time.status(user.id),
        "last_scan": iso(state.last_synced_at),
        "last_error": state.last_error,
        "days_not_read": len(waiting),
        "ai_off": effective_mode(get_settings(), profile) == "off",
    }


@router.post("/journal-time/scan")
def journal_time_scan(body: ReadIn, user: CurrentUser, profile: CurrentProfile) -> dict:
    if effective_mode(get_settings(), profile) == "off":
        raise HTTPException(400, "AI is off (Settings → AI): reading time from the journal needs a model.")
    journal_time.schedule(get_settings(), user.id, rescan_all=body.all)
    return journal_time.status(user.id)


@router.put("/journal-time/prefs")
def journal_time_prefs(body: JournalTimePrefs, profile: CurrentProfile, db: DB) -> dict:
    profile.prefs = {**(profile.prefs or {}), "journal_time": body.model_dump()}
    db.commit()
    return journal_time.settings_with_defaults(profile.prefs["journal_time"])


@router.get("/drafts")
def list_drafts(user: CurrentUser, db: DB, status: str = "pending", limit: int = Query(50, le=500)) -> list[dict]:
    rows = db.scalars(
        select(CaptureDraft)
        .where(CaptureDraft.user_id == user.id, CaptureDraft.status == status)
        .order_by(CaptureDraft.created_at.desc())
        .limit(limit)
    ).all()
    return [draft_out(r) for r in rows]


def _own_draft(db, user_id: int, draft_id: int) -> CaptureDraft:
    d = db.get(CaptureDraft, draft_id)
    if d is None or d.user_id != user_id:
        raise HTTPException(404, "Draft not found")
    return d


@router.get("/drafts/{draft_id}")
def get_draft(draft_id: int, user: CurrentUser, db: DB) -> dict:
    return draft_out(_own_draft(db, user.id, draft_id))


@router.post("/drafts/{draft_id}/commit")
def commit_draft(draft_id: int, body: CommitIn, user: CurrentUser, profile: CurrentProfile, db: DB) -> dict:
    row = _own_draft(db, user.id, draft_id)
    if row.status != "pending":
        raise HTTPException(409, f"This draft is already {row.status}")
    # Blocks read from the journal are your own account of the day: they rank as entries you typed.
    source = "journal" if (row.draft or {}).get("origin") == "journal" else "ai"
    result = commit(db, user.id, profile, body.draft, source=source)
    row.status = "committed"
    row.draft = {**(row.draft or {}), "committed": body.draft.model_dump(mode="json"), "result": result}
    db.commit()
    return result


@router.post("/drafts/{draft_id}/undo")
def undo_draft(draft_id: int, user: CurrentUser, db: DB) -> dict:
    """Removes what a committed capture created; the draft goes back to the
    inbox, so it can be corrected and saved again."""
    row = _own_draft(db, user.id, draft_id)
    if row.status != "committed":
        raise HTTPException(409, "Only a saved capture can be undone")
    records = ((row.draft or {}).get("result") or {}).get("records")
    if records is None:
        raise HTTPException(409, "This capture was saved before undo existed: delete its entries by hand")
    with filesync.LOCK:
        result = undo_capture(db, user.id, records, filesync.config(get_settings()))
        row.status = "pending"
        row.draft = {k: v for k, v in (row.draft or {}).items() if k not in ("committed", "result")}
        db.commit()
    return result


@router.post("/messages/{message_id}/actions/{index}/undo")
def undo_message_action(message_id: int, index: int, user: CurrentUser, db: DB) -> dict:
    msg = db.get(ChatMessage, message_id)
    if msg is None or msg.user_id != user.id:
        raise HTTPException(404, "Message not found")
    actions = list((msg.meta or {}).get("actions") or [])
    if not 0 <= index < len(actions):
        raise HTTPException(404, "No such action")
    if actions[index].get("undone"):
        raise HTTPException(409, "Already undone")
    with filesync.LOCK:
        try:
            message = undo_action(db, user.id, actions[index], filesync.config(get_settings()))
        except UndoError as e:
            raise HTTPException(409, str(e)) from e
        actions[index] = {**actions[index], "undone": True}
        msg.meta = {**(msg.meta or {}), "actions": actions}  # a new dict: the JSON column sees the change
        db.commit()
    return {"ok": True, "message": message, "actions": actions}


@router.post("/drafts/{draft_id}/discard")
def discard_draft(draft_id: int, user: CurrentUser, db: DB) -> dict:
    row = _own_draft(db, user.id, draft_id)
    row.status = "discarded"
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- threads
@router.get("/threads")
def list_threads(user: CurrentUser, db: DB) -> list[dict]:
    rows = db.scalars(select(ChatThread).where(ChatThread.user_id == user.id).order_by(ChatThread.updated_at.desc())).all()
    return [thread_out(t) for t in rows]


def _own_thread(db, user_id: int, thread_id: int) -> ChatThread:
    t = db.get(ChatThread, thread_id)
    if t is None or t.user_id != user_id:
        raise HTTPException(404, "Conversation not found")
    return t


@router.get("/threads/{thread_id}/messages")
def thread_messages(thread_id: int, user: CurrentUser, db: DB) -> list[dict]:
    _own_thread(db, user.id, thread_id)
    rows = db.scalars(select(ChatMessage).where(ChatMessage.thread_id == thread_id).order_by(ChatMessage.id)).all()
    return [message_out(m) for m in rows]


@router.patch("/threads/{thread_id}")
def rename_thread(thread_id: int, body: ThreadPatch, user: CurrentUser, db: DB) -> dict:
    t = _own_thread(db, user.id, thread_id)
    t.title = body.title
    db.commit()
    return thread_out(t)


@router.delete("/threads/{thread_id}")
def delete_thread(thread_id: int, user: CurrentUser, db: DB) -> dict:
    db.delete(_own_thread(db, user.id, thread_id))
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- chat
def _error_stream(message: str, thread_id: int | None):
    async def gen():
        yield sse("start", {"thread_id": thread_id, "models": []})
        yield sse("error", {"message": message})
        yield sse("done", {"thread_id": thread_id, "actions": [], "citations": []})
    return gen()


@router.post("/chat")
async def chat(body: ChatIn, user: CurrentUser) -> StreamingResponse:
    s = get_settings()
    uid = user.id
    with SessionLocal() as db:
        profile = db.get(Profile, uid)
        if body.thread_id:
            thread = _own_thread(db, uid, body.thread_id)
        else:
            thread = ChatThread(user_id=uid, title=body.message.strip().split("\n")[0][:60] or "Conversation")
            db.add(thread)
            db.flush()
        rows = db.scalars(
            select(ChatMessage).where(ChatMessage.thread_id == thread.id).order_by(ChatMessage.id.desc()).limit(HISTORY_MESSAGES)
        ).all()
        history = rows_to_messages(
            [{"role": r.role, "content": r.content, "tool_calls": r.tool_calls, "tool_call_id": r.tool_call_id,
              "name": r.name} for r in reversed(rows)]
        )
        db.add(ChatMessage(thread_id=thread.id, user_id=uid, role="user", content=body.message))
        thread.updated_at = utcnow()
        db.commit()
        thread_id = thread.id
        choices = chat_models(s, profile)
        for_cloud = bool(choices and choices[0].is_cloud)
        tz = tz_of(profile.timezone)
        categories = list(db.scalars(select(Category).where(Category.user_id == uid, Category.archived.is_(False))))
        habits = list(db.scalars(select(Habit).where(Habit.user_id == uid, Habit.archived.is_(False))))
        base_summary = today_summary(db, uid, for_cloud)
        db.expunge_all()

    embedder = get_embedder(s)
    ctx = ToolContext(uid, embedder, for_cloud)
    tools = build_tools(ctx)
    try:
        local_tools = [t for t in tools if t.name in LOCAL_TOOLS]
        llm = compose_each(choices, lambda c: c.llm.bind_tools(tools if c.is_cloud else local_tools))
    except AIUnavailableError as e:
        return StreamingResponse(_error_stream(str(e), thread_id), media_type="text/event-stream")

    message_text = redact(body.message, profile) if for_cloud else body.message

    def system_prompt(memory: str) -> str:
        now_local = datetime.now(tz)
        return chat_system_prompt(user, profile, categories, habits, now_local.date(), now_local,
                                  base_summary, memory, for_cloud)

    def retrieve_memory(query: str) -> str:
        with SessionLocal() as db:
            hits, _ = rag.search(db, uid, query, embedder, k=5, include_private=not for_cloud)
        parts = []
        for h in hits:
            ctx.retrieved.append(h.as_dict())
            parts.append(f"{h.citation()} {h.date or ''}\n{redact(h.text, profile) if for_cloud else h.text}")
        return "\n---\n".join(parts)

    graph = build_graph(llm, tools, system_prompt, retrieve_memory)

    async def events():
        yield sse("start", {"thread_id": thread_id, "models": [c.label for c in choices]})
        new_messages: list = []
        async for event in stream_turn(graph, history, message_text, s.agent_max_tool_rounds, new_messages):
            yield event
        final = next((text_of(m.content) for m in reversed(new_messages) if isinstance(m, AIMessage) and not m.tool_calls), "")
        sources = ctx.sources(final)
        with SessionLocal() as db:
            last_answer_row = None
            for m in new_messages:
                row = message_to_row(m)
                if row is None:
                    continue
                msg = ChatMessage(thread_id=thread_id, user_id=uid, **row)
                db.add(msg)
                if isinstance(m, AIMessage) and not m.tool_calls:
                    last_answer_row = msg
            if last_answer_row is None and ctx.actions:
                # The model stopped without a final answer after acting: keep the
                # actions somewhere visible, so that they can still be undone.
                last_answer_row = ChatMessage(thread_id=thread_id, user_id=uid, role="assistant", content="")
                db.add(last_answer_row)
            if last_answer_row is not None:
                last_answer_row.meta = {
                    "actions": ctx.actions,
                    "citations": sources,
                    "models": [c.label for c in choices],
                }
            t = db.get(ChatThread, thread_id)
            if t is not None:
                t.updated_at = utcnow()
            db.commit()
            message_id = last_answer_row.id if last_answer_row is not None else None
        yield sse("done", {"thread_id": thread_id, "actions": ctx.actions, "citations": sources, "answer": final,
                           "message_id": message_id})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------- review
def _review_out(r: DayReview | None) -> dict | None:
    if r is None:
        return None
    return {"date": r.review_date.isoformat(), "facts": r.facts, "text": r.text, "model": r.model,
            "error": r.error, "updated_at": r.updated_at.isoformat() if r.updated_at else None}


@router.get("/reviews")
def list_reviews(user: CurrentUser, db: DB, limit: int = Query(30, le=366)) -> list[dict]:
    rows = db.scalars(
        select(DayReview).where(DayReview.user_id == user.id).order_by(DayReview.review_date.desc()).limit(limit)
    ).all()
    return [_review_out(r) for r in rows]


@router.get("/review/{day}")
def review_facts(day: date, user: CurrentUser, profile: CurrentProfile, db: DB) -> dict:
    facts = day_facts(db, user.id, profile, day, utcnow())
    stored = db.scalar(select(DayReview).where(DayReview.user_id == user.id, DayReview.review_date == day))
    return {"facts": facts, "facts_text": facts_text(facts), "stored": _review_out(stored)}


@router.post("/review/{day}")
async def review(day: date, user: CurrentUser, profile: CurrentProfile, db: DB) -> dict:
    facts = day_facts(db, user.id, profile, day, utcnow())
    try:
        text, model = await run_in_threadpool(write_review, get_settings(), user, profile, facts)
    except AIUnavailableError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        raise HTTPException(502, describe_error(e)) from e
    row = db.scalar(select(DayReview).where(DayReview.user_id == user.id, DayReview.review_date == day))
    if row is None:
        row = DayReview(user_id=user.id, review_date=day)
        db.add(row)
    row.facts, row.text, row.model, row.error = facts, text, model, None
    db.commit()
    return {"facts": facts, "facts_text": facts_text(facts), "review": text, "model": model}


@router.get("/usage")
def usage(user: CurrentUser, profile: CurrentProfile, db: DB) -> dict:
    """Rough count of today's model calls, to keep an eye on the free tier's 50/day."""
    tz = tz_of(profile.timezone)
    since = day_start_utc(local_today(tz), tz)
    assistant_msgs = db.scalar(
        select(func.count()).select_from(ChatMessage).where(
            ChatMessage.user_id == user.id, ChatMessage.role == "assistant", ChatMessage.created_at >= since
        )
    ) or 0
    captures = db.scalar(
        select(func.count()).select_from(CaptureDraft).where(
            CaptureDraft.user_id == user.id, CaptureDraft.created_at >= since
        )
    ) or 0
    return {"date": local_today(tz).isoformat(), "model_calls_estimate": assistant_msgs + captures}
