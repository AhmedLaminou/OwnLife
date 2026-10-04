"""Two-way sync between OwnLife and your Markdown files.

The journal file(s) and the notes beside them stay the master copy of your
writing; OwnLife keeps a mirror it can search, link and analyse. Changes flow
both ways:

    you save the file in VS Code   --watcher, ~2 s-->   OwnLife updates the day
    you edit a day in OwnLife      --immediately--->    the file is updated

Each day (and each note) remembers the text both sides last agreed on: its
*base* (`source_hash`). Comparing each side with the base tells who changed:

    file = base, OwnLife = base   nothing to do
    file changed only             take the file's version
    OwnLife changed only          write OwnLife's version into the file
    both changed (differently)    a conflict: both versions are kept and shown,
                                  nothing is overwritten until you choose

A day that disappears from the file is marked "missing" — hidden, not deleted.
If it comes back (a typo in a header, fixed), it is restored as it was.
Every write goes through `mdfile`: minimal, verified, atomic, with the previous
version kept in backend/data/file-history.
"""

from __future__ import annotations

import glob
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import SessionLocal, utcnow
from app.models import JournalEntry, Note, Profile, User
from app.services import mdfile
from app.services.journal_import import (
    extract_tags,
    merge_glossary,
    parse_virtual_memory,
    title_from_filename,
)
from app.services.mdfile import EditError, MdText, sha

log = logging.getLogger("ownlife.sync")

# One lock for every read-modify-write of a synced file and of the rows that
# mirror it. Re-entrant: an API handler can hold it around its own commit.
LOCK = threading.RLock()

LEGACY_SOURCES = ("import", "import_edited")


class SyncError(Exception):
    """The edit was refused; the message says why, for the person."""


# ---------------------------------------------------------------- configuration
def _norm(p: Path | str) -> str:
    return os.path.normcase(os.path.abspath(str(p)))


@dataclass
class SyncConfig:
    pattern: str  # the journal path, possibly with {year}
    root: Path  # IMPORT_ROOT: nothing outside it is read or written
    history_dir: Path
    writeback: bool
    keep: int

    @classmethod
    def from_settings(cls, s: Settings) -> SyncConfig | None:
        if s.journal_sync_path is None or s.import_root is None:
            return None
        return cls(str(s.journal_sync_path), Path(s.import_root).resolve(), s.data_dir / "file-history",
                   s.file_sync_writeback, s.file_history_keep)

    @property
    def yearly(self) -> bool:
        return "{year}" in self.pattern

    def _matcher(self) -> re.Pattern:
        # Compared on normalised paths: "C:/a/b" and "C:\a\b" name the same file.
        parts = _norm(self.pattern).split("{year}")
        rx = re.escape(parts[0])
        for i, part in enumerate(parts[1:]):
            rx += (r"(?P<year>\d{4})" if i == 0 else "(?P=year)") + re.escape(part)
        return re.compile(rx)

    def _inside(self, p: Path) -> bool:
        try:
            self.allowed(p)
            return True
        except SyncError:
            return False

    def journal_files(self) -> list[Path]:
        if not self.yearly:
            p = Path(self.pattern)
            return [p] if p.is_file() and self._inside(p) else []
        candidates = glob.glob(self.pattern.replace("{year}", "[0-9][0-9][0-9][0-9]"))
        rx = self._matcher()
        return sorted(
            Path(c) for c in candidates
            if rx.fullmatch(_norm(c)) and Path(c).is_file() and self._inside(Path(c))
        )

    def journal_file_for(self, d: date) -> Path:
        """Where a new day goes: its year's file (created if needed), or the one file."""
        return Path(self.pattern.replace("{year}", f"{d.year:04d}"))

    def notes_dirs(self, today: date | None = None) -> list[Path]:
        dirs = {p.parent for p in self.journal_files()}
        if today is not None and self.journal_file_for(today).parent.is_dir():
            dirs.add(self.journal_file_for(today).parent)
        return sorted(dirs)

    def new_notes_dir(self, today: date) -> Path:
        here = self.journal_file_for(today).parent
        if here.is_dir():
            return here
        files = self.journal_files()
        return files[-1].parent if files else self.root

    def allowed(self, p: Path) -> Path:
        resolved = Path(p).resolve()
        if resolved != self.root and self.root not in resolved.parents:
            raise SyncError(f"{p} is outside IMPORT_ROOT ({self.root}): OwnLife does not read or write there")
        return resolved

    def check(self) -> None:
        """The journal path itself must live under IMPORT_ROOT."""
        self.allowed(Path(self.pattern.split("{year}")[0] or "."))


