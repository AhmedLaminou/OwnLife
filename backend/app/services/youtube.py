"""YouTube history: import, channel lookup, and time reconstruction.

The YouTube Data API has not returned watch history since 2016, so the past
comes from files:
- Google Takeout → YouTube → history (JSON, or the HTML export, which is also
  the format of My Activity): one line per video opened, with its channel.
- Chrome's history (Takeout → Chrome → Historique.json / History.json): the
  YouTube pages opened in that browser. It has titles but no channel; channels
  are looked up afterwards, in the background, through YouTube's oEmbed endpoint.
A history line says *when* a video was opened, not for how long. Durations are
therefore estimated: a video lasted until the next one started, capped, and
consecutive videos of the same category form one estimated block in the ledger.
Measured minutes come from the browser extension (services/watchlive.py); where
both describe the same time, the ledger counts the measured ones.
"""

from __future__ import annotations

import html
import json
import re
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import MediaItem, TimeEntry, WatchEvent
from app.services.rules import Activity, RuleSet, load_ruleset, youtube_video_id

# Watch events that come from history files (moments, without a duration).
HISTORY_SOURCES = ("youtube_takeout", "chrome_history")
TITLE_PREFIXES = ("Watched ", "Vous avez regardé ", "A regardé ")
# What a history file holds besides the videos watched: My Activity mixes them in.
NOT_WATCHED = {
    "likes": ("Liked", "Disliked", "A aimé", "N'a pas aimé", "Vous avez aimé", "Vous n'avez pas aimé"),
    "subscriptions": ("Subscribed to", "Vous vous êtes abonné", "Vous vous êtes abonnée"),
    "searches": ("Searched for", "Vous avez recherché"),
}
GAP_CAP_SECONDS = 20 * 60
LAST_VIDEO_SECONDS = 8 * 60
REVISIT = timedelta(minutes=10)  # Chrome: the same video opened again this soon is a reload
DUPLICATE_WINDOW = timedelta(minutes=15)  # two sources seeing one video at nearly the same moment
_SPACES = str.maketrans({
    "\N{NO-BREAK SPACE}": " ",
    "\N{NARROW NO-BREAK SPACE}": " ",
    "\N{RIGHT SINGLE QUOTATION MARK}": "'",
})


@dataclass
class RawWatch:
    occurred_at: datetime
    title: str
    channel: str | None
    url: str | None
    video_id: str | None


@dataclass
class HistoryFile:
    source: str  # youtube_takeout | chrome_history
    events: list[RawWatch]
    skipped: int = 0  # lines that are no video: removed videos, ads, other websites
    ignored: Counter = field(default_factory=Counter)  # likes, subscriptions, searches, other


def _norm(text: str) -> str:
    """Every kind of space made one plain space; typographic apostrophes made plain."""
    return " ".join(text.translate(_SPACES).split())


def _plain(fragment: str) -> str:
    """The visible text of an HTML fragment."""
    return _norm(html.unescape(re.sub(r"<[^>]+>", " ", fragment)))


def _not_watched(verb: str) -> str | None:
    for kind, prefixes in NOT_WATCHED.items():
        if any(verb == p or verb.startswith(p + " ") for p in prefixes):
            return kind
    return None


def _strip_prefix(title: str) -> str:
    for p in TITLE_PREFIXES:
        if title.startswith(p):
            return title[len(p):]
    return title


# ---------------------------------------------------------------- Takeout, JSON
def parse_takeout_json(data) -> tuple[list[RawWatch], int, Counter]:
    events: list[RawWatch] = []
    skipped = 0
    ignored: Counter = Counter()
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            skipped += 1
            continue
        details = item.get("details") or []
        if any("ads" in (d.get("name") or "").lower() for d in details if isinstance(d, dict)):
            skipped += 1  # "From Google Ads"
            continue
        title = _norm(item.get("title") or "")
        kind = _not_watched(title)
        if kind:
            ignored[kind] += 1
            continue
        url = item.get("titleUrl")
        ts = item.get("time")
        if not url or not ts:
            skipped += 1  # removed or private video
            continue
        video_id = youtube_video_id(url)
        if video_id is None:
            skipped += 1  # a search, a post: not a video watched
            continue
        try:
            when = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            skipped += 1
            continue
        subs = item.get("subtitles") or []
        channel = subs[0].get("name") if subs and isinstance(subs[0], dict) else None
        events.append(RawWatch(when.astimezone(timezone.utc), _strip_prefix(title)[:300], channel, url, video_id))
    return events, skipped, ignored


