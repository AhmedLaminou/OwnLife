"""Ideas from the journal, for the essays that wait for them.

The pages' thought sections — from a [SomeThoughts] marker (typos and
[KeyThoughts…] included) to the next [Tag] line, and lines starting with
"Idea :" — go to the online models of the chain (Nemotron first), with the
private aliases redacted and never a private page. Nothing else of the page is
sent. The model returns each idea: a title, the idea restated, the exact
passage (anchored again on the page, so what you place is always your own
words), its domain, the essay it belongs to (or a new one) and references to
authors who wrote on the same question. A reference is checked against Open
Library (only its author and title are sent): found, or marked unverified.

You decide on the Journal → Ideas tab: add it to an essay (a section appended
at the end, the essay's existing text never touched, the previous file kept,
undoable), start a new essay, keep it, or dismiss it.

Reading runs on demand, and — once you switch it on — by itself from the
server's loop: a page is read once its thought sections have been quiet for 10
minutes, at most every half hour. It is off by default: the thought sections
can be most of a journal.

Separately, and with no model at all: the passages of the journal closest in
meaning to each essay, from the search index's local embeddings.
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import httpx
import numpy as np
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai import rag
from app.ai.llm import AIUnavailableError, chat_models, compose, describe_error, effective_mode
from app.config import Settings, get_settings
from app.db import SessionLocal, utcnow
from app.models import Chunk, Idea, JournalEntry, Note, Profile, User
from app.services import filesync
from app.services.activitywatch import get_state
from app.services.privacy import redact

log = logging.getLogger("ownlife")

PROVIDER = "journal_ideas"
DEFAULTS = {"scan": False}  # off until you switch it on (Journal → Ideas)
QUIET = timedelta(minutes=10)
EVERY = timedelta(minutes=30)
BATCH_CHARS = 12000  # thought sections per request: the first read of a whole journal takes a few
DOMAINS = ("mathematics", "physics", "computer science", "ai", "robotics", "invention", "philosophy", "psychology",
           "society", "culture", "history", "geography", "literature", "religion", "other")

THOUGHT = re.compile(r"\[(?:Some|Key)\s*Thou?g?h?ts?[A-Za-z]*\]", re.I)
TAG_LINE = re.compile(r"^\s*\[[A-Za-z][A-Za-z ]*\]")
IDEA_LINE = re.compile(r"^\s*(?:\d+\)\s*)?idea\s*:", re.I)


def settings_with_defaults(prefs: dict | None) -> dict:
    return {**DEFAULTS, **(prefs or {})}


# ---------------------------------------------------------------- what is read
def thought_sections(body: str) -> list[str]:
    """The page's thought sections: from a thoughts marker line to the next line
    that starts with another [Tag], and "Idea :" paragraphs."""
    lines = (body or "").splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if TAG_LINE.match(line) and THOUGHT.search(line):
            j = i + 1
            while j < len(lines) and not (TAG_LINE.match(lines[j]) and not THOUGHT.search(lines[j])):
                j += 1
        elif IDEA_LINE.match(line):
            j = i + 1
            while j < len(lines) and lines[j].strip() and not TAG_LINE.match(lines[j]):
                j += 1
        else:
            i += 1
            continue
        text = "\n".join(lines[i:j]).strip()
        if len(text) > 40:
            out.append(text)
        i = j
    return out


def _pieces(text: str, limit: int = BATCH_CHARS) -> list[str]:
    """A section longer than a request, cut at blank lines (then at lines)."""
    if len(text) <= limit:
        return [text]
    out, cur = [], ""
    for para in re.split(r"\n\s*\n|\n(?=\s*\d+\))", text):
        while len(para) > limit:
            out.append(para[:limit])
            para = para[limit:]
        if cur and len(cur) + len(para) + 2 > limit:
            out.append(cur)
            cur = ""
        cur = f"{cur}\n\n{para}" if cur else para
    if cur:
        out.append(cur)
    return out


def _norm(s: str) -> str:
    return " ".join((s or "").split())


def _hash(sections: list[str]) -> str:
    return hashlib.sha256("\n\n".join(_norm(s) for s in sections).encode()).hexdigest()


def fingerprint(entry_id: int, quote: str) -> str:
    return hashlib.sha256(f"{entry_id}|{_norm(quote).casefold()[:300]}".encode()).hexdigest()


_SENTENCE = re.compile(r"(?<=[.!?;])\s+|\n+")
_WORD = re.compile(r"\w+")


def anchor(quote: str, text: str) -> str:
    """The writer's own words for what the model quoted: the quote itself when it
    is on the page, else the one to three sentences of the page that share the
    most words with it (the model saw redacted text, or rephrased)."""
    q = _norm(quote)
    if q and q in _norm(text):
        return q
    qw = set(_WORD.findall(q.casefold()))
    sentences = [s.strip() for s in _SENTENCE.split(text) if s.strip()]
    best, score = None, 0.0
    for i in range(len(sentences)):
        for n in (1, 2, 3):
            window = " ".join(sentences[i:i + n])
            ww = set(_WORD.findall(window.casefold()))
            if ww and qw:
                sc = len(qw & ww) / len(qw | ww)
                if sc > score:
                    best, score = window, sc
    return _norm(best) if best is not None and score >= 0.2 else q


def _key(title: str) -> str:
    return re.sub(r"[^0-9a-z]", "", (title or "").casefold())


def destinations(db: Session, user_id: int) -> list[Note]:
    """Where ideas can go: essays and notes (references and missing files excluded)."""
    return list(db.scalars(select(Note).where(Note.user_id == user_id, Note.visible(), Note.kind.in_(("essay", "note")))
                           .order_by(Note.title)))


# ---------------------------------------------------------------- the model
class Ref(BaseModel):
    author: str
    work: str = Field(description="The exact title of the book, essay or paper")
    why: str = Field(description="What it says on the same question, in under 20 words")


class FoundIdea(BaseModel):
    excerpt: int = Field(description="The number of the excerpt it comes from")
    title: str = Field(description="3 to 8 words naming the idea")
    statement: str = Field(description="The idea in one or two sentences, in the writer's voice and language")
    quote: str = Field(description="The sentence(s) of the excerpt that hold it, copied exactly")
    domain: str = Field(description=", ".join(DOMAINS))
    essay: str | None = Field(None, description="The title of the essay where it belongs, exactly as listed, or null")
    new_essay: str | None = Field(None, description="When no essay fits but the idea deserves one: a title like "
                                                    "'On Memory'; else null")
    references: list[Ref] = Field(default_factory=list)


class FoundIdeas(BaseModel):
    """The ideas found in the excerpts."""

    ideas: list[FoundIdea] = Field(default_factory=list)


SYSTEM = """You read excerpts of {name}'s journal — the sections where {name} writes thoughts — and pick out the IDEAS: thoughts worth keeping beyond the day. A theory, a hypothesis, an observation about people or society, a reflection on philosophy, psychology, culture, history, geography or religion, a question about mathematics, physics, computer science or AI, an invention or a project.

