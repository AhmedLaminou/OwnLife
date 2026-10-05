"""Capture: free text about a day → proposed records → the person reviews → commit.

The model only proposes. A draft is stored, shown as an editable table, and
nothing reaches the ledger until the person presses commit. Language models do
misread times; the review step is where that gets caught.
"""

from __future__ import annotations

import datetime as dt

from datetime import date
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.llm import AIUnavailableError, chat_models
from app.ai.prompts import capture_system_prompt
from app.ai import rag
from app.config import Settings, get_settings
from app.models import Category, Habit, MediaItem, Person, Profile
from app.services import filesync, records
from app.services import people as people_service
from app.services.privacy import redact
from app.services.quicklog import match_category, match_habit
from app.services.timeutil import hhmm_to_minutes, local_instant, minutes_to_hhmm, tz_of

MediaKind = Literal["book", "course", "series", "anime", "manga", "movie", "video", "channel", "podcast", "article", "music"]


# ---------------------------------------------------------------- what the model fills in
class XTimeEntry(BaseModel):
    title: str = Field(description="Short description, e.g. 'Linear algebra — freeCodeCamp'")
    category: str = Field(description="One of the given category names, exactly")
    start: str = Field(description="Start, 24-hour HH:MM")
    end: str = Field(description="End, 24-hour HH:MM")
    day_offset: int = Field(0, description="-1 if it happened on the day before the account's date")
    people: list[str] = Field(default_factory=list, description="Names of people present")
    location: str | None = None
    notes: str | None = None
    approximate: bool = Field(False, description="True when the times are estimated from vague wording")


class XTransaction(BaseModel):
    item: str
    amount: float = Field(description="Positive number in the profile currency")
    direction: Literal["out", "in"] = "out"
    category: str = Field("other", description="transport, food, clothing, gift, education, health, phone, other…")
    counterparty: str | None = Field(None, description="Who was paid or who paid, when named (a shop, a driver, a person)")
    person: str | None = Field(None, description="The name of someone the person knows (family, friend…) who gave or "
                                                  "received the money; null for shops, drivers, companies")


class XHabitLog(BaseModel):
    habit: str = Field(description="One of the given habit names")
    status: Literal["done", "missed", "urge", "relapse"]
    time: str | None = Field(None, description="HH:MM if stated")
    note: str | None = None


class XMedia(BaseModel):
    title: str
    kind: MediaKind = "video"
    creator: str | None = Field(None, description="Channel, author or studio")
    status: Literal["want", "in_progress", "done", "dropped", "none"] = "in_progress"


class XPerson(BaseModel):
    name: str
    relation: str | None = Field(None, description="family, friend, colleague, neighbour…")


class XMoment(BaseModel):
    person: str = Field(description="The name of the person it is about")
    kind: Literal["gift_from", "gift_to", "moment"] = Field(
        "moment", description="gift_from: they gave the person an object or a gift; gift_to: the person gave them one; "
                              "moment: something they did or said")
    text: str = Field(description="What happened, in one sentence, in the account's own words")
    day_offset: int = Field(0, description="-1 if it happened on the day before the account's date")


class Extraction(BaseModel):
    """Records found in an account of a day."""

    time_entries: list[XTimeEntry] = Field(default_factory=list)
    transactions: list[XTransaction] = Field(default_factory=list)
    habit_logs: list[XHabitLog] = Field(default_factory=list)
    media: list[XMedia] = Field(default_factory=list)
    people: list[XPerson] = Field(default_factory=list)
    moments: list[XMoment] = Field(default_factory=list)
    summary: str = ""


# ---------------------------------------------------------------- what the person commits
class CommitTimeEntry(BaseModel):
    include: bool = True
    title: str
    category_id: int | None = None
    start: str
    end: str
    day_offset: int = 0
    people: list[str] = Field(default_factory=list)
    location: str | None = None
    notes: str | None = None
    approximate: bool = False


