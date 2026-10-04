"""Exact, minimal edits to the Markdown files OwnLife shares with you.

Your files are the master copy of your writing. Every edit made here:

- keeps every byte it does not need to change: line endings (CRLF or LF, per
  line), trailing spaces (Markdown line breaks), a missing final newline, a BOM;
- changes only the lines that differ — a line-level diff, so git or your editor
  shows exactly what was edited;
- is checked before it is written: the edited text is parsed again, the edited
  day must read back as intended and every other day must be unchanged;
- keeps the previous version of the file in backend/data/file-history;
- is written atomically (a temporary file, then a rename): a crash or a power
  cut leaves either the old file or the new one, never half of each.

This module knows text, not the database; `filesync.py` decides what to edit.
"""

from __future__ import annotations

import difflib
import hashlib
import os
import re
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

# "Day 12 : 11/09/2026" — tolerant of spaces, a leading space, "13-09-2026" and "27/009/2026".
DAY_HEADER = re.compile(
    r"^\s*Day\s+(\d{1,5})\s*:\s*(\d{1,2})\s*[/.\-]\s*(\d{1,3})\s*[/.\-]\s*(\d{4})\s*$",
    re.IGNORECASE,
)
_LINE_SPLIT = re.compile(r"(?<=\n)")


class EditError(ValueError):
    """An edit that would not read back as intended. Nothing was written."""


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- lines
def split_lines(text: str) -> list[str]:
    """Lines with their own endings ("\\r\\n", "\\n", or "" for a last line
    without one). Only \\n ends a line — unlike str.splitlines, which would also
    split on characters such as U+2028 and so disagree with the parser."""
    lines = _LINE_SPLIT.split(text)
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def content(line: str) -> str:
    """The line without its ending."""
    if line.endswith("\r\n"):
        return line[:-2]
    if line.endswith("\n"):
        return line[:-1]
    return line


def _ending(line: str) -> str:
    return line[len(content(line)):]


def is_blank(line: str) -> bool:
    return not content(line).replace("\ufeff", "").strip()


@dataclass
class MdText:
    bom: bool
    newline: str  # the file's usual line ending, used for lines OwnLife writes
    lines: list[str]

    @classmethod
    def decode(cls, raw: bytes, default_newline: str = "\r\n" if os.name == "nt" else "\n") -> MdText:
        """Strict UTF-8: a file that is not valid UTF-8 is refused rather than
        silently "repaired" and written back damaged."""
        bom = raw.startswith(b"\xef\xbb\xbf")
        text = (raw[3:] if bom else raw).decode("utf-8")
        crlf = text.count("\r\n")
        lf = text.count("\n") - crlf
        newline = "\r\n" if crlf and crlf >= lf else ("\n" if lf else default_newline)
        return cls(bom, newline, split_lines(text))

    @classmethod
    def empty(cls, newline: str = "\r\n" if os.name == "nt" else "\n") -> MdText:
        return cls(False, newline, [])

    @property
    def text(self) -> str:
        return "".join(self.lines)

    def encode(self) -> bytes:
        data = self.text.encode("utf-8")
        return b"\xef\xbb\xbf" + data if self.bom else data

    @property
    def final_newline(self) -> bool:
        return bool(self.lines) and _ending(self.lines[-1]) != ""

    def with_lines(self, lines: list[str], final_newline: bool | None = None) -> MdText:
        """A copy holding `lines`, with endings repaired: every line but the last
        ends with a newline; the last keeps the file's habit (with or without)."""
        final = self.final_newline if final_newline is None else final_newline
        fixed = list(lines)
        for i, line in enumerate(fixed[:-1]):
            if _ending(line) == "":
                fixed[i] = line + self.newline
        if fixed:
            last = content(fixed[-1])
            fixed[-1] = last + (_ending(fixed[-1]) or self.newline if final else "")
        return MdText(self.bom, self.newline, fixed)


# ---------------------------------------------------------------- cleaning
def clean_day_lines(contents: list[str]) -> str:
    """The text of a day as OwnLife stores and compares it: trailing spaces
    dropped on every line, blank lines dropped at both ends."""
    return "\n".join(c.replace("\ufeff", "").rstrip() for c in contents).strip("\n")


def clean_day_body(text: str) -> str:
    return clean_day_lines(text.replace("\r\n", "\n").replace("\r", "\n").split("\n"))


def clean_note_body(text: str) -> str:
    return text.replace("\r\n", "\n").strip()


