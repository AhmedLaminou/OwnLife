"""Time from the journal: the blocks you wrote down ("from 13:30 to 16:20 the
lectures"), proposed as drafts in the Capture inbox.

Only the lines of a page that hold a time (13:30, 9h, 2 hours, 45 mn…) go to
the model, never the whole page, and a page is read again only when those lines
change — a new reflection elsewhere on the page costs nothing. The model returns
blocks (what, from when to when, which category) and the people met; they
become one draft per day, reviewed and saved like any capture. A block that
matches an entry already in the ledger arrives unticked, with a note. Nothing
reaches the ledger without your review.

Scans run on demand (Assistant → Inbox → Read my journal) and from the server's
loop: a page is read once it has been quiet for 10 minutes, at most every half
hour, one request for up to ~6,000 characters of lines.
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.capture import Extraction, XPerson, XTimeEntry, normalize
from app.ai.llm import AIUnavailableError, chat_models, describe_error, effective_mode
from app.ai.prompts import _categories_block
from app.config import Settings
from app.db import SessionLocal, utcnow
from app.models import CaptureDraft, Category, Habit, JournalEntry, Person, Profile, TimeEntry, User
from app.services.activitywatch import get_state
from app.services.ledger import AUTOMATIC
from app.services.privacy import redact
from app.services.timeutil import local_instant, range_utc, tz_of

log = logging.getLogger("ownlife")

PROVIDER = "journal_time"
DEFAULTS = {"scan": True}
QUIET = timedelta(minutes=10)
EVERY = timedelta(minutes=30)
BATCH_CHARS = 6000
LINE_CHARS = 900
SAME_BLOCK = timedelta(minutes=10)  # a block this close to a ledger entry is that entry

_TIME = re.compile(
    r"\b\d{1,2}\s?[:h]\s?\d{2}\b|\b\d{1,2}\s?(?:am|pm)\b|\b\d{1,2}\s?h\b|"
    r"\b\d+\s?(?:mn|min|mins|minutes?|hours?|heures?)\b",
    re.I,
)
_CLAUSE = re.compile(r"(?<=[;!?])\s+|(?<=\.)\s+(?=[A-Z0-9(])")


def settings_with_defaults(prefs: dict | None) -> dict:
    return {**DEFAULTS, **(prefs or {})}


def time_lines(body: str) -> list[str]:
    """The lines of a page that hold a time. A very long line keeps only its
    clauses with a time, each with the clause before it (what "then" refers to)."""
    out: list[str] = []
    for raw in (body or "").splitlines():
        line = " ".join(raw.split())
        if not line or not _TIME.search(line):
            continue
        if len(line) <= LINE_CHARS:
            out.append(line)
            continue
        clauses = _CLAUSE.split(line)
        for i, c in enumerate(clauses):
            if _TIME.search(c):
                out.append((" ".join(clauses[max(0, i - 1):i + 1]))[:LINE_CHARS])
    return list(dict.fromkeys(out))


def _hash(lines: list[str]) -> str:
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


# ---------------------------------------------------------------- the model
class Block(BaseModel):
    excerpt: int = Field(description="The number of the excerpt it comes from")
    title: str = Field(description="What was done, short: 'Linear algebra — chapter 3'")
    category: str = Field(description="One of the given category names, exactly")
    start: str = Field(description="Start, 24-hour HH:MM")
    end: str = Field(description="End, 24-hour HH:MM")
    approximate: bool = Field(False, description="True when the times are estimated from vague wording")
    people: list[str] = Field(default_factory=list, description="Names of the people present")
    location: str | None = None


class Met(BaseModel):
    excerpt: int = Field(description="The number of the excerpt it comes from")
    name: str
    relation: str | None = Field(None, description="family, friend, colleague, neighbour…")


class Blocks(BaseModel):
    """Time blocks and people found in the excerpts."""

    blocks: list[Block] = Field(default_factory=list)
    people: list[Met] = Field(default_factory=list)


SYSTEM = """You read excerpts of {name}'s journal and list the time blocks {name} describes: what was done,
from when to when.
- Only times written in the excerpt: a start and an end, or a start and a duration. Never invent a time;
  an activity without one gives no block.
- 24-hour HH:MM. "13:15 AM" in this person's writing means 13:15. "around 17-18h" -> 17:00-18:00 with
  approximate=true. An activity crossing midnight keeps its clock times (the end is before the start).
