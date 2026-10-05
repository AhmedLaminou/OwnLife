"""People: who the time is spent with, the money that went between you, the
gifts, and the moments worth remembering."""

from __future__ import annotations

import datetime as dt
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.db import utcnow
from app.deps import DB, CurrentProfile, CurrentUser
from app.models import Person, PersonMoment, TimeEntry, Transaction, time_entry_people
from app.serializers import entry_out, iso, moment_out, person_out, tx_out
from app.services import people as people_service
from app.services.records import from_minor
from app.services.timeutil import local_today, tz_of

router = APIRouter(prefix="/api/people", tags=["people"])

MomentKind = Literal["gift_from", "gift_to", "moment"]


class PersonIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    relation: str | None = Field(None, max_length=40)
    notes: str | None = None
    tags: list[str] = Field(default_factory=list)
    is_private: bool = False


class PersonPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    relation: str | None = Field(None, max_length=40)
    notes: str | None = None
    tags: list[str] | None = None
    is_private: bool | None = None


class MomentIn(BaseModel):
    date: dt.date | None = None  # today when left out
    kind: MomentKind = "moment"
    text: str = Field(min_length=1, max_length=1000)
    is_private: bool | None = None  # the default of Settings when left out


class MomentPatch(BaseModel):
    date: dt.date | None = None
    kind: MomentKind | None = None
    text: str | None = Field(None, min_length=1, max_length=1000)
    is_private: bool | None = None
    person_id: int | None = None


class MergeIn(BaseModel):
    into_id: int


class PeoplePrefs(BaseModel):
    moments_private: bool = False


def _own(db, user_id: int, person_id: int) -> Person:
    p = db.get(Person, person_id)
    if p is None or p.user_id != user_id:
        raise HTTPException(404, "Person not found")
    return p


def _own_moment(db, user_id: int, moment_id: int) -> PersonMoment:
    m = db.get(PersonMoment, moment_id)
    if m is None or m.user_id != user_id:
        raise HTTPException(404, "Moment not found")
    return m


def _money(rows) -> dict[int, dict[str, float]]:
    out: dict[int, dict[str, float]] = {}
    for t in rows:
        d = out.setdefault(t.person_id, {"in": 0.0, "out": 0.0})
        d[t.direction] += from_minor(t.amount_minor, t.currency)
    return out


# ---------------------------------------------------------------- preferences (before /{person_id})
@router.get("/prefs")
def get_prefs(profile: CurrentProfile) -> dict:
    return people_service.settings_with_defaults((profile.prefs or {}).get("people"))


@router.put("/prefs")
def put_prefs(body: PeoplePrefs, profile: CurrentProfile, db: DB) -> dict:
    profile.prefs = {**(profile.prefs or {}), "people": body.model_dump()}
    db.commit()
    return people_service.settings_with_defaults(profile.prefs["people"])


# ---------------------------------------------------------------- gifts and moments (before /{person_id})
@router.patch("/moments/{moment_id}")
def update_moment(moment_id: int, body: MomentPatch, user: CurrentUser, db: DB) -> dict:
    m = _own_moment(db, user.id, moment_id)
    data = body.model_dump(exclude_unset=True)
    if "person_id" in data:
        m.person_id = _own(db, user.id, data.pop("person_id")).id
    if "date" in data:
        m.occurred_on = data.pop("date") or m.occurred_on
    if "text" in data:
        m.text = " ".join(data.pop("text").split())
    for k, v in data.items():
        setattr(m, k, v)
    db.commit()
    return moment_out(m)