def config(settings: Settings) -> SyncConfig | None:
    return SyncConfig.from_settings(settings)


def sync_owner(db: Session) -> User | None:
    """The account the files belong to: the first one (OwnLife runs for one person;
    a hosted version would give each account its own folder)."""
    return db.scalar(select(User).where(User.is_active.is_(True)).order_by(User.id))


# ---------------------------------------------------------------- report
@dataclass
class SyncReport:
    created: int = 0
    updated: int = 0
    pushed: int = 0
    unchanged: int = 0
    restored: int = 0
    notes_created: int = 0
    notes_updated: int = 0
    notes_pushed: int = 0
    glossary_added: int = 0
    conflicts: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    changed_entry_ids: list[int] = field(default_factory=list)
    hidden_entry_ids: list[int] = field(default_factory=list)
    changed_note_ids: list[int] = field(default_factory=list)
    hidden_note_ids: list[int] = field(default_factory=list)
    files_written: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(
            self.changed_entry_ids or self.hidden_entry_ids or self.changed_note_ids or self.hidden_note_ids
            or self.files_written or self.conflicts or self.glossary_added
        )

    def as_dict(self) -> dict:
        return {
            "created": self.created,
            "updated": self.updated,
            "pushed": self.pushed,
            "unchanged": self.unchanged,
            "restored": self.restored,
            "notes_created": self.notes_created,
            "notes_updated": self.notes_updated,
            "notes_pushed": self.notes_pushed,
            # kept for the older "Sync from file" toast
            "notes_changed": self.notes_created + self.notes_updated,
            "glossary_added": self.glossary_added,
            "conflicts": self.conflicts,
            "missing": self.missing,
            "warnings": self.warnings,
            "errors": self.errors,
            "files_written": self.files_written,
            "changed_entry_ids": self.changed_entry_ids,
        }


# ---------------------------------------------------------------- reading and writing files
def _read(path: Path) -> MdText:
    return MdText.decode(path.read_bytes())


def _write(cfg: SyncConfig, path: Path, md: MdText, report: SyncReport | None = None, label: str = "") -> None:
    path = cfg.allowed(path)
    mdfile.keep_history(path, cfg.history_dir, cfg.root, cfg.keep, label)
    mdfile.write_atomic(path, md.encode())
    if report is not None:
        report.files_written.append(str(path))
    log.info("Wrote %s", path)


def _label(e: JournalEntry) -> str:
    return f"Day {e.day_number} ({e.entry_date:%d/%m/%Y})" if e.day_number else f"{e.entry_date:%d/%m/%Y}"


def _linked(e: JournalEntry) -> bool:
    """Was this day ever agreed with a file? (Then vanishing from it means something.)"""
    return bool(e.file_path) or (e.source in LEGACY_SOURCES and e.source_hash is not None)


@dataclass
class _Located:
    path: Path
    md: MdText
    section: mdfile.Section
    text: str


def _locate(cfg: SyncConfig, d: date) -> _Located | None:
    """The first section dated `d` across the journal files (files in order)."""
    for path in cfg.journal_files():
        try:
            md = _read(path)
        except (OSError, UnicodeDecodeError):
            continue
        layout = mdfile.parse_layout(md.lines)
        found = layout.by_date(d)
        if found:
            return _Located(path, md, found[0], mdfile.section_text(md.lines, found[0]))
    return None


def _set_synced(e: JournalEntry | Note, text_hash: str) -> None:
    e.source_hash = text_hash
    e.sync_state = "synced"
    e.conflict_body = None


