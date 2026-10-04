"""The personal seed loader, with an inline seed (tests never read real personal data)."""

from app.db import SessionLocal
from app.models import User
from app.seed.personal import apply_personal_seed

SEED = {
    "profile": {"display_name": "Seeded", "birth_date": "2000-01-01", "awakening_date": "2026-08-31",
                "timezone": "Africa/Niamey", "glossary": [{"term": "[X]", "meaning": "y", "private": False}]},
    "rename_categories": {"Destructive habits": "{Alias}"},
    "categories": [{"name": "Woodworking", "kind": "core"}],
    "chapters": [{"title": "School", "start_date": "2004-09-01", "end_date": "2016-06-30", "approximate": True}],
    "goals": [{"title": "Vision", "level": "vision", "progress_mode": "children", "children": [
        {"title": "Maths", "progress_mode": "hours", "hours_target": 10, "categories": ["Mathematics"]}]}],
    "rename_habits": {"Workout": "Workout — routine"},
    "habits": [{"name": "{Alias}-free", "kind": "quit", "is_private": True, "start_date": "2026-09-01",
                "logs": [{"date": "2026-09-10", "status": "relapse", "time": "21:15"}]}],
    "people": [{"name": "Sam", "relation": "friend"}],
    "rules": [{"field": "channel", "pattern": "ReactionHub", "category": "Reaction videos"}],
    "media": [{"kind": "book", "title": "On the Origin of Species", "category": "Reading", "status": "in_progress"}],
    "plan_templates": [{"name": "Day", "weekdays": [0], "blocks": [
        {"start": "05:40", "end": "06:10", "title": "Fajr", "category": "Prayer"}]}],
}


def test_seed_applies_once_and_is_idempotent(client):
    with SessionLocal() as db:
        user = db.query(User).one()
        first = apply_personal_seed(db, user, SEED)
        db.commit()
        second = apply_personal_seed(db, user, SEED)
        db.commit()
    assert first["goals"] == 2 and first["habit_logs"] == 1 and first["rules"] == 1
    assert second == {}  # nothing new the second time

    cats = {c["name"] for c in client.get("/api/categories").json()}
    assert "{Alias}" in cats and "Destructive habits" not in cats and "Woodworking" in cats
    habits = {h["name"]: h for h in client.get("/api/habits").json()}
    assert "Workout — routine" in habits
    assert habits["{Alias}-free"]["stats"]["relapses_total"] == 1
    assert client.get("/api/profile").json()["display_name"] == "Seeded"
    assert client.get("/api/goals").json()[0]["children"][0]["title"] == "Maths"


def test_seed_rejects_unknown_category(client):
    import pytest

    bad = {"rules": [{"field": "channel", "pattern": "x", "category": "Does not exist"}]}
    with SessionLocal() as db:
        user = db.query(User).one()
        with pytest.raises(ValueError, match="unknown category"):
            apply_personal_seed(db, user, bad)