# ---------------------------------------------------------------- Takeout, HTML
_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
    "janv": 1, "févr": 2, "fevr": 2, "mars": 3, "avr": 4, "mai": 5, "juin": 6,
    "juil": 7, "août": 8, "aout": 8, "déc": 12,
}
_EN_DATE = re.compile(
    r"([A-Za-z]{3,5})\.?\s+(\d{1,2}),\s+(\d{4}),\s+(\d{1,2}):(\d{2}):(\d{2})\s*([AP]M)", re.I
)
_FR_DATE = re.compile(r"(\d{1,2})\s+([A-Za-zéû]{3,5})\.?\s+(\d{4}),?\s+(\d{1,2}):(\d{2}):(\d{2})")
_ZONE = re.compile(r"\s*(?:UTC|GMT)\s*(?:([+-])\s*(\d{1,2})(?::?(\d{2}))?)?", re.I)
_CELL = re.compile(r'<div class="content-cell[^"]*mdl-typography--body-1">(.*?)</div>', re.S)
_LINK = re.compile(r'<a href="([^"]+)">(.*?)</a>', re.S)


def _month(name: str) -> int | None:
    n = name.lower()
    return _MONTHS.get(n[:4]) or _MONTHS.get(n[:3])


def _zone(after: str, tz: ZoneInfo) -> tzinfo:
    """The zone written after the time: "UTC+01:00" and "GMT" are read as such;
    an abbreviation ("WAT", "CET") means the profile's time zone."""
    m = _ZONE.match(after)
    if not m:
        return tz
    if not m.group(1):
        return timezone.utc
    minutes = int(m.group(2)) * 60 + int(m.group(3) or 0)
    return timezone(timedelta(minutes=minutes if m.group(1) == "+" else -minutes))


def _parse_html_date(text: str, tz: ZoneInfo) -> datetime | None:
    text = _norm(text)
    m = _EN_DATE.search(text)
    if m and _month(m.group(1)):
        h = int(m.group(4)) % 12 + (12 if m.group(7).upper() == "PM" else 0)
        local = datetime(int(m.group(3)), _month(m.group(1)), int(m.group(2)), h, int(m.group(5)), int(m.group(6)),
                         tzinfo=_zone(text[m.end():], tz))
        return local.astimezone(timezone.utc)
    m = _FR_DATE.search(text)
    if m and _month(m.group(2)):
        local = datetime(int(m.group(3)), _month(m.group(2)), int(m.group(1)), int(m.group(4)), int(m.group(5)),
                         int(m.group(6)), tzinfo=_zone(text[m.end():], tz))
        return local.astimezone(timezone.utc)
    return None


def parse_takeout_html(text: str, tz: ZoneInfo) -> tuple[list[RawWatch], int, Counter]:
    """The HTML export (English or French), which is also the format of My
    Activity. An entry reads "<verb> <video link> <channel link> <date>"; only
    videos watched are kept — likes, subscriptions and searches sit in the same
    file. The JSON export is exact; prefer it."""
    events: list[RawWatch] = []
    skipped = 0
    ignored: Counter = Counter()
    for cell in _CELL.findall(text):
        links = _LINK.findall(cell)
        if not links:
            skipped += 1  # "Watched a video that has been removed"
            continue
        verb = _plain(cell.split("<a ", 1)[0])
        kind = _not_watched(verb) if verb else "other"  # starting with its link: a share, a dismissal
        if kind:
            ignored[kind] += 1
            continue
        url = html.unescape(links[0][0])
        video_id = youtube_video_id(url)
        if video_id is None:
            ignored["other"] += 1
            continue
        when = _parse_html_date(_plain(cell.rsplit("</a>", 1)[-1]), tz)  # the date follows the last link
        if when is None:
            skipped += 1
            continue
        channel = _plain(links[1][1]) if len(links) > 1 else ""
        events.append(RawWatch(when, _plain(links[0][1])[:300], channel or None, url, video_id))
    return events, skipped, ignored


# ---------------------------------------------------------------- Chrome
_CHROME_TITLE = re.compile(r"^\(\d+\)\s*|\s*-\s*YouTube$")


def _chrome_title(title: str) -> str:
    """'(3) Some video - YouTube' → 'Some video' (the number counts notifications)."""
    t = _CHROME_TITLE.sub("", _norm(title)).strip()
    return "" if t == "YouTube" else t


