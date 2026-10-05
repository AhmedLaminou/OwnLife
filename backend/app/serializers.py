"""ORM rows → JSON-ready dicts. One function per model keeps every endpoint
returning the same shape for the same thing."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from app.models import (
    CaptureDraft,
    Category,
    ChatMessage,
    ChatThread,
    ClassificationRule,
    Goal,
    Habit,
    Idea,
    JournalEntry,
    LifeChapter,
    LifeEvent,
    MediaItem,
    MoneySuggestion,
    Note,
    Person,
    PersonMoment,
    PlanBlock,
    PlanTemplate,
    Profile,
    TimeEntry,
    Transaction,
    User,
)
from app.services.mdfile import sha
from app.services.records import from_minor
from app.services.timeutil import minutes_to_hhmm


def iso(v: datetime | date | None) -> str | None:
    return v.isoformat() if v is not None else None


def user_out(u: User) -> dict:
    return {"id": u.id, "email": u.email, "display_name": u.display_name, "created_at": iso(u.created_at)}


def profile_out(p: Profile, u: User) -> dict:
    return {
        "display_name": u.display_name,
        "email": u.email,
        "birth_date": iso(p.birth_date),
        "life_expectancy_years": p.life_expectancy_years,
        "timezone": p.timezone,
        "currency": p.currency,
        "awakening_date": iso(p.awakening_date),
        "mission_title": p.mission_title,
        "mission_text": p.mission_text,
        "focus_target_hours": p.focus_target_hours,
        "stretch_focus_hours": p.stretch_focus_hours,
        "sleep_target_hours": p.sleep_target_hours,
        "noise_budget_hours": p.noise_budget_hours,
        "wake_target": p.wake_target,
        "bed_target": p.bed_target,
        "glossary": p.glossary or [],
        "redactions": p.redactions or [],
        "ai_settings": p.ai_settings or {},
    }


def category_out(c: Category) -> dict:
    return {
        "id": c.id,
        "name": c.name,
        "kind": c.kind,
        "color": c.color,
        "icon": c.icon,
        "parent_id": c.parent_id,
        "is_private": c.is_private,
        "archived": c.archived,
        "sort": c.sort,
    }


def entry_out(e: TimeEntry, now: datetime) -> dict:
    end = e.ended_at or now
    return {
        "id": e.id,
        "title": e.title,
        "category_id": e.category_id,
        "category": (
            {"id": e.category.id, "name": e.category.name, "kind": e.category.kind, "icon": e.category.icon}
            if e.category
            else None
        ),
        "kind": e.category.kind if e.category else "uncategorized",
        "started_at": iso(e.started_at),
        "ended_at": iso(e.ended_at),
        "running": e.ended_at is None,
        "duration_seconds": max(0.0, (end - e.started_at).total_seconds()),
        "source": e.source,
        "is_estimate": e.is_estimate,
        "category_locked": e.category_locked,
        "location": e.location,
        "notes": e.notes,
        "goal_id": e.goal_id,
        "media_item_id": e.media_item_id,
        "focus_rating": e.focus_rating,
        "is_private": e.is_private,
        "people": [{"id": p.id, "name": p.name} for p in e.people],
        "meta": e.meta,
    }


def _excerpt(text: str, n: int = 220) -> str:
    flat = " ".join((text or "").split())
    return flat if len(flat) <= n else flat[: n - 1].rstrip() + "…"


def journal_out(j: JournalEntry, with_body: bool = True) -> dict:
    out = {
        "id": j.id,
        "entry_date": iso(j.entry_date),
        "day_number": j.day_number,
        "title": j.title,
        "excerpt": _excerpt(j.body),
        "tags": j.tags or [],
        "mood": j.mood,
        "is_private": j.is_private,
        "source": j.source,
        "sync_state": j.sync_state or ("synced" if j.file_path else "local"),
        "file_name": Path(j.file_path).name if j.file_path else None,
        "word_count": len((j.body or "").split()),
        "created_at": iso(j.created_at),
        "updated_at": iso(j.updated_at),
    }
    if with_body:
        out["body"] = j.body
        # The editor sends this back: a save based on an older version is refused.
        out["body_hash"] = sha(j.body or "")
        out["conflict_body"] = j.conflict_body if j.sync_state == "conflict" else None
    return out


def note_out(n: Note, with_body: bool = True) -> dict:
    out = {
        "id": n.id,
        "title": n.title,
        "kind": n.kind,
        "excerpt": _excerpt(n.body),
        "tags": n.tags or [],
        "is_private": n.is_private,
        "source_path": n.source_path,
        "file_name": Path(n.source_path).name if n.source_path else None,
        "sync_state": n.sync_state or ("synced" if n.source_path else "local"),
        "word_count": len((n.body or "").split()),
        "created_at": iso(n.created_at),
        "updated_at": iso(n.updated_at),
    }
    if with_body:
        out["body"] = n.body
        out["body_hash"] = sha(n.body or "")
        out["conflict_body"] = n.conflict_body if n.sync_state == "conflict" else None
    return out


def person_out(p: Person) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "relation": p.relation,
        "notes": p.notes,
        "tags": p.tags or [],
        "is_private": p.is_private,
        "created_at": iso(p.created_at),
    }


def tx_out(t: Transaction) -> dict:
    return {
        "id": t.id,
        "date": iso(t.occurred_on),
        "direction": t.direction,
        "amount": from_minor(t.amount_minor, t.currency),
        "currency": t.currency,
        "item": t.item,
        "category": t.category,
        "counterparty": t.counterparty,
        "person_id": t.person_id,
        "person": t.person.name if t.person is not None else None,
        "note": t.note,
        "source": t.source,
    }


def moment_out(m: PersonMoment) -> dict:
    return {
        "id": m.id,
        "person_id": m.person_id,
        "date": iso(m.occurred_on),
        "kind": m.kind,
        "text": m.text,
        "source": m.source,
        "journal_entry_id": m.journal_entry_id,
        "is_private": m.is_private,
    }


def idea_out(i: Idea, notes: dict[int, Note] | None = None) -> dict:
    notes = notes or {}
    essay = notes.get(i.note_id) if i.note_id else None
    placed = notes.get(i.placed_note_id) if i.placed_note_id else None
    return {
        "id": i.id,
        "journal_entry_id": i.journal_entry_id,
        "date": iso(i.entry_date),
        "day_number": i.day_number,
        "title": i.title,
        "statement": i.statement,
        "quote": i.quote,
        "domain": i.domain,
        "essay": {"id": essay.id, "title": essay.title} if essay else None,
        "new_essay": i.new_essay,
        "references": i.refs or [],
        "status": i.status,
        "placed_in": {"id": placed.id, "title": placed.title} if placed else None,
        "placed_at": iso(i.placed_at),
        "model": i.model,
    }


def money_suggestion_out(s: MoneySuggestion) -> dict:
    return {
        "id": s.id,
        "date": iso(s.entry_date),
        "journal_entry_id": s.journal_entry_id,
        "direction": s.direction,
        "amount": from_minor(s.amount_minor, s.currency),
        "currency": s.currency,
        "item": s.item,
        "category": s.category,
        "counterparty": s.counterparty,
        "quote": s.quote,
        "status": s.status,
    }


def media_out(m: MediaItem) -> dict:
    return {
        "id": m.id,
        "kind": m.kind,
        "title": m.title,
        "creator": m.creator,
        "url": m.url,
        "external_id": m.external_id,
        "category_id": m.category_id,
        "status": m.status,
        "progress_current": m.progress_current,
        "progress_total": m.progress_total,
        "progress_unit": m.progress_unit,
        "rating": m.rating,
        "notes": m.notes,
        "updated_at": iso(m.updated_at),
    }


def chapter_out(c: LifeChapter) -> dict:
    return {
        "id": c.id,
        "title": c.title,
        "start_date": iso(c.start_date),
        "end_date": iso(c.end_date),
        "kind": c.kind,
        "color": c.color,
        "description": c.description,
        "approximate": c.approximate,
        "sort": c.sort,
    }


def event_out(e: LifeEvent) -> dict:
    return {
        "id": e.id,
        "date": iso(e.event_date),
        "precision": e.precision,
        "title": e.title,
        "description": e.description,
        "area": e.area,
        "importance": e.importance,
        "is_private": e.is_private,
    }


def goal_row_out(g: Goal) -> dict:
    return {
        "id": g.id,
        "parent_id": g.parent_id,
        "title": g.title,
        "level": g.level,
        "status": g.status,
        "target_date": iso(g.target_date),
    }


def habit_out(h: Habit) -> dict:
    return {
        "id": h.id,
        "name": h.name,
        "description": h.description,
        "kind": h.kind,
        "target_per_week": h.target_per_week,
        "rule": h.rule,
        "start_date": iso(h.start_date),
        "is_private": h.is_private,
        "color": h.color,
        "icon": h.icon,
        "archived": h.archived,
        "sort": h.sort,
    }


def plan_block_out(b: PlanBlock) -> dict:
    return {
        "id": b.id,
        "date": iso(b.plan_date),
        "start": minutes_to_hhmm(b.start_minute),
        "end": minutes_to_hhmm(b.end_minute),
        "start_minute": b.start_minute,
        "end_minute": b.end_minute,
        "title": b.title,
        "category_id": b.category_id,
        "kind": b.category.kind if b.category else "uncategorized",
        "category": b.category.name if b.category else None,
        "is_fixed": b.is_fixed,
        "notes": b.notes,
    }


def template_out(t: PlanTemplate) -> dict:
    return {"id": t.id, "name": t.name, "weekdays": t.weekdays or [], "blocks": t.blocks or []}


def rule_out(r: ClassificationRule) -> dict:
    return {
        "id": r.id,
        "field": r.field,
        "pattern": r.pattern,
        "is_regex": r.is_regex,
        "category_id": r.category_id,
        "priority": r.priority,
        "note": r.note,
    }


def thread_out(t: ChatThread) -> dict:
    return {"id": t.id, "title": t.title, "created_at": iso(t.created_at), "updated_at": iso(t.updated_at)}


def message_out(m: ChatMessage) -> dict:
    return {
        "id": m.id,
        "role": m.role,
        "content": m.content,
        "tool_calls": m.tool_calls,
        "name": m.name,
        "meta": m.meta,
        "created_at": iso(m.created_at),
    }


def draft_out(d: CaptureDraft) -> dict:
    return {
        "id": d.id,
        "date": iso(d.capture_date),
        "input_text": d.input_text,
        "draft": d.draft,
        "status": d.status,
        "model": d.model,
        "created_at": iso(d.created_at),
    }
