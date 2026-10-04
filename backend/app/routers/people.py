"""People: who the time is spent with."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.db import utcnow
from app.deps import DB, CurrentUser
from app.models import Person, TimeEntry, Transaction, time_entry_people
from app.serializers import entry_out, iso, person_out, tx_out

router = APIRouter(prefix="/api/people", tags=["people"])


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


def _own(db, user_id: int, person_id: int) -> Person:
    p = db.get(Person, person_id)
    if p is None or p.user_id != user_id:
        raise HTTPException(404, "Person not found")
    return p


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
    out = []
    for p in db.scalars(select(Person).where(Person.user_id == user.id).order_by(Person.name)):
        secs, last, n = stats.get(p.id, (0.0, None, 0))
        out.append({**person_out(p), "hours_together": round(secs / 3600, 2), "entries": n,
                    "last_seen": iso(last) if last else None})
    return out


@router.post("")
def create_person(body: PersonIn, user: CurrentUser, db: DB) -> dict:
    if db.scalar(select(Person).where(Person.user_id == user.id, Person.name.ilike(body.name.strip()))):
        raise HTTPException(409, "This person already exists")
    p = Person(user_id=user.id, **{**body.model_dump(), "name": body.name.strip()})
    db.add(p)
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
    txs = db.scalars(select(Transaction).where(Transaction.person_id == p.id).order_by(Transaction.occurred_on.desc())).all()
    total = sum(((e.ended_at or now) - e.started_at).total_seconds() for e in entries)
    return {
        **person_out(p),
        "hours_together": round(total / 3600, 2),
        "entries": [entry_out(e, now) for e in entries],
        "transactions": [tx_out(t) for t in txs],
    }


@router.patch("/{person_id}")
def update_person(person_id: int, body: PersonPatch, user: CurrentUser, db: DB) -> dict:
    p = _own(db, user.id, person_id)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(p, k, v.strip() if k == "name" and isinstance(v, str) else v)
    db.commit()
    return person_out(p)


@router.delete("/{person_id}")
def delete_person(person_id: int, user: CurrentUser, db: DB) -> dict:
    db.delete(_own(db, user.id, person_id))
    db.commit()
    return {"ok": True}
