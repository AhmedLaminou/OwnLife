"""Prayer times, computed offline from the position of the sun.

No internet and no API: the times follow from astronomy. For a date and a
place, the sun's declination and the equation of time give solar noon (Dhuhr);
the hour angle at which the sun reaches a given altitude gives the rest:

    Fajr     the sun 18° below the horizon, before sunrise (Muslim World League)
    Sunrise  the sun's upper edge on the horizon (0.833°: refraction + radius)
    Dhuhr    the sun at its highest (solar noon)
    Asr      an object's shadow = its length × 1 (× 2 for Hanafi) + its noon shadow
    Maghrib  sunset
    Isha     the sun 17° below the horizon (MWL), or 90 min after Maghrib (Umm al-Qura)

In Niamey the times move by up to ~40 minutes over the year. Mosques announce
the adhan at these times and hold the prayer a little later: the per-prayer
offsets (Settings → Prayer times) shift each one to match your mosque.

The solar formulas are the standard low-precision ones (U.S. Naval Observatory,
as used by PrayTimes.org): accurate to about a minute, which is the precision
of the times themselves.
"""

from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

PRAYERS = ("fajr", "sunrise", "dhuhr", "asr", "maghrib", "isha")
LABELS = {"fajr": "Fajr", "sunrise": "Sunrise", "dhuhr": "Dhuhr", "asr": "Asr", "maghrib": "Maghrib", "isha": "Isha"}

METHODS: dict[str, dict] = {
    "MWL": {"name": "Muslim World League", "fajr": 18.0, "isha": 17.0},
    "ISNA": {"name": "Islamic Society of North America", "fajr": 15.0, "isha": 15.0},
    "Egypt": {"name": "Egyptian General Authority of Survey", "fajr": 19.5, "isha": 17.5},
    "Makkah": {"name": "Umm al-Qura, Makkah", "fajr": 18.5, "isha_minutes": 90},
    "Karachi": {"name": "University of Islamic Sciences, Karachi", "fajr": 18.0, "isha": 18.0},
    "France": {"name": "UOIF (France)", "fajr": 12.0, "isha": 12.0},
}

DEFAULTS: dict = {
    "city": "Niamey",
    "latitude": 13.5116,
    "longitude": 2.1254,
    "method": "MWL",
    "asr": "standard",  # standard (Maliki, Shafi'i, Hanbali) | hanafi
    # Minutes added to each computed time, e.g. to match a mosque's iqama.
    "offsets": {p: 0 for p in PRAYERS},
    # When a template is applied, its prayer blocks move to these times.
    "align_planner": True,
}

# Block titles recognised as prayers when a template is applied.
ALIASES = {
    "fajr": "fajr", "subh": "fajr", "sobh": "fajr",
    "dhuhr": "dhuhr", "zuhr": "dhuhr", "duhr": "dhuhr", "dohr": "dhuhr",
    "jumuah": "dhuhr", "jumua": "dhuhr", "jummah": "dhuhr", "jumaa": "dhuhr",
    "asr": "asr",
    "maghrib": "maghrib", "magrib": "maghrib",
    "isha": "isha", "icha": "isha",
}


def settings_with_defaults(prefs: dict | None) -> dict:
    s = {**DEFAULTS, **(prefs or {})}
    s["offsets"] = {**DEFAULTS["offsets"], **((prefs or {}).get("offsets") or {})}
    return s


# ---------------------------------------------------------------- astronomy
def _sin(d: float) -> float:
    return math.sin(math.radians(d))


def _cos(d: float) -> float:
    return math.cos(math.radians(d))


def _tan(d: float) -> float:
    return math.tan(math.radians(d))


def _arcsin(x: float) -> float:
    return math.degrees(math.asin(x))


def _arccos(x: float) -> float:
    return math.degrees(math.acos(x))


def _arctan2(y: float, x: float) -> float:
    return math.degrees(math.atan2(y, x))


def _arccot(x: float) -> float:
    return math.degrees(math.atan(1 / x))


def _fix(a: float, b: float) -> float:
    a = a - b * math.floor(a / b)
    return a + b if a < 0 else a


def julian(d: date) -> float:
    y, m = d.year, d.month
    if m <= 2:
        y -= 1
        m += 12
    a = math.floor(y / 100)
    b = 2 - a + math.floor(a / 4)
    return math.floor(365.25 * (y + 4716)) + math.floor(30.6001 * (m + 1)) + d.day + b - 1524.5


def sun_position(jd: float) -> tuple[float, float]:
    """(declination in degrees, equation of time in hours) at Julian day jd."""
    n = jd - 2451545.0
    g = _fix(357.529 + 0.98560028 * n, 360)
    q = _fix(280.459 + 0.98564736 * n, 360)
    lon = _fix(q + 1.915 * _sin(g) + 0.020 * _sin(2 * g), 360)
    e = 23.439 - 0.00000036 * n
    ra = _fix(_arctan2(_cos(e) * _sin(lon), _cos(lon)) / 15, 24)
    eqt = q / 15 - ra
    return _arcsin(_sin(e) * _sin(lon)), eqt


