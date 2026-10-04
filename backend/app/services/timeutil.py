"""Local days versus UTC instants.

Instants are stored in UTC. Days are the person's local days: "what did I do on
12/09" means midnight-to-midnight in their time zone (Africa/Niamey is UTC+1).
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# "09:00", "9h30", "9.30", and the AM/PM forms — including "13:15 AM", which in this
# journal means 13:15: a suffix only changes hours that are ambiguous (1-12).
_HHMM = re.compile(r"^\s*(\d{1,2})\s*[:hH.]\s*(\d{2})\s*(?:([AaPp])\.?\s*[Mm]\.?)?\s*$")


def tz_of(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def local_today(tz: ZoneInfo) -> date:
    return datetime.now(tz).date()


def local_date_of(instant: datetime, tz: ZoneInfo) -> date:
    return instant.astimezone(tz).date()


def day_start_utc(d: date, tz: ZoneInfo) -> datetime:
    return datetime.combine(d, time(0, 0), tzinfo=tz).astimezone(timezone.utc)


def range_utc(start: date, end_inclusive: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    return day_start_utc(start, tz), day_start_utc(end_inclusive + timedelta(days=1), tz)


def parse_hhmm(value: str) -> tuple[int, int]:
    m = _HHMM.match(value or "")
    if not m:
        raise ValueError(f"not a time: {value!r} (expected HH:MM)")
    h, mi = int(m.group(1)), int(m.group(2))
    suffix = (m.group(3) or "").lower()
    if suffix == "p" and 1 <= h < 12:
        h += 12
    elif suffix == "a" and h == 12:
        h = 0
    if h > 24 or mi > 59 or (h == 24 and mi != 0):
        raise ValueError(f"not a time: {value!r}")
    return h, mi


def hhmm_to_minutes(value: str) -> int:
    h, m = parse_hhmm(value)
    return h * 60 + m


def minutes_to_hhmm(minutes: int) -> str:
    minutes %= 24 * 60
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def local_instant(d: date, hhmm: str, tz: ZoneInfo, day_offset: int = 0) -> datetime:
    """The UTC instant of HH:MM on local day d (+ day_offset days)."""
    h, m = parse_hhmm(hhmm)
    base = datetime.combine(d + timedelta(days=day_offset), time(0, 0), tzinfo=tz)
    return (base + timedelta(hours=h, minutes=m)).astimezone(timezone.utc)


def daterange(start: date, end_inclusive: date):
    d = start
    while d <= end_inclusive:
        yield d
        d += timedelta(days=1)


def overlap_seconds(a0: datetime, a1: datetime, b0: datetime, b1: datetime) -> float:
    return max(0.0, (min(a1, b1) - max(a0, b0)).total_seconds())