Not ideas: what {name} did, ate, watched or spent; plans and to-dos; moods, unless they come with a general thought.

For each idea:
- excerpt: its number;
- title: 3 to 8 words;
- statement: the idea itself in one or two sentences, in {name}'s voice and the excerpt's language;
- quote: the sentence(s) of the excerpt that hold it, copied exactly;
- domain: one of {domains};
- essay: the essay below where it belongs, its title exactly as written, or null;
- new_essay: when none fits but the idea deserves a home, a title in the same style ("On Memory"), else null;
- references: up to 3 well-known authors who wrote about the same question — the exact title of their work and what it says, in under 20 words. Only works you are sure exist; none is better than an invented one.

{name}'s essays:
{essays}

Words in [brackets] or {{braces}} are {name}'s own tags and aliases: keep them as written, never guess what they mean. Most excerpts hold 0 to 5 ideas; return none when there is none."""

Excerpt = tuple[int, date, int | None, str]  # (journal entry id, its day, its day number, the section)
Extractor = Callable[[Settings, User, Profile, list[Note], list[Excerpt]], tuple[list[tuple[Excerpt, FoundIdea]], str]]


def _essays_block(notes: list[Note], profile: Profile | None = None) -> str:
    """Each essay's title and first words (redacted), so the model knows what
    it is about. A private essay is not listed."""
    lines = []
    for n in notes:
        if n.is_private:
            continue
        body = redact(_norm(n.body)[:160], profile)
        lines.append(f"- {n.title}" + (f": {body}…" if body else " (empty so far)"))
    return "\n".join(lines) or "(none yet)"