def parse_chrome_history(data: dict) -> tuple[list[RawWatch], int]:
    """Chrome's history, one line per page opened: the YouTube videos among it.
    The same video opened again within minutes (a reload, a redirect, a return
    to its tab) is one watch."""
    visits = sorted(
        (v for v in data.get("Browser History") or [] if isinstance(v, dict) and v.get("time_usec")),
        key=lambda v: int(v["time_usec"]),
    )
    events: list[RawWatch] = []
    skipped = 0
    seen: dict[str, datetime] = {}
    for v in visits:
        video_id = youtube_video_id(v.get("url"))
        if video_id is None:
            skipped += 1  # another website, or a YouTube page that is not a video
            continue
        when = datetime.fromtimestamp(int(v["time_usec"]) / 1_000_000, tz=timezone.utc)
        previous = seen.get(video_id)
        seen[video_id] = when
        if previous is not None and when - previous < REVISIT:
            continue
        title = _chrome_title(v.get("title") or "") or video_id
        events.append(RawWatch(when, title[:300], None, f"https://www.youtube.com/watch?v={video_id}", video_id))
    return events, skipped


# ---------------------------------------------------------------- any history file
def parse_history(content: bytes, filename: str, tz: ZoneInfo) -> HistoryFile:
    """A YouTube history (JSON or HTML, from Takeout or My Activity) or a
    Chrome history (JSON). Raises ValueError for anything else."""
    text = content.decode("utf-8-sig", errors="replace")
    if text.lstrip()[:1] in ("[", "{") or filename.lower().endswith(".json"):
        data = json.loads(text)  # ValueError when it is not JSON
        if isinstance(data, dict) and "Browser History" in data:
            events, skipped = parse_chrome_history(data)
            return HistoryFile("chrome_history", events, skipped)
        if isinstance(data, list):
            events, skipped, ignored = parse_takeout_json(data)
            return HistoryFile("youtube_takeout", events, skipped, ignored)
        raise ValueError("this JSON file is neither a YouTube history nor a Chrome history")
    events, skipped, ignored = parse_takeout_html(text, tz)
    return HistoryFile("youtube_takeout", events, skipped, ignored)


def store_watch_events(db: Session, user_id: int, events: list[RawWatch], source: str) -> int:
    """Inserts new events, ignores ones already stored. Returns how many were new."""
    count = select(func.count()).select_from(WatchEvent).where(WatchEvent.user_id == user_id)
    before = db.scalar(count)
    rows = [
        {
            "user_id": user_id,
            "occurred_at": e.occurred_at.replace(tzinfo=None),
            "title": e.title,
            "channel": e.channel,
            "url": e.url,
            "video_id": e.video_id,
            "source": source,
        }
        for e in events
    ]
    for i in range(0, len(rows), 500):
        stmt = sqlite_insert(WatchEvent).values(rows[i : i + 500]).on_conflict_do_nothing()
        db.execute(stmt)
    db.flush()
    return db.scalar(count) - before


# ---------------------------------------------------------------- durations
def _dedupe(rows: list[WatchEvent]) -> list[WatchEvent]:
    """One line per video per moment: Takeout and Chrome may both have seen it."""
    kept: dict[str, datetime] = {}
    out = []
    for r in rows:
        key = r.video_id or r.url or r.title
        previous = kept.get(key)
        if previous is not None and r.occurred_at - previous < DUPLICATE_WINDOW:
            continue
        kept[key] = r.occurred_at
        out.append(r)
    return out


def estimated_seconds(rows: list[WatchEvent]) -> list[float]:
    """A history line lasts until the next one, if that is close; otherwise a default."""
    out = []
    for i, ev in enumerate(rows):
        gap = (rows[i + 1].occurred_at - ev.occurred_at).total_seconds() if i + 1 < len(rows) else None
        out.append(gap if gap is not None and 0 < gap <= GAP_CAP_SECONDS else LAST_VIDEO_SECONDS)
    return out


