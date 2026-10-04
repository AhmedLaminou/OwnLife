"""Categories and the rules that classify imported activity."""

from __future__ import annotations

import re
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.deps import DB, CurrentUser
from app.models import CATEGORY_KINDS, Category, ClassificationRule, TimeEntry, WatchEvent
from app.serializers import category_out, rule_out
from app.services import watchlive
from app.services.rules import Activity, load_ruleset
from app.services.youtube import HISTORY_SOURCES, rebuild_sessions

router = APIRouter(prefix="/api", tags=["categories"])

Kind = Literal[CATEGORY_KINDS]  # type: ignore[valid-type]


class CategoryIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    kind: Kind
    color: str | None = Field(None, max_length=9)
    icon: str | None = Field(None, max_length=40)
    parent_id: int | None = None
    is_private: bool = False


class CategoryPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=80)
    kind: Kind | None = None
    color: str | None = Field(None, max_length=9)
    icon: str | None = Field(None, max_length=40)
    parent_id: int | None = None
    is_private: bool | None = None
    archived: bool | None = None
    sort: int | None = None


class RuleIn(BaseModel):
    field: Literal["channel", "title", "url", "domain", "app"]
    pattern: str = Field(min_length=1, max_length=300)
    is_regex: bool = False
    category_id: int
    priority: int = 100
    note: str | None = Field(None, max_length=200)


class RulePatch(BaseModel):
    field: Literal["channel", "title", "url", "domain", "app"] | None = None
    pattern: str | None = Field(None, min_length=1, max_length=300)
    is_regex: bool | None = None
    category_id: int | None = None
    priority: int | None = None
    note: str | None = None


class RuleTestIn(BaseModel):
    title: str | None = None
    channel: str | None = None
    url: str | None = None
    app: str | None = None


def _own_category(db, user_id: int, category_id: int) -> Category:
    c = db.get(Category, category_id)
    if c is None or c.user_id != user_id:
        raise HTTPException(404, "Category not found")
    return c


@router.get("/categories")
def list_categories(user: CurrentUser, db: DB, include_archived: bool = False) -> list[dict]:
    q = select(Category).where(Category.user_id == user.id)
    if not include_archived:
        q = q.where(Category.archived.is_(False))
    return [category_out(c) for c in db.scalars(q.order_by(Category.sort, Category.name))]


@router.post("/categories")
def create_category(body: CategoryIn, user: CurrentUser, db: DB) -> dict:
    if db.scalar(select(Category).where(Category.user_id == user.id, Category.name == body.name.strip())):
        raise HTTPException(409, "A category with this name exists")
    if body.parent_id:
        _own_category(db, user.id, body.parent_id)
    top = db.scalar(select(func.max(Category.sort)).where(Category.user_id == user.id)) or 0
    c = Category(user_id=user.id, **{**body.model_dump(), "name": body.name.strip()}, sort=top + 1)
    db.add(c)
    db.commit()
    return category_out(c)


@router.patch("/categories/{category_id}")
def update_category(category_id: int, body: CategoryPatch, user: CurrentUser, db: DB) -> dict:
    c = _own_category(db, user.id, category_id)
    data = body.model_dump(exclude_unset=True)
    if data.get("parent_id"):
        if data["parent_id"] == c.id:
            raise HTTPException(422, "A category cannot be its own parent")
        _own_category(db, user.id, data["parent_id"])
    for k, v in data.items():
        setattr(c, k, v.strip() if k == "name" and isinstance(v, str) else v)
    db.commit()
    return category_out(c)


@router.delete("/categories/{category_id}")
def delete_category(category_id: int, user: CurrentUser, db: DB, hard: bool = False) -> dict:
    c = _own_category(db, user.id, category_id)
    used = db.scalar(select(func.count()).select_from(TimeEntry).where(TimeEntry.category_id == c.id)) or 0
    if used and not hard:
        c.archived = True  # keep history intact
        db.commit()
        return {"archived": True, "entries": used}
    db.delete(c)
    db.commit()
    return {"deleted": True, "entries_uncategorised": used}


# ---------------------------------------------------------------- rules
@router.get("/rules")
def list_rules(user: CurrentUser, db: DB) -> list[dict]:
    q = select(ClassificationRule).where(ClassificationRule.user_id == user.id)
    return [rule_out(r) for r in db.scalars(q.order_by(ClassificationRule.priority, ClassificationRule.id))]


def _check_pattern(pattern: str, is_regex: bool) -> None:
    if is_regex:
        try:
            re.compile(pattern)
        except re.error as e:
            raise HTTPException(422, f"Invalid regular expression: {e}") from e


@router.post("/rules")
def create_rule(body: RuleIn, user: CurrentUser, db: DB) -> dict:
    _own_category(db, user.id, body.category_id)
    _check_pattern(body.pattern, body.is_regex)
    r = ClassificationRule(user_id=user.id, **body.model_dump())
    db.add(r)
    db.commit()
    return rule_out(r)


@router.patch("/rules/{rule_id}")
def update_rule(rule_id: int, body: RulePatch, user: CurrentUser, db: DB) -> dict:
    r = db.get(ClassificationRule, rule_id)
    if r is None or r.user_id != user.id:
        raise HTTPException(404, "Rule not found")
    data = body.model_dump(exclude_unset=True)
    if "category_id" in data:
        _own_category(db, user.id, data["category_id"])
    _check_pattern(data.get("pattern", r.pattern), data.get("is_regex", r.is_regex))
    for k, v in data.items():
        setattr(r, k, v)
    db.commit()
    return rule_out(r)


@router.delete("/rules/{rule_id}")
def delete_rule(rule_id: int, user: CurrentUser, db: DB) -> dict:
    r = db.get(ClassificationRule, rule_id)
    if r is None or r.user_id != user.id:
        raise HTTPException(404, "Rule not found")
    db.delete(r)
    db.commit()
    return {"ok": True}


@router.post("/rules/test")
def test_rules(body: RuleTestIn, user: CurrentUser, db: DB) -> dict:
    cat_id = load_ruleset(db, user.id).classify(Activity(**body.model_dump()))
    c = db.get(Category, cat_id) if cat_id else None
    return {"category": category_out(c) if c else None}


@router.post("/rules/reapply")
def reapply_rules(user: CurrentUser, db: DB) -> dict:
    """Re-classifies imported entries after the rules changed. Entries whose
    category was set by hand are left alone."""
    rules = load_ruleset(db, user.id)
    changed = 0
    for e in db.scalars(
        select(TimeEntry).where(
            TimeEntry.user_id == user.id,
            TimeEntry.source == "activitywatch",
            TimeEntry.category_locked.is_(False),
        )
    ):
        meta = e.meta or {}
        titles = meta.get("titles") or [None]
        new = rules.classify(Activity(title=titles[0], channel=meta.get("channel"), app=meta.get("app"),
                                      url=f"https://{meta['domain']}/" if meta.get("domain") else None))
        if new != e.category_id:
            e.category_id = new
            changed += 1
    rebuilt = 0
    history = WatchEvent.source.in_(HISTORY_SOURCES)
    first = db.scalar(select(func.min(WatchEvent.occurred_at)).where(WatchEvent.user_id == user.id, history))
    last = db.scalar(select(func.max(WatchEvent.occurred_at)).where(WatchEvent.user_id == user.id, history))
    if first and last:
        rebuilt = rebuild_sessions(db, user.id, rules, first, last)
    measured = watchlive.rebuild(db, user.id, rules)
    db.commit()
    return {"activitywatch_reclassified": changed, "youtube_blocks_rebuilt": rebuilt, "extension_segments_refiled": measured}
