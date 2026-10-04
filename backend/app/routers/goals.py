"""Goals (vision → objectives → milestones) and life chapters."""

from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.db import utcnow
from app.deps import DB, CurrentProfile, CurrentUser
from app.models import Category, Goal, LifeChapter
from app.serializers import chapter_out
from app.services.goals import goal_tree

router = APIRouter(prefix="/api", tags=["direction"])

Level = Literal["vision", "objective", "milestone"]
Status = Literal["active", "done", "paused", "dropped"]
Mode = Literal["manual", "children", "hours", "metric"]


class GoalIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    level: Level = "objective"
    parent_id: int | None = None
    description: str | None = None
    status: Status = "active"
    start_date: date | None = None
    target_date: date | None = None
    progress_mode: Mode = "manual"
    progress: float = Field(0, ge=0, le=100)
    hours_target: float | None = Field(None, gt=0)
    metric_unit: str | None = Field(None, max_length=30)
    metric_start: float | None = None
    metric_target: float | None = None
    metric_current: float | None = None
    color: str | None = Field(None, max_length=9)
    category_ids: list[int] = Field(default_factory=list)


class GoalPatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=200)
    level: Level | None = None
    parent_id: int | None = None
    description: str | None = None
    status: Status | None = None
    start_date: date | None = None
    target_date: date | None = None
    progress_mode: Mode | None = None
    progress: float | None = Field(None, ge=0, le=100)
    hours_target: float | None = Field(None, gt=0)
    metric_unit: str | None = Field(None, max_length=30)
    metric_start: float | None = None
    metric_target: float | None = None
    metric_current: float | None = None
    color: str | None = Field(None, max_length=9)
    sort: int | None = None
    category_ids: list[int] | None = None


class ChapterIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    start_date: date
    end_date: date | None = None
    kind: Literal["past", "plan"] = "past"
    color: str | None = Field(None, max_length=9)
    description: str | None = None
    approximate: bool = False


class ChapterPatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=200)
    start_date: date | None = None
    end_date: date | None = None
    kind: Literal["past", "plan"] | None = None
    color: str | None = Field(None, max_length=9)
    description: str | None = None
    approximate: bool | None = None
    sort: int | None = None


def _own_goal(db, user_id: int, goal_id: int) -> Goal:
    g = db.get(Goal, goal_id)
    if g is None or g.user_id != user_id:
        raise HTTPException(404, "Goal not found")
    return g


def _categories(db, user_id: int, ids: list[int]) -> list[Category]:
    cats = [c for c in (db.get(Category, i) for i in ids) if c is not None and c.user_id == user_id]
    if len(cats) != len(set(ids)):
        raise HTTPException(422, "Unknown category in category_ids")
    return cats


def _check_parent(db, user_id: int, goal_id: int | None, parent_id: int | None) -> None:
    if parent_id is None:
        return
    parent = _own_goal(db, user_id, parent_id)
    # walk up: a goal cannot become its own ancestor
    seen = set()
    while parent is not None:
        if parent.id == goal_id or parent.id in seen:
            raise HTTPException(422, "That would create a cycle in the goal tree")
        seen.add(parent.id)
        parent = db.get(Goal, parent.parent_id) if parent.parent_id else None


@router.get("/goals")
def list_goals(user: CurrentUser, profile: CurrentProfile, db: DB) -> list[dict]:
    return goal_tree(db, user.id, profile.timezone, utcnow())


@router.post("/goals")
def create_goal(body: GoalIn, user: CurrentUser, db: DB) -> dict:
    _check_parent(db, user.id, None, body.parent_id)
    data = body.model_dump(exclude={"category_ids"})
    g = Goal(user_id=user.id, **data)
    g.categories = _categories(db, user.id, body.category_ids)
    if g.status == "done":
        g.completed_at = utcnow()
    db.add(g)
    db.commit()
    return {"id": g.id}


@router.patch("/goals/{goal_id}")
def update_goal(goal_id: int, body: GoalPatch, user: CurrentUser, db: DB) -> dict:
    g = _own_goal(db, user.id, goal_id)
    data = body.model_dump(exclude_unset=True)
    if "parent_id" in data:
        _check_parent(db, user.id, g.id, data["parent_id"])
    if "category_ids" in data:
        g.categories = _categories(db, user.id, data.pop("category_ids") or [])
    if "status" in data:
        g.completed_at = utcnow() if data["status"] == "done" else None
    for k, v in data.items():
        setattr(g, k, v)
    db.commit()
    return {"id": g.id}


@router.delete("/goals/{goal_id}")
def delete_goal(goal_id: int, user: CurrentUser, db: DB) -> dict:
    db.delete(_own_goal(db, user.id, goal_id))  # sub-goals cascade
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- chapters
@router.get("/chapters")
def list_chapters(user: CurrentUser, db: DB) -> list[dict]:
    rows = db.scalars(
        select(LifeChapter).where(LifeChapter.user_id == user.id).order_by(LifeChapter.start_date, LifeChapter.sort)
    ).all()
    return [chapter_out(c) for c in rows]


def _check_dates(start: date | None, end: date | None) -> None:
    if start and end and end < start:
        raise HTTPException(422, "A chapter cannot end before it starts")


@router.post("/chapters")
def create_chapter(body: ChapterIn, user: CurrentUser, db: DB) -> dict:
    _check_dates(body.start_date, body.end_date)
    c = LifeChapter(user_id=user.id, **body.model_dump())
    db.add(c)
    db.commit()
    return chapter_out(c)


@router.patch("/chapters/{chapter_id}")
def update_chapter(chapter_id: int, body: ChapterPatch, user: CurrentUser, db: DB) -> dict:
    c = db.get(LifeChapter, chapter_id)
    if c is None or c.user_id != user.id:
        raise HTTPException(404, "Chapter not found")
    data = body.model_dump(exclude_unset=True)
    _check_dates(data.get("start_date", c.start_date), data.get("end_date", c.end_date))
    for k, v in data.items():
        setattr(c, k, v)
    db.commit()
    return chapter_out(c)


@router.delete("/chapters/{chapter_id}")
def delete_chapter(chapter_id: int, user: CurrentUser, db: DB) -> dict:
    c = db.get(LifeChapter, chapter_id)
    if c is None or c.user_id != user.id:
        raise HTTPException(404, "Chapter not found")
    db.delete(c)
    db.commit()
    return {"ok": True}
