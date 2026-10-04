"""Where the life is going: goals, life chapters, habits, and day plans."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, UTCDateTime
from app.models.base import Timestamps, UserOwned
from app.models.ledger import Category

# Hours logged in these categories count as hours invested in the goal.
goal_categories = Table(
    "goal_categories",
    Base.metadata,
    Column("goal_id", ForeignKey("goals.id", ondelete="CASCADE"), primary_key=True),
    Column("category_id", ForeignKey("categories.id", ondelete="CASCADE"), primary_key=True),
)


class Goal(UserOwned, Timestamps, Base):
    __tablename__ = "goals"

    id: Mapped[int] = mapped_column(primary_key=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("goals.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    level: Mapped[str] = mapped_column(String(20), default="objective")  # vision | objective | milestone
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | done | paused | dropped
    start_date: Mapped[date | None] = mapped_column(Date)
    target_date: Mapped[date | None] = mapped_column(Date)
    # manual: `progress` is typed in. children: average of sub-goals.
    # hours: invested hours / hours_target. metric: from metric_start to metric_target.
    progress_mode: Mapped[str] = mapped_column(String(20), default="manual")
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    hours_target: Mapped[float | None] = mapped_column(Float)
    metric_unit: Mapped[str | None] = mapped_column(String(30))
    metric_start: Mapped[float | None] = mapped_column(Float)
    metric_target: Mapped[float | None] = mapped_column(Float)
    metric_current: Mapped[float | None] = mapped_column(Float)
    color: Mapped[str | None] = mapped_column(String(9))
    sort: Mapped[int] = mapped_column(Integer, default=0)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    categories: Mapped[list[Category]] = relationship(secondary=goal_categories, lazy="selectin")


class LifeChapter(UserOwned, Base):
    """An era of the life, past or planned, drawn on the weeks grid."""

    __tablename__ = "life_chapters"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    kind: Mapped[str] = mapped_column(String(10), default="past")  # past | plan
    color: Mapped[str | None] = mapped_column(String(9))
    description: Mapped[str | None] = mapped_column(Text)
    # The dates are a best guess and should be checked.
    approximate: Mapped[bool] = mapped_column(Boolean, default=False)
    sort: Mapped[int] = mapped_column(Integer, default=0)


class LifeEvent(UserOwned, Timestamps, Base):
    """A dated moment of the life — a move, an exam, a first time — shown on the
    weeks grid and in the timeline. Chapters are eras; events are points."""

    __tablename__ = "life_events"
    __table_args__ = (Index("ix_life_events_user_date", "user_id", "event_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    event_date: Mapped[date] = mapped_column(Date)
    # day | month | year: how much of event_date is actually known.
    precision: Mapped[str] = mapped_column(String(5), default="day")
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    # education | family | faith | health | work | move | travel | achievement | turning_point | other
    area: Mapped[str] = mapped_column(String(20), default="other")
    importance: Mapped[int] = mapped_column(Integer, default=2)  # 1 minor .. 3 defining
    is_private: Mapped[bool] = mapped_column(Boolean, default=False)


class Habit(UserOwned, Timestamps, Base):
    __tablename__ = "habits"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)
    # build: something to do. quit: something to stop — its streak is the number
    # of days since the last relapse.
    kind: Mapped[str] = mapped_column(String(10), default="build")
    target_per_week: Mapped[int] = mapped_column(Integer, default=7)
    # When set, the habit is evaluated from data instead of being ticked by hand:
    # {"type": "kind_hours_min", "kind": "core", "hours": 4}
    # {"type": "kind_hours_max", "kind": "noise", "hours": 1}
    # {"type": "journal_written"}
    rule: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    start_date: Mapped[date] = mapped_column(Date)
    is_private: Mapped[bool] = mapped_column(Boolean, default=False)
    color: Mapped[str | None] = mapped_column(String(9))
    icon: Mapped[str | None] = mapped_column(String(40))
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    sort: Mapped[int] = mapped_column(Integer, default=0)


class HabitLog(UserOwned, Base):
    __tablename__ = "habit_logs"
    __table_args__ = (Index("ix_habit_logs_habit_date", "habit_id", "log_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    habit_id: Mapped[int] = mapped_column(ForeignKey("habits.id", ondelete="CASCADE"))
    log_date: Mapped[date] = mapped_column(Date)
    # done | missed | skip | relapse | urge  (urge = resisted, which is a win)
    status: Mapped[str] = mapped_column(String(10))
    value: Mapped[float | None] = mapped_column(Float)
    occurred_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    note: Mapped[str | None] = mapped_column(Text)


class PlanBlock(UserOwned, Base):
    """A planned block of a given day, in local minutes since midnight."""

    __tablename__ = "plan_blocks"
    __table_args__ = (Index("ix_plan_blocks_user_date", "user_id", "plan_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    plan_date: Mapped[date] = mapped_column(Date)
    start_minute: Mapped[int] = mapped_column(Integer)
    end_minute: Mapped[int] = mapped_column(Integer)  # may exceed 1440: ends after midnight
    title: Mapped[str] = mapped_column(String(200))
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL")
    )
    is_fixed: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text)

    category: Mapped[Category | None] = relationship(lazy="joined")


class PlanTemplate(UserOwned, Timestamps, Base):
    """A reusable day shape, e.g. "Sprint weekday" or "Friday — Jumu'ah"."""

    __tablename__ = "plan_templates"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    weekdays: Mapped[list[int]] = mapped_column(JSON, default=list)  # 0 = Monday
    # [{"start": "05:40", "end": "06:10", "title": "Fajr", "category": "Prayer", "is_fixed": true}]
    blocks: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
