"""The daily review: facts computed locally, prose written by the model.

The facts section never needs AI and is always shown. The model only turns
the facts into three short paragraphs.
"""

from __future__ import annotations

from datetime import date, datetime

from langchain_core.messages import HumanMessage
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.llm import chat_models, compose
from app.ai.prompts import REVIEW_PROMPT
from app.config import Settings
from app.models import Habit, JournalEntry, PlanBlock, Profile, Transaction, User
from app.services.habits import habit_stats
from app.services.ledger import KIND_ORDER, daily_breakdown, entries_overlapping
from app.services.records import from_minor
from app.services.timeutil import local_today, range_utc, tz_of


def _hours(seconds: float) -> float:
    return round(seconds / 3600, 2)


def day_facts(db: Session, user_id: int, profile: Profile, d: date, now: datetime) -> dict:
    tz = tz_of(profile.timezone)
    kinds = daily_breakdown(db, user_id, d, d, tz, now).get(d, {})
    lo, hi = range_utc(d, d, tz)
    entries = entries_overlapping(db, user_id, lo, hi)

    blocks = db.scalars(select(PlanBlock).where(PlanBlock.user_id == user_id, PlanBlock.plan_date == d)).all()
    planned: dict[str, float] = {}
    for b in blocks:
        kind = b.category.kind if b.category else "uncategorized"
        planned[kind] = planned.get(kind, 0.0) + (b.end_minute - b.start_minute) * 60

    habits = []
    for h in db.scalars(select(Habit).where(Habit.user_id == user_id, Habit.archived.is_(False))):
        st = habit_stats(db, h, profile.timezone, now, days=max(1, (local_today(tz) - d).days + 1))
        status = next((c["status"] for c in st["calendar"] if c["date"] == d.isoformat()), None)
        habits.append({"name": h.name, "kind": h.kind, "status": status, "streak": st["current_streak"]})

    spent = received = 0.0
    for tx in db.scalars(select(Transaction).where(Transaction.user_id == user_id, Transaction.occurred_on == d)):
        amount = from_minor(tx.amount_minor, tx.currency)
        if tx.direction == "out":
            spent += amount
        else:
            received += amount

    journal = db.scalar(select(JournalEntry).where(
        JournalEntry.user_id == user_id, JournalEntry.entry_date == d, JournalEntry.visible()))
    longest = sorted(
        entries,
        key=lambda e: -((min(e.ended_at or now, hi) - max(e.started_at, lo)).total_seconds()),
    )[:6]
    return {
        "date": d.isoformat(),
        "hours_by_kind": {k: _hours(v) for k, v in kinds.items()},
        "planned_hours_by_kind": {k: _hours(v) for k, v in planned.items()},
        "logged_hours": _hours(sum(kinds.values())),
        "core_hours": _hours(kinds.get("core", 0.0)),
        "core_target": profile.focus_target_hours,
        "noise_hours": _hours(kinds.get("noise", 0.0) + kinds.get("destructive", 0.0)),
        "noise_budget": profile.noise_budget_hours,
        "sleep_hours": _hours(sum(
            (min(e.ended_at or now, hi) - max(e.started_at, lo)).total_seconds()
            for e in entries
            if e.category is not None and "sleep" in e.category.name.casefold()
        )),
        "habits": habits,
        "money": {"spent": spent, "received": received, "currency": profile.currency},
        "journal_written": journal is not None and bool((journal.body or "").strip()),
        "longest_entries": [
            {
                "title": e.title,
                "category": e.category.name if e.category else None,
                "kind": e.category.kind if e.category else "uncategorized",
                "start": e.started_at.astimezone(tz).strftime("%H:%M"),
                "end": (e.ended_at or now).astimezone(tz).strftime("%H:%M"),
            }
            for e in longest
            if not e.is_private
        ],
    }


def facts_text(f: dict) -> str:
    kinds = ", ".join(f"{k} {f['hours_by_kind'][k]}h" for k in (*KIND_ORDER, "uncategorized") if f["hours_by_kind"].get(k))
    lines = [
        f"Logged {f['logged_hours']}h. By kind: {kinds or 'nothing'}.",
        f"Core work {f['core_hours']}h vs target {f['core_target']}h. Noise {f['noise_hours']}h vs budget {f['noise_budget']}h.",
    ]
    if f["sleep_hours"]:
        lines.append(f"Sleep logged: {f['sleep_hours']}h.")
    if f["planned_hours_by_kind"]:
        lines.append("Planned: " + ", ".join(f"{k} {v}h" for k, v in f["planned_hours_by_kind"].items()))
    if f["longest_entries"]:
        lines.append("Main blocks: " + "; ".join(
            f"{e['start']}–{e['end']} {e['title']} ({e['category'] or e['kind']})" for e in f["longest_entries"]
        ))
    if f["habits"]:
        lines.append("Habits: " + "; ".join(f"{h['name']}: {h['status'] or '—'} (streak {h['streak']})" for h in f["habits"]))
    m = f["money"]
    if m["spent"] or m["received"]:
        lines.append(f"Money: spent {m['spent']:g} {m['currency']}, received {m['received']:g}.")
    lines.append("Journal written." if f["journal_written"] else "No journal entry.")
    return "\n".join(lines)


def write_review(settings: Settings, user: User, profile: Profile, facts: dict) -> tuple[str, str]:
    choices = chat_models(settings, profile, temperature=0.4)
    runnable = compose(choices, lambda m: m)
    aliases = ", ".join(g["term"] for g in (profile.glossary or []) if g.get("private")) or "none"
    prompt = REVIEW_PROMPT.format(
        name=user.display_name, day=facts["date"], facts=facts_text(facts), aliases=aliases
    )
    msg = runnable.invoke([HumanMessage(prompt)])
    content = msg.content if isinstance(msg.content, str) else str(msg.content)
    return content.strip(), " → ".join(c.label for c in choices)
