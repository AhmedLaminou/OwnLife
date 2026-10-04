"""What every new account starts with. Personal data never lives here: it comes
from the git-ignored backend/data/personal_seed.json (see app/seed/personal.py)."""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.models import Category, Habit, PlanTemplate, User

# (name, kind, icon, private). Icons are lucide icon names.
DEFAULT_CATEGORIES: list[tuple[str, str, str, bool]] = [
    ("Mathematics", "core", "sigma", False),
    ("Physics", "core", "atom", False),
    ("Computer Science", "core", "cpu", False),
    ("AI & ML", "core", "brain", False),
    ("Robotics", "core", "bot", False),
    ("Building projects", "core", "hammer", False),
    ("Broad learning", "growth", "library", False),
    ("Reading", "growth", "book-open", False),
    ("Languages", "growth", "languages", False),
    ("Writing & journaling", "growth", "pen-line", False),
    ("Job / Internship", "work", "briefcase", False),
    ("Prayer", "spirit", "moon-star", False),
    ("Quran & theology", "spirit", "book-marked", False),
    ("Family", "social", "house", False),
    ("Friends", "social", "users", False),
    ("Workout", "body", "dumbbell", False),
    ("Sleep", "maintenance", "bed", False),
    ("Meals", "maintenance", "utensils", False),
    ("Hygiene", "maintenance", "droplets", False),
    ("Commute", "maintenance", "car", False),
    ("Chores & errands", "maintenance", "shopping-bag", False),
    ("Anime & series", "noise", "tv", False),
    ("Reaction videos", "noise", "clapperboard", False),
    ("News & geopolitics", "noise", "newspaper", False),
    ("Documentaries (non-science)", "noise", "film", False),
    ("Music videos", "noise", "music", False),
    ("Social media", "noise", "smartphone", False),
    ("Destructive habits", "destructive", "shield-alert", True),
]

DEFAULT_HABITS: list[dict] = [
    {
        "name": "Minimum day: 4h of core work",
        "kind": "build",
        "rule": {"type": "kind_hours_min", "kind": "core", "hours": 4},
        "description": "Evaluated from the ledger. A floor, not the target: the "
        "target lives in the profile.",
        "icon": "target",
    },
    {
        "name": "Noise budget: 1h or less",
        "kind": "build",
        "rule": {"type": "kind_hours_max", "kind": "noise", "hours": 1},
        "description": "Evaluated from the ledger. Only counts days that have "
        "something logged.",
        "icon": "volume-x",
    },
    {
        "name": "Write the journal",
        "kind": "build",
        "rule": {"type": "journal_written"},
        "icon": "notebook-pen",
    },
    {"name": "Workout", "kind": "build", "target_per_week": 5, "icon": "dumbbell"},
]

DEFAULT_TEMPLATE = {
    "name": "Deep work day",
    "weekdays": [0, 1, 2, 3, 4, 5],
    "blocks": [
        {"start": "06:00", "end": "08:30", "title": "Deep work I", "category": "Mathematics"},
        {"start": "08:30", "end": "09:00", "title": "Breakfast", "category": "Meals"},
        {"start": "09:00", "end": "12:00", "title": "Deep work II", "category": "AI & ML"},
        {"start": "14:00", "end": "17:00", "title": "Deep work III", "category": "Computer Science"},
        {"start": "17:30", "end": "18:30", "title": "Workout", "category": "Workout"},
        {"start": "23:00", "end": "06:00", "title": "Sleep", "category": "Sleep", "is_fixed": True},
    ],
}


def seed_new_user(db: Session, user: User, today: date) -> None:
    for i, (name, kind, icon, private) in enumerate(DEFAULT_CATEGORIES):
        db.add(Category(user_id=user.id, name=name, kind=kind, icon=icon, is_private=private, sort=i))
    for i, h in enumerate(DEFAULT_HABITS):
        db.add(
            Habit(
                user_id=user.id,
                name=h["name"],
                kind=h["kind"],
                rule=h.get("rule"),
                description=h.get("description"),
                target_per_week=h.get("target_per_week", 7),
                icon=h.get("icon"),
                start_date=today,
                sort=i,
            )
        )
    db.add(PlanTemplate(user_id=user.id, **DEFAULT_TEMPLATE))
