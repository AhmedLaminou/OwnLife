"""All models, imported here so that Base.metadata knows every table."""

from app.models.assistant import CaptureDraft, ChatMessage, ChatThread, DayReview, IntegrationState
from app.models.consumption import MediaItem, MoneySuggestion, Transaction, WatchEvent
from app.models.core import ApiToken, AuthSession, Profile, User
from app.models.direction import (
    Goal,
    Habit,
    HabitLog,
    LifeChapter,
    LifeEvent,
    PlanBlock,
    PlanTemplate,
    goal_categories,
)
from app.models.ledger import (
    CATEGORY_KINDS,
    Category,
    ClassificationRule,
    Person,
    TimeEntry,
    time_entry_people,
)
from app.models.memory import Chunk, JournalEntry, Note

__all__ = [
    "CATEGORY_KINDS",
    "ApiToken",
    "AuthSession",
    "CaptureDraft",
    "Category",
    "ChatMessage",
    "ChatThread",
    "Chunk",
    "ClassificationRule",
    "DayReview",
    "Goal",
    "Habit",
    "HabitLog",
    "IntegrationState",
    "JournalEntry",
    "LifeChapter",
    "LifeEvent",
    "MediaItem",
    "MoneySuggestion",
    "Note",
    "Person",
    "PlanBlock",
    "PlanTemplate",
    "Profile",
    "TimeEntry",
    "Transaction",
    "User",
    "WatchEvent",
    "goal_categories",
    "time_entry_people",
]
