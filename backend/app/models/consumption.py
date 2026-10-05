"""What goes in: media watched and read, and money spent or received."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, UTCDateTime
from app.models.base import Timestamps, UserOwned


class MediaItem(UserOwned, Timestamps, Base):
    """A book, course, series, channel or single video."""

    __tablename__ = "media_items"
    __table_args__ = (UniqueConstraint("user_id", "kind", "external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    # book | course | series | anime | manga | movie | video | channel | podcast | article | music
    kind: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(300))
    creator: Mapped[str | None] = mapped_column(String(200))
    url: Mapped[str | None] = mapped_column(String(500))
    # YouTube video id, ISBN… Unique per kind.
    external_id: Mapped[str | None] = mapped_column(String(100))
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL")
    )
    # A single video's category: "you" chose it, the local "model" did, or the
    # model only made a "guess" (not applied; waits for you). None: never sorted.
    sorted_by: Mapped[str | None] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(20), default="none")  # want | in_progress | done | dropped | none
    progress_current: Mapped[float | None] = mapped_column(Float)
    progress_total: Mapped[float | None] = mapped_column(Float)
    progress_unit: Mapped[str | None] = mapped_column(String(20))  # pages | episodes | lectures
    rating: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(Text)


class WatchEvent(UserOwned, Base):
    """One video watched. From a history file (Google Takeout, Chrome's history)
    it is a moment without a duration (durations are estimated from the gaps
    between events); from the OwnLife browser extension it is a measured segment,
    with `ended_at` and `seconds`."""

    __tablename__ = "watch_events"
    __table_args__ = (
        UniqueConstraint("user_id", "source", "occurred_at", "video_id"),
        Index("ix_watch_events_user_time", "user_id", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime)
    title: Mapped[str] = mapped_column(String(300))
    channel: Mapped[str | None] = mapped_column(String(200))
    url: Mapped[str | None] = mapped_column(String(500))
    video_id: Mapped[str | None] = mapped_column(String(32))
    source: Mapped[str] = mapped_column(String(30), default="youtube_takeout")  # | chrome_history | extension
    # Measured segments only (source "extension"): when playing stopped, and
    # the seconds actually played in between.
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    seconds: Mapped[float | None] = mapped_column(Float)


class Transaction(UserOwned, Timestamps, Base):
    """Money in or out. Amounts are integers in the currency's minor unit
    (FCFA has none, so 300 FCFA is stored as 300)."""

    __tablename__ = "transactions"
    __table_args__ = (Index("ix_transactions_user_date", "user_id", "occurred_on"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    occurred_on: Mapped[date] = mapped_column(Date)
    occurred_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    direction: Mapped[str] = mapped_column(String(3), default="out")  # out | in
    amount_minor: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(8), default="XOF")
    item: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(60), default="other")
    counterparty: Mapped[str | None] = mapped_column(String(120))
    person_id: Mapped[int | None] = mapped_column(ForeignKey("people.id", ondelete="SET NULL"))
    note: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(30), default="manual")


class MoneySuggestion(UserOwned, Timestamps, Base):
    """An amount found in the journal, waiting for a yes or a no. Its
    fingerprint (day, sentence, amount, direction) keeps a later scan from
    proposing again what was already added or dismissed."""

    __tablename__ = "money_suggestions"
    __table_args__ = (
        UniqueConstraint("user_id", "fingerprint"),
        Index("ix_money_suggestions_user_status", "user_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    entry_date: Mapped[date] = mapped_column(Date)
    journal_entry_id: Mapped[int | None] = mapped_column(ForeignKey("journal_entries.id", ondelete="SET NULL"))
    item: Mapped[str] = mapped_column(String(200))
    amount_minor: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(8), default="XOF")
    direction: Mapped[str] = mapped_column(String(3), default="out")  # out | in
    category: Mapped[str] = mapped_column(String(60), default="other")
    counterparty: Mapped[str | None] = mapped_column(String(120))
    quote: Mapped[str] = mapped_column(Text)  # the sentence of the journal it comes from
    fingerprint: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(12), default="pending")  # pending | added | dismissed
    transaction_id: Mapped[int | None] = mapped_column(ForeignKey("transactions.id", ondelete="SET NULL"))