def infer_day_number(db: Session, user_id: int, profile: Profile | None, d: date) -> int:
    """Day N of the Virtual Memory: counted from the Awakening when it is set,
    otherwise one more than the highest number used so far."""
    if profile is not None and profile.awakening_date and d >= profile.awakening_date:
        return (d - profile.awakening_date).days + 1
    highest = db.scalar(
        select(JournalEntry.day_number).where(JournalEntry.user_id == user_id, JournalEntry.day_number.is_not(None))
        .order_by(JournalEntry.day_number.desc()).limit(1)
    )
    return (highest or 0) + 1


# ---------------------------------------------------------------- full sync
def sync_all(db: Session, user: User, cfg: SyncConfig, today: date | None = None) -> SyncReport:
    cfg.check()
    with LOCK:
        report = SyncReport()
        _sync_journal(db, user, cfg, report)
        _sync_notes(db, user, cfg, report, today or date.today())
        db.flush()
        return report


def _sync_journal(db: Session, user: User, cfg: SyncConfig, report: SyncReport) -> None:
    profile = db.get(Profile, user.id)
    files = cfg.journal_files()
    entries = list(db.scalars(select(JournalEntry).where(JournalEntry.user_id == user.id).order_by(JournalEntry.id)))
    by_date: dict[date, list[JournalEntry]] = {}
    for e in entries:
        by_date.setdefault(e.entry_date, []).append(e)

    texts: dict[str, MdText] = {}
    sections: dict[date, tuple[Path, mdfile.Section, object]] = {}
    for path in files:
        try:
            md = _read(path)
        except UnicodeDecodeError:
            report.errors.append(f"{path.name} is not valid UTF-8: it was not read, and will not be written")
            continue
        except OSError as e:
            report.errors.append(f"{path.name}: {e}")
            continue
        texts[_norm(path)] = md
        parsed = parse_virtual_memory(md.text)
        report.warnings.extend(f"{path.name}: {w}" for w in parsed.warnings)
        if profile is not None:
            report.glossary_added += merge_glossary(profile, parsed.glossary)
        days = {d.entry_date: d for d in parsed.days}
        for s in mdfile.parse_layout(md.lines).sections:
            if s.entry_date not in sections and s.entry_date in days:
                sections[s.entry_date] = (path, s, days[s.entry_date])

    pushes: dict[str, list[JournalEntry]] = {}
    matched: set[int] = set()
    for d, (path, section, day) in sections.items():
        candidates = by_date.get(d, [])
        entry = (
            next((e for e in candidates if e.file_path and _norm(e.file_path) == _norm(path)), None)
            or next((e for e in candidates if _linked(e)), None)
            or next(iter(candidates), None)
        )
        if entry is None:
            # A missing day with exactly this text, under another date: the date was corrected.
            moved = next(
                (e for e in entries if e.sync_state == "missing" and e.source_hash == day.content_hash and e.id not in matched),
                None,
            )
            if moved is not None:
                moved.entry_date = d
                entry = moved
            else:
                entry = JournalEntry(
                    user_id=user.id, entry_date=d, day_number=day.day_number, body=day.body, tags=day.tags,
                    source="import", file_path=str(path),
                )
                _set_synced(entry, day.content_hash)
                db.add(entry)
                db.flush()
                report.created += 1
                report.changed_entry_ids.append(entry.id)
                matched.add(entry.id)
                continue
        matched.add(entry.id)
        was_missing = entry.sync_state == "missing"
        entry.file_path = str(path)
        if entry.day_number != day.day_number:
            entry.day_number = day.day_number
        outcome = _three_way(entry, day.body, day.content_hash)
        if outcome == "pull":
            entry.body = day.body
            entry.tags = day.tags
            _set_synced(entry, day.content_hash)
            report.updated += 1
            report.changed_entry_ids.append(entry.id)
        elif outcome == "push":
            pushes.setdefault(_norm(path), []).append(entry)
        elif outcome == "conflict":
            if entry.sync_state != "conflict" or entry.conflict_body != day.body:
                report.conflicts.append(f"{_label(entry)} changed both in the file and in OwnLife — choose a version")
            entry.sync_state = "conflict"
            entry.conflict_body = day.body
        else:
            report.unchanged += 1
            if was_missing:
                _set_synced(entry, day.content_hash)
        if was_missing and outcome != "conflict":
            report.restored += 1
            if entry.id not in report.changed_entry_ids:
                report.changed_entry_ids.append(entry.id)

    for e in entries:
        if e.id in matched or e.sync_state == "missing":
            continue
        if _linked(e):
            # It was in a file and is not anymore.
            if sha(e.body or "") == e.source_hash:
                e.sync_state = "missing"
                report.missing.append(f"{_label(e)} is no longer in the file — hidden (restore it from Journal → Sync)")
                report.hidden_entry_ids.append(e.id)
            elif e.sync_state != "conflict" or e.conflict_body is not None:
                e.sync_state = "conflict"
                e.conflict_body = None
                report.conflicts.append(f"{_label(e)} was removed from the file but edited in OwnLife — choose")
        elif cfg.writeback and (e.sync_state in (None, "local", "pending")) and e.entry_date not in sections:
            pushes.setdefault(_norm(cfg.journal_file_for(e.entry_date)), []).append(e)

    if cfg.writeback:
        for key, items in pushes.items():
            _apply_day_pushes(db, user, cfg, key, texts.get(key), items, report)