- category: exactly one of these names, as written before the brackets (the bracket is its kind):
{categories}
- An activity is filed by what was done, not by where: videos watched at work are watching, not work.
- people: the people met or talked with, by name. Known people: {known}."""

Excerpt = tuple[int, date, str]  # (journal entry id, its day, the line)
Found = tuple[list[tuple[Excerpt, Block]], list[tuple[Excerpt, Met]], str]
Extractor = Callable[[Settings, User, Profile, list[Category], list[Person], list[Excerpt]], Found]


def extract(settings: Settings, user: User, profile: Profile, categories: list[Category],
            people: list[Person], excerpts: list[Excerpt]) -> Found:
    choices = chat_models(settings, profile, temperature=0)
    if not choices:
        raise AIUnavailableError("No model is configured: time cannot be read from the journal without one.")
    for_cloud = choices[0].is_cloud
    runnables = [c.llm.with_structured_output(Blocks, method="function_calling" if c.is_cloud else "json_schema")
                 for c in choices]
    runnable = runnables[0].with_fallbacks(runnables[1:]) if len(runnables) > 1 else runnables[0]
    system = SYSTEM.format(name=user.display_name, categories=_categories_block(categories),
                           known=", ".join(p.name for p in people[:200]) or "none yet")
    blocks: list[tuple[Excerpt, Block]] = []
    met: list[tuple[Excerpt, Met]] = []
    batches: list[list[Excerpt]] = [[]]
    for ex in excerpts:
        if batches[-1] and sum(len(x[2]) for x in batches[-1]) + len(ex[2]) > BATCH_CHARS:
            batches.append([])
        batches[-1].append(ex)
    for batch in batches:
        lines, day = [], None
        for i, (_, d, text) in enumerate(batch, 1):
            if d != day:
                lines.append(d.isoformat())
                day = d
            lines.append(f"[{i}] {redact(text, profile) if for_cloud else text}")
        result = runnable.invoke([SystemMessage(system), HumanMessage("\n".join(lines))])
        if isinstance(result, dict):
            result = Blocks.model_validate(result)
        blocks += [(batch[b.excerpt - 1], b) for b in result.blocks if 1 <= b.excerpt <= len(batch)]
        met += [(batch[m.excerpt - 1], m) for m in result.people if 1 <= m.excerpt <= len(batch)]
    return blocks, met, " → ".join(c.label for c in choices)


# ---------------------------------------------------------------- drafts
def _mark_known(db: Session, user_id: int, draft: dict, day: date, tz) -> None:
    """Unticks a proposed block that an entry of the ledger already covers."""
    lo, hi = range_utc(day - timedelta(days=1), day + timedelta(days=1), tz)
    entries = [e for e in db.scalars(select(TimeEntry).where(
        TimeEntry.user_id == user_id, TimeEntry.started_at < hi, TimeEntry.ended_at > lo)) if e.source not in AUTOMATIC]
    for t in draft["time_entries"]:
        start = local_instant(day, t["start"], tz, t["day_offset"])
        end = local_instant(day, t["end"], tz, t["day_offset"] + (1 if t["crosses_midnight"] else 0))
        same = next((e for e in entries if abs(e.started_at - start) <= SAME_BLOCK
                     and abs((e.ended_at or e.started_at) - end) <= SAME_BLOCK), None)
        if same is not None:
            t["include"] = False
            draft["warnings"].append(f"“{t['title']}” {t['start']}–{t['end']} is already in the ledger "
                                     f"(“{same.title}”): left unticked.")


def sends_to_cloud(settings: Settings, profile: Profile) -> bool:
    return any(c.is_cloud for c in chat_models(settings, profile))


@dataclass
class ScanResult:
    days: int = 0  # pages whose time lines were read
    lines: int = 0  # lines sent to the model
    blocks: int = 0
    drafts: int = 0  # days with something to review
    model: str = ""


def _pages(db: Session, user_id: int) -> list[JournalEntry]:
    return list(db.scalars(select(JournalEntry).where(JournalEntry.user_id == user_id, JournalEntry.visible())))


def changed_entries(db: Session, user_id: int, cursor: dict, quiet_before: datetime | None = None) -> list[JournalEntry]:
    """Pages whose time lines changed since they were read."""
    out = [e for e in _pages(db, user_id) if cursor.get(str(e.id)) != _hash(time_lines(e.body))]
    if quiet_before is not None:
        out = [e for e in out if (e.updated_at or e.created_at) <= quiet_before]
    return sorted(out, key=lambda e: e.entry_date)


def scan(settings: Settings, user_id: int, rescan_all: bool = False, extractor: Extractor | None = None) -> ScanResult:
    with SessionLocal() as db:
        user, profile = db.get(User, user_id), db.get(Profile, user_id)
        state = get_state(db, user_id, PROVIDER)
        read = {} if rescan_all else dict((state.cursor or {}).get("days") or {})
        cloud = sends_to_cloud(settings, profile)
        pages = []
        for e in changed_entries(db, user_id, read):
            lines = [] if cloud and e.is_private else time_lines(e.body)
            pages.append((e.id, e.entry_date, e.day_number, _hash(time_lines(e.body)), lines))
        categories = list(db.scalars(select(Category).where(Category.user_id == user_id, Category.archived.is_(False))))
        people = list(db.scalars(select(Person).where(Person.user_id == user_id).order_by(Person.name)))
        db.commit()
    excerpts: list[Excerpt] = [(eid, d, line) for eid, d, _, _, lines in pages for line in lines]
    result = ScanResult(days=len(pages), lines=len(excerpts))
    blocks: list[tuple[Excerpt, Block]] = []
    met: list[tuple[Excerpt, Met]] = []
    if excerpts:
        blocks, met, result.model = (extractor or extract)(settings, user, profile, categories, people, excerpts)
    result.blocks = len(blocks)

    with SessionLocal() as db:
        profile = db.get(Profile, user_id)
        tz = tz_of(profile.timezone)
        categories = list(db.scalars(select(Category).where(Category.user_id == user_id, Category.archived.is_(False))))
        habits = list(db.scalars(select(Habit).where(Habit.user_id == user_id, Habit.archived.is_(False))))
        people = list(db.scalars(select(Person).where(Person.user_id == user_id)))
        for eid, day, number, _, lines in pages:
            old = db.scalars(select(CaptureDraft).where(
                CaptureDraft.user_id == user_id, CaptureDraft.status == "pending")).all()
            for d in old:  # this page's previous proposal is replaced
                if (d.draft or {}).get("origin") == "journal" and (d.draft or {}).get("journal_entry_id") == eid:
                    db.delete(d)
            ex = Extraction(
                time_entries=[XTimeEntry(title=b.title, category=b.category, start=b.start, end=b.end,
                                         people=b.people, location=b.location, approximate=b.approximate)
                              for (e_id, _, _), b in blocks if e_id == eid],
                people=[XPerson(name=m.name, relation=m.relation) for (e_id, _, _), m in met if e_id == eid],
            )
            draft = normalize(ex, day, categories, habits, people)
            if not draft["time_entries"] and not draft["people"]:
                continue
            _mark_known(db, user_id, draft, day, tz)
            draft.update(origin="journal", journal_entry_id=eid, day_number=number,
                         summary=f"From your journal{f', Day {number}' if number else ''}.")
            db.add(CaptureDraft(user_id=user_id, capture_date=day, input_text="\n".join(lines), draft=draft,
                                model=result.model or None))
            result.drafts += 1
        state = get_state(db, user_id, PROVIDER)
        cursor = dict(state.cursor or {})
        cursor["days"] = {**({} if rescan_all else dict(cursor.get("days") or {})),
                          **{str(eid): h for eid, _, _, h, _ in pages}}
        state.cursor = cursor
        state.last_synced_at, state.last_error = utcnow(), None
        db.commit()
    return result


# ---------------------------------------------------------------- in the background
_lock = threading.Lock()
_status: dict[int, dict] = {}


def status(user_id: int) -> dict:
    with _lock:
        return dict(_status.get(user_id) or {"state": "idle"})


def schedule(settings: Settings, user_id: int, rescan_all: bool = False) -> bool:
    with _lock:
        if (_status.get(user_id) or {}).get("state") == "running":
            return True
        _status[user_id] = {"state": "running", "started_at": utcnow().isoformat()}
    threading.Thread(target=_run, args=(settings, user_id, rescan_all), daemon=True,
                     name=f"journal-time-{user_id}").start()
    return True


def _run(settings: Settings, user_id: int, rescan_all: bool) -> None:
    with SessionLocal() as db:
        state = get_state(db, user_id, PROVIDER)
        state.cursor = {**(state.cursor or {}), "attempted_at": utcnow().isoformat()}
        db.commit()
    try:
        r = scan(settings, user_id, rescan_all)
        new = {"state": "idle", "error": None, "days": r.days, "lines": r.lines, "blocks": r.blocks,
               "drafts": r.drafts, "model": r.model, "finished_at": utcnow().isoformat()}
    except Exception as e:
        message = describe_error(e)
        log.warning("Reading time from the journal failed: %s", message)
        with SessionLocal() as db:
            get_state(db, user_id, PROVIDER).last_error = message
            db.commit()
        new = {"state": "error", "error": message, "finished_at": utcnow().isoformat()}
    with _lock:
        _status[user_id] = new


def tick(settings: Settings, now: datetime | None = None) -> list[int]:
    now = now or utcnow()
    started = []
    with SessionLocal() as db:
        for user in db.scalars(select(User).where(User.is_active.is_(True))):
            profile = db.get(Profile, user.id)
            if profile is None or not settings_with_defaults((profile.prefs or {}).get("journal_time"))["scan"]:
                continue
            if effective_mode(settings, profile) == "off":
                continue
            cursor = get_state(db, user.id, PROVIDER).cursor or {}
            attempted = cursor.get("attempted_at")
            if attempted and now - datetime.fromisoformat(attempted) < EVERY:
                continue
            if changed_entries(db, user.id, dict(cursor.get("days") or {}), quiet_before=now - QUIET):
                started.append(user.id)
        db.commit()
    for uid in started:
        schedule(settings, uid)
    return started
