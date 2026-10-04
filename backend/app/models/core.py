"""Accounts, login sessions and the profile that parameterises a life."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, Date, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, UTCDateTime, utcnow
from app.models.base import Timestamps


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    profile: Mapped[Profile] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )


class AuthSession(Base):
    """A login. The cookie holds a random token; only its SHA-256 is stored here,
    so a copy of the database cannot be used to impersonate the user."""

    __tablename__ = "auth_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    user_agent: Mapped[str | None] = mapped_column(String(255))
    ip: Mapped[str | None] = mapped_column(String(64))


class ApiToken(Base):
    """A key for a device or tool that is not the browser session — the YouTube
    extension, later a phone. Like a session token, only its SHA-256 is stored;
    unlike one, it is limited to the scopes it was created with."""

    __tablename__ = "api_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # The first characters, shown in the list so a token can be recognised.
    prefix: Mapped[str] = mapped_column(String(12))
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class Profile(Timestamps, Base):
    """The parameters of a life: when it started, how long it may last, and what
    the person has decided to spend it on."""

    __tablename__ = "profiles"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    birth_date: Mapped[date | None] = mapped_column(Date)
    life_expectancy_years: Mapped[int] = mapped_column(Integer, default=90)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    currency: Mapped[str] = mapped_column(String(8), default="XOF")
    # The day the person started living deliberately — "conscious since".
    awakening_date: Mapped[date | None] = mapped_column(Date)
    mission_title: Mapped[str | None] = mapped_column(String(120))
    mission_text: Mapped[str | None] = mapped_column(Text)

    focus_target_hours: Mapped[float] = mapped_column(Float, default=8.0)
    stretch_focus_hours: Mapped[float] = mapped_column(Float, default=12.0)
    sleep_target_hours: Mapped[float] = mapped_column(Float, default=8.0)
    noise_budget_hours: Mapped[float] = mapped_column(Float, default=1.0)
    wake_target: Mapped[str | None] = mapped_column(String(5))  # "HH:MM"
    bed_target: Mapped[str | None] = mapped_column(String(5))

    # [{"term": "[Sprint]", "meaning": "...", "private": false}]
    glossary: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    # Text sent to a cloud model passes through these first:
    # [{"pattern": "chocolate\\w*", "replace": "{Treat}"}]
    redactions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    # Overrides of the server AI settings: {"mode": "...", "models": [...]}
    ai_settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # Everything else that is a preference, by section: {"prayer": {...},
    # "reminders": {...}}. Defaults live in the code that reads them.
    prefs: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=dict)

    user: Mapped[User] = relationship(back_populates="profile")