def _three_way(e: JournalEntry | Note, file_text: str, file_hash: str) -> str:
    base, app_hash = e.source_hash, sha(e.body or "")
    if base is None:
        if app_hash != file_hash:
            return "conflict"
        _set_synced(e, file_hash)
        return "same"
    if file_hash == base:
        return "same" if app_hash == base else "push"
    if app_hash == base:
        return "pull"
    if app_hash == file_hash:
        _set_synced(e, file_hash)
        return "same"
    return "conflict"


def _apply_day_pushes(db: Session, user: User, cfg: SyncConfig, key: str, md: MdText | None,
                      items: list[JournalEntry], report: SyncReport) -> None:
    path = Path(next((e.file_path for e in items if e.file_path and _norm(e.file_path) == key), None)
                or cfg.journal_file_for(items[0].entry_date))
    if md is None:
        md = _read(path) if path.exists() else MdText.empty()
    original = md.encode()
    profile = db.get(Profile, user.id)
    done: list[tuple[JournalEntry, str]] = []
    try:
        for e in items:
            body = mdfile.clean_day_body(e.body or "")
            layout = mdfile.parse_layout(md.lines)
            found = layout.by_date(e.entry_date)
            if found:
                md = mdfile.day_replace(md, found[0], body)
            else:
                n = e.day_number or infer_day_number(db, user.id, profile, e.entry_date)
                md = mdfile.day_insert(md, n, e.entry_date, body)
                e.day_number = n
            done.append((e, body))
        if md.encode() != original or not path.exists():
            _write(cfg, path, md, report)
    except (EditError, SyncError, OSError) as exc:
        for e in items:
            e.sync_state = "pending"
        report.errors.append(f"{path.name}: not written ({exc}) — will retry")
        return
    for e, body in done:
        e.body = body
        e.file_path = str(path)
        _set_synced(e, sha(body))
        report.pushed += 1
        report.changed_entry_ids.append(e.id)


# ---------------------------------------------------------------- notes
def note_filename(title: str) -> str:
    """'Reading notes' -> 'ReadingNotes.md', the way the existing notes are named."""
    words = re.findall(r"[0-9A-Za-zÀ-ÖØ-öø-ÿ]+", title)
    stem = "".join(w[:1].upper() + w[1:] for w in words) or "Note"
    return f"{stem[:80]}.md"


def _note_files(cfg: SyncConfig, notes: list[Note], today: date) -> dict[str, Path]:
    journals = {_norm(p) for p in cfg.journal_files()}
    files: dict[str, Path] = {}
    for folder in cfg.notes_dirs(today):
        for p in sorted(folder.glob("*.md")):
            if p.name.startswith((".", "~")) or _norm(p) in journals:
                continue
            files[_norm(p)] = p
    for n in notes:  # a note already linked keeps syncing even if the folders change
        p = Path(n.source_path) if n.source_path else None
        if p is not None and p.is_file() and _norm(p) not in journals and cfg._inside(p):
            files.setdefault(_norm(p), p)
    return files


