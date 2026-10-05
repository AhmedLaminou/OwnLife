"""The evening ritual and the morning summary.

    bed time − 30 min   "Capture Day 34": what is still unlogged, one click away
    bed time            the bedtime guard: screen off, phone away, and the
                        clean streak of the habit you are quitting
    wake time           yesterday in one line, today's Fajr, and yesterday's
                        review written (facts always; prose when a model is on)
    any time            the noise budget: 15 minutes before it is spent, when
                        it is, then every 15 minutes past it — counted from
                        everything the ledger knows of today (the YouTube
                        extension, the window tracker, your own entries)

Times come from the profile (its wake and bed times). A reminder is sent once per
day, and skipped if the laptop was off at the time and more than 45 minutes have
passed — a 22:30 reminder at 08:00 the next morning would be noise. Everything
is local: the toasts are Windows notifications sent by this machine.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import PROJECT_DIR, Settings
from app.db import SessionLocal, utcnow
from app.models import DayReview, Habit, Profile, User
from app.services import notify, prayer
from app.services.activitywatch import get_state
from app.services.ledger import covered_seconds, daily_breakdown, entries_overlapping, entry_kind, split_by_day
from app.services.records import running_timer
from app.services.timeutil import parse_hhmm, range_utc, tz_of

log = logging.getLogger("ownlife.reminders")

DEFAULTS: dict = {
    "enabled": True,
    "capture": True,
    "capture_before_bed_minutes": 30,
    "bedtime": True,
    "bedtime_text": "Bedtime. Screen off, phone in another room.",
    "morning": True,
    "auto_review": True,
    # "Still on it?" when a timer has run this long (then twice, three times as long); 0 = never.
    "timer_nudge_minutes": 120,
    # The noise budget (Settings → profile): a warning this many minutes before
    # it is spent (0 = none), a notification when it is, then one every
    # `noise_repeat_minutes` past it (0 = only once).
    "noise_alert": True,
    "noise_warn_minutes": 15,
    "noise_repeat_minutes": 15,
}
LATE_LIMIT = timedelta(minutes=45)


def settings_with_defaults(prefs: dict | None) -> dict:
    return {**DEFAULTS, **(prefs or {})}


def app_url(settings: Settings) -> str:
    """Where the person opens OwnLife: the Vite dev server in development, the
    backend itself when it serves the built frontend."""
    if settings.environment == "development" and not (PROJECT_DIR / "frontend" / "dist" / "index.html").exists():
        return "http://localhost:5173"
    host = "127.0.0.1" if settings.host in ("0.0.0.0", "::") else settings.host
    return f"http://{host}:{settings.port}"


def _hm(seconds: float) -> str:
    m = int(round(seconds / 60))
    return f"{m // 60}h{m % 60:02d}"


def _dur(seconds: float) -> str:
    m = int(round(seconds / 60))
    return f"{m} min" if m < 60 else _hm(seconds)


@dataclass
class Slot:
    key: str  # capture | bedtime | morning
    day: date  # the day the reminder belongs to
    at: datetime  # aware, local


def _at(d: date, hhmm: str, tz) -> datetime:
    h, m = parse_hhmm(hhmm)
    return datetime.combine(d, time(0), tzinfo=tz) + timedelta(hours=h, minutes=m)


def slots_for(profile: Profile, prefs: dict, d: date) -> list[Slot]:
    tz = tz_of(profile.timezone)
    bed = profile.bed_target or "23:00"
    wake = profile.wake_target or "06:00"
    bed_at = _at(d, bed, tz)
    if bed_at.hour < 12:  # a bed time after midnight belongs to the evening before
        bed_at += timedelta(days=1)
    out = []
    if prefs["capture"]:
        out.append(Slot("capture", d, bed_at - timedelta(minutes=int(prefs["capture_before_bed_minutes"]))))
    if prefs["bedtime"]:
        out.append(Slot("bedtime", d, bed_at))
    if prefs["morning"]:
        out.append(Slot("morning", d, _at(d, wake, tz)))
    return out


def day_number(profile: Profile, d: date) -> int | None:
    return (d - profile.awakening_date).days + 1 if profile.awakening_date else None


def _day_label(profile: Profile, d: date) -> str:
    n = day_number(profile, d)
    return f"Day {n}" if n else f"{d:%d/%m}"


def compose(db: Session, user: User, profile: Profile, slot: Slot, now: datetime, base: str) -> tuple[str, str, str, str]:
    """(title, body, url, button) for a slot."""
    tz = tz_of(profile.timezone)
    if slot.key == "capture":
        lo, hi = range_utc(slot.day, slot.day, tz)
        entries = entries_overlapping(db, user.id, lo, hi)
        logged = covered_seconds(entries, lo, min(hi, now), now)
        awake = max(0.0, (min(hi, now) - lo).total_seconds() - profile.sleep_target_hours * 3600)
        missing = max(0.0, awake - logged)
        body = f"Logged {_hm(logged)} so far" + (f"; about {_hm(missing)} of the waking day is not." if missing > 1800 else ".")
        return (f"Capture {_day_label(profile, slot.day)} before bed", body + " Two minutes, then sleep.",
                f"{base}/?capture=1", "Capture now")
    if slot.key == "bedtime":
        streak = _quit_streak(db, user, profile, now)
        prefs = settings_with_defaults((profile.prefs or {}).get("reminders"))
        body = prefs["bedtime_text"] + (f" Clean streak: {streak} day{'s' if streak != 1 else ''}." if streak else "")
        return (f"{slot.at:%H:%M} — bedtime", body, f"{base}/", "Open OwnLife")
    # morning
    y = slot.day - timedelta(days=1)
    kinds = daily_breakdown(db, user.id, y, y, tz, now).get(y, {})
    core, noise = kinds.get("core", 0.0), kinds.get("noise", 0.0) + kinds.get("destructive", 0.0)
    pt = prayer.times_for(slot.day, (profile.prefs or {}).get("prayer"), tz)["times"]
    parts = []
    if kinds:
        parts.append(f"Yesterday: core {_hm(core)} of {profile.focus_target_hours:g}h, noise {_hm(noise)}.")
    else:
        parts.append("Yesterday has nothing logged yet — capture it while you remember.")
    if pt.get("fajr"):
        parts.append(f"Fajr {pt['fajr']}, Dhuhr {pt['dhuhr']}.")
    return (f"{_day_label(profile, slot.day)} — good morning", " ".join(parts),
            f"{base}/assistant?tab=review&day={y.isoformat()}", "Yesterday's review")


def _quit_streak(db: Session, user: User, profile: Profile, now: datetime) -> int:
    from app.services.habits import habit_stats

    habit = db.scalar(select(Habit).where(Habit.user_id == user.id, Habit.kind == "quit", Habit.archived.is_(False))
                      .order_by(Habit.sort, Habit.id))
    if habit is None:
        return 0
    return int(habit_stats(db, habit, profile.timezone, now, days=60)["current_streak"])


def due(profile: Profile, prefs: dict, sent: dict, now: datetime) -> list[Slot]:
    """Slots whose time has come (and not by more than 45 minutes), not yet sent."""
    if not prefs["enabled"]:
        return []
    tz = tz_of(profile.timezone)
    today = now.astimezone(tz).date()
    out = []
    for d in (today - timedelta(days=1), today):
        for s in slots_for(profile, prefs, d):
            if sent.get(s.key) == s.day.isoformat():
                continue
            if s.at <= now < s.at + LATE_LIMIT:
                out.append(s)
    return out


def ensure_review(db: Session, settings: Settings, user: User, profile: Profile, d: date, now: datetime,
                  with_text: bool = True) -> DayReview:
    """The stored review of day d: facts always, prose when a model answers."""
    from app.ai.llm import AIUnavailableError, describe_error, effective_mode
    from app.ai.review import day_facts, write_review

    row = db.scalar(select(DayReview).where(DayReview.user_id == user.id, DayReview.review_date == d))
    if row is None:
        row = DayReview(user_id=user.id, review_date=d)
        db.add(row)
    row.facts = day_facts(db, user.id, profile, d, now)
    if with_text and effective_mode(settings, profile) != "off":
        try:
            row.text, row.model = write_review(settings, user, profile, row.facts)
            row.error = None
        except AIUnavailableError as e:
            row.error = str(e)
        except Exception as e:  # offline, rate-limited: the facts are still there
            row.error = describe_error(e)
    elif with_text:
        row.error = "AI is off: facts only."
    db.flush()
    return row


def _timer_nudge(db: Session, user: User, prefs: dict, sent: dict, now: datetime, base: str, send) -> tuple[bool, str]:
    """A timer left running: "still on it?" after N minutes, again after 2N and
    3N, never more — a forgotten timer otherwise counts a whole night as work."""
    running = running_timer(db, user.id)
    for key in [k for k in sent if k.startswith("timer:")]:
        if running is None or key != f"timer:{running.id}":
            del sent[key]  # that timer was stopped: forget it
    step = int(prefs.get("timer_nudge_minutes") or 0)
    if not prefs["enabled"] or running is None or step <= 0:
        return False, ""
    elapsed = (now - running.started_at).total_seconds()
    level = int(elapsed // (step * 60))
    key = f"timer:{running.id}"
    if not 1 <= level <= 3 or int(sent.get(key, 0)) >= level:
        return False, ""
    ok, detail = send(
        f"Still on “{running.title}”?",
        f"The timer has been running for {_hm(elapsed)}. If you switched to something else, "
        "pause or stop it at the time you did.",
        f"{base}/",
        "Open the timer",
    )
    sent[key] = level
    return True, "" if ok else detail


def noise_today(db: Session, user_id: int, profile: Profile, now: datetime) -> dict:
    """Today's noise so far, from every source (no minute counted twice), and
    the budget it is measured against."""
    tz = tz_of(profile.timezone)
    today = now.astimezone(tz).date()
    lo, hi = range_utc(today, today, tz)
    by = split_by_day(entries_overlapping(db, user_id, lo, hi), today, today, tz, now,
                      key=lambda e: (entry_kind(e), e.category.name if e.category else ""))[today]
    noise = sorted(((name, secs) for (kind, name), secs in by.items() if kind == "noise"), key=lambda x: -x[1])
    return {"seconds": round(sum(secs for _, secs in noise)), "budget_seconds": round(profile.noise_budget_hours * 3600),
            "categories": [{"name": n, "seconds": round(v)} for n, v in noise]}


def noise_level(seconds: float, budget: float, warn: float, repeat: float) -> int:
    """0 nothing to say · 1 the budget is nearly spent · 2 spent · 3, 4… each
    `repeat` seconds past it."""
    if seconds < 60:
        return 0
    if seconds >= budget:
        return 2 + (int((seconds - budget) // repeat) if repeat > 0 else 0)
    return 1 if 0 < warn < budget and seconds >= budget - warn else 0


def _noise_alert(db: Session, user: User, profile: Profile, prefs: dict, sent: dict, now: datetime, base: str,
                 send) -> tuple[bool, str]:
    """The noise budget, while the day goes: a word before it is spent, when it
    is, and every quarter of an hour past it — each once."""
    if not prefs["enabled"] or not prefs["noise_alert"]:
        return False, ""
    n = noise_today(db, user.id, profile, now)
    day = now.astimezone(tz_of(profile.timezone)).date().isoformat()
    level = noise_level(n["seconds"], n["budget_seconds"], int(prefs["noise_warn_minutes"] or 0) * 60,
                        int(prefs["noise_repeat_minutes"] or 0) * 60)
    last_day, last = (sent.get("noise") or [None, 0])[:2]
    if level == 0 or (last_day == day and last >= level):
        return False, ""
    spent, budget = n["seconds"], n["budget_seconds"]
    detail = ", ".join(f"{c['name']} {_dur(c['seconds'])}" for c in n["categories"][:3])
    if level == 1:
        title = f"{_dur(budget - spent)} of noise left today"
        body = f"Noise so far: {_dur(spent)} of your {_hm(budget)} — {detail}."
    elif level == 2:
        title = "Your hour of noise is spent" if budget == 3600 else f"Your {_hm(budget)} of noise is spent"
        body = f"Noise today: {_dur(spent)} — {detail}. The rest of the day is for your real work."
    else:
        title = f"Noise: {_dur(spent - budget)} over your budget"
        body = f"Noise today: {_dur(spent)} of {_hm(budget)} — {detail}. Close the tab?"
    ok, info = send(title, body, f"{base}/", "See today")
    sent["noise"] = [day, level]
    return True, "" if ok else info


def tick(settings: Settings, send=notify.send, now: datetime | None = None) -> list[str]:
    """One pass of the scheduler: send what is due, write yesterday's review."""
    from app.services.filesync import sync_owner

    now = now or utcnow()
    done: list[str] = []
    with SessionLocal() as db:
        user = sync_owner(db)
        if user is None:
            return done
        profile = db.get(Profile, user.id)
        prefs = settings_with_defaults((profile.prefs or {}).get("reminders"))
        state = get_state(db, user.id, "reminders")
        sent = dict(state.cursor or {})
        base = app_url(settings)
        for slot in due(profile, prefs, sent, now):
            title, body, url, button = compose(db, user, profile, slot, now, base)
            ok, detail = send(title, body, url, button)
            sent[slot.key] = slot.day.isoformat()
            state.last_error = None if ok else f"{slot.key}: {detail}"
            done.append(slot.key)
            log.info("Reminder %s for %s: %s", slot.key, slot.day, detail if not ok else "shown")
        nudged, error = _timer_nudge(db, user, prefs, sent, now, base, send)
        if nudged:
            done.append("timer")
            state.last_error = f"timer: {error}" if error else state.last_error
        warned, error = _noise_alert(db, user, profile, prefs, sent, now, base, send)
        if warned:
            done.append("noise")
            state.last_error = f"noise: {error}" if error else state.last_error
        tz = tz_of(profile.timezone)
        local = now.astimezone(tz)
        yesterday = local.date() - timedelta(days=1)
        wake_at = _at(local.date(), profile.wake_target or "06:00", tz)
        if prefs["auto_review"] and local >= wake_at and sent.get("review") != yesterday.isoformat():
            exists = db.scalar(select(DayReview.id).where(DayReview.user_id == user.id, DayReview.review_date == yesterday))
            if exists is None:
                ensure_review(db, settings, user, profile, yesterday, now)
                done.append("review")
            sent["review"] = yesterday.isoformat()
        state.cursor = sent
        state.last_synced_at = now
        db.commit()
    return done


def status(db: Session, profile: Profile, state_cursor: dict | None, now: datetime) -> list[dict]:
    """Today's reminders and whether each was sent, for the settings page."""
    prefs = settings_with_defaults((profile.prefs or {}).get("reminders"))
    tz = tz_of(profile.timezone)
    today = now.astimezone(tz).date()
    sent = state_cursor or {}
    return [
        {"key": s.key, "at": s.at.isoformat(), "time": s.at.strftime("%H:%M"),
         "sent": sent.get(s.key) == s.day.isoformat(), "past": s.at <= now}
        for s in sorted(slots_for(profile, prefs, today), key=lambda s: s.at)
    ] if prefs["enabled"] else []
