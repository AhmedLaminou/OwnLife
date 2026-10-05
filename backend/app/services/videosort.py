"""Sorting YouTube videos that no rule places, with the local model.

A video measured by the extension that matches no rule (a new channel, a
channel of many subjects) is shown to the Ollama model on this machine — its
title and channel, nothing else, and never to a cloud model — which picks one
of your categories:

    "Eigenvectors, visually" · SomeMathChannel      →  Mathematics   (sure)
    "Election night: live results" · SomeNewsTV     →  News & geopolitics
    "Episode 112 with Dr X" · SomePodcast           →  a guess, not sure

Before that, a video that arrived without its channel (the page did not show
it and the internet was down) gets it from YouTube's oEmbed: a channel rule
may then place it without any model.

A sure answer applies at once; your rules still come first. An unsure one is
kept as a guess: the video stays "not sorted yet" (noise, in strict mode) and
waits for you on the Watching page, where one click confirms or corrects it. A
category you choose for a video wins over every rule.

The sorter runs in a background thread shortly after the extension sends
videos nothing places, a few dozen at a time, at most every minute; after an
error (Ollama not running) it waits 10 minutes.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.ai.llm import describe_error, effective_mode, local_model
from app.ai.prompts import _categories_block
from app.config import Settings
from app.db import SessionLocal, utcnow
from app.models import Category, MediaItem, Profile, TimeEntry, WatchEvent
from app.services import watchlive
from app.services.rules import Activity, RuleSet, load_ruleset
from app.services.youtube import _cached, _remember, oembed

log = logging.getLogger("ownlife")

RECENT = timedelta(days=7)  # videos watched this recently are sorted by the model
BATCH = 25
MAX_BATCHES = 4  # per run: 100 videos
EVERY = timedelta(minutes=1)
AFTER_ERROR = timedelta(minutes=10)


@dataclass
class Video:
    video_id: str
    title: str
    channel: str | None
    seconds: float = 0.0
    last: datetime | None = None


def watched_videos(db: Session, user_id: int, since: datetime) -> dict[str, Video]:
    """The videos the extension measured since then, with their minutes."""
    out: dict[str, Video] = {}
    for seg in db.scalars(select(WatchEvent).where(
            WatchEvent.user_id == user_id, WatchEvent.source == watchlive.SOURCE,
            WatchEvent.video_id.is_not(None), WatchEvent.occurred_at >= since).order_by(WatchEvent.occurred_at)):
        v = out.get(seg.video_id)
        if v is None:
            v = out[seg.video_id] = Video(seg.video_id, seg.title, seg.channel)
        v.seconds += seg.seconds or 0.0
        v.last = seg.ended_at or seg.occurred_at
        v.title = seg.title or v.title
        v.channel = seg.channel or v.channel
    return out


def _url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def verdicts(db: Session, user_id: int) -> dict[str, MediaItem]:
    return {m.external_id: m for m in db.scalars(select(MediaItem).where(
        MediaItem.user_id == user_id, MediaItem.kind == "video", MediaItem.sorted_by.is_not(None)))}


def to_sort(db: Session, user_id: int, ruleset: RuleSet, since: datetime) -> list[Video]:
    """Watched videos that nothing places and the model has not seen yet."""
    seen = verdicts(db, user_id)
    return [v for v in watched_videos(db, user_id, since).values()
            if v.video_id not in seen and ruleset.classify(Activity(title=v.title, channel=v.channel,
                                                                    url=_url(v.video_id))) is None]


def choices(db: Session, user_id: int) -> list[Category]:
    """What the model may answer: your categories, minus the private ones and
    the "not sorted yet" one."""
    return [c for c in db.scalars(select(Category).where(Category.user_id == user_id).order_by(Category.sort, Category.id))
            if not c.is_private and not c.archived and c.name != watchlive.UNSORTED]


def remember(db: Session, user_id: int, v: Video, category_id: int | None, who: str) -> None:
    """Stores a video's category: who = "you" | "model" | "guess" (unsure)."""
    db.execute(sqlite_insert(MediaItem).values(
        user_id=user_id, kind="video", external_id=v.video_id, title=(v.title or v.video_id)[:300],
        creator=v.channel, url=_url(v.video_id), status="none",
        created_at=utcnow(), updated_at=utcnow()).on_conflict_do_nothing())
    item = db.scalar(select(MediaItem).where(MediaItem.user_id == user_id, MediaItem.kind == "video",
                                             MediaItem.external_id == v.video_id))
    item.category_id, item.sorted_by = category_id, who
    db.flush()


# ---------------------------------------------------------------- the model
class Verdict(BaseModel):
    n: int = Field(description="The video's number")
    category: str | None = Field(description="A category name exactly as listed, or null")
    sure: bool = Field(description="false when the title and channel cannot tell")


class Verdicts(BaseModel):
    videos: list[Verdict]


