"""Creating records. Shared by the REST API, quick-log, AI capture and the
assistant's tools, so that all four behave the same way."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Habit, HabitLog, JournalEntry, Person, TimeEntry, Transaction

# Currencies whose minor unit is not 1/100. FCFA (XOF/XAF) has none.
CURRENCY_EXPONENT = {"XOF": 0, "XAF": 0, "JPY": 0, "KRW": 0, "TND": 3, "KWD": 3, "BHD": 3, "OMR": 3}


def to_minor(amount: float, currency: str) -> int:
    return round(amount * 10 ** CURRENCY_EXPONENT.get(currency.upper(), 2))


def from_minor(amount_minor: int, currency: str) -> float:
    exp = CURRENCY_EXPONENT.get(currency.upper(), 2)
    return amount_minor if exp == 0 else amount_minor / 10**exp


def resolve_people(db: Session, user_id: int, names: list[str], create: bool = True) -> list[Person]:
    out: list[Person] = []
    for raw in names:
        name = (raw or "").strip().lstrip("@")
        if not name:
            continue
        person = db.scalar(select(Person).where(Person.user_id == user_id, Person.name.ilike(name)))
        if person is None and create:
            person = Person(user_id=user_id, name=name[:120])
            db.add(person)
            db.flush()
        if person is not None and person not in out:
            out.append(person)
    return out


def create_time_entry(
    db: Session,
    user_id: int,
    *,
    title: str,
    category_id: int | None,
    started_at: datetime,
    ended_at: datetime | None,
    source: str = "manual",
    people: list[Person] | None = None,
    location: str | None = None,
    notes: str | None = None,
    is_estimate: bool = False,
    goal_id: int | None = None,
    media_item_id: int | None = None,
    focus_rating: int | None = None,
    meta: dict | None = None,
) -> TimeEntry:
    if ended_at is not None and ended_at <= started_at:
        raise ValueError("The end must be after the start")
    entry = TimeEntry(
        user_id=user_id,
        title=(title or "Untitled").strip()[:300],
        category_id=category_id,
        started_at=started_at,
        ended_at=ended_at,
        source=source,
        location=location,
        notes=notes,
        is_estimate=is_estimate,
        goal_id=goal_id,
        media_item_id=media_item_id,
        focus_rating=focus_rating,
        category_locked=category_id is not None and source not in ("activitywatch", "youtube_takeout"),
        meta=meta,
    )
    entry.people = list(people or [])
    db.add(entry)
    db.flush()
    return entry


def running_timer(db: Session, user_id: int) -> TimeEntry | None:
    return db.scalar(
        select(TimeEntry)
        .where(TimeEntry.user_id == user_id, TimeEntry.ended_at.is_(None))
        .order_by(TimeEntry.started_at.desc())
    )


def stop_timer(db: Session, user_id: int, now: datetime) -> TimeEntry | None:
    entry = running_timer(db, user_id)
    if entry is None:
        return None
    entry.ended_at = max(now, entry.started_at)
    db.flush()
    return entry


def start_timer(
    db: Session, user_id: int, title: str, category_id: int | None, now: datetime, source: str = "timer"
) -> TimeEntry:
    stop_timer(db, user_id, now)
    return create_time_entry(
        db, user_id, title=title, category_id=category_id, started_at=now, ended_at=None, source=source
    )


# ---------------------------------------------------------------- pause and resume
# A paused timer is a stopped segment marked `paused` in its meta. Resuming
# starts a new segment of the same activity; the segments share a `session`
# (the id of the first one). The pause itself is simply not counted — the time
# in between is free for whatever you did instead.
PAUSE_EXPIRES = timedelta(hours=18)  # a pause longer than this was a finished timer


def pause_timer(db: Session, user_id: int, now: datetime) -> TimeEntry | None:
    entry = stop_timer(db, user_id, now)
    if entry is None:
        return None
    meta = dict(entry.meta or {})
    entry.meta = {**meta, "paused": True, "session": meta.get("session", entry.id)}
    db.flush()
    return entry


def paused_timers(db: Session, user_id: int, now: datetime) -> list[TimeEntry]:
    """Paused timers, the most recent first."""
    recent = db.scalars(
        select(TimeEntry)
        .where(TimeEntry.user_id == user_id, TimeEntry.ended_at.is_not(None),
               TimeEntry.ended_at >= now - PAUSE_EXPIRES)
        .order_by(TimeEntry.ended_at.desc())
    ).all()
    return [e for e in recent if (e.meta or {}).get("paused")]


def _unpause(entry: TimeEntry) -> None:
    meta = dict(entry.meta or {})
    meta.pop("paused", None)
    entry.meta = meta


def resume_timer(db: Session, user_id: int, now: datetime, entry_id: int | None = None) -> TimeEntry | None:
    """Starts the next segment of a paused timer (the latest one, or entry_id).
    A timer running meanwhile is stopped, as when any timer starts."""
    target = next((e for e in paused_timers(db, user_id, now) if entry_id in (None, e.id)), None)
    if target is None:
        return None
    stop_timer(db, user_id, now)
    _unpause(target)
    entry = create_time_entry(
        db, user_id, title=target.title, category_id=target.category_id, started_at=now, ended_at=None,
        source="timer", people=list(target.people), location=target.location, notes=target.notes,
        goal_id=target.goal_id, meta={"session": (target.meta or {}).get("session", target.id)},
    )
    entry.is_private = target.is_private
    db.flush()
    return entry


def finish_paused(db: Session, user_id: int, entry_id: int, now: datetime) -> TimeEntry | None:
    """A paused timer that will not be resumed: it stays as it is, just no longer paused."""
    target = next((e for e in paused_timers(db, user_id, now) if e.id == entry_id), None)
    if target is not None:
        _unpause(target)
        db.flush()
    return target


def session_seconds(db: Session, user_id: int, entry: TimeEntry, now: datetime) -> float:
    """The time of every segment of the entry's session, the running one included."""
    session = (entry.meta or {}).get("session")
    if session is None:
        return ((entry.ended_at or now) - entry.started_at).total_seconds()
    segments = db.scalars(
        select(TimeEntry).where(TimeEntry.user_id == user_id, TimeEntry.started_at >= now - timedelta(days=3))
    ).all()
    return sum(
        ((s.ended_at or now) - s.started_at).total_seconds()
        for s in segments
        if s.id == session or (s.meta or {}).get("session") == session
    )