def _sync_notes(db: Session, user: User, cfg: SyncConfig, report: SyncReport, today: date) -> None:
    notes = list(db.scalars(select(Note).where(Note.user_id == user.id).order_by(Note.id)))
    by_path = {_norm(n.source_path): n for n in notes if n.source_path}
    files = _note_files(cfg, notes, today)
    for key, path in files.items():
        try:
            md = _read(path)
        except UnicodeDecodeError:
            report.warnings.append(f"{path.name} is not valid UTF-8: skipped")
            continue
        except OSError as e:
            report.errors.append(f"{path.name}: {e}")
            continue
        text = mdfile.clean_note_body(md.text)
        h = sha(text)
        note = by_path.get(key)
        if note is None:
            moved = next((n for n in notes if n.sync_state == "missing" and n.source_hash == h), None)
            if moved is not None:  # renamed or moved in the explorer
                moved.source_path = str(path)
                _set_synced(moved, h)
                report.changed_note_ids.append(moved.id)
                continue
            note = Note(user_id=user.id, title=title_from_filename(path), body=text, kind="essay",
                        source_path=str(path), tags=extract_tags(text))
            _set_synced(note, h)
            db.add(note)
            db.flush()
            report.notes_created += 1
            report.changed_note_ids.append(note.id)
            continue
        was_missing = note.sync_state == "missing"
        outcome = _three_way(note, text, h)
        if outcome == "pull":
            note.body, note.tags = text, extract_tags(text)
            _set_synced(note, h)
            report.notes_updated += 1
            report.changed_note_ids.append(note.id)
        elif outcome == "push" and cfg.writeback:
            _push_note_md(cfg, note, path, md, report)
        elif outcome == "conflict":
            if note.sync_state != "conflict" or note.conflict_body != text:
                report.conflicts.append(f"Note “{note.title}” changed both in the file and in OwnLife — choose a version")
            note.sync_state, note.conflict_body = "conflict", text
        elif was_missing:
            _set_synced(note, h)
            report.changed_note_ids.append(note.id)

    for n in notes:
        if n.source_path and _norm(n.source_path) not in files and n.sync_state != "missing":
            if sha(n.body or "") == n.source_hash:
                n.sync_state = "missing"
                report.missing.append(f"Note “{n.title}”: its file is gone — hidden")
                report.hidden_note_ids.append(n.id)
            elif n.sync_state != "conflict" or n.conflict_body is not None:
                n.sync_state, n.conflict_body = "conflict", None
                report.conflicts.append(f"Note “{n.title}”: its file is gone but it was edited in OwnLife — choose")
        elif not n.source_path and cfg.writeback and n.sync_state in (None, "local", "pending"):
            try:
                create_note_file(db, cfg, n, today, report)
            except (SyncError, OSError) as exc:
                n.sync_state = "pending"
                report.errors.append(f"Note “{n.title}”: no file created ({exc})")


def _push_note_md(cfg: SyncConfig, note: Note, path: Path, md: MdText, report: SyncReport | None) -> None:
    body = mdfile.clean_note_body(note.body or "")
    new = mdfile.note_replace(md, body)
    if new.encode() != md.encode():
        _write(cfg, path, new, report)
    note.body = body
    _set_synced(note, sha(body))
    if report is not None:
        report.notes_pushed += 1
        report.changed_note_ids.append(note.id)


def create_note_file(db: Session, cfg: SyncConfig, note: Note, today: date, report: SyncReport | None = None) -> Path:
    folder = cfg.new_notes_dir(today)
    name = note_filename(note.title)
    path = folder / name
    i = 2
    while path.exists():
        path = folder / f"{Path(name).stem} ({i}).md"
        i += 1
    body = mdfile.clean_note_body(note.body or "")
    md = mdfile.note_replace(MdText.empty(), body)
    _write(cfg, path, md, report)
    note.source_path = str(path)
    note.body = body
    _set_synced(note, sha(body))
    db.flush()
    return path