def watched(db: Session, user_id: int, start: datetime, end: datetime) -> list[tuple[WatchEvent, float, bool]]:
    """Every video watched in [start, end), oldest first: (event, seconds, measured).
    The extension's segments carry measured seconds; history lines get estimated
    ones, and are dropped where the extension measured the same video then."""
    rows = db.scalars(
        select(WatchEvent)
        .where(WatchEvent.user_id == user_id, WatchEvent.occurred_at >= start, WatchEvent.occurred_at < end)
        .order_by(WatchEvent.occurred_at)
    ).all()
    measured = [r for r in rows if r.seconds is not None]
    measured_at: dict[str | None, list[datetime]] = {}
    for m in measured:
        measured_at.setdefault(m.video_id, []).append(m.occurred_at)
    window = DUPLICATE_WINDOW.total_seconds()
    history = [
        r for r in _dedupe([r for r in rows if r.seconds is None])
        if not any(abs((t - r.occurred_at).total_seconds()) < window for t in measured_at.get(r.video_id, ()))
    ]
    out = [(r, s, False) for r, s in zip(history, estimated_seconds(history))]
    out += [(m, m.seconds or 0.0, True) for m in measured]
    return sorted(out, key=lambda t: t[0].occurred_at)


# ---------------------------------------------------------------- ledger blocks
def _block_title(channels: dict[str, int], n: int) -> str:
    top = [c for c, _ in sorted(channels.items(), key=lambda kv: -kv[1])][:2]
    label = ", ".join(top) if top else "videos"
    more = len(channels) - len(top)
    return f"YouTube · {label}" + (f" (+{more})" if more > 0 else "") + f" — {n} video{'s' if n > 1 else ''}"


def rebuild_sessions(db: Session, user_id: int, ruleset: RuleSet, start: datetime, end: datetime) -> int:
    """Regenerates the estimated YouTube blocks between start and end. Where
    another source covers the same minutes (your own entry, the extension,
    ActivityWatch), the ledger counts that one instead (ledger.effective_spans),
    so an estimate only fills the time nothing better describes."""
    db.execute(
        delete(TimeEntry).where(
            TimeEntry.user_id == user_id,
            TimeEntry.source == "youtube_takeout",
            TimeEntry.category_locked.is_(False),
            TimeEntry.started_at >= start,
            TimeEntry.started_at <= end,
        )
    )
    events = _dedupe(list(db.scalars(
        select(WatchEvent)
        .where(WatchEvent.user_id == user_id, WatchEvent.source.in_(HISTORY_SOURCES),
               WatchEvent.occurred_at >= start, WatchEvent.occurred_at <= end)
        .order_by(WatchEvent.occurred_at)
    )))
    if not events:
        return 0

    created = 0
    block: dict | None = None

    def flush() -> None:
        nonlocal created
        if block is None:
            return
        db.add(
            TimeEntry(
                user_id=user_id,
                title=_block_title(block["channels"], block["n"]),
                category_id=block["category_id"],
                started_at=block["start"],
                ended_at=block["end"],
                source="youtube_takeout",
                source_ref=f"yt:{block['start'].isoformat()}",
                is_estimate=True,
                meta={"videos": block["n"], "channels": block["channels"], "sample": block["sample"]},
            )
        )
        created += 1

    for ev, dur in zip(events, estimated_seconds(events)):
        cat = ruleset.classify(Activity(title=ev.title, channel=ev.channel, url=ev.url))
        ev_end = ev.occurred_at + timedelta(seconds=dur)
        contiguous = block is not None and (ev.occurred_at - block["end"]).total_seconds() <= 60
        if block is not None and contiguous and block["category_id"] == cat:
            block["end"] = ev_end
            block["n"] += 1
            if ev.channel:
                block["channels"][ev.channel] = block["channels"].get(ev.channel, 0) + 1
            if len(block["sample"]) < 3:
                block["sample"].append(ev.title)
        else:
            flush()
            block = {
                "start": ev.occurred_at,
                "end": ev_end,
                "category_id": cat,
                "n": 1,
                "channels": {ev.channel: 1} if ev.channel else {},
                "sample": [ev.title],
            }
    flush()
    db.flush()
    return created


# ---------------------------------------------------------------- channels
def oembed(video_id: str) -> dict | None:
    """Title and channel of a public video, from YouTube's oEmbed endpoint (no
    key needed). None when offline, or for a private or removed video."""
    try:
        r = httpx.get(
            "https://www.youtube.com/oembed",
            params={"url": f"https://www.youtube.com/watch?v={video_id}", "format": "json"},
            timeout=6,
        )
        return r.json() if r.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        return None


def _cached(db: Session, user_id: int, video_id: str) -> MediaItem | None:
    return db.scalar(
        select(MediaItem).where(
            MediaItem.user_id == user_id, MediaItem.kind == "video", MediaItem.external_id == video_id
        )
    )