SYSTEM = """You sort YouTube videos into the categories of a personal time ledger, from their title and channel only.

Categories — the word in brackets is only the kind, never part of the name:
{categories}

For each numbered video, answer its number, one category name exactly as written above, and whether you are sure.
- A core or growth (learning) category only when the video teaches or explains: a lecture, a course, a tutorial, a solved problem, a concept explained, a scientist's talk, a book, a period of history or a school of thought explained.
- News, current events, politics, geopolitics, wars, elections, and debates or commentary about them, go to the noise category about news.
- Reactions, entertainment, pranks, vlogs, gaming, music, anime, series, films and clips go to the matching noise category.
- Recitation, sermons and lessons about the religion go to the spirit category.
- sure is false when the title and channel cannot tell (an interview or a podcast whose subject is unclear, a vague title). Give your best guess anyway."""


Asker = Callable[[list[Category], list[Video]], list[tuple[str, int | None, bool]]]


def ask_model(settings: Settings) -> tuple[Asker, str]:
    model = local_model(settings, temperature=0)
    runnable = model.llm.with_structured_output(Verdicts, method="json_schema")

    def ask(categories: list[Category], videos: list[Video]) -> list[tuple[str, int | None, bool]]:
        by_name = {c.name.casefold(): c.id for c in categories}
        lines = [f"[{i}] {v.title}" + (f" — channel: {v.channel}" if v.channel else "") for i, v in enumerate(videos, 1)]
        result = runnable.invoke([SystemMessage(SYSTEM.format(categories=_categories_block(categories))),
                                  HumanMessage("\n".join(lines))])
        if isinstance(result, dict):
            result = Verdicts.model_validate(result)
        out = []
        for x in result.videos:
            if not 1 <= x.n <= len(videos):
                continue
            name = (x.category or "").split(" (")[0].strip().casefold()
            cat = by_name.get(name)
            out.append((videos[x.n - 1].video_id, cat, bool(x.sure) and cat is not None))
        return out

    return ask, model.label


# ---------------------------------------------------------------- channels that did not arrive
_no_channel: set[str] = set()  # looked up in vain (private, removed): not again in this run of the server


def fill_channels(settings: Settings, user_id: int, since: datetime, lookup=oembed) -> set[str]:
    """Finds the channel of recent measured videos that came without one.
    Returns the videos that got it. No session is open during the lookups."""
    if not settings.youtube_oembed:
        return set()
    with SessionLocal() as db:
        missing = [v for v in db.scalars(select(WatchEvent.video_id).where(
            WatchEvent.user_id == user_id, WatchEvent.source == watchlive.SOURCE, WatchEvent.channel.is_(None),
            WatchEvent.video_id.is_not(None), WatchEvent.occurred_at >= since).distinct()) if v not in _no_channel][:30]
        names = {}
        for vid in missing:
            item = _cached(db, user_id, vid)
            if item is not None and item.creator:
                names[vid] = item.creator
    todo = [v for v in missing if v not in names]
    found: dict[str, dict] = {}
    if todo:
        with ThreadPoolExecutor(max_workers=6) as pool:
            for vid, data in zip(todo, pool.map(lookup, todo)):
                if data and data.get("author_name"):
                    found[vid] = data
                    names[vid] = data["author_name"]
                else:
                    _no_channel.add(vid)
    if not names:
        return set()
    with SessionLocal() as db:
        for vid, data in found.items():
            _remember(db, user_id, vid, data)
            item = _cached(db, user_id, vid)
            if item is not None and not item.creator:
                item.creator = data["author_name"]
        for vid, name in names.items():
            db.execute(update(WatchEvent).where(WatchEvent.user_id == user_id, WatchEvent.source == watchlive.SOURCE,
                                                WatchEvent.video_id == vid, WatchEvent.channel.is_(None))
                       .values(channel=name))
        db.commit()
    return set(names)


# ---------------------------------------------------------------- the run
@dataclass
class SortResult:
    asked: int = 0
    sorted: int = 0  # sure answers, applied
    guesses: int = 0  # unsure: waiting for you
    refiled: int = 0  # measured segments re-filed in the ledger
    model: str = ""
    errors: list[str] = field(default_factory=list)