# ---------------------------------------------------------------- edits made in OwnLife
def push_day(db: Session, user_id: int, cfg: SyncConfig | None, entry: JournalEntry,
             file_date: date | None = None) -> dict:
    """Makes the file agree with `entry` after it was edited in OwnLife.
    `file_date` is the date the file knows the day by, when OwnLife just changed
    it. Returns {"state": synced | conflict | pending | local, "message": ...}.
    Raises SyncError when the text cannot go into the file as it is."""
    entry.body = mdfile.clean_day_body(entry.body or "")
    if cfg is None or not cfg.writeback:
        if not _linked(entry):
            entry.sync_state = "local"
        return {"state": entry.sync_state or "local", "message": None}
    with LOCK:
        body_hash = sha(entry.body)
        moving = file_date is not None and file_date != entry.entry_date
        try:
            current = _locate(cfg, file_date or entry.entry_date)
            if moving or current is None:
                if _locate(cfg, entry.entry_date) is not None and (current is None or moving):
                    raise SyncError(f"{entry.entry_date:%d/%m/%Y} already has a day in the file")
            if current is None:
                if _linked(entry) and entry.sync_state != "missing":
                    entry.sync_state, entry.conflict_body = "conflict", None
                    return {"state": "conflict", "message": "This day is no longer in the file: keep it or let it go."}
                path = cfg.journal_file_for(entry.entry_date)
                md = _read(path) if path.exists() else MdText.empty()
                n = entry.day_number or infer_day_number(db, user_id, db.get(Profile, user_id), entry.entry_date)
                _write(cfg, path, mdfile.day_insert(md, n, entry.entry_date, entry.body))
                entry.day_number, entry.file_path = n, str(path)
                _set_synced(entry, body_hash)
                return {"state": "synced", "message": f"Added to {path.name}"}

            file_hash = sha(current.text)
            if entry.source_hash is not None and file_hash not in (entry.source_hash, body_hash):
                entry.sync_state, entry.conflict_body = "conflict", current.text
                entry.file_path = str(current.path)
                return {"state": "conflict",
                        "message": "The file changed at the same time: both versions are kept — choose one."}
            new_number = entry.day_number if entry.day_number and entry.day_number != current.section.day_number else None
            new_date = entry.entry_date if moving else None
            entry.file_path = str(current.path)
            if file_hash == body_hash and new_number is None and new_date is None:
                _set_synced(entry, body_hash)
                return {"state": "synced", "message": None}
            md = mdfile.day_replace(current.md, current.section, entry.body, day_number=new_number, new_date=new_date)
            _write(cfg, current.path, md)
            _set_synced(entry, body_hash)
            return {"state": "synced", "message": f"Saved to {current.path.name}"}
        except EditError as e:
            raise SyncError(str(e)) from e
        except OSError as e:
            entry.sync_state = "pending"
            return {"state": "pending", "message": f"Saved in OwnLife; the file could not be written ({e}). It will be retried."}


def remove_day(db: Session, cfg: SyncConfig | None, entry: JournalEntry) -> None:  # noqa: ARG001
    """Deletes the day from its file too. Refused if the file holds changes to
    that day that OwnLife has not seen yet — deleting must never lose text."""
    if cfg is None or not cfg.writeback or entry.sync_state == "missing":
        return
    with LOCK:
        loc = _locate(cfg, entry.entry_date)
        if loc is None:
            return
        if sha(loc.text) not in (entry.source_hash, sha(entry.body or "")):
            raise SyncError("The file has changes to this day that OwnLife has not seen yet: sync first, then delete.")
        try:
            _write(cfg, loc.path, mdfile.day_delete(loc.md, loc.section), label="before-delete")
        except EditError as e:
            raise SyncError(str(e)) from e


