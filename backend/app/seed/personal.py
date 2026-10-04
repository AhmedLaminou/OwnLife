"""Applies a personal seed file (backend/data/personal_seed.json, git-ignored) to an account.

The file pre-fills what is already known — profile, life chapters, goals, habits,
people, classification rules, media — so the app starts alive instead of empty.
Applying it twice changes nothing: every section is matched on a natural key
(title, name, pattern) and only missing rows are created.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Category,
    ClassificationRule,
    Goal,
    Habit,
    HabitLog,
    LifeChapter,
    LifeEvent,
    MediaItem,
    Person,
    PlanTemplate,
    Profile,
    User,
)
from app.seed.defaults import DEFAULT_TEMPLATE
from app.services.timeutil import local_instant, tz_of

PROFILE_FIELDS = {
    "birth_date", "life_expectancy_years", "timezone", "currency", "awakening_date", "mission_title",
    "mission_text", "focus_target_hours", "stretch_focus_hours", "sleep_target_hours", "noise_budget_hours",
    "wake_target", "bed_target", "glossary", "redactions", "ai_settings",
}
DATE_FIELDS = {"birth_date", "awakening_date"}


def _d(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def apply_personal_seed(db: Session, user: User, data: dict) -> dict:
    report: dict[str, int] = {}

    def bump(key: str, n: int = 1) -> None:
        report[key] = report.get(key, 0) + n

    profile = db.get(Profile, user.id) or Profile(user_id=user.id)
    db.add(profile)
    for key, value in (data.get("profile") or {}).items():
        if key == "display_name":
            user.display_name = value
        elif key in PROFILE_FIELDS:
            setattr(profile, key, _d(value) if key in DATE_FIELDS else value)
    tz = tz_of(profile.timezone)

    cats = {c.name: c for c in db.scalars(select(Category).where(Category.user_id == user.id))}
    for old, new in (data.get("rename_categories") or {}).items():
        if old in cats and new not in cats:
            cats[old].name = new
            cats[new] = cats.pop(old)
            bump("categories_renamed")
    for c in data.get("categories") or []:
        if c["name"] not in cats:
            cat = Category(user_id=user.id, name=c["name"], kind=c["kind"], icon=c.get("icon"),
                           is_private=c.get("is_private", False), sort=len(cats))
            db.add(cat)
            cats[c["name"]] = cat
            bump("categories")
    db.flush()

    def cat_id(name: str | None) -> int | None:
        if not name:
            return None
        c = cats.get(name)
        if c is None:
            raise ValueError(f"personal seed refers to unknown category {name!r}")
        return c.id

    existing_chapters = {c.title for c in db.scalars(select(LifeChapter).where(LifeChapter.user_id == user.id))}
    for i, ch in enumerate(data.get("chapters") or []):
        if ch["title"] in existing_chapters:
            continue
        db.add(LifeChapter(user_id=user.id, title=ch["title"], start_date=_d(ch["start_date"]),
                           end_date=_d(ch.get("end_date")), kind=ch.get("kind", "past"), color=ch.get("color"),
                           description=ch.get("description"), approximate=ch.get("approximate", False), sort=i))
        bump("chapters")

    def add_goals(items: list[dict], parent: Goal | None) -> None:
        for i, g in enumerate(items):
            same_parent = Goal.parent_id == parent.id if parent else Goal.parent_id.is_(None)
            found = db.scalar(select(Goal).where(Goal.user_id == user.id, Goal.title == g["title"], same_parent))
            if found is None:
                found = Goal(
                    user_id=user.id, parent_id=parent.id if parent else None, title=g["title"],
                    description=g.get("description"), level=g.get("level", "objective"),
                    status=g.get("status", "active"), start_date=_d(g.get("start_date")),
                    target_date=_d(g.get("target_date")), progress_mode=g.get("progress_mode", "manual"),
                    progress=g.get("progress", 0), hours_target=g.get("hours_target"),
                    metric_unit=g.get("metric_unit"), metric_start=g.get("metric_start"),
                    metric_target=g.get("metric_target"), metric_current=g.get("metric_current"), sort=i,
                )
                found.categories = [cats[n] for n in g.get("categories", [])]
                db.add(found)
                db.flush()
                bump("goals")
            add_goals(g.get("children") or [], found)

    add_goals(data.get("goals") or [], None)

    habits = {h.name: h for h in db.scalars(select(Habit).where(Habit.user_id == user.id))}
    for old, new in (data.get("rename_habits") or {}).items():
        if old in habits and new not in habits:
            habits[old].name = new
            habits[new] = habits.pop(old)
            bump("habits_renamed")
    for h in data.get("habits") or []:
        habit = habits.get(h["name"])
        if habit is None:
            habit = Habit(user_id=user.id, name=h["name"], kind=h.get("kind", "build"),
                          description=h.get("description"), target_per_week=h.get("target_per_week", 7),
                          rule=h.get("rule"), start_date=_d(h.get("start_date")) or date.today(),
                          is_private=h.get("is_private", False), icon=h.get("icon"), sort=len(habits))
            db.add(habit)
            db.flush()
            habits[h["name"]] = habit
            bump("habits")
        for log in h.get("logs") or []:
            d = _d(log["date"])
            exists = db.scalar(select(HabitLog).where(
                HabitLog.habit_id == habit.id, HabitLog.log_date == d, HabitLog.status == log["status"]))
            if exists:
                continue
            when = local_instant(d, log["time"], tz) if log.get("time") else None
            db.add(HabitLog(user_id=user.id, habit_id=habit.id, log_date=d, status=log["status"],
                            occurred_at=when, note=log.get("note")))
            bump("habit_logs")

    people = {p.name.casefold() for p in db.scalars(select(Person).where(Person.user_id == user.id))}
    for p in data.get("people") or []:
        if p["name"].casefold() in people:
            continue
        db.add(Person(user_id=user.id, name=p["name"], relation=p.get("relation"), notes=p.get("notes"),
                      tags=p.get("tags", [])))
        people.add(p["name"].casefold())
        bump("people")

    rules = {(r.field, r.pattern) for r in db.scalars(
        select(ClassificationRule).where(ClassificationRule.user_id == user.id))}
    for r in data.get("rules") or []:
        if (r["field"], r["pattern"]) in rules:
            continue
        db.add(ClassificationRule(user_id=user.id, field=r["field"], pattern=r["pattern"],
                                  is_regex=r.get("is_regex", False), category_id=cat_id(r["category"]),
                                  priority=r.get("priority", 100), note=r.get("note")))
        rules.add((r["field"], r["pattern"]))
        bump("rules")

    media = {(m.kind, m.title.casefold()) for m in db.scalars(select(MediaItem).where(MediaItem.user_id == user.id))}
    for m in data.get("media") or []:
        if (m["kind"], m["title"].casefold()) in media:
            continue
        db.add(MediaItem(user_id=user.id, kind=m["kind"], title=m["title"], creator=m.get("creator"),
                         url=m.get("url"), category_id=cat_id(m.get("category")), status=m.get("status", "none"),
                         progress_current=m.get("progress_current"), progress_total=m.get("progress_total"),
                         progress_unit=m.get("progress_unit"), notes=m.get("notes")))
        media.add((m["kind"], m["title"].casefold()))
        bump("media")

    events = {(e.title.casefold(), e.event_date) for e in db.scalars(select(LifeEvent).where(LifeEvent.user_id == user.id))}
    for ev in data.get("events") or []:
        key = (ev["title"].casefold(), _d(ev["date"]))
        if key in events:
            continue
        db.add(LifeEvent(user_id=user.id, event_date=_d(ev["date"]), precision=ev.get("precision", "day"),
                         title=ev["title"], description=ev.get("description"), area=ev.get("area", "other"),
                         importance=ev.get("importance", 2), is_private=ev.get("is_private", False)))
        events.add(key)
        bump("events")

    # A generic default the personal templates replace — removed only if untouched.
    for name in data.get("remove_default_templates") or []:
        t = db.scalar(select(PlanTemplate).where(PlanTemplate.user_id == user.id, PlanTemplate.name == name))
        if t is not None and name == DEFAULT_TEMPLATE["name"] and (t.blocks or []) == DEFAULT_TEMPLATE["blocks"]:
            db.delete(t)
            bump("templates_removed")

    templates = {t.name for t in db.scalars(select(PlanTemplate).where(PlanTemplate.user_id == user.id))}
    for t in data.get("plan_templates") or []:
        if t["name"] in templates:
            continue
        for b in t["blocks"]:
            cat_id(b.get("category"))  # fail early on a typo
        db.add(PlanTemplate(user_id=user.id, name=t["name"], weekdays=t.get("weekdays", []), blocks=t["blocks"]))
        bump("plan_templates")

    db.flush()
    return report


def load_seed(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))