def create_transaction(
    db: Session,
    user_id: int,
    currency: str,
    *,
    occurred_on: date,
    direction: str,
    amount: float,
    item: str,
    category: str = "other",
    counterparty: str | None = None,
    note: str | None = None,
    source: str = "manual",
) -> Transaction:
    if direction not in ("in", "out"):
        raise ValueError("direction must be 'in' or 'out'")
    if amount <= 0:
        raise ValueError("amount must be positive")
    person = None
    if counterparty:
        found = resolve_people(db, user_id, [counterparty], create=False)
        person = found[0] if found else None
    tx = Transaction(
        user_id=user_id,
        occurred_on=occurred_on,
        direction=direction,
        amount_minor=to_minor(amount, currency),
        currency=currency,
        item=item.strip()[:200] or "Unspecified",
        category=(category or "other").strip().lower()[:60],
        counterparty=counterparty,
        person_id=person.id if person else None,
        note=note,
        source=source,
    )
    db.add(tx)
    db.flush()
    return tx


def log_habit(
    db: Session,
    habit: Habit,
    log_date: date,
    status: str,
    occurred_at: datetime | None = None,
    note: str | None = None,
    value: float | None = None,
    replaced: list[dict] | None = None,
) -> HabitLog:
    """`replaced`, when given, receives the statuses this log replaced (for undo)."""
    allowed = {"build": {"done", "missed", "skip"}, "quit": {"relapse", "urge"}}[habit.kind]
    if status not in allowed:
        raise ValueError(f"'{status}' is not valid for a {habit.kind} habit (use {', '.join(sorted(allowed))})")
    if habit.kind == "build":
        # one manual status per day: the latest replaces the previous one
        for old in db.scalars(
            select(HabitLog).where(HabitLog.habit_id == habit.id, HabitLog.log_date == log_date)
        ):
            if replaced is not None:
                replaced.append({"date": old.log_date.isoformat(), "status": old.status, "note": old.note,
                                 "occurred_at": old.occurred_at.isoformat() if old.occurred_at else None})
            db.delete(old)
    log = HabitLog(
        user_id=habit.user_id,
        habit_id=habit.id,
        log_date=log_date,
        status=status,
        occurred_at=occurred_at,
        note=note,
        value=value,
    )
    db.add(log)
    db.flush()
    return log


def append_to_journal(db: Session, user_id: int, d: date, text: str) -> tuple[JournalEntry, str]:
    """Appends to the day's page (creating it when needed). Returns the page and
    its previous text, so that the caller can undo it, or put it back if the
    file refuses the edit. The caller pushes the change to the file."""
    entry = db.scalar(
        select(JournalEntry)
        .where(JournalEntry.user_id == user_id, JournalEntry.entry_date == d, JournalEntry.visible())
        .order_by(JournalEntry.id)
    )
    text = text.strip()
    if entry is None:
        entry = JournalEntry(user_id=user_id, entry_date=d, body=text, tags=[], source="manual")
        db.add(entry)
        previous = ""
    else:
        previous = entry.body or ""
        entry.body = f"{previous.rstrip()}\n\n{text}" if previous.strip() else text
    db.flush()
    return entry, previous