@router.delete("/moments/{moment_id}")
def delete_moment(moment_id: int, user: CurrentUser, db: DB) -> dict:
    db.delete(_own_moment(db, user.id, moment_id))
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- people
@router.get("")
def list_people(user: CurrentUser, db: DB) -> list[dict]:
    now = utcnow().replace(tzinfo=None)
    seconds = func.sum(
        (func.julianday(func.coalesce(TimeEntry.ended_at, now)) - func.julianday(TimeEntry.started_at)) * 86400.0
    )
    stats = {
        pid: (secs or 0.0, last, n)
        for pid, secs, last, n in db.execute(
            select(time_entry_people.c.person_id, seconds, func.max(TimeEntry.started_at), func.count())
            .join(TimeEntry, TimeEntry.id == time_entry_people.c.time_entry_id)
            .where(TimeEntry.user_id == user.id)
            .group_by(time_entry_people.c.person_id)
        )
    }
    txs = db.scalars(select(Transaction).where(Transaction.user_id == user.id, Transaction.person_id.is_not(None))).all()
    money = _money(txs)
    last_tx: dict[int, dt.date] = {}
    for t in txs:
        last_tx[t.person_id] = max(last_tx.get(t.person_id, t.occurred_on), t.occurred_on)
    moments: dict[int, dict] = {}
    for pid, kind, n, last in db.execute(
            select(PersonMoment.person_id, PersonMoment.kind, func.count(), func.max(PersonMoment.occurred_on))
            .where(PersonMoment.user_id == user.id).group_by(PersonMoment.person_id, PersonMoment.kind)):
        d = moments.setdefault(pid, {"gifts": 0, "moments": 0, "last": None})
        d["gifts" if kind.startswith("gift") else "moments"] += n
        d["last"] = max(d["last"] or last, last)
    out = []
    for p in db.scalars(select(Person).where(Person.user_id == user.id).order_by(Person.name)):
        secs, last, n = stats.get(p.id, (0.0, None, 0))
        mo = moments.get(p.id, {"gifts": 0, "moments": 0, "last": None})
        dates = [d for d in (last.date() if last else None, last_tx.get(p.id), mo["last"]) if d]
        out.append({**person_out(p), "hours_together": round(secs / 3600, 2), "entries": n,
                    "last_seen": iso(last) if last else None,
                    "money_in": money.get(p.id, {}).get("in", 0.0), "money_out": money.get(p.id, {}).get("out", 0.0),
                    "gifts": mo["gifts"], "moments": mo["moments"],
                    "last_interaction": iso(max(dates)) if dates else None})
    return out


@router.post("")
def create_person(body: PersonIn, user: CurrentUser, db: DB) -> dict:
    if people_service.match(db, user.id, body.name).person is not None:
        raise HTTPException(409, "This person already exists")
    p = people_service.create(db, user.id, body.name, body.relation)
    p.notes, p.tags, p.is_private = body.notes, body.tags, body.is_private
    db.commit()
    return person_out(p)


@router.get("/{person_id}")
def get_person(person_id: int, user: CurrentUser, db: DB) -> dict:
    p = _own(db, user.id, person_id)
    now = utcnow()
    entries = db.scalars(
        select(TimeEntry)
        .join(time_entry_people, TimeEntry.id == time_entry_people.c.time_entry_id)
        .where(time_entry_people.c.person_id == p.id)
        .order_by(TimeEntry.started_at.desc())
        .limit(100)
    ).unique().all()
    txs = db.scalars(select(Transaction).where(Transaction.person_id == p.id)
                     .order_by(Transaction.occurred_on.desc(), Transaction.id.desc())).all()
    moments = db.scalars(select(PersonMoment).where(PersonMoment.person_id == p.id)
                         .order_by(PersonMoment.occurred_on.desc(), PersonMoment.id.desc())).all()
    total = sum(((e.ended_at or now) - e.started_at).total_seconds() for e in entries)
    money = _money(txs).get(p.id, {"in": 0.0, "out": 0.0})
    return {
        **person_out(p),
        "hours_together": round(total / 3600, 2),
        "money_in": money["in"],
        "money_out": money["out"],
        "entries": [entry_out(e, now) for e in entries],
        "transactions": [tx_out(t) for t in txs],
        "moments": [moment_out(m) for m in moments],
    }


@router.post("/{person_id}/moments")
def add_moment(person_id: int, body: MomentIn, user: CurrentUser, profile: CurrentProfile, db: DB) -> dict:
    p = _own(db, user.id, person_id)
    try:
        m = people_service.add_moment(db, user.id, p, body.date or local_today(tz_of(profile.timezone)), body.text,
                                      body.kind, is_private=body.is_private)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    db.commit()
    return moment_out(m)


@router.post("/{person_id}/merge")
def merge_person(person_id: int, body: MergeIn, user: CurrentUser, db: DB) -> dict:
    """This entry and another are the same person: everything moves to the other."""
    source, into = _own(db, user.id, person_id), _own(db, user.id, body.into_id)
    try:
        moved = people_service.merge(db, source, into)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    db.commit()
    return {"ok": True, "into": person_out(into), "moved": moved}


@router.patch("/{person_id}")
def update_person(person_id: int, body: PersonPatch, user: CurrentUser, db: DB) -> dict:
    p = _own(db, user.id, person_id)
    data = body.model_dump(exclude_unset=True)
    if "name" in data:
        other = people_service.match(db, user.id, data["name"]).person
        if other is not None and other.id != p.id:
            raise HTTPException(409, f"{other.name} already exists: merge the two instead")
    for k, v in data.items():
        setattr(p, k, " ".join(v.split()) if k == "name" and isinstance(v, str) else v)
    if "name" in data:
        db.flush()
        people_service.link_transactions(db, p)
    db.commit()
    return person_out(p)


@router.delete("/{person_id}")
def delete_person(person_id: int, user: CurrentUser, db: DB) -> dict:
    db.delete(_own(db, user.id, person_id))
    db.commit()
    return {"ok": True}
