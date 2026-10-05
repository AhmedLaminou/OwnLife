"""Classification rules: which category an imported activity belongs to."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ClassificationRule, MediaItem

_YT_HOSTS = {"youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be", "youtube-nocookie.com"}


@dataclass
class Activity:
    title: str | None = None
    channel: str | None = None
    url: str | None = None
    app: str | None = None


def domain_of(url: str | None) -> str | None:
    if not url:
        return None
    try:
        host = urlparse(url).hostname or ""
    except ValueError:
        return None
    host = host.lower()
    return host[4:] if host.startswith("www.") else host or None


def youtube_video_id(url: str | None) -> str | None:
    if not url:
        return None
    try:
        p = urlparse(url)
    except ValueError:
        return None
    host = (p.hostname or "").lower().removeprefix("www.")
    if host not in _YT_HOSTS:
        return None
    if host == "youtu.be":
        vid = p.path.strip("/").split("/")[0]
    elif p.path.startswith(("/shorts/", "/live/", "/embed/")):
        vid = p.path.split("/")[2] if len(p.path.split("/")) > 2 else ""
    else:
        vid = (parse_qs(p.query).get("v") or [""])[0]
    return vid if re.fullmatch(r"[A-Za-z0-9_-]{6,20}", vid or "") else None


class RuleSet:
    """Rules sorted by priority (lowest first); the first match wins.
    Plain patterns match case-insensitively anywhere in the field.

    A single YouTube video can also carry its own category: the one you chose
    for it wins over every rule (it is more precise than a channel rule); the
    one the local model chose applies only when no rule matches."""

    def __init__(self, rules: list[ClassificationRule], yours: dict[str, int] | None = None,
                 model: dict[str, int] | None = None) -> None:
        self.yours = yours or {}
        self.model = model or {}
        self._rules: list[tuple[str, re.Pattern | None, str | None, int]] = []
        for r in sorted(rules, key=lambda r: (r.priority, r.id or 0)):
            if r.is_regex:
                try:
                    self._rules.append((r.field, re.compile(r.pattern, re.IGNORECASE), None, r.category_id))
                except re.error:
                    continue
            else:
                self._rules.append((r.field, None, r.pattern.casefold(), r.category_id))

    def classify(self, act: Activity) -> int | None:
        vid = youtube_video_id(act.url) if self.yours or self.model else None
        if vid in self.yours:
            return self.yours[vid]
        found = self.by_rules(act)
        return found if found is not None or vid is None else self.model.get(vid)

    def by_rules(self, act: Activity) -> int | None:
        values = {
            "title": act.title,
            "channel": act.channel,
            "url": act.url,
            "domain": domain_of(act.url),
            "app": act.app,
        }
        for field, rx, needle, category_id in self._rules:
            value = values.get(field)
            if not value:
                continue
            if rx is not None:
                if rx.search(value):
                    return category_id
            elif needle and needle in value.casefold():
                return category_id
        return None


def load_ruleset(db: Session, user_id: int) -> RuleSet:
    yours: dict[str, int] = {}
    model: dict[str, int] = {}
    for vid, cat, who in db.execute(select(MediaItem.external_id, MediaItem.category_id, MediaItem.sorted_by).where(
            MediaItem.user_id == user_id, MediaItem.kind == "video", MediaItem.sorted_by.in_(("you", "model")),
            MediaItem.category_id.is_not(None))):
        (yours if who == "you" else model)[vid] = cat
    return RuleSet(
        list(db.scalars(select(ClassificationRule).where(ClassificationRule.user_id == user_id))), yours, model
    )
