"""Reads the "Virtual Memory" Markdown format into journal days.

The format:

    {X} = a private habit, by its alias ...         <- glossary (preamble)
    [BigVision] = the long-term roadmap :

    Day 1 : 31/08/2026                                         <- one header per day
    ...free text, numbered thoughts, [SomeFacts] [SomeThoughts] tags...

Parsing is built on `mdfile` — the same line model the two-way sync uses to
write the file back — so what is read and what is written can never disagree.
The live file is kept in sync by `filesync.py`; `import_virtual_memory` below
is the one-way import of an uploaded file, used when no file is synced.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import JournalEntry, Profile
from app.services.mdfile import DAY_HEADER, content, parse_layout, section_text, split_lines

__all__ = ["DAY_HEADER", "parse_virtual_memory", "import_virtual_memory", "extract_tags"]

GLOSSARY_LINE = re.compile(r"^\s*([\[{][^\]}\n]{1,60}[\]}])\s*=\s*(.+?)\s*[;:!]?\s*$")
TAG = re.compile(r"\[[A-Za-z][A-Za-z0-9_]{1,40}\]|\{[A-Za-z][^{}\n]{0,40}\}")


@dataclass
class ParsedDay:
    day_number: int
    entry_date: date
    body: str
    tags: list[str]

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.body.encode("utf-8")).hexdigest()


@dataclass
class ParsedJournal:
    preamble: str
    glossary: list[dict]
    days: list[ParsedDay]
    warnings: list[str] = field(default_factory=list)


def extract_tags(text: str) -> list[str]:
    seen: dict[str, str] = {}
    for m in TAG.finditer(text):
        tag = m.group(0)
        seen.setdefault(tag.casefold(), tag)
    return list(seen.values())


def _tag_key(tag: str) -> str:
    core = re.sub(r"[^a-z0-9]", "", tag.casefold())
    return tag[0] + (core[:-1] if core.endswith("s") else core)


def canonicalize_tags(days: list[ParsedDay]) -> None:
    """[SomeThought], [SomeTHoughts] and [SomeThoughts] are one tag written three
    ways: rewrite every variant to its most frequent spelling."""
    counts: dict[str, dict[str, int]] = {}
    for d in days:
        for t in d.tags:
            spellings = counts.setdefault(_tag_key(t), {})
            spellings[t] = spellings.get(t, 0) + 1
    best = {k: max(v.items(), key=lambda kv: kv[1])[0] for k, v in counts.items()}
    for d in days:
        d.tags = list(dict.fromkeys(best[_tag_key(t)] for t in d.tags))


def parse_glossary(preamble_lines: list[str]) -> list[dict]:
    glossary = []
    for line in preamble_lines:
        g = GLOSSARY_LINE.match(line)
        if g:
            term, meaning = g.group(1).strip(), g.group(2).strip()
            # Terms in braces name what the author wants to quit: keep them private.
            glossary.append({"term": term, "meaning": meaning, "private": term.startswith("{")})
    return glossary


def parse_virtual_memory(text: str) -> ParsedJournal:
    lines = split_lines(text.removeprefix("\ufeff"))
    layout = parse_layout(lines)
    days = []
    seen: set[date] = set()
    for s in layout.sections:
        if s.entry_date in seen:
            continue  # parse_layout already warned; the first occurrence is the one synced
        seen.add(s.entry_date)
        body = section_text(lines, s)
        days.append(ParsedDay(s.day_number, s.entry_date, body, extract_tags(body)))
    preamble = [content(x).replace("\ufeff", "") for x in lines[: layout.preamble_end]]
    canonicalize_tags(days)
    return ParsedJournal("\n".join(preamble).strip(), parse_glossary(preamble), days, list(layout.warnings))


@dataclass
class ImportReport:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    conflicts: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    glossary_added: int = 0
    changed_entry_ids: list[int] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "created": self.created,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "conflicts": self.conflicts,
            "warnings": self.warnings,
            "glossary_added": self.glossary_added,
            "changed_entry_ids": self.changed_entry_ids,
        }


def merge_glossary(profile: Profile, items: list[dict]) -> int:
    existing = {g.get("term", "").casefold() for g in profile.glossary or []}
    added = [g for g in items if g["term"].casefold() not in existing]
    if added:
        profile.glossary = [*(profile.glossary or []), *added]  # reassign: JSON column
    return len(added)


def import_virtual_memory(db: Session, user_id: int, profile: Profile, text: str) -> ImportReport:
    """One-way import of an uploaded file: new days are created, changed days
    updated, and a day edited inside OwnLife since its import is kept (reported
    as a conflict). Days are matched on their date."""
    parsed = parse_virtual_memory(text)
    report = ImportReport(warnings=list(parsed.warnings))
    report.glossary_added = merge_glossary(profile, parsed.glossary)

    existing: dict[date, JournalEntry] = {}
    for e in db.scalars(
        select(JournalEntry).where(JournalEntry.user_id == user_id).order_by(JournalEntry.id)
    ):
        existing.setdefault(e.entry_date, e)
    for day in parsed.days:
        entry = existing.get(day.entry_date)
        if entry is None:
            entry = JournalEntry(
                user_id=user_id,
                entry_date=day.entry_date,
                day_number=day.day_number,
                body=day.body,
                tags=day.tags,
                source="import",
                source_hash=day.content_hash,
            )
            db.add(entry)
            db.flush()
            report.created += 1
            report.changed_entry_ids.append(entry.id)
        elif entry.source_hash == day.content_hash:
            report.unchanged += 1
        elif entry.source_hash is not None and sha256(entry.body) != entry.source_hash:
            report.conflicts.append(
                f"Day {day.day_number} ({day.entry_date}) changed in the file but was also "
                "edited in OwnLife — the OwnLife version was kept"
            )
        else:
            entry.body = day.body
            entry.tags = day.tags
            entry.day_number = day.day_number
            entry.source_hash = day.content_hash
            report.updated += 1
            report.changed_entry_ids.append(entry.id)
    db.flush()
    return report


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def title_from_filename(path: Path) -> str:
    """'ReadingNotes.md' -> 'Reading Notes'."""
    stem = path.stem.replace("_", " ")
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", stem).strip()