def online_models(settings: Settings, profile: Profile):
    return [c for c in chat_models(settings, profile, temperature=0.2) if c.is_cloud]


def batches(excerpts: list[Excerpt]) -> list[list[Excerpt]]:
    """Excerpts grouped into requests of about BATCH_CHARS characters."""
    out: list[list[Excerpt]] = [[]]
    for ex in excerpts:
        if out[-1] and sum(len(x[3]) for x in out[-1]) + len(ex[3]) > BATCH_CHARS:
            out.append([])
        out[-1].append(ex)
    return [b for b in out if b]


def extract(settings: Settings, user: User, profile: Profile, notes: list[Note],
            batch: list[Excerpt]) -> tuple[list[tuple[Excerpt, FoundIdea]], str]:
    """One request: the ideas of these excerpts, each with the excerpt it came from."""
    choices = online_models(settings, profile)
    if not choices:
        raise AIUnavailableError("Reading ideas needs an online model (OpenRouter key and an AI mode that uses it): "
                                 "the local one is too slow for whole sections.")
    runnable = compose(choices, lambda m: m.with_structured_output(FoundIdeas, method="function_calling"))
    system = SYSTEM.format(name=user.display_name, domains=", ".join(DOMAINS), essays=_essays_block(notes, profile))
    parts = [f"[{i}] Day {n or '?'} — {d.isoformat()}\n{redact(text, profile)}"
             for i, (_, d, n, text) in enumerate(batch, 1)]
    result = runnable.invoke([SystemMessage(system), HumanMessage("\n\n".join(parts))])
    if isinstance(result, dict):
        result = FoundIdeas.model_validate(result)
    found = [(batch[x.excerpt - 1], x) for x in result.ideas if 1 <= x.excerpt <= len(batch)]
    return found, " → ".join(c.label for c in choices)


# ---------------------------------------------------------------- references, checked
_checked: dict[tuple[str, str], tuple[bool | None, str | None]] = {}


def check_reference(author: str, work: str, get=httpx.get) -> tuple[bool | None, str | None]:
    """(found, link) in Open Library; (None, None) when it could not be asked."""
    key = (_norm(author).casefold(), _norm(work).casefold())
    if key in _checked:
        return _checked[key]
    try:
        r = get("https://openlibrary.org/search.json", timeout=8,
                params={"title": work, "author": author, "limit": 1, "fields": "key,title,author_name"},
                headers={"User-Agent": "OwnLife/0.2 (a personal journal tool)"})
        if r.status_code != 200:
            return None, None
        docs = r.json().get("docs") or []
        result = (True, f"https://openlibrary.org{docs[0]['key']}") if docs and docs[0].get("key") else (False, None)
    except (httpx.HTTPError, ValueError, KeyError):
        return None, None
    _checked[key] = result
    return result