def _remember(db: Session, user_id: int, video_id: str, data: dict) -> None:
    db.execute(
        sqlite_insert(MediaItem)
        .values(
            user_id=user_id,
            kind="video",
            external_id=video_id,
            title=(data.get("title") or video_id)[:300],
            creator=(data.get("author_name") or None),
            url=f"https://www.youtube.com/watch?v={video_id}",
        )
        .on_conflict_do_nothing()
    )


def channel_for_video(db: Session, user_id: int, video_id: str, enabled: bool = True) -> str | None:
    """Channel name of a video, cached in media_items. Returns None when offline."""
    item = _cached(db, user_id, video_id)
    if item is not None:
        return item.creator
    if not enabled:
        return None
    data = oembed(video_id)
    if data is None:
        return None
    _remember(db, user_id, video_id, data)
    db.flush()
    return data.get("author_name")


_lookup_lock = threading.Lock()
_lookups: dict[int, dict] = {}


def lookup_status(user_id: int) -> dict:
    with _lookup_lock:
        return dict(_lookups.get(user_id) or {"state": "idle", "done": 0, "total": 0})


def _set(user_id: int, **kw) -> None:
    with _lookup_lock:
        _lookups.setdefault(user_id, {}).update(kw)


def schedule_channel_lookup(user_id: int, enabled: bool) -> bool:
    """Chrome's history knows titles, not channels, and the rules mostly go by
    channel: a background thread finds the channels (a minute or two for
    hundreds of videos), then rebuilds the estimated blocks so the rules apply.
    Returns whether a lookup is running."""
    if not enabled:
        return False
    with _lookup_lock:
        if (_lookups.get(user_id) or {}).get("state") == "running":
            return True
        _lookups[user_id] = {"state": "running", "done": 0, "total": 0}
    threading.Thread(target=_lookup_channels, args=(user_id,), daemon=True, name=f"yt-channels-{user_id}").start()
    return True


def _lookup_channels(user_id: int) -> None:
    try:
        with SessionLocal() as db:
            missing = list(db.scalars(
                select(WatchEvent.video_id).where(
                    WatchEvent.user_id == user_id,
                    WatchEvent.source.in_(HISTORY_SOURCES),
                    WatchEvent.channel.is_(None),
                    WatchEvent.video_id.is_not(None),
                ).distinct()
            ))
            names: dict[str, tuple[str, str | None]] = {}
            for vid in missing:
                item = _cached(db, user_id, vid)
                if item is not None and item.creator:
                    names[vid] = (item.creator, item.title)
        # No session is open while waiting on the network: nothing else is blocked.
        todo = [v for v in missing if v not in names]
        _set(user_id, total=len(missing), done=len(names))
        fetched: dict[str, dict] = {}
        with ThreadPoolExecutor(max_workers=6) as pool:
            for i, (vid, data) in enumerate(zip(todo, pool.map(oembed, todo)), 1):
                if data and data.get("author_name"):
                    fetched[vid] = data
                    names[vid] = (data["author_name"], data.get("title"))
                if i % 20 == 0:
                    _set(user_id, done=len(missing) - len(todo) + i)
        with SessionLocal() as db:
            for vid, data in fetched.items():
                _remember(db, user_id, vid, data)
            for vid, (channel, title) in names.items():
                mine = (WatchEvent.user_id == user_id, WatchEvent.video_id == vid)
                db.execute(update(WatchEvent).where(*mine, WatchEvent.channel.is_(None)).values(channel=channel[:200]))
                if title:  # Chrome had no title yet when the page was saved
                    db.execute(update(WatchEvent).where(*mine, WatchEvent.title == vid).values(title=title[:300]))
            if names:
                first, last = db.execute(
                    select(func.min(WatchEvent.occurred_at), func.max(WatchEvent.occurred_at)).where(
                        WatchEvent.user_id == user_id, WatchEvent.source.in_(HISTORY_SOURCES)
                    )
                ).one()
                rebuild_sessions(db, user_id, load_ruleset(db, user_id), first, last)
            db.commit()
        _set(user_id, state="idle", done=len(missing), found=len(names), error=None)
    except Exception as e:  # offline midway: the channels found stay, the others can be retried
        _set(user_id, state="error", error=f"{e.__class__.__name__}: {str(e)[:200]}")
