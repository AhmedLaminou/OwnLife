"""Money in and out, in the profile currency."""

from __future__ import annotations

import datetime as dt

from datetime import date, timedelta
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.ai.llm import effective_mode
from app.config import get_settings
from app.deps import DB, CurrentProfile, CurrentUser
from app.models import MoneySuggestion, Person, Transaction
from app.serializers import iso, money_suggestion_out, tx_out
from app.services import journal_money, records
from app.services import people as people_service
from app.services.activitywatch import get_state
from app.services.records import from_minor, to_minor
from app.services.timeutil import local_today, tz_of

router = APIRouter(prefix="/api/money", tags=["money"])


class TxIn(BaseModel):
    date: dt.date
    direction: Literal["in", "out"] = "out"
    amount: float = Field(gt=0)
    item: str = Field(min_length=1, max_length=200)
    category: str = Field("other", max_length=60)
    counterparty: str | None = Field(None, max_length=120)
    # someone you know who gave or received it: linked, or added to People
    person: str | None = Field(None, max_length=120)
    note: str | None = None


class TxPatch(BaseModel):
    date: dt.date | None = None
    direction: Literal["in", "out"] | None = None
    amount: float | None = Field(None, gt=0)
    item: str | None = Field(None, min_length=1, max_length=200)
    category: str | None = Field(None, max_length=60)
    counterparty: str | None = Field(None, max_length=120)
    note: str | None = None
    person_id: int | None = None  # link to this person; null unlinks
    person: str | None = Field(None, max_length=120)  # or by name (added to People when new)


class JournalPrefs(BaseModel):
    journal_scan: bool = True
    auto_add: bool = False


class ScanIn(BaseModel):
    all: bool = False  # read every page again, not only the changed ones


class IdsIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=1000)


def _own(db, user_id: int, tx_id: int) -> Transaction:
    t = db.get(Transaction, tx_id)
    if t is None or t.user_id != user_id:
        raise HTTPException(404, "Transaction not found")
    return t


def _range(profile, start: date | None, end: date | None) -> tuple[date, date]:
    today = local_today(tz_of(profile.timezone))
    end = end or today
    return start or end - timedelta(days=29), end


@router.get("")
def list_tx(user: CurrentUser, profile: CurrentProfile, db: DB, start: date | None = None, end: date | None = None) -> list[dict]:
    s, e = _range(profile, start, end)
    rows = db.scalars(
        select(Transaction)
        .where(Transaction.user_id == user.id, Transaction.occurred_on >= s, Transaction.occurred_on <= e)
        .order_by(Transaction.occurred_on.desc(), Transaction.id.desc())
    ).all()
    return [tx_out(t) for t in rows]


@router.post("")
def create_tx(body: TxIn, user: CurrentUser, profile: CurrentProfile, db: DB) -> dict:
    warnings: list[str] = []
    try:
        t = records.create_transaction(
            db, user.id, profile.currency, occurred_on=body.date, direction=body.direction, amount=body.amount,
            item=body.item, category=body.category, counterparty=body.counterparty, note=body.note,
            person=body.person, warnings=warnings,
        )
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    db.commit()
    return {**tx_out(t), "warnings": warnings}


@router.patch("/{tx_id}")
def update_tx(tx_id: int, body: TxPatch, user: CurrentUser, db: DB) -> dict:
    t = _own(db, user.id, tx_id)
    data = body.model_dump(exclude_unset=True)
    warnings: list[str] = []
    if "amount" in data:
        t.amount_minor = to_minor(data.pop("amount"), t.currency)
    if "date" in data:
        t.occurred_on = data.pop("date")
    if "person_id" in data:
        pid = data.pop("person_id")
        if pid is not None:
            p = db.get(Person, pid)
            if p is None or p.user_id != user.id:
                raise HTTPException(404, "Person not found")
        t.person_id = pid
    if "person" in data:
        name = (data.pop("person") or "").strip()
        if not name:
            t.person_id = None
        else:
            found = people_service.link(db, user.id, name)
            if found.warning:
                warnings.append(found.warning)
            t.person_id = found.person.id if found.person else t.person_id
            t.counterparty = t.counterparty or name
    for k, v in data.items():
        setattr(t, k, v)
    db.commit()
    db.refresh(t)
    return {**tx_out(t), "warnings": warnings}