def compute(d: date, latitude: float, longitude: float, utc_offset_hours: float,
            method: str = "MWL", asr: str = "standard") -> dict[str, float | None]:
    """Times in local decimal hours (None where the sun never gets there)."""
    params = METHODS.get(method, METHODS["MWL"])
    jd = julian(d) - longitude / (15 * 24)

    def mid_day(t: float) -> float:
        return _fix(12 - sun_position(jd + t)[1], 24)

    def angle_time(angle: float, t: float, before_noon: bool) -> float | None:
        decl = sun_position(jd + t)[0]
        x = (-_sin(angle) - _sin(decl) * _sin(latitude)) / (_cos(decl) * _cos(latitude))
        if not -1 <= x <= 1:
            return None
        h = _arccos(x) / 15
        noon = mid_day(t)
        return noon - h if before_noon else noon + h

    def asr_time(factor: float, t: float) -> float | None:
        decl = sun_position(jd + t)[0]
        return angle_time(-_arccot(factor + _tan(abs(latitude - decl))), t, before_noon=False)

    rise_set = 0.833
    factor = 2.0 if asr == "hanafi" else 1.0
    # A first guess, then refinements with the sun's position at the time found.
    guess = {"fajr": 5.0, "sunrise": 6.0, "dhuhr": 12.0, "asr": 13.0, "maghrib": 18.0, "isha": 18.0}
    t: dict[str, float | None] = dict(guess)
    for _ in range(2):
        f = {k: (t[k] if t[k] is not None else guess[k]) / 24 for k in guess}
        t = {
            "fajr": angle_time(params["fajr"], f["fajr"], True),
            "sunrise": angle_time(rise_set, f["sunrise"], True),
            "dhuhr": mid_day(f["dhuhr"]),
            "asr": asr_time(factor, f["asr"]),
            "maghrib": angle_time(rise_set, f["maghrib"], False),
            "isha": angle_time(params["isha"], f["isha"], False) if "isha" in params else None,
        }
    out: dict[str, float | None] = {}
    shift = utc_offset_hours - longitude / 15
    for k in PRAYERS:
        v = t.get(k)
        out[k] = v + shift if v is not None else None
    if "isha_minutes" in params and out["maghrib"] is not None:
        out["isha"] = out["maghrib"] + params["isha_minutes"] / 60
    return out


def _hhmm(hours: float) -> str:
    total = int(round(hours * 60)) % (24 * 60)
    return f"{total // 60:02d}:{total % 60:02d}"


def times_for(d: date, prefs: dict | None, tz: ZoneInfo) -> dict:
    """{"fajr": "05:31", ...} for local date d, with the person's offsets applied."""
    s = settings_with_defaults(prefs)
    noon = datetime.combine(d, time(12), tzinfo=tz)
    offset = noon.utcoffset().total_seconds() / 3600 if noon.utcoffset() else 0.0
    raw = compute(d, float(s["latitude"]), float(s["longitude"]), offset, s["method"], s["asr"])
    times: dict[str, str | None] = {}
    minutes: dict[str, int | None] = {}
    for k in PRAYERS:
        v = raw[k]
        if v is None:
            times[k], minutes[k] = None, None
            continue
        v += int(s["offsets"].get(k, 0) or 0) / 60
        times[k] = _hhmm(v)
        minutes[k] = int(round(v * 60))
    return {
        "date": d.isoformat(),
        "city": s["city"],
        "method": s["method"],
        "method_name": METHODS.get(s["method"], METHODS["MWL"])["name"],
        "asr": s["asr"],
        "times": times,
        "minutes": minutes,
    }


def prayer_of(title: str) -> str | None:
    key = "".join(ch for ch in title.casefold() if ch.isalpha())
    return ALIASES.get(key)


def next_prayer(now_local: datetime, prefs: dict | None, tz: ZoneInfo) -> dict | None:
    """The next of the five prayers (sunrise excluded) after now, possibly tomorrow's Fajr."""
    for d in (now_local.date(), now_local.date() + timedelta(days=1)):
        info = times_for(d, prefs, tz)
        for k in ("fajr", "dhuhr", "asr", "maghrib", "isha"):
            m = info["minutes"][k]
            if m is None:
                continue
            at = datetime.combine(d, time(0), tzinfo=tz) + timedelta(minutes=m)
            if at > now_local:
                return {"name": k, "label": LABELS[k], "time": info["times"][k], "at": at.isoformat(),
                        "minutes_left": int((at - now_local).total_seconds() // 60)}
    return None


def align_blocks(blocks: list[dict], info: dict, min_minutes: int = 10) -> list[dict]:
    """Moves prayer blocks ({"start_minute", "end_minute", "title", ...}) to the
    day's computed times, keeping their length; other blocks give way: the part
    a prayer now covers is cut out (a block spanning it is split in two)."""
    prayers, others = [], []
    for b in blocks:
        p = prayer_of(b["title"])
        m = info["minutes"].get(p) if p else None
        if m is not None and b["end_minute"] - b["start_minute"] <= 180:
            length = b["end_minute"] - b["start_minute"]
            prayers.append({**b, "start_minute": m, "end_minute": m + length, "prayer": p})
        else:
            others.append(b)
    result = list(prayers)
    for b in others:
        pieces = [(b["start_minute"], b["end_minute"])]
        if not b.get("is_fixed"):
            for p in prayers:
                cut = []
                for s, e in pieces:
                    if p["end_minute"] <= s or p["start_minute"] >= e:
                        cut.append((s, e))
                        continue
                    if s < p["start_minute"]:
                        cut.append((s, p["start_minute"]))
                    if p["end_minute"] < e:
                        cut.append((p["end_minute"], e))
                pieces = cut
        for s, e in pieces:
            if e - s >= min_minutes or (len(pieces) == 1 and (s, e) == (b["start_minute"], b["end_minute"])):
                result.append({**b, "start_minute": s, "end_minute": e})
    return sorted(result, key=lambda x: x["start_minute"])
