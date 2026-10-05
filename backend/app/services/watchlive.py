"""Live YouTube watching, measured by the OwnLife browser extension.

While a video plays, the extension sends a heartbeat every 15 seconds (and one
when it pauses or ends): "video X was playing at time t". Here, heartbeats
become *segments* — continuous playing time of one video — and segments become
ledger blocks, classified by the same rules as the YouTube history:

    heartbeats 21:02:10 … 21:14:55 (video A)  ─►  segment A 21:02–21:15
    heartbeats 21:15:20 … 21:31:40 (video B)  ─►  segment B 21:15–21:32
    A and B, same category, < 3 min apart     ─►  one block "YouTube · ReactionHub"

Unlike Google Takeout (a moment per video, durations guessed from the gaps),
these are real minutes, pauses excluded. In the ledger they outrank
ActivityWatch's browser time, so the same minutes never count twice.

Strict mode (on unless you turn it off): a video that no rule and no sorting
places counts as noise — it lands in "YouTube, not sorted yet", a noise
category, until it is sorted (services/videosort.py). Such a block still yields
to a running timer, like a video without a category: a lecture from a channel
nobody sorted yet must not erase study time.
"""

from __future__ import annotations

import re
import threading
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import Category, Profile, TimeEntry, WatchEvent
from app.services.rules import Activity, RuleSet
from app.services.youtube import channel_for_video

SOURCE = "extension"
PULSE = timedelta(seconds=45)  # a heartbeat this close to a segment's end extends it
MERGE_GAP = timedelta(minutes=3)  # segments of one category this close form one block
MIN_BLOCK = timedelta(seconds=20)  # shorter than this stays out of the ledger
VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{6,20}$")
UNSORTED = "YouTube, not sorted yet"
DEFAULTS = {"strict": True, "sort": True}

# Everything that rewrites the extension's blocks takes this lock: an upload
# from the extension and a re-filing after a sorting must not interleave.
LOCK = threading.Lock()


def settings_with_defaults(prefs: dict | None) -> dict:
    return {**DEFAULTS, **(prefs or {})}


def strict_category(db: Session, user_id: int) -> int | None:
    """The noise category of the videos nothing sorted, when strict mode is on
    (created the first time it is needed)."""
    profile = db.get(Profile, user_id)
    if profile is None or not settings_with_defaults((profile.prefs or {}).get("youtube"))["strict"]:
        return None
    cat = db.scalar(select(Category).where(Category.user_id == user_id, Category.name == UNSORTED)
                    .order_by(Category.id).limit(1))
    if cat is None:
        cat = Category(user_id=user_id, name=UNSORTED, kind="noise", icon="circle-help", sort=900)
        db.add(cat)
        db.flush()
    return cat.id


def _ts(value: str) -> datetime:
    t = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def _title(channels: dict[str, float], videos: int) -> str:
    top = [c for c, _ in sorted(channels.items(), key=lambda kv: -kv[1])][:2]
    label = ", ".join(top) if top else "videos"
    more = len(channels) - len(top)
    return f"YouTube · {label}" + (f" (+{more})" if more > 0 else "") + (f" — {videos} videos" if videos > 1 else "")


def _entries_near(db: Session, user_id: int, a: datetime, b: datetime) -> list[TimeEntry]:
    return list(db.scalars(
        select(TimeEntry).where(
            TimeEntry.user_id == user_id, TimeEntry.source == SOURCE,
            TimeEntry.started_at <= b + MERGE_GAP, TimeEntry.ended_at >= a - MERGE_GAP,
        ).order_by(TimeEntry.started_at)
    ))


def _segments_of(entry: TimeEntry) -> list[int]:
    return list((entry.meta or {}).get("segments") or [])