class CommitTransaction(BaseModel):
    include: bool = True
    item: str
    amount: float
    direction: Literal["out", "in"] = "out"
    category: str = "other"
    counterparty: str | None = None
    person: str | None = None


class CommitHabitLog(BaseModel):
    include: bool = True
    habit_id: int
    status: Literal["done", "missed", "skip", "urge", "relapse"]
    time: str | None = None
    note: str | None = None


class CommitMedia(BaseModel):
    include: bool = True
    title: str
    kind: MediaKind = "video"
    creator: str | None = None
    status: Literal["want", "in_progress", "done", "dropped", "none"] = "in_progress"


class CommitPerson(BaseModel):
    include: bool = True
    name: str
    relation: str | None = None


class CommitMoment(BaseModel):
    include: bool = True
    person: str
    kind: Literal["gift_from", "gift_to", "moment"] = "moment"
    text: str
    day_offset: int = 0


class CommitDraft(BaseModel):
    date: dt.date
    summary: str | None = None
    time_entries: list[CommitTimeEntry] = Field(default_factory=list)
    transactions: list[CommitTransaction] = Field(default_factory=list)
    habit_logs: list[CommitHabitLog] = Field(default_factory=list)
    media: list[CommitMedia] = Field(default_factory=list)
    people: list[CommitPerson] = Field(default_factory=list)
    moments: list[CommitMoment] = Field(default_factory=list)
    # When set, this text is appended to the day's journal entry.
    journal_text: str | None = None


# ---------------------------------------------------------------- pipeline
def extract(
    settings: Settings,
    profile: Profile,
    categories: list[Category],
    habits: list[Habit],
    people: list[Person],
    text: str,
    capture_date: date,
) -> tuple[Extraction, str]:
    choices = chat_models(settings, profile, temperature=0)
    if not choices:
        raise AIUnavailableError(
            "No model is configured: add OPENROUTER_API_KEY to backend/.env or set AI mode to local. "
            "The quick-log syntax works without AI."
        )
    for_cloud = choices[0].is_cloud
    system = capture_system_prompt(profile, categories, habits, people, capture_date, for_cloud)
    content = redact(text, profile) if for_cloud else text
    # Cloud models: function calling (all the free ones support tools).
    # Ollama: JSON-schema constrained decoding, which small local models follow better.
    runnables = [
        c.llm.with_structured_output(Extraction, method="function_calling" if c.is_cloud else "json_schema")
        for c in choices
    ]
    runnable = runnables[0].with_fallbacks(runnables[1:]) if len(runnables) > 1 else runnables[0]
    result = runnable.invoke([SystemMessage(system), HumanMessage(content)])
    if isinstance(result, dict):
        result = Extraction.model_validate(result)
    return result, " → ".join(c.label for c in choices)


def _clean_hhmm(value: str) -> str | None:
    try:
        return minutes_to_hhmm(hhmm_to_minutes(value.replace("h", ":").replace("H", ":")))
    except (ValueError, AttributeError):
        return None