def check_references(user_id: int, idea_ids: list[int], get=httpx.get) -> int:
    """Checks the references of these ideas; no session is open while asking."""
    with SessionLocal() as db:
        todo = {i.id: list(i.refs or []) for i in db.scalars(select(Idea).where(Idea.user_id == user_id, Idea.id.in_(idea_ids)))}
    checked = 0
    for refs in todo.values():
        for ref in refs:
            if ref.get("verified") is None and ref.get("author") and ref.get("work"):
                ref["verified"], ref["url"] = check_reference(ref["author"], ref["work"], get)
                checked += 1
    with SessionLocal() as db:
        for iid, refs in todo.items():
            idea = db.get(Idea, iid)
            if idea is not None:
                idea.refs = refs
        db.commit()
    return checked


# ---------------------------------------------------------------- reading
@dataclass
class ScanResult:
    days: int = 0
    sections: int = 0
    chars: int = 0
    found: int = 0
    new: int = 0
    model: str = ""


def changed_entries(db: Session, user_id: int, cursor: dict, quiet_before: datetime | None = None) -> list[JournalEntry]:
    rows = db.scalars(select(JournalEntry).where(JournalEntry.user_id == user_id, JournalEntry.visible())).all()
    out = [e for e in rows if thought_sections(e.body) and cursor.get(str(e.id)) != _hash(thought_sections(e.body))]
    if quiet_before is not None:
        out = [e for e in out if (e.updated_at or e.created_at) <= quiet_before]
    return sorted(out, key=lambda e: e.entry_date)


def _store(user_id: int, found: list[tuple[Excerpt, FoundIdea]], model: str) -> list[int]:
    """Saves the ideas of one request; returns the ids of the new ones."""
    created: list[int] = []
    with SessionLocal() as db:
        by_key = {_key(n.title): n.id for n in destinations(db, user_id)}
        existing = {i.fingerprint for i in db.scalars(select(Idea).where(Idea.user_id == user_id))}
        for (eid, d, n, text), x in found:
            quote = anchor(x.quote, text)
            fp = fingerprint(eid, quote)
            if not quote or fp in existing:
                continue  # already proposed: new, kept, placed or dismissed, it is not proposed again
            existing.add(fp)
            domain = (x.domain or "other").strip().lower()
            idea = Idea(
                user_id=user_id, journal_entry_id=eid, entry_date=d, day_number=n,
                title=_norm(x.title)[:200] or quote[:60], statement=_norm(x.statement) or quote, quote=quote,
                domain=domain if domain in DOMAINS else "other",
                note_id=by_key.get(_key(x.essay or "")) if x.essay else None,
                new_essay=None if x.essay and _key(x.essay) in by_key else (_norm(x.new_essay or "")[:200] or None),
                refs=[{"author": _norm(r.author)[:120], "work": _norm(r.work)[:200], "why": _norm(r.why)[:200],
                       "verified": None, "url": None} for r in x.references[:3] if r.author and r.work],
                fingerprint=fp, model=model[:300] or None,
            )
            db.add(idea)
            db.flush()
            created.append(idea.id)
        db.commit()
    return created


def _mark_read(user_id: int, hashes: dict[str, str], rescan_all: bool) -> None:
    with SessionLocal() as db:
        state = get_state(db, user_id, PROVIDER)
        cursor = dict(state.cursor or {})
        cursor["days"] = {**({} if rescan_all else dict(cursor.get("days") or {})), **hashes}
        state.cursor = cursor
        state.last_synced_at, state.last_error = utcnow(), None
        db.commit()


