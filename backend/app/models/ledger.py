"""The time ledger: categories, time entries, the people they were spent with,
and the rules that classify imported activity."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, UTCDateTime
from app.models.base import Timestamps, UserOwned

# How a category relates to the mission. The ledger's "signal vs noise" accounting
# is computed from these kinds, not from category names.
CATEGORY_KINDS = (
    "core",         # the mission itself: maths, physics, CS, AI, robotics, building
    "growth",       # useful learning outside the core
    "work",         # job / internship
    "spirit",       # prayer, Quran, theology
    "body",         # training, sport
    "maintenance",  # sleep, meals, hygiene, commute, chores
    "social",       # family, friends
    "noise",        # entertainment that was not chosen deliberately
    "destructive",  # what the person has decided to quit
)

time_entry_people = Table(
    "time_entry_people",
    Base.metadata,
    Column("time_entry_id", ForeignKey("time_entries.id", ondelete="CASCADE"), primary_key=True),
    Column("person_id", ForeignKey("people.id", ondelete="CASCADE"), primary_key=True),
)


class Category(UserOwned, Base):
    __tablename__ = "categories"
    __table_args__ = (UniqueConstraint("user_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL")
    )
    name: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(20))
    color: Mapped[str | None] = mapped_column(String(9))
    icon: Mapped[str | None] = mapped_column(String(40))
    is_private: Mapped[bool] = mapped_column(Boolean, default=False)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    sort: Mapped[int] = mapped_column(Integer, default=0)


class Person(UserOwned, Timestamps, Base):
    __tablename__ = "people"
    __table_args__ = (UniqueConstraint("user_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    relation: Mapped[str | None] = mapped_column(String(40))
    notes: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    is_private: Mapped[bool] = mapped_column(Boolean, default=False)


class PersonMoment(UserOwned, Timestamps, Base):
    """Something between you and a person, on a day: a gift they gave you
    (gift_from), one you gave them (gift_to), or a moment — what they did or
    said. Money goes through transactions; time through time entries. A private
    moment never reaches a cloud model."""

    __tablename__ = "person_moments"
    __table_args__ = (Index("ix_person_moments_person_date", "person_id", "occurred_on"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("people.id", ondelete="CASCADE"))
    occurred_on: Mapped[date] = mapped_column(Date)
    kind: Mapped[str] = mapped_column(String(12), default="moment")  # gift_from | gift_to | moment
    text: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(20), default="manual")  # manual | ai | capture | journal
    journal_entry_id: Mapped[int | None] = mapped_column(ForeignKey("journal_entries.id", ondelete="SET NULL"))
    is_private: Mapped[bool] = mapped_column(Boolean, default=False)


class TimeEntry(UserOwned, Timestamps, Base):
    """One block of time. A running timer is an entry whose ended_at is NULL."""

    __tablename__ = "time_entries"
    __table_args__ = (
        UniqueConstraint("user_id", "source", "source_ref"),
        Index("ix_time_entries_user_start", "user_id", "started_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL"), index=True
    )
    title: Mapped[str] = mapped_column(String(300))
    started_at: Mapped[datetime] = mapped_column(UTCDateTime)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # manual | timer | quick | ai | journal | activitywatch | youtube_takeout
    source: Mapped[str] = mapped_column(String(30), default="manual")
    # External identity, used to avoid importing the same thing twice.
    source_ref: Mapped[str | None] = mapped_column(String(200))
    # True when the duration is reconstructed (e.g. from YouTube history gaps).
    is_estimate: Mapped[bool] = mapped_column(Boolean, default=False)
    # Set when the person picks the category by hand: re-running the
    # classification rules will then leave this entry alone.
    category_locked: Mapped[bool] = mapped_column(Boolean, default=False)
    location: Mapped[str | None] = mapped_column(String(120))
    notes: Mapped[str | None] = mapped_column(Text)
    goal_id: Mapped[int | None] = mapped_column(ForeignKey("goals.id", ondelete="SET NULL"))
    media_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("media_items.id", ondelete="SET NULL")
    )
    focus_rating: Mapped[int | None] = mapped_column(Integer)  # 1..5
    is_private: Mapped[bool] = mapped_column(Boolean, default=False)
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    category: Mapped[Category | None] = relationship(lazy="joined")
    people: Mapped[list[Person]] = relationship(secondary=time_entry_people, lazy="selectin")


class ClassificationRule(UserOwned, Base):
    """Maps imported activity (a YouTube channel, a window title, a domain) to a
    category. Rules are applied by priority, lowest number first."""

    __tablename__ = "classification_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    # channel | title | url | domain | app
    field: Mapped[str] = mapped_column(String(20))
    pattern: Mapped[str] = mapped_column(String(300))
    is_regex: Mapped[bool] = mapped_column(Boolean, default=False)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id", ondelete="CASCADE"))
    priority: Mapped[int] = mapped_column(Integer, default=100)
    note: Mapped[str | None] = mapped_column(String(200))