@router.delete("/{tx_id}")
def delete_tx(tx_id: int, user: CurrentUser, db: DB) -> dict:
    db.delete(_own(db, user.id, tx_id))
    db.commit()
    return {"ok": True}


@router.get("/summary")
def summary(user: CurrentUser, profile: CurrentProfile, db: DB, start: date | None = None, end: date | None = None) -> dict:
    s, e = _range(profile, start, end)
    rows = db.scalars(
        select(Transaction).where(
            Transaction.user_id == user.id, Transaction.occurred_on >= s, Transaction.occurred_on <= e
        )
    ).all()
    out_by_cat: dict[str, float] = {}
    by_day: dict[str, dict] = {}
    total_out = total_in = 0.0
    for t in rows:
        amount = from_minor(t.amount_minor, t.currency)
        day = by_day.setdefault(t.occurred_on.isoformat(), {"date": t.occurred_on.isoformat(), "out": 0.0, "in": 0.0})
        day[t.direction] += amount
        if t.direction == "out":
            total_out += amount
            out_by_cat[t.category] = out_by_cat.get(t.category, 0.0) + amount
        else:
            total_in += amount
    days = max(1, (e - s).days + 1)
    return {
        "start": s.isoformat(),
        "end": e.isoformat(),
        "currency": profile.currency,
        "total_out": total_out,
        "total_in": total_in,
        "net": total_in - total_out,
        "avg_out_per_day": round(total_out / days, 2),
        "out_by_category": [{"category": c, "amount": a} for c, a in sorted(out_by_cat.items(), key=lambda kv: -kv[1])],
        "by_day": sorted(by_day.values(), key=lambda d: d["date"]),
    }


# ---------------------------------------------------------------- from the journal
@router.get("/journal")
def journal_money_state(user: CurrentUser, profile: CurrentProfile, db: DB) -> dict:
    """The amounts found in the journal and waiting for an answer, and the scanner's state."""
    rows = db.scalars(
        select(MoneySuggestion)
        .where(MoneySuggestion.user_id == user.id, MoneySuggestion.status == "pending")
        .order_by(MoneySuggestion.entry_date.desc(), MoneySuggestion.id)
    ).all()
    state = get_state(db, user.id, journal_money.PROVIDER)
    waiting = journal_money.changed_entries(db, user.id, dict((state.cursor or {}).get("days") or {}))
    db.commit()
    return {
        "suggestions": [money_suggestion_out(s) for s in rows],
        "prefs": journal_money.settings_with_defaults((profile.prefs or {}).get("money")),
        "scan": journal_money.status(user.id),
        "last_scan": iso(state.last_synced_at),
        "last_error": state.last_error,
        "days_not_read": len(waiting),
        "ai_off": effective_mode(get_settings(), profile) == "off",
    }


@router.post("/journal/scan")
def journal_money_scan(body: ScanIn, user: CurrentUser, profile: CurrentProfile) -> dict:
    if effective_mode(get_settings(), profile) == "off":
        raise HTTPException(400, "AI is off (Settings → AI): reading money from the journal needs a model.")
    journal_money.schedule(get_settings(), user.id, rescan_all=body.all)
    return journal_money.status(user.id)


@router.post("/journal/accept")
def journal_money_accept(body: IdsIn, user: CurrentUser, db: DB) -> dict:
    added = journal_money.accept(db, user.id, body.ids)
    db.commit()
    return {"added": len(added)}


@router.post("/journal/dismiss")
def journal_money_dismiss(body: IdsIn, user: CurrentUser, db: DB) -> dict:
    n = journal_money.dismiss(db, user.id, body.ids)
    db.commit()
    return {"dismissed": n}


@router.put("/journal/prefs")
def journal_money_prefs(body: JournalPrefs, profile: CurrentProfile, db: DB) -> dict:
    profile.prefs = {**(profile.prefs or {}), "money": body.model_dump()}
    db.commit()
    return journal_money.settings_with_defaults(profile.prefs["money"])