# ---------------------------------------------------------------- journal sections
@dataclass
class Section:
    day_number: int
    entry_date: date
    header: int  # index of the header line
    end: int  # index one past the section's last line

    @property
    def body_start(self) -> int:
        return self.header + 1


@dataclass
class Layout:
    preamble_end: int  # the preamble is lines[:preamble_end]
    sections: list[Section]
    warnings: list[str]

    def by_date(self, d: date) -> list[Section]:
        return [s for s in self.sections if s.entry_date == d]


def parse_layout(lines: list[str]) -> Layout:
    """Where each day starts and ends. A header line with an impossible date
    (31/02) is not a boundary: its lines stay with the day before, with a
    warning, so no text ever disappears because of a typo."""
    headers: list[tuple[int, int, date]] = []
    warnings: list[str] = []
    for i, line in enumerate(lines):
        m = DAY_HEADER.match(content(line).replace("\ufeff", ""))
        if not m:
            continue
        n, dd, mm, yyyy = (int(g) for g in m.groups())
        try:
            headers.append((i, n, date(yyyy, mm, dd)))
        except ValueError:
            warnings.append(f"line {i + 1}: invalid date in {content(line).strip()!r} — kept as text of the day before")
    sections = [
        Section(n, d, i, headers[k + 1][0] if k + 1 < len(headers) else len(lines))
        for k, (i, n, d) in enumerate(headers)
    ]
    seen: dict[date, int] = {}
    for s in sections:
        if s.entry_date in seen:
            warnings.append(
                f"{s.entry_date:%d/%m/%Y} appears twice (Day {seen[s.entry_date]} and Day {s.day_number}): "
                "only the first is synced until one is fixed"
            )
        else:
            seen[s.entry_date] = s.day_number
    return Layout(headers[0][0] if headers else len(lines), sections, warnings)


def section_text(lines: list[str], s: Section) -> str:
    return clean_day_lines([content(x) for x in lines[s.body_start : s.end]])


def header_line(day_number: int, d: date) -> str:
    return f"Day {day_number} : {d:%d/%m/%Y}"