def _refresh(db: Session, entry: TimeEntry) -> None:
    """Span, title and per-channel seconds recomputed from the entry's segments;
    an entry left without segments is removed."""
    segs = list(db.scalars(select(WatchEvent).where(WatchEvent.id.in_(_segments_of(entry))))) if _segments_of(entry) else []
    if not segs:
        db.delete(entry)
        return
    channels: dict[str, float] = {}
    videos: dict[str, str] = {}
    for x in segs:
        name = x.channel or "Unknown channel"
        channels[name] = channels.get(name, 0.0) + (x.seconds or 0.0)
        videos[x.video_id] = x.title
    entry.started_at = min(x.occurred_at for x in segs)
    entry.ended_at = max(x.ended_at for x in segs)
    unsorted = {"unsorted": True} if (entry.meta or {}).get("unsorted") else {}
    entry.meta = {"segments": sorted(x.id for x in segs), "channels": channels, "videos": videos, **unsorted}
    entry.title = _title(channels, len(videos))[:300]


def attach(db: Session, user_id: int, seg: WatchEvent, ruleset: RuleSet,
           fallback: int | None = None) -> TimeEntry | None:
    """Puts a segment into the ledger: into the block that already holds it,
    else a block of the same category less than 3 minutes away (before or
    after), else a new block. Blocks that end up touching are merged.
    `fallback`: the category of what nothing sorted (strict mode)."""
    if seg.ended_at - seg.occurred_at < MIN_BLOCK:
        return None
    cat = ruleset.classify(Activity(title=seg.title, channel=seg.channel, url=seg.url))
    unsorted = cat is None and fallback is not None
    if unsorted:
        cat = fallback
    near = _entries_near(db, user_id, seg.occurred_at, seg.ended_at)
    entry = next((e for e in near if seg.id in _segments_of(e)), None)
    if entry is None:
        entry = next((e for e in near if e.category_id == cat), None)
    if entry is None:
        entry = TimeEntry(user_id=user_id, title="YouTube", category_id=cat, started_at=seg.occurred_at,
                          ended_at=seg.ended_at, source=SOURCE, source_ref=f"ext:{seg.id}",
                          meta={"segments": [], **({"unsorted": True} if unsorted else {})})
        db.add(entry)
    if seg.id not in _segments_of(entry):
        entry.meta = {**(entry.meta or {}), "segments": [*_segments_of(entry), seg.id]}
    _refresh(db, entry)
    db.flush()
    # neighbours of the same category now within reach: one block
    for other in _entries_near(db, user_id, entry.started_at, entry.ended_at):
        if other.id != entry.id and other.category_id == entry.category_id and not other.category_locked:
            entry.meta = {**(entry.meta or {}), "segments": [*_segments_of(entry), *_segments_of(other)]}
            db.delete(other)
            _refresh(db, entry)
    db.flush()
    return entry


def _runs(stamps: list[datetime]) -> list[tuple[datetime, datetime]]:
    runs: list[tuple[datetime, datetime]] = []
    for t in sorted(stamps):
        if runs and t - runs[-1][1] <= PULSE:
            runs[-1] = (runs[-1][0], t)
        else:
            runs.append((t, t))
    return runs


