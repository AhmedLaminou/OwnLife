"""State of the AI assistant and of the integrations."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, Date, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, UTCDateTime, utcnow
from app.models.base import Timestamps, UserOwned


class ChatThread(UserOwned, Timestamps, Base):
    __tablename__ = "chat_threads"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200), default="New conversation")


class ChatMessage(UserOwned, Base):
    """Conversation history lives here rather than in a LangGraph checkpointer:
    it stays readable, searchable and exportable like the rest of the data."""

    __tablename__ = "chat_messages"
    __table_args__ = (Index("ix_chat_messages_thread", "thread_id", "id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    thread_id: Mapped[int] = mapped_column(ForeignKey("chat_threads.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(12))  # user | assistant | tool
    content: Mapped[str] = mapped_column(Text, default="")
    tool_calls: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    tool_call_id: Mapped[str | None] = mapped_column(String(100))
    name: Mapped[str | None] = mapped_column(String(100))
    # Sources cited, records created, model used…
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class CaptureDraft(UserOwned, Timestamps, Base):
    """What the AI extracted from a free-text capture, waiting for the person to
    check it. Nothing reaches the ledger without that review."""

    __tablename__ = "capture_drafts"

    id: Mapped[int] = mapped_column(primary_key=True)
    capture_date: Mapped[date] = mapped_column(Date)
    input_text: Mapped[str] = mapped_column(Text)
    draft: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(12), default="pending")  # pending | committed | discarded
    model: Mapped[str | None] = mapped_column(String(120))


class DayReview(UserOwned, Timestamps, Base):
    """The review of one day: the facts (always computed locally) and, when a
    model was available, a short text. Written automatically each morning for
    the day before, or on demand."""

    __tablename__ = "day_reviews"
    __table_args__ = (UniqueConstraint("user_id", "review_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    review_date: Mapped[date] = mapped_column(Date)
    facts: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    text: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(String(200))
    # Why there is no text, when there is none (AI off, offline, daily limit…).
    error: Mapped[str | None] = mapped_column(Text)


class IntegrationState(UserOwned, Base):
    __tablename__ = "integration_states"
    __table_args__ = (UniqueConstraint("user_id", "provider"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(40))  # activitywatch | youtube_takeout | journal_file
    cursor: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    last_synced_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_error: Mapped[str | None] = mapped_column(Text)
    settings: Mapped[dict[str, Any] | None] = mapped_column(JSON)