# ---------------------------------------------------------------- minimal edits
def _merge(core: list[str], old: list[str], new: list[str], nl: str) -> list[str]:
    """`core` are raw lines whose cleaned form is `old`. Returns raw lines whose
    cleaned form is `new`, reusing the raw line wherever a line is unchanged."""
    out: list[str] = []
    sm = difflib.SequenceMatcher(a=old, b=new, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            out.extend(core[i1:i2])
        else:
            out.extend(line + nl for line in new[j1:j2])
    return out


def _replace_region(lines: list[str], start: int, end: int, new_text: str, nl: str, per_line_clean) -> list[str]:
    """Replaces the content of lines[start:end]. Its blank lines at both ends are
    layout and are kept; the lines between are diffed against `new_text`."""
    region = lines[start:end]
    filled = [i for i, line in enumerate(region) if not is_blank(line)]
    new = new_text.split("\n") if new_text else []
    if not filled:
        return lines[:start] + [x + nl for x in new] + region + lines[end:]
    f, last = filled[0], filled[-1]
    core = region[f : last + 1]
    old = per_line_clean([content(x) for x in core])
    merged = _merge(core, old, new, nl)
    return lines[:start] + region[:f] + merged + region[last + 1 :] + lines[end:]


def _day_clean_lines(contents: list[str]) -> list[str]:
    return [c.replace("\ufeff", "").rstrip() for c in contents]


def _verify_days(before: list[str], after: list[str], expect: dict[date, str | None]) -> None:
    """`expect` maps each changed date to its new text (None: removed). The
    preamble, and every other day — number, date, text and order — must read
    back exactly as before."""
    a, b = parse_layout(before), parse_layout(after)
    if [content(x) for x in before[: a.preamble_end]] != [content(x) for x in after[: b.preamble_end]]:
        raise EditError("the edit would change the lines before the first day")

    def untouched(lines: list[str], layout: Layout) -> list[tuple]:
        return [
            (s.entry_date, s.day_number, section_text(lines, s))
            for s in layout.sections
            if s.entry_date not in expect
        ]

    if untouched(before, a) != untouched(after, b):
        old_dates = {s.entry_date for s in a.sections}
        if any(s.entry_date not in old_dates and s.entry_date not in expect for s in b.sections):
            raise EditError(
                "the text contains a line that looks like a day header (\"Day N : DD/MM/YYYY\"), "
                "which would split the day in two — nothing was written"
            )
        raise EditError("the edit would also change another day — nothing was written")
    for d, text in expect.items():
        found = [section_text(after, s) for s in b.sections if s.entry_date == d]
        if text is None and found:
            raise EditError(f"{d:%d/%m/%Y} would still be in the file")
        if text is not None and found != [text]:
            raise EditError(
                f"the text for {d:%d/%m/%Y} would not read back as written — does it contain "
                "a line that looks like a day header (\"Day N : DD/MM/YYYY\")?"
            )


def day_replace(md: MdText, s: Section, new_text: str, day_number: int | None = None, new_date: date | None = None) -> MdText:
    """New text (and optionally a new header) for one day."""
    new_text = clean_day_body(new_text)
    lines = _replace_region(md.lines, s.body_start, s.end, new_text, md.newline, _day_clean_lines)
    target = s.entry_date
    if (day_number is not None and day_number != s.day_number) or (new_date is not None and new_date != s.entry_date):
        old_header = lines[s.header]
        lead = re.match(r"\s*", content(old_header)).group(0)
        target = new_date or s.entry_date
        lines[s.header] = lead + header_line(day_number or s.day_number, target) + _ending(old_header)
    result = md.with_lines(lines)
    expect: dict[date, str | None] = {target: new_text}
    if target != s.entry_date:
        expect[s.entry_date] = None
    _verify_days(md.lines, result.lines, expect)
    return result


def day_insert(md: MdText, day_number: int, d: date, text: str) -> MdText:
    """Adds a day, in date order, separated from its neighbours by a blank line."""
    text = clean_day_body(text)
    layout = parse_layout(md.lines)
    if layout.by_date(d):
        raise EditError(f"{d:%d/%m/%Y} is already in the file")
    nl = md.newline
    block = [header_line(day_number, d) + nl] + [x + nl for x in (text.split("\n") if text else [])]
    after = next((s for s in layout.sections if s.entry_date > d), None)
    lines = list(md.lines)
    if after is not None:
        # Before the next day's header: it keeps the blank line it had before it.
        at = after.header
        lines[at:at] = block + [nl]
    else:
        if lines and not is_blank(lines[-1]):
            lines.append(nl)  # a blank line between the last day and the new one
        lines.extend(block)
    result = md.with_lines(lines)
    _verify_days(md.lines, result.lines, {d: text})
    return result


def day_delete(md: MdText, s: Section) -> MdText:
    lines = md.lines[: s.header] + md.lines[s.end :]
    result = md.with_lines(lines)
    _verify_days(md.lines, result.lines, {s.entry_date: None})
    return result


def note_replace(md: MdText, new_text: str) -> MdText:
    """New content for a whole note, keeping its leading and trailing layout."""
    new_text = clean_note_body(new_text)
    lines = _replace_region(md.lines, 0, len(md.lines), new_text, md.newline, lambda cs: clean_note_body("\n".join(cs)).split("\n"))
    result = md.with_lines(lines) if md.lines else MdText(md.bom, md.newline, lines).with_lines(lines, final_newline=True)
    if clean_note_body(result.text) != new_text:
        raise EditError("the note would not read back as written")
    return result


# ---------------------------------------------------------------- writing
def _history_name(path: Path, root: Path | None) -> str:
    try:
        rel = path.resolve().relative_to(root.resolve()) if root else Path(path.name)
    except ValueError:
        rel = Path(path.name)
    return "__".join(rel.with_suffix("").parts)


def keep_history(path: Path, history_dir: Path, root: Path | None, keep: int = 50, label: str = "") -> Path | None:
    """Copies the current file into history_dir/<relative path>/<timestamp>.md."""
    if not path.exists():
        return None
    folder = history_dir / _history_name(path, root)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    dest = folder / f"{stamp}{('-' + label) if label else ''}{path.suffix}"
    dest.write_bytes(path.read_bytes())
    for old in sorted(folder.iterdir(), reverse=True)[keep:]:
        old.unlink(missing_ok=True)
    return dest


def write_atomic(path: Path, data: bytes, attempts: int = 6) -> None:
    """Temporary file in the same folder, flushed to disk, then renamed over the
    original. Retries briefly when another program holds the file (antivirus,
    a sync client)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.ownlife-tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    for i in range(attempts):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if i == attempts - 1:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(0.15 * (i + 1))
