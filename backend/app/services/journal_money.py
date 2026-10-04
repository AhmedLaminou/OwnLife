"""Money from the journal: the amounts you wrote down, proposed as transactions.

A page is cut into clauses, and only the clauses that may speak of money (a
number with a currency, or a number next to words like bought, paid, taxi,
gives me) go to the language model — never the whole page. The model returns
each payment: what, how much, paid or received. They wait as suggestions on
the Money page until you add or dismiss them, or are added at once if you
chose so. A fingerprint per sentence and amount keeps a later scan from
proposing again what you already added or dismissed.

Scans run on demand (Money → Scan my journal) and by themselves from the
server's loop: a day is read once it has not changed for 10 minutes, so not
while you are still writing it, and at most every half hour.
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.llm import AIUnavailableError, chat_models, describe_error, effective_mode
from app.config import Settings
from app.db import SessionLocal, utcnow
from app.models import JournalEntry, MoneySuggestion, Profile, Transaction, User
from app.services import records
from app.services.activitywatch import get_state
from app.services.privacy import redact

log = logging.getLogger("ownlife")

PROVIDER = "journal_money"
DEFAULTS = {"journal_scan": True, "auto_add": False}
QUIET = timedelta(minutes=10)  # a day is read once it has not changed for this long
EVERY = timedelta(minutes=30)  # automatic scans at most this often
BATCH_CHARS = 6000  # excerpts per model request

_CURRENCY = r"(?:f\b|fcfa|cfa|francs?|frs?\b|xof|eur|€|usd|\$)"
_AMOUNT = re.compile(rf"\d[\d\s.,]*\s*{_CURRENCY}", re.I)
_WORDS = re.compile(
    r"\b(?:achet|pay|paid|bought|buy|spen[dt]|d[ée]pens|donn|gave|give|prix|price|co[uû]t|cost|taxi|"
    r"vend|sold|sell|re[cç]u|received|lend|lent|pr[êe]t|rembours|loan|borrow|emprunt|fee|frais|ticket|cr[ée]dit)",
    re.I,
)
_DIGIT = re.compile(r"\d")
_CLAUSE = re.compile(r"(?<=[;!?])\s+|(?<=\.)\s+(?=[A-Z0-9(])|\s+(?=//)")


def settings_with_defaults(prefs: dict | None) -> dict:
    return {**DEFAULTS, **(prefs or {})}


def money_clauses(body: str) -> list[str]:
    """The clauses of a page that may mention money: a number with a currency,
    or a number next to a word of buying, paying or giving."""
    out: list[str] = []
    for line in (body or "").splitlines():
        if not _DIGIT.search(line):
            continue
        for clause in _CLAUSE.split(line):
            c = " ".join(clause.split())
            if c and _DIGIT.search(c) and (_AMOUNT.search(c) or _WORDS.search(c)) and c not in out:
                out.append(c[:400])
    return out


def _hash(body: str | None) -> str:
    return hashlib.sha256(" ".join((body or "").split()).encode()).hexdigest()


def _fingerprint(day: date, quote: str, amount_minor: int, direction: str, n: int) -> str:
    key = f"{day.isoformat()}|{' '.join(quote.casefold().split())}|{amount_minor}|{direction}|{n}"
    return hashlib.sha256(key.encode()).hexdigest()


# ---------------------------------------------------------------- the model
class Payment(BaseModel):
    excerpt: int = Field(description="The number of the excerpt it comes from")
    item: str = Field(description="What it was for, in a few words: 'taxi', 'biscuits', 'gift from my uncle'")
    amount: float = Field(description="The total, a positive number: two pants at 1000 each is 2000")
    direction: Literal["out", "in"] = Field(description="out: the person paid; in: the person received")
    category: str = Field("other", description="transport, food, clothing, gift, education, health, phone, "
                                               "leisure or other")
    counterparty: str | None = Field(None, description="Who was paid, or who gave, when named")


class Payments(BaseModel):
    """The payments found in the excerpts."""

    payments: list[Payment] = Field(default_factory=list)


SYSTEM = """You read excerpts of {name}'s journal and list the money {name} paid or received.
- Only amounts written in the excerpt. An amount without a currency is in {currency} ("bought bread for 250").
- One line per payment: "biscuits for 50 and beans for 50" is two payments; "2 pants at 1000 each" is one payment of 2000.
- "X gives me 1000" is money {name} received (in).
- Money that someone else pays to a third person is not {name}'s, even in the same sentence: "a woman gave the
  driver 1000 to be dropped at her place" is nothing for {name}.