def push_note(db: Session, cfg: SyncConfig | None, note: Note, today: date) -> dict:
    note.body = mdfile.clean_note_body(note.body or "")
    if cfg is None or not cfg.writeback:
        if not note.source_path:
            note.sync_state = "local"
        return {"state": note.sync_state or "local", "message": None}
    with LOCK:
        try:
            if not note.source_path or not Path(note.source_path).exists():
                if note.source_path and note.sync_state != "missing":
                    note.sync_state, note.conflict_body = "conflict", None
                    return {"state": "conflict", "message": "Its file is gone: keep the note (a new file is made) or let it go."}
                path = create_note_file(db, cfg, note, today)
                return {"state": "synced", "message": f"Created {path.name}"}
            path = Path(note.source_path)
            md = _read(path)
            file_text = mdfile.clean_note_body(md.text)
            if note.source_hash is not None and sha(file_text) not in (note.source_hash, sha(note.body)):
                note.sync_state, note.conflict_body = "conflict", file_text
                return {"state": "conflict", "message": "The file changed at the same time: both versions are kept — choose one."}
            _push_note_md(cfg, note, path, md, None)
            return {"state": "synced", "message": f"Saved to {path.name}"}
        except EditError as e:
            raise SyncError(str(e)) from e
        except (OSError, UnicodeDecodeError) as e:
            note.sync_state = "pending"
            return {"state": "pending", "message": f"Saved in OwnLife; the file could not be written ({e})."}


def remove_note_file(cfg: SyncConfig | None, note: Note) -> str | None:
    """Moves the note's file into the file history (recoverable), then it is gone
    from the folder. Returns where the copy is."""
    if cfg is None or not cfg.writeback or not note.source_path:
        return None
    with LOCK:
        path = Path(note.source_path)
        if not path.exists():
            return None
        current = mdfile.clean_note_body(_read(path).text)
        if sha(current) not in (note.source_hash, sha(note.body or "")):
            raise SyncError("The file has changes OwnLife has not seen yet: sync first, then delete.")
        cfg.allowed(path)
        copy = mdfile.keep_history(path, cfg.history_dir, cfg.root, cfg.keep, "deleted")
        path.unlink()
        return str(copy) if copy else None


def resolve(db: Session, user_id: int, cfg: SyncConfig | None, obj: JournalEntry | Note, keep: str,
            text: str | None, today: date) -> dict:
    """keep = "file": take the file's version (or, if the file no longer has it,
    let the OwnLife copy go); "app": write OwnLife's version into the file;
    "text": write `text` (a merge made by hand)."""
    if obj.sync_state != "conflict":
        raise SyncError("There is no conflict to resolve here.")
    is_day = isinstance(obj, JournalEntry)
    with LOCK:
        if keep == "file":
            if obj.conflict_body is None:
                return {"state": "deleted"}
            obj.body = obj.conflict_body
            if is_day:
                obj.tags = extract_tags(obj.body)
            _set_synced(obj, sha(obj.body))
            return {"state": "synced"}
        if keep == "text":
            obj.body = text or ""
        # Write OwnLife's version: the file's current text becomes the base, so the
        # push below sees "only OwnLife changed".
        if is_day:
            loc = _locate(cfg, obj.entry_date) if cfg else None
            obj.source_hash = sha(loc.text) if loc else None
            obj.conflict_body = None
            if loc is None:
                obj.file_path, obj.source, obj.sync_state = None, "manual", "local"
            return push_day(db, user_id, cfg, obj)
        path = Path(obj.source_path) if obj.source_path else None
        if path is not None and path.exists():
            obj.source_hash = sha(mdfile.clean_note_body(_read(path).text))
        else:
            obj.source_path, obj.source_hash, obj.sync_state = None, None, "local"
        obj.conflict_body = None
        return push_note(db, cfg, obj, today)