def scan(settings: Settings, user_id: int, rescan_all: bool = False, extractor: Extractor | None = None,
         get=httpx.get) -> ScanResult:
    """Reads the pages whose thought sections changed and stores the ideas found,
    one request at a time: a page counts as read once all its sections were
    answered, so a rate limit halfway through keeps what was done and the next
    run goes on from there. No database session is open while the model
    answers. A private page is never read (it counts as read, and is read again
    only if it changes)."""
    with SessionLocal() as db:
        user, profile = db.get(User, user_id), db.get(Profile, user_id)
        state = get_state(db, user_id, PROVIDER)
        days = {} if rescan_all else dict((state.cursor or {}).get("days") or {})
        pages = [(e.id, e.entry_date, e.day_number, thought_sections(e.body), e.is_private)
                 for e in changed_entries(db, user_id, days)]
        notes = destinations(db, user_id)
        for n in notes:
            db.expunge(n)
        db.commit()
    excerpts: list[Excerpt] = [(eid, d, n, piece) for eid, d, n, sections, private in pages if not private
                               for s in sections for piece in _pieces(s)]
    result = ScanResult(days=len(pages), sections=len(excerpts), chars=sum(len(x[3]) for x in excerpts))
    left = {eid: 0 for eid, *_ in pages}  # requests still to answer, per page
    for ex in excerpts:
        left[ex[0]] += 1
    hashes = {eid: _hash(sections) for eid, _, _, sections, _ in pages}
    done = {str(eid): hashes[eid] for eid, n in left.items() if n == 0}  # private pages, nothing to send
    created: list[int] = []
    try:
        for batch in batches(excerpts):
            found, result.model = (extractor or extract)(settings, user, profile, notes, batch)
            result.found += len(found)
            created += _store(user_id, found, result.model)
            for ex in batch:
                left[ex[0]] -= 1
                if left[ex[0]] == 0:
                    done[str(ex[0])] = hashes[ex[0]]
    finally:
        _mark_read(user_id, done, rescan_all)
        result.new = len(created)
        if created:
            check_references(user_id, created, get)
    return result


# ---------------------------------------------------------------- into an essay
class PlaceError(Exception):
    pass


def section_text(idea: Idea, text: str) -> str:
    """The section appended to an essay: the idea's title, your words, where they
    come from, and the references found in Open Library."""
    when = f"{idea.entry_date:%d %B %Y}".lstrip("0")
    source = f"*From Day {idea.day_number}, {when}.*" if idea.day_number else f"*From the journal, {when}.*"
    refs = [r for r in idea.refs or [] if r.get("verified")]
    see = (" See also: " + "; ".join(f"{r['author']}, *{r['work']}*" for r in refs) + ".") if refs else ""
    return f"## {idea.title.strip()}\n\n{text.strip()}\n\n{source}{see}"


def place(db: Session, user_id: int, idea: Idea, cfg, *, note_id: int | None = None, new_title: str | None = None,
          text: str | None = None, today: date | None = None) -> dict:
    """Appends the idea to an essay (or a new one), writes the file, indexes it."""
    if idea.status == "placed":
        raise PlaceError("Already placed: undo it first to place it elsewhere.")
    words = (text or idea.quote or "").strip()
    if not words:
        raise PlaceError("Nothing to add.")
    section = section_text(idea, words)
    if new_title and new_title.strip():
        title = _norm(new_title)[:200]
        if any(_key(n.title) == _key(title) for n in destinations(db, user_id)):
            raise PlaceError(f"“{title}” already exists: choose it in the list.")
        note = Note(user_id=user_id, title=title, kind="essay", body=section)
        db.add(note)
        db.flush()
    else:
        note = db.get(Note, note_id) if note_id else None
        if note is None or note.user_id != user_id:
            raise PlaceError("Choose an essay.")
        if note.sync_state == "conflict":
            raise PlaceError(f"“{note.title}” has a conflict to resolve first (Journal → Notes).")
        body = (note.body or "").rstrip()
        note.body = f"{body}\n\n{section}" if body else section
    sync = filesync.push_note(db, cfg, note, today or date.today())
    if sync["state"] == "conflict":  # nothing is committed: the essay stays as it was
        raise PlaceError(f"“{note.title}” changed in its file at the same time: sync or resolve it (Journal → Notes), "
                         "then add the idea again.")
    rag.index_note(db, note)
    idea.status, idea.placed_note_id, idea.placed_text, idea.placed_at = "placed", note.id, section, utcnow()
    db.flush()
    return {"note": {"id": note.id, "title": note.title}, "sync": sync}