def normalize(
    ex: Extraction,
    capture_date: date,
    categories: list[Category],
    habits: list[Habit],
    people: list[Person],
) -> dict:
    """Turns the model's names into ids and flags what needs a human look."""
    warnings: list[str] = []
    known = {p.name.casefold() for p in people}
    out: dict = {
        "date": capture_date.isoformat(),
        "summary": ex.summary,
        "time_entries": [],
        "transactions": [],
        "habit_logs": [],
        "media": [],
        "people": [],
        "moments": [],
        "warnings": warnings,
    }
    for t in ex.time_entries:
        start, end = _clean_hhmm(t.start), _clean_hhmm(t.end)
        if start is None or end is None:
            warnings.append(f"Unreadable time in “{t.title}” ({t.start}–{t.end}): left out.")
            continue
        if start == end:
            warnings.append(f"“{t.title}” has no duration ({start}–{end}): left out.")
            continue
        cat = match_category(t.category, categories)
        minutes = (hhmm_to_minutes(end) - hhmm_to_minutes(start)) % 1440
        if cat is None:
            warnings.append(f"No category named “{t.category}” for “{t.title}”: pick one.")
        if minutes > 16 * 60:
            warnings.append(f"“{t.title}” lasts {minutes // 60}h — check the times.")
        out["time_entries"].append(
            {
                "include": True,
                "title": t.title,
                "category_id": cat.id if cat else None,
                "category": cat.name if cat else t.category,
                "start": start,
                "end": end,
                "day_offset": max(-1, min(0, t.day_offset)),
                "crosses_midnight": hhmm_to_minutes(end) <= hhmm_to_minutes(start),
                "minutes": minutes,
                "people": t.people,
                "location": t.location,
                "notes": t.notes,
                "approximate": t.approximate,
            }
        )
    for tx in ex.transactions:
        if tx.amount <= 0:
            continue
        out["transactions"].append({"include": True, **tx.model_dump()})
    for m in ex.moments:
        if m.person.strip() and m.text.strip():
            out["moments"].append({"include": True, "person": m.person.strip(), "kind": m.kind,
                                   "text": " ".join(m.text.split()), "day_offset": max(-1, min(0, m.day_offset))})
    for h in ex.habit_logs:
        habit = match_habit(h.habit, habits)
        if habit is None:
            warnings.append(f"No habit named “{h.habit}”: left out.")
            continue
        if habit.rule:
            continue  # evaluated from the data itself: a hand-written status would override it
        ok = {"build": {"done", "missed"}, "quit": {"urge", "relapse"}}[habit.kind]
        if h.status not in ok:
            warnings.append(f"“{h.status}” does not apply to {habit.name}: left out.")
            continue
        # A model inferring a relapse (or a "missed" from silence) is the costly kind
        # of mistake — seen with a small local model in testing. Such lines arrive
        # unticked: the person opts in.
        sensitive = h.status in ("relapse", "missed")
        if sensitive:
            warnings.append(
                f"The model proposes “{h.status}” for {habit.name}. It is unticked: keep it only if your text says so."
            )
        out["habit_logs"].append(
            {"include": not sensitive, "habit_id": habit.id, "habit": habit.name, "status": h.status,
             "time": _clean_hhmm(h.time) if h.time else None, "note": h.note}
        )
    for m in ex.media:
        out["media"].append({"include": True, **m.model_dump()})
    seen = {people_service.name_key(n) for n in known}
    for p in ex.people:
        key = people_service.name_key(p.name)
        if key and key not in seen:
            seen.add(key)
            out["people"].append({"include": True, "name": p.name.strip(), "relation": p.relation})
    return out


