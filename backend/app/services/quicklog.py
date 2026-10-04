"""Quick-log syntax: one line per record, no AI needed (works offline, costs nothing).

    05:40-06:10 Fajr #prayer
    09:00-11:30 Linear algebra, chapter 3 #math @Ibrahim !Office
    23:10-01:40 Anime X reactions #reaction       (crosses midnight: fine)
    -400 taxi #transport                              (money out, in the profile currency)
    +1000 gift from Uncle Karim #gift              (money in)
    !! Workout done                                    (habit: done | missed | urge | relapse)

#word picks the category whose name contains the word, @Name links a person,
and !Place (inside a time line) sets the location.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from app.models import Category, Habit, Person

_TIME_LINE = re.compile(r"^\s*(\d{1,2}[:hH.]\d{2})\s*[-–—]\s*(\d{1,2}[:hH.]\d{2})\s+(.+)$")
_MONEY_LINE = re.compile(r"^\s*([+-])\s*(\d[\d\s.,]*)\s*(?:f|fcfa|cfa|xof)?\s+(.+)$", re.IGNORECASE)
_HABIT_LINE = re.compile(r"^\s*!!\s*(.+?)\s+(done|missed|urge|relapse|skip)\s*$", re.IGNORECASE)
_HASH = re.compile(r"#([\w&-]+)")
_AT = re.compile(r"@([\w.-]+(?:\s[A-Z][\w.-]+)?)")
_PLACE = re.compile(r"(?<!\S)!([\w.-]+(?:\s[A-Z][\w.-]+)?)")


@dataclass
class QuickResult:
    time_entries: list[dict] = field(default_factory=list)
    transactions: list[dict] = field(default_factory=list)
    habit_logs: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def match_category(word: str | None, categories: list[Category]) -> Category | None:
    if not word:
        return None
    # "Mathematics (core)": models sometimes copy the kind written after the name.
    w = re.sub(r"\s*\([^)]*\)\s*$", "", word).casefold().strip()
    if not w:
        return None
    exact = [c for c in categories if c.name.casefold() == w]
    if exact:
        return exact[0]
    starts = [c for c in categories if c.name.casefold().startswith(w)]
    if starts:
        return starts[0]
    contains = [c for c in categories if w in c.name.casefold()]
    if contains:
        return contains[0]
    # "math" -> "Mathematics", "reaction" -> "Reaction videos": try word stems both ways
    loose = [c for c in categories if any(part.startswith(w[:4]) for part in c.name.casefold().split())]
    return loose[0] if loose and len(w) >= 4 else None


def match_habit(name: str, habits: list[Habit]) -> Habit | None:
    n = name.casefold().strip()
    for h in habits:
        if h.name.casefold() == n:
            return h
    for h in habits:
        if n in h.name.casefold() or h.name.casefold() in n:
            return h
    return None


def match_person(name: str, people: list[Person]) -> Person | None:
    n = name.casefold()
    for p in people:
        if p.name.casefold() == n:
            return p
    for p in people:
        if p.name.casefold().startswith(n):
            return p
    return None


def parse_quick(
    text: str,
    day: date,
    categories: list[Category],
    habits: list[Habit],
    people: list[Person],
) -> QuickResult:
    res = QuickResult()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("//"):
            continue
        if m := _HABIT_LINE.match(line):
            habit = match_habit(m.group(1), habits)
            if habit is None:
                res.errors.append(f"No habit matches {m.group(1)!r}: {line}")
                continue
            res.habit_logs.append(
                {"habit_id": habit.id, "habit": habit.name, "date": day.isoformat(), "status": m.group(2).lower()}
            )
            continue
        if m := _TIME_LINE.match(line):
            start, end, rest = m.group(1), m.group(2), m.group(3)
            cat_word = (_HASH.search(rest) or [None, None])[1]
            category = match_category(cat_word, categories)
            names = _AT.findall(rest)
            place = (_PLACE.search(rest) or [None, None])[1]
            title = _PLACE.sub("", _AT.sub("", _HASH.sub("", rest))).strip(" ,;-") or (
                category.name if category else "Untitled"
            )
            linked = [p for p in (match_person(n, people) for n in names) if p]
            res.time_entries.append(
                {
                    "title": title,
                    "date": day.isoformat(),
                    "start": start.replace("h", ":").replace("H", ":").replace(".", ":"),
                    "end": end.replace("h", ":").replace("H", ":").replace(".", ":"),
                    "category_id": category.id if category else None,
                    "category": category.name if category else None,
                    "person_ids": [p.id for p in linked],
                    "new_people": [n for n in names if not match_person(n, people)],
                    "location": place,
                }
            )
            if cat_word and category is None:
                res.errors.append(f"No category matches #{cat_word} (entry kept uncategorised): {line}")
            continue
        if m := _MONEY_LINE.match(line):
            amount = int(re.sub(r"[\s.,]", "", m.group(2)))
            rest = m.group(3)
            cat_word = (_HASH.search(rest) or [None, None])[1]
            item = _HASH.sub("", rest).strip(" ,;-") or "Unspecified"
            res.transactions.append(
                {
                    "date": day.isoformat(),
                    "direction": "in" if m.group(1) == "+" else "out",
                    "amount": amount,
                    "item": item,
                    "category": (cat_word or "other").lower(),
                }
            )
            continue
        res.errors.append(f"Not understood: {line}")
    return res