def reindex(db: Session, report: SyncReport) -> None:
    """Keyword search follows at once; embeddings follow in the background."""
    from app.ai import rag

    for eid in dict.fromkeys(report.changed_entry_ids):
        e = db.get(JournalEntry, eid)
        if e is not None and e.sync_state != "missing":
            rag.index_journal_entry(db, e)
    for eid in report.hidden_entry_ids:
        e = db.get(JournalEntry, eid)
        if e is not None:
            rag.delete_source_chunks(db, e.user_id, "journal", eid)
    for nid in dict.fromkeys(report.changed_note_ids):
        n = db.get(Note, nid)
        if n is not None and n.sync_state != "missing":
            rag.index_note(db, n)
    for nid in report.hidden_note_ids:
        n = db.get(Note, nid)
        if n is not None:
            rag.delete_source_chunks(db, n.user_id, "note", nid)


# ---------------------------------------------------------------- the watcher
class Watcher:
    """Polls the synced files (a stat per file per second: nothing for the disk)
    and runs a full sync after a change has settled. Polling instead of OS file
    events: editors save in many ways (in place, rename, temp files), and a stat
    loop sees all of them the same."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.cfg = SyncConfig.from_settings(settings)
        self.revision = 0
        self.last_sync_at: datetime | None = None
        self.last_change_at: datetime | None = None
        self.last_report: dict | None = None
        self.last_error: str | None = None
        self._snapshot: dict[str, tuple[int, int]] = {}
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.cfg is None or self.running:
            return
        try:
            self.cfg.check()
        except SyncError as e:
            self.last_error = str(e)
            log.error("File sync not started: %s", e)
            return
        self._thread = threading.Thread(target=self._loop, daemon=True, name="ownlife-filesync")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def poke(self) -> None:
        self._wake.set()

    def _signature(self) -> dict[str, tuple[int, int]]:
        sig: dict[str, tuple[int, int]] = {}
        if self.cfg is None:
            return sig
        paths = list(self.cfg.journal_files())
        for folder in self.cfg.notes_dirs(date.today()):
            paths.extend(folder.glob("*.md"))
        for p in paths:
            try:
                st = p.stat()
            except OSError:
                continue
            sig[_norm(p)] = (st.st_mtime_ns, st.st_size)
        return sig

    def _loop(self) -> None:
        self.run_once("startup")
        last_full = time.monotonic()
        while not self._stop.is_set():
            self._wake.wait(1.0)
            poked = self._wake.is_set()
            self._wake.clear()
            if self._stop.is_set():
                break
            sig = self._signature()
            if sig != self._snapshot or poked:
                time.sleep(0.8)  # let the editor finish saving
                if self._signature() != sig:
                    continue
                self.run_once("change")
                last_full = time.monotonic()
            elif time.monotonic() - last_full > 300:
                self.run_once("periodic")
                last_full = time.monotonic()

    def run_once(self, reason: str = "manual") -> SyncReport | None:
        if self.cfg is None:
            return None
        sig = self._signature()
        try:
            with LOCK, SessionLocal() as db:
                user = sync_owner(db)
                if user is None:
                    self._snapshot = sig
                    return None
                report = sync_all(db, user, self.cfg)
                if report.changed:
                    reindex(db, report)
                db.commit()
                user_id = user.id
        except SyncError as e:
            self.last_error = str(e)
            return None
        except Exception as e:  # never let the thread die: report and try again later
            log.exception("File sync failed (%s)", reason)
            self.last_error = f"{e.__class__.__name__}: {e}"
            return None
        self._snapshot = sig
        self.last_sync_at = utcnow()
        self.last_error = "; ".join(report.errors) or None
        if report.changed:
            from app.services import indexer

            indexer.schedule(user_id, self.settings)
            self.revision += 1
            self.last_change_at = self.last_sync_at
            self.last_report = report.as_dict()
            log.info("File sync (%s): %s", reason, {k: v for k, v in report.as_dict().items() if v})
        return report

    def status(self) -> dict:
        return {
            "running": self.running,
            "revision": self.revision,
            "last_sync_at": self.last_sync_at.isoformat() if self.last_sync_at else None,
            "last_change_at": self.last_change_at.isoformat() if self.last_change_at else None,
            "last_report": self.last_report,
            "last_error": self.last_error,
        }


_watcher: Watcher | None = None


def watcher() -> Watcher | None:
    return _watcher


def set_watcher(w: Watcher | None) -> None:
    global _watcher
    _watcher = w