def ingest(db: Session, user_id: int, heartbeats: list[dict], ruleset: RuleSet, oembed: bool = True,
           now: datetime | None = None) -> dict:
    """Applies a batch of heartbeats. A batch may arrive late or out of order
    (the extension keeps them while OwnLife is off): heartbeats are grouped into
    runs per video, and every stored segment a run touches is merged into one."""
    now = now or datetime.now(timezone.utc)
    fallback = strict_category(db, user_id)
    by_video: dict[str, list[tuple[datetime, dict]]] = {}
    used = 0
    for hb in heartbeats:
        vid = str(hb.get("video_id") or "")
        try:
            t = _ts(str(hb["ts"]))
        except (KeyError, ValueError):
            continue
        if not VIDEO_ID.match(vid) or t > now + timedelta(minutes=5) or t < now - timedelta(days=30):
            continue
        by_video.setdefault(vid, []).append((t, hb))
        used += 1
    for vid, beats in by_video.items():
        info = next((hb for _, hb in sorted(beats, key=lambda x: x[0], reverse=True) if hb.get("title")), beats[-1][1])
        for a, b in _runs([t for t, _ in beats]):
            touching = list(db.scalars(select(WatchEvent).where(
                WatchEvent.user_id == user_id, WatchEvent.source == SOURCE, WatchEvent.video_id == vid,
                WatchEvent.occurred_at <= b + PULSE, WatchEvent.ended_at >= a - PULSE,
            ).order_by(WatchEvent.id)))
            if touching:
                seg, extra = touching[0], touching[1:]
                seg.occurred_at = min([a, *(x.occurred_at for x in touching)])
                seg.ended_at = max([b, *(x.ended_at for x in touching)])
                for x in extra:  # the late heartbeats joined two pieces of one viewing
                    for e in db.scalars(select(TimeEntry).where(TimeEntry.user_id == user_id, TimeEntry.source == SOURCE)):
                        if x.id in _segments_of(e):
                            e.meta = {**(e.meta or {}), "segments": [i for i in _segments_of(e) if i != x.id]}
                            _refresh(db, e)
                    db.delete(x)
            else:
                seg = WatchEvent(user_id=user_id, source=SOURCE, video_id=vid, occurred_at=a, ended_at=b,
                                 title=(info.get("title") or vid)[:300], url=(info.get("url") or
                                 f"https://www.youtube.com/watch?v={vid}")[:500])
                db.add(seg)
            seg.seconds = (seg.ended_at - seg.occurred_at).total_seconds()
            if info.get("title") and (not seg.title or seg.title == vid):
                seg.title = str(info["title"])[:300]
            if not seg.channel:
                seg.channel = info.get("channel") or channel_for_video(db, user_id, vid, enabled=oembed)
            db.flush()
            attach(db, user_id, seg, ruleset, fallback)
    db.flush()
    return {"accepted": used, "ignored": len(heartbeats) - used}


def rebuild(db: Session, user_id: int, ruleset: RuleSet, since: datetime | None = None) -> int:
    """Re-files every measured segment after a rule changed (entries whose
    category you set by hand are kept as they are)."""
    fallback = strict_category(db, user_id)
    q = delete(TimeEntry).where(TimeEntry.user_id == user_id, TimeEntry.source == SOURCE,
                                TimeEntry.category_locked.is_(False))
    if since is not None:
        q = q.where(TimeEntry.started_at >= since)
    db.execute(q)
    locked = {sid for e in db.scalars(select(TimeEntry).where(
        TimeEntry.user_id == user_id, TimeEntry.source == SOURCE, TimeEntry.category_locked.is_(True)))
        for sid in ((e.meta or {}).get("segments") or [])}
    segs = select(WatchEvent).where(WatchEvent.user_id == user_id, WatchEvent.source == SOURCE)
    if since is not None:
        segs = segs.where(WatchEvent.occurred_at >= since)
    n = 0
    for seg in db.scalars(segs.order_by(WatchEvent.occurred_at)):
        if seg.id not in locked and attach(db, user_id, seg, ruleset, fallback) is not None:
            n += 1
    return n


def today_summary(db: Session, user_id: int, start: datetime, end: datetime) -> dict:
    by_channel: dict[str, float] = {}
    for s in db.scalars(select(WatchEvent).where(
        WatchEvent.user_id == user_id, WatchEvent.source == SOURCE,
        WatchEvent.occurred_at >= start, WatchEvent.occurred_at < end,
    )):
        name = s.channel or "Unknown channel"
        by_channel[name] = by_channel.get(name, 0.0) + (s.seconds or 0.0)
    top = sorted(by_channel.items(), key=lambda kv: -kv[1])
    return {"seconds": sum(by_channel.values()),
            "channels": [{"channel": c, "seconds": round(v)} for c, v in top[:5]]}