def refile(db: Session, user_id: int, video_ids: set[str], *, unsorted_too: bool = True) -> int:
    """Re-files the ledger blocks of these videos (their category changed) —
    and the blocks without a category, which strict mode now files. From the
    earliest block concerned to now."""
    starts = [t for t in db.scalars(select(WatchEvent.occurred_at).where(
        WatchEvent.user_id == user_id, WatchEvent.source == watchlive.SOURCE,
        WatchEvent.video_id.in_(video_ids)))] if video_ids else []
    if unsorted_too and watchlive.strict_category(db, user_id) is not None:
        starts += list(db.scalars(select(TimeEntry.started_at).where(
            TimeEntry.user_id == user_id, TimeEntry.source == watchlive.SOURCE,
            TimeEntry.category_id.is_(None), TimeEntry.category_locked.is_(False))))
    if not starts:
        return 0
    since = min(starts)
    holding = db.scalars(select(TimeEntry.started_at).where(  # a block holding them may have started earlier
        TimeEntry.user_id == user_id, TimeEntry.source == watchlive.SOURCE,
        TimeEntry.started_at < since, TimeEntry.ended_at >= since - watchlive.MERGE_GAP))
    since = min([since, *holding])
    with watchlive.LOCK:
        n = watchlive.rebuild(db, user_id, load_ruleset(db, user_id), since)
        db.commit()
    return n


def sort(settings: Settings, user_id: int, asker: Asker | None = None, label: str = "") -> SortResult:
    """Asks the model about the recent videos nothing places, stores the
    answers, and re-files the blocks they change. No session stays open while
    the model thinks."""
    res = SortResult()
    changed: set[str] = set()
    try:
        changed |= fill_channels(settings, user_id, utcnow() - RECENT)
    except Exception as e:  # offline: the channels wait for the next run
        log.warning("Finding the channels of measured videos failed: %s", e)
    with SessionLocal() as db:
        pending = to_sort(db, user_id, load_ruleset(db, user_id), utcnow() - RECENT)
        pending.sort(key=lambda v: v.last or utcnow(), reverse=True)
        pending = pending[:BATCH * MAX_BATCHES]
        categories = choices(db, user_id)
        for c in categories:
            db.expunge(c)
    if pending and categories:
        if asker is None:
            asker, label = ask_model(settings)
        res.model = label
        by_id = {v.video_id: v for v in pending}
        for i in range(0, len(pending), BATCH):
            batch = pending[i:i + BATCH]
            try:
                answers = asker(categories, batch)
            except Exception as e:  # Ollama not running, a malformed answer: the rest waits
                res.errors.append(describe_error(e))
                break
            res.asked += len(batch)
            with SessionLocal() as db:
                for vid, cat, sure in answers:
                    remember(db, user_id, by_id[vid], cat, "model" if sure else "guess")
                    if sure:
                        res.sorted += 1
                        changed.add(vid)
                    else:
                        res.guesses += 1
                db.commit()
    with SessionLocal() as db:
        res.refiled = refile(db, user_id, changed)
    return res


# ---------------------------------------------------------------- background
_lock = threading.Lock()
_status: dict[int, dict] = {}
_next: dict[int, datetime] = {}


def status(user_id: int) -> dict:
    with _lock:
        return dict(_status.get(user_id) or {"state": "idle"})


def enabled(db: Session, settings: Settings, user_id: int) -> bool:
    profile = db.get(Profile, user_id)
    prefs = watchlive.settings_with_defaults((profile.prefs or {}).get("youtube") if profile else None)
    return bool(prefs["sort"]) and effective_mode(settings, profile) != "off"


def schedule(settings: Settings, user_id: int, force: bool = False) -> bool:
    """Starts a sorting in the background, unless one runs, one ran less than a
    minute ago (10 after an error), sorting is off, or AI is off."""
    now = utcnow()
    with _lock:
        if (_status.get(user_id) or {}).get("state") == "running":
            return True
        if not force and now < _next.get(user_id, now - timedelta(seconds=1)):
            return False
        _next[user_id] = now + EVERY
    with SessionLocal() as db:
        if not enabled(db, settings, user_id):
            return False
    with _lock:
        _status[user_id] = {**(_status.get(user_id) or {}), "state": "running"}
    threading.Thread(target=_run, args=(settings, user_id), daemon=True, name=f"video-sort-{user_id}").start()
    return True


def _run(settings: Settings, user_id: int) -> None:
    try:
        res = sort(settings, user_id)
        state = {"state": "error" if res.errors else "idle", "at": utcnow().isoformat(), "asked": res.asked,
                 "sorted": res.sorted, "guesses": res.guesses, "model": res.model,
                 "error": res.errors[0] if res.errors else None}
        if res.asked or res.errors:
            log.info("Video sorting: %s asked, %s sorted, %s guesses%s", res.asked, res.sorted, res.guesses,
                     f" — {res.errors[0]}" if res.errors else "")
    except Exception as e:  # never kill the server for this
        log.exception("Video sorting failed")
        state = {"state": "error", "at": utcnow().isoformat(), "error": describe_error(e)}
    with _lock:
        prev = _status.get(user_id) or {}
        if state["state"] == "error":
            _status[user_id] = {**prev, **state}
            _next[user_id] = utcnow() + AFTER_ERROR
        elif state.get("asked"):
            _status[user_id] = {**prev, **state}
        else:  # nothing new to sort: the counts of the last run that sorted something stay
            _status[user_id] = {**prev, "state": "idle", "error": None}
