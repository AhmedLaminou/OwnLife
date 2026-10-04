"""Habit evaluation and streaks.

A "build" habit is either ticked by hand or evaluated from data (its `rule`).
A "quit" habit counts clean days since the last relapse; resisted urges are
logged too, because resisting is the actual skill being trained.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Habit, HabitLog, JournalEntry
from app.services.ledger import TRACKED_DAY_MIN_SECONDS, daily_breakdown
from app.services.timeutil import daterange, local_today, tz_of

QUIT_MILESTONES = (1, 3, 7, 14, 21, 30, 60, 90, 180, 365, 730)


def _rule_status(rule: dict, d: date, today: date, kinds: dict[str, float], has_journal: bool) -> str | None:
    t = rule.get("type")
    tracked = sum(kinds.values()) >= TRACKED_DAY_MIN_SECONDS
    if t == "journal_written":
        if has_journal:
            return "done"
        return "pending" if d == today else "missed"
    if t in ("kind_hours_min", "kind_hours_max"):
        hours = kinds.get(rule.get("kind", ""), 0.0) / 3600
        if rule.get("kind") == "noise":
            hours += kinds.get("destructive", 0.0) / 3600
        threshold = float(rule.get("hours", 0))
        if t == "kind_hours_min":
            if hours >= threshold:
                return "done"
            if d == today:
                return "pending"
            return "missed" if tracked else None
        # kind_hours_max: only judged on days that were tracked
        if not tracked:
            return "pending" if d == today else None
        if hours <= threshold:
            return "pending" if d == today else "done"
        return "missed"
    return None


def build_calendar(
    db: Session, habit: Habit, start: date, end: date, today: date, tz, now: datetime
) -> dict[date, str | None]:
    logs = db.scalars(
        select(HabitLog).where(
            HabitLog.habit_id == habit.id, HabitLog.log_date >= start, HabitLog.log_date <= end
        )
    ).all()
    by_date: dict[date, list[HabitLog]] = {}
    for log in logs:
        by_date.setdefault(log.log_date, []).append(log)

    cal: dict[date, str | None] = {}
    first = max(start, habit.start_date)
    if habit.kind == "quit":
        for d in daterange(start, end):
            if d < habit.start_date:
                cal[d] = None
                continue
            statuses = {log.status for log in by_date.get(d, [])}
            if "relapse" in statuses:
                cal[d] = "relapse"
            elif "urge" in statuses:
                cal[d] = "urge"
            else:
                cal[d] = "clean" if d < today else "pending"
        return cal

    kinds_by_day: dict[date, dict[str, float]] = {}
    journal_days: set[date] = set()
    if habit.rule:
        kinds_by_day = daily_breakdown(db, habit.user_id, first, end, tz, now) if first <= end else {}
        journal_days = set(
            db.scalars(
                select(JournalEntry.entry_date).where(
                    JournalEntry.user_id == habit.user_id,
                    JournalEntry.visible(),
                    JournalEntry.entry_date >= first,
                    JournalEntry.entry_date <= end,
                )
            )
        )
    for d in daterange(start, end):
        if d < habit.start_date:
            cal[d] = None
            continue
        manual = [log.status for log in by_date.get(d, [])]
        if manual:
            cal[d] = manual[-1]
        elif habit.rule:
            cal[d] = _rule_status(habit.rule, d, today, kinds_by_day.get(d, {}), d in journal_days)
        else:
            cal[d] = "pending" if d == today else None
    return cal


def _build_streaks(cal: dict[date, str | None], today: date) -> tuple[int, int]:
    days = sorted(cal)
    best = run = 0
    for d in days:
        s = cal[d]
        if s == "done":
            run += 1
            best = max(best, run)
        elif s in ("skip", "pending") or (s is None and d == today):
            continue
        else:
            run = 0
    # current: walk back from today; an unfinished today does not break the streak
    current = 0
    for d in reversed(days):
        s = cal[d]
        if s == "done":
            current += 1
        elif s in ("skip", "pending"):
            continue
        else:
            break
    return current, best


def habit_stats(db: Session, habit: Habit, profile_tz: str, now: datetime, days: int = 120) -> dict:
    tz = tz_of(profile_tz)
    today = local_today(tz)
    start = today - timedelta(days=days - 1)
    if habit.kind == "quit":
        return _quit_stats(db, habit, tz, today, start, now)

    cal = build_calendar(db, habit, min(start, habit.start_date), today, today, tz, now)
    current, best = _build_streaks(cal, today)
    last30 = [cal.get(today - timedelta(days=i)) for i in range(30)]
    done = sum(1 for s in last30 if s == "done")
    judged = sum(1 for s in last30 if s in ("done", "missed"))
    return {
        "habit_id": habit.id,
        "kind": "build",
        "current_streak": current,
        "best_streak": best,
        "done_last_30": done,
        "rate_last_30": round(done / judged, 3) if judged else None,
        "today": cal.get(today),
        "calendar": [
            {"date": d.isoformat(), "status": cal[d]} for d in sorted(cal) if d >= start
        ],
    }


def _quit_stats(db: Session, habit: Habit, tz, today: date, start: date, now: datetime) -> dict:
    logs = db.scalars(
        select(HabitLog).where(HabitLog.habit_id == habit.id).order_by(HabitLog.log_date)
    ).all()
    relapse_dates = sorted({log.log_date for log in logs if log.status == "relapse"})
    urges = [log for log in logs if log.status == "urge"]

    anchor = relapse_dates[-1] if relapse_dates else habit.start_date
    current = max(0, (today - anchor).days)
    # Best run: the longest clean gap, counting the current one.
    points = [habit.start_date, *relapse_dates, today]
    best = max((b - a).days for a, b in zip(points, points[1:])) if len(points) > 1 else current
    best = max(best, current)
    next_milestone = next((m for m in QUIT_MILESTONES if m > current), None)

    hours = Counter()
    weekdays = Counter()
    for log in logs:
        if log.status == "relapse":
            weekdays[log.log_date.weekday()] += 1
            if log.occurred_at is not None:
                hours[log.occurred_at.astimezone(tz).hour] += 1

    cal = build_calendar(db, habit, start, today, today, tz, now)
    return {
        "habit_id": habit.id,
        "kind": "quit",
        "current_streak": current,
        "best_streak": best,
        "clean_since": anchor.isoformat(),
        "relapses_total": len(relapse_dates),
        "relapses_last_30": sum(1 for d in relapse_dates if d > today - timedelta(days=30)),
        "urges_resisted_total": len(urges),
        "urges_resisted_last_30": sum(1 for u in urges if u.log_date > today - timedelta(days=30)),
        "next_milestone": next_milestone,
        "days_to_next_milestone": (next_milestone - current) if next_milestone else None,
        "relapse_hours": [hours.get(h, 0) for h in range(24)],
        "relapse_weekdays": [weekdays.get(w, 0) for w in range(7)],
        "today": cal.get(today),
        "calendar": [{"date": d.isoformat(), "status": cal[d]} for d in sorted(cal)],
    }
