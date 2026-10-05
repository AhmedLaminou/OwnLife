"""The written memory: journal days, longer notes and essays, and the chunks
they are cut into for search and retrieval."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, Date, ForeignKey, Index, Integer, LargeBinary, String, Text, UniqueConstraint, or_
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, UTCDateTime
from app.models.base import Timestamps, UserOwned


class JournalEntry(UserOwned, Timestamps, Base):
    __tablename__ = "journal_entries"
    __table_args__ = (Index("ix_journal_user_date", "user_id", "entry_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    entry_date: Mapped[date] = mapped_column(Date)
    # "Day 12" in the Virtual Memory numbering.
    day_number: Mapped[int | None] = mapped_column(Integer)
    title: Mapped[str | None] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    mood: Mapped[int | None] = mapped_column(Integer)  # -2..+2
    # Private entries are never sent to a cloud model.
    is_private: Mapped[bool] = mapped_column(Boolean, default=False)
    # manual (written in OwnLife) | import (came from the file). import_edited: an
    # edit made before the two-way sync; the sync now writes it into the file.
    source: Mapped[str] = mapped_column(String(30), default="manual")
    # Hash of the text as last agreed with the file (the "base" of a three-way
    # sync): if the file differs from it, the file changed; if the body differs
    # from it, OwnLife changed. See app/services/filesync.py.
    source_hash: Mapped[str | None] = mapped_column(String(64))
    # The Markdown file this day lives in, when file sync is on.
    file_path: Mapped[str | None] = mapped_column(String(500))
    # synced | pending (to be written) | conflict | missing (gone from the file) | local
    sync_state: Mapped[str | None] = mapped_column(String(12))
    # The file's version of the day while a conflict waits to be resolved.
    conflict_body: Mapped[str | None] = mapped_column(Text)

    @classmethod
    def visible(cls):
        """A day that vanished from its file is hidden, not deleted (a typo in a
        header must not erase anything): it is left out of lists, search and AI."""
        return or_(cls.sync_state.is_(None), cls.sync_state != "missing")


class Note(UserOwned, Timestamps, Base):
    """A longer piece of writing that is not tied to one day: an essay, an idea list."""

    __tablename__ = "notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(String(30), default="note")  # note | essay | idea | reference
    source_path: Mapped[str | None] = mapped_column(String(500))
    source_hash: Mapped[str | None] = mapped_column(String(64))
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    is_private: Mapped[bool] = mapped_column(Boolean, default=False)
    # Same meaning as on JournalEntry.
    sync_state: Mapped[str | None] = mapped_column(String(12))
    conflict_body: Mapped[str | None] = mapped_column(Text)

    @classmethod
    def visible(cls):
        return or_(cls.sync_state.is_(None), cls.sync_state != "missing")


class Idea(UserOwned, Timestamps, Base):
    """An idea found in a journal page (a [SomeThoughts] section): in your words
    (`quote`, the exact passage), restated by the model (`statement`), the essay
    it belongs to — an existing one (`note_id`) or a new one (`new_essay`) — and
    references to authors who wrote on the same question. Placing it appends a
    section to the essay; `placed_text` is that section, kept for undo."""

    __tablename__ = "ideas"
    __table_args__ = (
        UniqueConstraint("user_id", "fingerprint"),
        Index("ix_ideas_user_status", "user_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    journal_entry_id: Mapped[int | None] = mapped_column(ForeignKey("journal_entries.id", ondelete="SET NULL"))
    entry_date: Mapped[date] = mapped_column(Date)
    day_number: Mapped[int | None] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200))
    statement: Mapped[str] = mapped_column(Text)
    quote: Mapped[str] = mapped_column(Text)
    # mathematics, physics, computer science, ai, robotics, invention, philosophy,
    # psychology, society, culture, history, geography, literature, religion, other
    domain: Mapped[str] = mapped_column(String(30), default="other")
    note_id: Mapped[int | None] = mapped_column(ForeignKey("notes.id", ondelete="SET NULL"))
    new_essay: Mapped[str | None] = mapped_column(String(200))
    # [{"author", "work", "why", "verified": bool | None, "url"}]
    refs: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(12), default="new")  # new | placed | kept | dismissed
    placed_note_id: Mapped[int | None] = mapped_column(ForeignKey("notes.id", ondelete="SET NULL"))
    placed_text: Mapped[str | None] = mapped_column(Text)
    placed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    fingerprint: Mapped[str] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(300))


class Chunk(UserOwned, Base):
    """A searchable passage. Full-text search goes through the `chunks_fts` FTS5
    table (kept in sync by triggers); semantic search through `embedding`."""

    __tablename__ = "chunks"
    __table_args__ = (Index("ix_chunks_source", "user_id", "source_type", "source_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source_type: Mapped[str] = mapped_column(String(20))  # journal | note
    source_id: Mapped[int] = mapped_column(Integer)
    chunk_index: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # float32 vector, L2-normalised. NULL until embedded.
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary)
    embedding_model: Mapped[str | None] = mapped_column(String(100))
    is_private: Mapped[bool] = mapped_column(Boolean, default=False)