def unplace(db: Session, user_id: int, idea_id: int, cfg, today: date | None = None) -> str:
    """Removes the section an idea added, if the essay still ends with it."""
    from app.services.undo import UndoError

    idea = db.get(Idea, idea_id)
    if idea is None or idea.user_id != user_id:
        return "Already gone."
    note = db.get(Note, idea.placed_note_id) if idea.placed_note_id else None
    if idea.status != "placed" or note is None:
        return "It is not in an essay any more."
    body, section = (note.body or "").rstrip(), (idea.placed_text or "").strip()
    if not section or not body.endswith(section):
        raise UndoError(f"“{note.title}” was edited after the idea was added: remove its section by hand.")
    note.body = body[: len(body) - len(section)].rstrip()
    if filesync.push_note(db, cfg, note, today or date.today())["state"] == "conflict":
        raise UndoError(f"“{note.title}” changed in its file at the same time: sync or resolve it first.")
    rag.index_note(db, note)
    idea.status, idea.placed_note_id, idea.placed_text, idea.placed_at = "new", None, None, None
    db.flush()
    return f"Removed from “{note.title}”."


# ---------------------------------------------------------------- passages for each essay, locally
def essay_passages(db: Session, user_id: int, embedder, note: Note, k: int = 3, floor: float = 0.3) -> list[dict]:
    """The journal passages closest in meaning to an essay (its title and its
    first lines), from the search index's local embeddings — no model."""
    index = rag._vectors(db, user_id, embedder.name)
    if len(index.ids) == 0:
        return []
    sims = index.matrix @ embedder.embed_query_np(f"{note.title}. {_norm(note.body)[:400]}")
    order = [int(i) for i in np.argsort(-sims)[:40]]
    chunks = {c.id: c for c in db.scalars(select(Chunk).where(Chunk.id.in_([int(index.ids[i]) for i in order])))}
    out, seen = [], set()
    for i in order:
        c = chunks.get(int(index.ids[i]))
        if c is None or c.source_type != "journal" or sims[i] < floor or c.source_id in seen:
            continue
        seen.add(c.source_id)
        out.append({"journal_entry_id": c.source_id, "day_number": c.meta.get("day_number"), "date": c.meta.get("date"),
                    "text": _norm(c.text)[:420], "score": round(float(sims[i]), 2)})
        if len(out) >= k:
            break
    return out


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
                     name=f"journal-ideas-{user_id}").start()
    return True


def _run(settings: Settings, user_id: int, rescan_all: bool) -> None:
    with SessionLocal() as db:  # an attempt counts even when it fails: no retry storm on a rate limit
        state = get_state(db, user_id, PROVIDER)
        state.cursor = {**(state.cursor or {}), "attempted_at": utcnow().isoformat()}
        db.commit()
    try:
        r = scan(settings, user_id, rescan_all)
        new = {"state": "idle", "error": None, "days": r.days, "sections": r.sections, "chars": r.chars,
               "found": r.found, "new": r.new, "model": r.model, "finished_at": utcnow().isoformat()}
    except Exception as e:
        message = describe_error(e)
        log.warning("Reading ideas from the journal failed: %s", message)
        with SessionLocal() as db:
            get_state(db, user_id, PROVIDER).last_error = message
            db.commit()
        new = {"state": "error", "error": message, "finished_at": utcnow().isoformat()}
    with _lock:
        _status[user_id] = new


def tick(settings: Settings, now: datetime | None = None) -> list[int]:
    """From the server's loop, for whoever switched it on."""
    now = now or utcnow()
    started = []
    with SessionLocal() as db:
        for user in db.scalars(select(User).where(User.is_active.is_(True))):
            profile = db.get(Profile, user.id)
            if profile is None or not settings_with_defaults((profile.prefs or {}).get("ideas"))["scan"]:
                continue
            if effective_mode(settings, profile) == "off" or not online_models(settings, profile):
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


def config():
    return filesync.config(get_settings())
