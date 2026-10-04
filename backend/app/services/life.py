"""The scale of a life: weeks lived and left, and where the current habits lead.

The arithmetic of a "scale of life" essay, made live. The essay used assumed habits;
here the averages come from the ledger, so the projection changes as the days do.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import JournalEntry, Profile, TimeEntry
from app.services.ledger import (
    KIND_ORDER,
    average_hours_per_kind,
    daily_breakdown,
    entries_overlapping,
    split_by_day,
)
from app.services.timeutil import local_date_of, local_today, range_utc, tz_of

DAYS_PER_YEAR = 365.2425
HOURS_PER_YEAR = 24 * DAYS_PER_YEAR
# Meals, hygiene, transport: the unavoidable upkeep the essay assumed.
MAINTENANCE_BASELINE_HOURS = 3.5
MEASURE_WINDOW_DAYS = 30


def add_years(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:  # born on 29 February
        return d.replace(year=d.year + years, day=28)


def _projection(kind: str, hours_per_day: float, days_to_60: int, days_left: int, sleep: float) -> dict:
    return {
        "kind": kind,
        "avg_hours_per_day": round(hours_per_day, 3),
        "hours_until_60": round(hours_per_day * days_to_60),
        "continuous_years_until_60": round(hours_per_day * days_to_60 / HOURS_PER_YEAR, 2),
        "hours_remaining_life": round(hours_per_day * days_left),
        "continuous_years_remaining": round(hours_per_day * days_left / HOURS_PER_YEAR, 2),
        "share_of_waking_day": round(hours_per_day / max(1e-9, 24 - sleep), 4),
    }


def life_overview(db: Session, user_id: int, profile: Profile, now: datetime) -> dict:
    tz = tz_of(profile.timezone)
    today = local_today(tz)
    if profile.birth_date is None:
        return {"configured": False, "today": today.isoformat()}

    birth = profile.birth_date
    end = add_years(birth, profile.life_expectancy_years)
    sixty = add_years(birth, 60)
    days_alive = (today - birth).days
    days_left = max(0, (end - today).days)
    days_to_60 = max(0, (sixty - today).days)
    sleep = profile.sleep_target_hours
    total_weeks = (end - birth).days // 7

    age_this_year = today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))
    next_birthday = add_years(birth, age_this_year + 1)

    window_start = today - timedelta(days=MEASURE_WINDOW_DAYS)
    breakdown = daily_breakdown(db, user_id, window_start, today - timedelta(days=1), tz, now)
    avg, tracked_days = average_hours_per_kind(breakdown)
    projections = [
        _projection(k, avg[k], days_to_60, days_left, sleep)
        for k in (*KIND_ORDER, "uncategorized")
        if avg.get(k, 0) > 0
    ]

    what_if = None
    noise_avg = avg.get("noise", 0.0) + avg.get("destructive", 0.0)
    if tracked_days and noise_avg > profile.noise_budget_hours:
        reclaim = noise_avg - profile.noise_budget_hours
        what_if = {
            "noise_avg_hours": round(noise_avg, 2),
            "noise_budget_hours": profile.noise_budget_hours,
            "reclaimed_hours_per_day": round(reclaim, 2),
            "reclaimed_hours_until_60": round(reclaim * days_to_60),
            "reclaimed_continuous_years_until_60": round(reclaim * days_to_60 / HOURS_PER_YEAR, 2),
            "core_continuous_years_until_60_if_redirected": round(
                (avg.get("core", 0.0) + reclaim) * days_to_60 / HOURS_PER_YEAR, 2
            ),
        }

    awakening = None
    if profile.awakening_date:
        a = profile.awakening_date
        since = max(0, (today - a).days)
        totals = since_totals(db, user_id, a, today, tz, now)
        awakening = {
            "date": a.isoformat(),
            "days_since": since,
            "day_number": since + 1,
            "weeks_since": since // 7,
            "age_at_awakening": round((a - birth).days / DAYS_PER_YEAR, 2),
            "share_of_life_before": round((a - birth).days / max(1, days_alive), 4),
            "hours_by_kind": totals,
        }

    return {
        "configured": True,
        "today": today.isoformat(),
        "birth_date": birth.isoformat(),
        "life_expectancy_years": profile.life_expectancy_years,
        "end_date": end.isoformat(),
        "age_years": round(days_alive / DAYS_PER_YEAR, 3),
        "age": age_this_year,
        "next_birthday": next_birthday.isoformat(),
        "days_to_next_birthday": (next_birthday - today).days,
        "days_alive": days_alive,
        "weeks_alive": days_alive // 7,
        "total_weeks": total_weeks,
        "weeks_left": max(0, total_weeks - days_alive // 7),
        "days_left": days_left,
        "days_to_60": days_to_60,
        "hours_left": days_left * 24,
        "sleep_hours_per_day": sleep,
        "sleep_years_left": round(days_left * sleep / HOURS_PER_YEAR, 2),
        "waking_hours_left": round(days_left * (24 - sleep)),
        "discretionary_hours_left": round(
            days_left * max(0.0, 24 - sleep - MAINTENANCE_BASELINE_HOURS)
        ),
        "measured": {
            "window_days": MEASURE_WINDOW_DAYS,
            "tracked_days": tracked_days,
            "avg_hours_by_kind": {k: round(v, 3) for k, v in avg.items()},
        },
        "projections": projections,
        "what_if": what_if,
        "awakening": awakening,
    }


def since_totals(
    db: Session, user_id: int, start: date, end: date, tz, now: datetime
) -> dict[str, float]:
    breakdown = daily_breakdown(db, user_id, start, end, tz, now)
    totals: dict[str, float] = {}
    for kinds in breakdown.values():
        for k, v in kinds.items():
            totals[k] = totals.get(k, 0.0) + v
    return {k: round(v / 3600, 2) for k, v in totals.items()}


def weekly_hours(db: Session, user_id: int, profile: Profile, now: datetime) -> dict[int, dict]:
    """Hours per kind for every week of life that has data, keyed by week index
    (0 = the week of birth). Feeds the colour of the life grid's cells."""
    if profile.birth_date is None:
        return {}
    tz = tz_of(profile.timezone)
    first = db.scalar(select(func.min(TimeEntry.started_at)).where(TimeEntry.user_id == user_id))
    journal_dates = set(
        db.scalars(select(JournalEntry.entry_date).where(JournalEntry.user_id == user_id, JournalEntry.visible()))
    )
    out: dict[int, dict] = {}
    birth = profile.birth_date
    if first is not None:
        start = max(local_date_of(first, tz), birth)
        end = local_today(tz)
        s, e = range_utc(start, end, tz)
        days = split_by_day(entries_overlapping(db, user_id, s, e), start, end, tz, now)
        for d, kinds in days.items():
            if not kinds:
                continue
            w = out.setdefault((d - birth).days // 7, {"hours": {}, "journal_days": 0})
            for k, v in kinds.items():
                w["hours"][k] = round(w["hours"].get(k, 0.0) + v / 3600, 2)
    for d in journal_dates:
        if d >= birth:
            w = out.setdefault((d - birth).days // 7, {"hours": {}, "journal_days": 0})
            w["journal_days"] += 1
    return out
