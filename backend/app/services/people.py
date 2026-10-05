"""People: knowing who a name means, linking money to them, gifts and moments,
and merging two entries that turn out to be one person.

A name is compared without case, accents or extra spaces ("uncle  kofi" is
Uncle Kofi). When it is not exactly someone already known but shares their
words ("Kofi" and "Uncle Kofi"), nothing is guessed: the record stays
unlinked and a warning says who it may be, to be linked by hand.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import Person, PersonMoment, Profile, TimeEntry, Transaction, time_entry_people

MOMENT_KINDS = ("gift_from", "gift_to", "moment")
DEFAULTS = {"moments_private": False}  # new gifts and moments are private (never sent to a cloud model)


def settings_with_defaults(prefs: dict | None) -> dict:
    return {**DEFAULTS, **(prefs or {})}


def name_key(name: str | None) -> str:
    """'Uncle  Kofi' and 'uncle kofi' are one key; 'Ines' and 'Inès' too."""
    s = unicodedata.normalize("NFKD", name or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return " ".join(s.casefold().replace("-", " ").replace("_", " ").split())


@dataclass
class Match:
    person: Person | None = None  # the one person the name means
    candidates: list[Person] = field(default_factory=list)  # who it may be, when not certain


def match(db: Session, user_id: int, name: str | None) -> Match:
    key = name_key((name or "").lstrip("@"))
    if not key:
        return Match()
    people = list(db.scalars(select(Person).where(Person.user_id == user_id).order_by(Person.id)))
    exact = next((p for p in people if name_key(p.name) == key), None)
    if exact is not None:
        return Match(exact)
    words = set(key.split())
    close = [p for p in people if (pw := set(name_key(p.name).split())) and (pw <= words or words <= pw)]
    return Match(None, close)


def link_transactions(db: Session, person: Person) -> int:
    """Money recorded earlier under this exact name, not linked to anyone yet,
    now belongs to this person."""
    key = name_key(person.name)
    n = 0
    for t in db.scalars(select(Transaction).where(
            Transaction.user_id == person.user_id, Transaction.person_id.is_(None), Transaction.counterparty.is_not(None))):
        if name_key(t.counterparty) == key:
            t.person_id = person.id
            n += 1
    return n


def create(db: Session, user_id: int, name: str, relation: str | None = None) -> Person:
    p = Person(user_id=user_id, name=" ".join(name.split()).lstrip("@")[:120], relation=(relation or None))
    db.add(p)
    db.flush()
    link_transactions(db, p)
    return p


@dataclass
class Linked:
    person: Person | None
    created: bool = False
    warning: str | None = None


def link(db: Session, user_id: int, name: str | None, create_new: bool = True) -> Linked:
    """The person a name means: found, created (when nobody is close), or —
    when the name looks like someone without being them — left for you to say."""
    m = match(db, user_id, name)
    if m.person is not None:
        return Linked(m.person)
    if m.candidates:
        who = " or ".join(p.name for p in m.candidates[:3])
        return Linked(None, warning=f"“{name}” may be {who}: not linked, to be sure — link it on the Money or People page.")
    if not create_new or not name_key(name):
        return Linked(None)
    return Linked(create(db, user_id, name), created=True)


def moments_private(db: Session, user_id: int) -> bool:
    profile = db.get(Profile, user_id)
    return bool(settings_with_defaults((profile.prefs or {}).get("people") if profile else None)["moments_private"])


def add_moment(db: Session, user_id: int, person: Person, occurred_on: date, text: str, kind: str = "moment",
               source: str = "manual", journal_entry_id: int | None = None,
               is_private: bool | None = None) -> PersonMoment:
    if kind not in MOMENT_KINDS:
        raise ValueError(f"kind must be one of {', '.join(MOMENT_KINDS)}")
    clean = " ".join((text or "").split())[:1000]
    if not clean:
        raise ValueError("say what happened")
    m = PersonMoment(user_id=user_id, person_id=person.id, occurred_on=occurred_on, kind=kind, text=clean,
                     source=source, journal_entry_id=journal_entry_id,
                     is_private=moments_private(db, user_id) if is_private is None else is_private)
    db.add(m)
    db.flush()
    return m


def in_use(db: Session, person_id: int) -> bool:
    """Whether anything points at this person (time, money, gifts, moments)."""
    return any(db.scalar(q.limit(1)) is not None for q in (
        select(time_entry_people.c.person_id).where(time_entry_people.c.person_id == person_id),
        select(Transaction.id).where(Transaction.person_id == person_id),
        select(PersonMoment.id).where(PersonMoment.person_id == person_id),
    ))


def merge(db: Session, source: Person, into: Person) -> dict:
    """Two entries for one person: everything of `source` (time together, money,
    gifts, moments, notes) moves to `into`, then `source` is removed."""
    if source.id == into.id or source.user_id != into.user_id:
        raise ValueError("choose another person")
    moved = {"time_entries": 0, "transactions": 0, "moments": 0}
    for e in db.scalars(select(TimeEntry).join(time_entry_people, TimeEntry.id == time_entry_people.c.time_entry_id)
                        .where(time_entry_people.c.person_id == source.id)).unique():
        e.people = [*(p for p in e.people if p.id not in (source.id, into.id)), into]
        moved["time_entries"] += 1
    moved["transactions"] = db.execute(update(Transaction).where(Transaction.person_id == source.id)
                                       .values(person_id=into.id)).rowcount or 0
    moved["moments"] = db.execute(update(PersonMoment).where(PersonMoment.person_id == source.id)
                                  .values(person_id=into.id)).rowcount or 0
    if source.notes:
        into.notes = f"{into.notes}\n\n{source.notes}".strip() if into.notes else source.notes
    into.relation = into.relation or source.relation
    into.tags = sorted(set(into.tags or []) | set(source.tags or []))
    db.flush()
    db.delete(source)
    db.flush()
    return moved
