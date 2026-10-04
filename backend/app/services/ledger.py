"""Time accounting: how the hours of each local day were spent."""

from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import TimeEntry
from app.services.timeutil import (
    day_start_utc,
    daterange,
    local_date_of,
    overlap_seconds,
    range_utc,
)

# Stacking order of the kinds in every chart. It matches the order of the
# categorical palette, which was validated for adjacent pairs.
KIND_ORDER = (
    "core",
    "growth",
    "work",
    "spirit",
    "social",
    "body",
    "maintenance",
    "noise",
    "destructive",
)
UNCATEGORIZED = "uncategorized"
# A day with less than this logged is "not tracked" and left out of averages,
# so a forgotten day does not count as a day of zero study.
TRACKED_DAY_MIN_SECONDS = 2 * 3600


def entries_overlapping(
    db: Session, user_id: int, start: datetime, end: datetime
) -> list[TimeEntry]:
    q = (
        select(TimeEntry)
        .where(
            TimeEntry.user_id == user_id,
            TimeEntry.started_at < end,
            or_(TimeEntry.ended_at.is_(None), TimeEntry.ended_at > start),
        )
        .order_by(TimeEntry.started_at)
    )
    return list(db.scalars(q).unique())


def entry_kind(e: TimeEntry) -> str:
    return e.category.kind if e.category is not None else UNCATEGORIZED


# When two sources describe the same minutes, one of them wins: what you wrote
# yourself (form, quick-log, capture, assistant), then the most exact automatic
# source. A running timer is the exception: it says when something started, not
# that it went on — when the extension measured YouTube of a known category
# meanwhile (you switched without pausing), the measured minutes count. YouTube
# without a category does not override a timer: a lecture from a channel that
# has no rule yet must not erase study time. Entries of the same rank all
# count — you can recite Quran during the commute, and both are true.
# The built-in window tracker ("window") and ActivityWatch see the same thing;
# when both run, the built-in one is counted.
SOURCE_RANK = {"extension": 1, "timer": 2, "window": 3, "activitywatch": 3.5, "youtube_takeout": 4}
AUTOMATIC = ("extension", "window", "activitywatch", "youtube_takeout")


def source_rank(e: TimeEntry) -> float:
    if e.source == "extension" and e.category_id is None:
        return 2.5
    return SOURCE_RANK.get(e.source, 0)


Span = tuple[TimeEntry, datetime, datetime]


def _merge(intervals: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    out: list[tuple[datetime, datetime]] = []
    for a, b in sorted(intervals):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def _subtract(a: datetime, b: datetime, covered: list[tuple[datetime, datetime]],
              starts: list[datetime]) -> list[tuple[datetime, datetime]]:
    """[a, b) minus a sorted, merged list of intervals."""
    out, cur = [], a
    i = max(0, bisect_right(starts, a) - 1)
    while i < len(covered) and covered[i][0] < b:
        x, y = covered[i]
        if y > cur:
            if x > cur:
                out.append((cur, min(x, b)))
            cur = max(cur, y)
        i += 1
    if cur < b:
        out.append((cur, b))
    return out


def effective_spans(entries: list[TimeEntry], now: datetime) -> list[Span]:
    """Every entry's time, minus what entries of a higher rank already cover.
    All the statistics run on these spans, so nothing is counted twice when
    ActivityWatch and your own entry describe the same hour."""
    spans: list[Span] = []
    covered: list[tuple[datetime, datetime]] = []
    for rank in sorted({source_rank(e) for e in entries}):
        group = [e for e in entries if source_rank(e) == rank]
        starts = [c[0] for c in covered]
        for e in group:
            a, b = e.started_at, e.ended_at or now
            if b <= a:
                continue
            for x, y in (_subtract(a, b, covered, starts) if covered else [(a, b)]):
                spans.append((e, x, y))
        covered = _merge(covered + [(e.started_at, e.ended_at or now) for e in group
                                    if (e.ended_at or now) > e.started_at])
    return spans


def overlap_notes(entries: list[TimeEntry], now: datetime, min_minutes: float = 10) -> list[dict]:
    """An automatic record and one of your entries of a *different* kind on the
    same minutes — "ActivityWatch saw YouTube 23:30-00:15 during 'Sleep'". Worth
    a look: one of the two is wrong. `counted` says which one the totals use."""
    notes = []
    yours = [e for e in entries if e.source not in AUTOMATIC]
    for e in entries:
        if e.source not in AUTOMATIC:
            continue
        a0, a1 = e.started_at, e.ended_at or now
        for m in yours:
            b0, b1 = m.started_at, m.ended_at or now
            secs = (min(a1, b1) - max(a0, b0)).total_seconds()
            if secs >= min_minutes * 60 and entry_kind(m) != entry_kind(e):
                notes.append({"entry_id": e.id, "covered_by": m.id, "seconds": secs,
                              "start": max(a0, b0).isoformat(), "end": min(a1, b1).isoformat(),
                              "counted": "automatic" if source_rank(e) < source_rank(m) else "yours"})
    return notes


def split_by_day(
    entries: list[TimeEntry],
    start_date: date,
    end_date: date,
    tz: ZoneInfo,
    now: datetime,
    key=entry_kind,
) -> dict[date, dict[str, float]]:
    """Seconds per local day per key. An entry crossing midnight is split; time
    already covered by a higher-ranked source is not counted again."""
    result: dict[date, defaultdict[str, float]] = {
        d: defaultdict(float) for d in daterange(start_date, end_date)
    }
    for e, a0, a1 in effective_spans(entries, now):
        first = max(local_date_of(a0, tz), start_date)
        last = min(local_date_of(a1 - timedelta(microseconds=1), tz), end_date)
        k = key(e)
        for d in daterange(first, last):
            secs = overlap_seconds(
                a0, a1, day_start_utc(d, tz), day_start_utc(d + timedelta(days=1), tz)
            )
            if secs > 0:
                result[d][k] += secs
    return {d: dict(v) for d, v in result.items()}


def daily_breakdown(
    db: Session, user_id: int, start_date: date, end_date: date, tz: ZoneInfo, now: datetime
) -> dict[date, dict[str, float]]:
    s, e = range_utc(start_date, end_date, tz)
    return split_by_day(entries_overlapping(db, user_id, s, e), start_date, end_date, tz, now)


def covered_seconds(entries: list[TimeEntry], start: datetime, end: datetime, now: datetime) -> float:
    """Length of the union of the entries inside [start, end): overlapping entries
    (Quran on the way to work) are not counted twice."""
    spans = sorted(
        (max(e.started_at, start), min(e.ended_at or now, end))
        for e in entries
        if (e.ended_at or now) > start and e.started_at < end
    )
    total, cur0, cur1 = 0.0, None, None
    for a, b in spans:
        if b <= a:
            continue
        if cur1 is None or a > cur1:
            if cur1 is not None:
                total += (cur1 - cur0).total_seconds()
            cur0, cur1 = a, b
        else:
            cur1 = max(cur1, b)
    if cur1 is not None:
        total += (cur1 - cur0).total_seconds()
    return total


def average_hours_per_kind(breakdown: dict[date, dict[str, float]]) -> tuple[dict[str, float], int]:
    tracked = [kinds for kinds in breakdown.values() if sum(kinds.values()) >= TRACKED_DAY_MIN_SECONDS]
    if not tracked:
        return {}, 0
    n = len(tracked)
    keys = {k for kinds in tracked for k in kinds}
    return {k: sum(day.get(k, 0.0) for day in tracked) / n / 3600 for k in keys}, n