def commit(db: Session, user_id: int, profile: Profile, draft: CommitDraft, source: str = "ai") -> dict:
    """Writes the ticked lines. The result lists every record created (or the
    values replaced) under "records", which is what makes the capture undoable."""
    tz = tz_of(profile.timezone)
    counts = {"time_entries": 0, "transactions": 0, "habit_logs": 0, "media": 0, "people": 0, "moments": 0}
    rec: dict = {"time_entries": [], "transactions": [], "habit_logs": [], "media_created": [],
                 "media_updated": [], "people_created": [], "moments": [], "journal": None}
    errors: list[str] = []
    warnings: list[str] = []

    def people_for(names: list[str]) -> list[Person]:
        before = {p.id for p in db.scalars(select(Person).where(Person.user_id == user_id))}
        found = records.resolve_people(db, user_id, names)
        rec["people_created"].extend(p.id for p in found if p.id not in before and p.id not in rec["people_created"])
        return found

    for p in draft.people:
        if p.include and p.name.strip():
            person = people_for([p.name])[0]
            if p.relation and not person.relation:
                person.relation = p.relation[:40]
            counts["people"] += 1

    for t in draft.time_entries:
        if not t.include:
            continue
        try:
            s = local_instant(draft.date, t.start, tz, t.day_offset)
            e = local_instant(draft.date, t.end, tz, t.day_offset)
            if e <= s:
                e = local_instant(draft.date, t.end, tz, t.day_offset + 1)
            if t.category_id is not None and db.get(Category, t.category_id) is None:
                raise ValueError("unknown category")
            entry = records.create_time_entry(
                db, user_id,
                title=t.title, category_id=t.category_id, started_at=s, ended_at=e, source=source,
                people=people_for(t.people),
                location=t.location, notes=t.notes, is_estimate=t.approximate,
            )
            rec["time_entries"].append(entry.id)
            counts["time_entries"] += 1
        except ValueError as exc:
            errors.append(f"{t.title}: {exc}")

    for tx in draft.transactions:
        if not tx.include:
            continue
        try:
            row = records.create_transaction(
                db, user_id, profile.currency,
                occurred_on=draft.date, direction=tx.direction, amount=tx.amount,
                item=tx.item, category=tx.category, counterparty=tx.counterparty, source=source,
                person=tx.person, warnings=warnings, people_created=rec["people_created"],
            )
            rec["transactions"].append(row.id)
            counts["transactions"] += 1
        except ValueError as exc:
            errors.append(f"{tx.item}: {exc}")

    for mo in draft.moments:
        if not mo.include:
            continue
        found = people_service.link(db, user_id, mo.person)
        if found.person is None:
            warnings.append(found.warning or f"“{mo.text}”: no person named {mo.person!r}.")
            continue
        if found.created:
            rec["people_created"].append(found.person.id)
        try:
            row = people_service.add_moment(db, user_id, found.person, draft.date + dt.timedelta(days=mo.day_offset),
                                            mo.text, mo.kind, source="journal" if source == "journal" else "capture")
            rec["moments"].append(row.id)
            counts["moments"] += 1
        except ValueError as exc:
            errors.append(f"{mo.person}: {exc}")

    for h in draft.habit_logs:
        if not h.include:
            continue
        habit = db.get(Habit, h.habit_id)
        if habit is None or habit.user_id != user_id:
            errors.append(f"habit #{h.habit_id} not found")
            continue
        try:
            when = local_instant(draft.date, h.time, tz) if h.time else None
            replaced: list[dict] = []
            log = records.log_habit(db, habit, draft.date, h.status, occurred_at=when, note=h.note, replaced=replaced)
            rec["habit_logs"].append({"id": log.id, "habit_id": habit.id, "replaced": replaced})
            counts["habit_logs"] += 1
        except ValueError as exc:
            errors.append(f"{habit.name}: {exc}")

    for m in draft.media:
        if not m.include or not m.title.strip():
            continue
        item = db.scalar(
            select(MediaItem).where(
                MediaItem.user_id == user_id,
                MediaItem.kind == m.kind,
                func.lower(MediaItem.title) == m.title.strip().lower(),
            )
        )
        if item is None:
            item = MediaItem(user_id=user_id, kind=m.kind, title=m.title.strip()[:300],
                             creator=m.creator, status=m.status)
            db.add(item)
            db.flush()
            rec["media_created"].append(item.id)
        else:
            rec["media_updated"].append({"id": item.id, "status_before": item.status})
            item.status = m.status
            item.creator = item.creator or m.creator
        counts["media"] += 1

    journal = None
    if draft.journal_text and draft.journal_text.strip():
        entry, previous = records.append_to_journal(db, user_id, draft.date, draft.journal_text)
        try:
            journal = filesync.push_day(db, user_id, filesync.config(get_settings()), entry)
            rag.index_journal_entry(db, entry)
            rec["journal"] = {"id": entry.id, "appended": draft.journal_text.strip(), "created": not previous.strip()}
        except filesync.SyncError as exc:
            entry.body = previous  # the file refused it: OwnLife does not keep it either
            if not previous.strip():
                db.delete(entry)
            errors.append(f"Journal not updated: {exc}")
    db.flush()
    return {"created": counts, "errors": errors, "warnings": warnings, "records": rec, "journal_sync": journal}