- Times, durations, ages, counts and dates are not money.
- An excerpt without any payment of {name} gives nothing."""

Excerpt = tuple[int, date, str]  # (journal entry id, its day, the clause)
Extractor = Callable[[Settings, User, Profile, list[Excerpt]], tuple[list[tuple[Excerpt, Payment]], str]]


def extract(settings: Settings, user: User, profile: Profile,
            excerpts: list[Excerpt]) -> tuple[list[tuple[Excerpt, Payment]], str]:
    """Asks the model, in batches of a few thousand characters. Returns each
    payment with the excerpt it came from, and the models used."""
    choices = chat_models(settings, profile, temperature=0)
    if not choices:
        raise AIUnavailableError("No model is configured: money cannot be read from the journal without one.")
    for_cloud = choices[0].is_cloud
    runnables = [
        c.llm.with_structured_output(Payments, method="function_calling" if c.is_cloud else "json_schema")
        for c in choices
    ]
    runnable = runnables[0].with_fallbacks(runnables[1:]) if len(runnables) > 1 else runnables[0]
    system = SYSTEM.format(name=user.display_name, currency=profile.currency)
    found: list[tuple[Excerpt, Payment]] = []
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
            result = Payments.model_validate(result)
        for p in result.payments:
            if 1 <= p.excerpt <= len(batch) and p.amount > 0:
                found.append((batch[p.excerpt - 1], p))
    return found, " → ".join(c.label for c in choices)


# ---------------------------------------------------------------- suggestions
def accept(db: Session, user_id: int, ids: list[int]) -> list[Transaction]:
    """Turns pending suggestions into transactions (source "journal")."""
    out = []
    for s in db.scalars(select(MoneySuggestion).where(
            MoneySuggestion.user_id == user_id, MoneySuggestion.id.in_(ids), MoneySuggestion.status == "pending")):
        try:
            tx = records.create_transaction(
                db, user_id, s.currency, occurred_on=s.entry_date, direction=s.direction,
                amount=records.from_minor(s.amount_minor, s.currency), item=s.item, category=s.category,
                counterparty=s.counterparty, note=f"From the journal: “{s.quote[:300]}”", source="journal",
            )
        except ValueError:
            continue
        s.status, s.transaction_id = "added", tx.id
        out.append(tx)
    db.flush()
    return out


def dismiss(db: Session, user_id: int, ids: list[int]) -> int:
    rows = db.scalars(select(MoneySuggestion).where(
        MoneySuggestion.user_id == user_id, MoneySuggestion.id.in_(ids), MoneySuggestion.status == "pending")).all()
    for s in rows:
        s.status = "dismissed"
    db.flush()
    return len(rows)


def _recorded_by_hand(db: Session, user_id: int, day: date, amount_minor: int, direction: str) -> bool:
    """The same amount, the same day, already entered another way (form, quick-log, capture)."""
    return db.scalar(select(Transaction.id).where(
        Transaction.user_id == user_id, Transaction.occurred_on == day, Transaction.amount_minor == amount_minor,
        Transaction.direction == direction, Transaction.source != "journal",
    ).limit(1)) is not None


@dataclass
class ScanResult:
    days: int = 0  # pages read
    excerpts: int = 0  # clauses sent to the model
    found: int = 0  # payments the model returned
    new: int = 0  # new suggestions
    added: int = 0  # added at once ("add without asking")
    model: str = ""


def changed_entries(db: Session, user_id: int, cursor: dict, quiet_before: datetime | None = None) -> list[JournalEntry]:
    """Pages whose text changed since they were last read (and, for the automatic
    scan, that have not changed for a while)."""
    rows = db.scalars(select(JournalEntry).where(JournalEntry.user_id == user_id, JournalEntry.visible())).all()
    out = [e for e in rows if cursor.get(str(e.id)) != _hash(e.body)]
    if quiet_before is not None:
        out = [e for e in out if (e.updated_at or e.created_at) <= quiet_before]
    return sorted(out, key=lambda e: e.entry_date)


def sends_to_cloud(settings: Settings, profile: Profile) -> bool:
    return any(c.is_cloud for c in chat_models(settings, profile))


def scan(settings: Settings, user_id: int, rescan_all: bool = False, extractor: Extractor | None = None) -> ScanResult:
    """Reads the changed pages and stores what the model finds. No database
    session is open while the model answers. A page marked private is never
    read when a cloud model is in the chain (it is counted as read, and read
    again only if it changes)."""
    with SessionLocal() as db:
        user, profile = db.get(User, user_id), db.get(Profile, user_id)
        state = get_state(db, user_id, PROVIDER)
        days = {} if rescan_all else dict((state.cursor or {}).get("days") or {})
        entries = changed_entries(db, user_id, days)
        cloud = sends_to_cloud(settings, profile)
        pages = [(e.id, e.entry_date, _hash(e.body), [] if cloud and e.is_private else money_clauses(e.body))
                 for e in entries]
        db.commit()
    excerpts: list[Excerpt] = [(eid, d, c) for eid, d, _, clauses in pages for c in clauses]
    result = ScanResult(days=len(pages), excerpts=len(excerpts))
    found: list[tuple[Excerpt, Payment]] = []
    if excerpts:
        found, result.model = (extractor or extract)(settings, user, profile, excerpts)
    result.found = len(found)

    with SessionLocal() as db:
        profile = db.get(Profile, user_id)
        entry_ids = [eid for eid, *_ in pages]
        existing = {s.fingerprint: s for s in db.scalars(select(MoneySuggestion).where(
            MoneySuggestion.user_id == user_id, MoneySuggestion.journal_entry_id.in_(entry_ids)))}
        keep: set[str] = set()
        seen: Counter = Counter()
        for (eid, day, quote), p in found:
            amount_minor = records.to_minor(p.amount, profile.currency)
            n = seen[(eid, quote, amount_minor, p.direction)]
            seen[(eid, quote, amount_minor, p.direction)] += 1
            fp = _fingerprint(day, quote, amount_minor, p.direction, n)
            keep.add(fp)
            if fp in existing or _recorded_by_hand(db, user_id, day, amount_minor, p.direction):
                continue  # pending stays pending; added and dismissed are not proposed again
            db.add(MoneySuggestion(
                user_id=user_id, entry_date=day, journal_entry_id=eid, item=(p.item.strip() or "?")[:200],
                amount_minor=amount_minor, currency=profile.currency, direction=p.direction,
                category=(p.category or "other").strip().lower()[:60] or "other",
                counterparty=(p.counterparty or "").strip()[:120] or None, quote=quote, fingerprint=fp,
            ))
            existing[fp] = None  # a second identical line in one answer is the same payment
            result.new += 1
        for fp, s in existing.items():
            if s is not None and s.status == "pending" and fp not in keep:
                db.delete(s)  # the sentence changed, or the model no longer sees an amount in it
        db.flush()
        if settings_with_defaults((profile.prefs or {}).get("money"))["auto_add"]:
            pending = db.scalars(select(MoneySuggestion.id).where(
                MoneySuggestion.user_id == user_id, MoneySuggestion.status == "pending")).all()
            result.added = len(accept(db, user_id, list(pending)))
        state = get_state(db, user_id, PROVIDER)
        cursor = dict(state.cursor or {})
        cursor["days"] = {**({} if rescan_all else dict(cursor.get("days") or {})),
                          **{str(eid): h for eid, _, h, _ in pages}}
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
    """Starts a scan in a thread, unless one is running. Returns whether one runs."""
    with _lock:
        if (_status.get(user_id) or {}).get("state") == "running":
            return True
        _status[user_id] = {"state": "running", "started_at": utcnow().isoformat()}
    threading.Thread(target=_run, args=(settings, user_id, rescan_all), daemon=True,
                     name=f"journal-money-{user_id}").start()
    return True


def _run(settings: Settings, user_id: int, rescan_all: bool) -> None:
    with SessionLocal() as db:  # an attempt counts even when it fails: no retry storm on a rate limit
        state = get_state(db, user_id, PROVIDER)
        state.cursor = {**(state.cursor or {}), "attempted_at": utcnow().isoformat()}
        db.commit()
    try:
        r = scan(settings, user_id, rescan_all)
        new = {"state": "idle", "error": None, "days": r.days, "excerpts": r.excerpts, "found": r.found,
               "new": r.new, "added": r.added, "model": r.model, "finished_at": utcnow().isoformat()}
    except Exception as e:  # offline, rate-limited, no model: say so; the next scan retries
        message = describe_error(e)
        log.warning("Money scan of the journal failed: %s", message)
        with SessionLocal() as db:
            get_state(db, user_id, PROVIDER).last_error = message
            db.commit()
        new = {"state": "error", "error": message, "finished_at": utcnow().isoformat()}
    with _lock:
        _status[user_id] = new


def tick(settings: Settings, now: datetime | None = None) -> list[int]:
    """From the server's loop: starts a scan for each person whose journal
    changed, once the change is 10 minutes old, at most every 30 minutes."""
    now = now or utcnow()
    started = []
    with SessionLocal() as db:
        for user in db.scalars(select(User).where(User.is_active.is_(True))):
            profile = db.get(Profile, user.id)
            if profile is None or not settings_with_defaults((profile.prefs or {}).get("money"))["journal_scan"]:
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
