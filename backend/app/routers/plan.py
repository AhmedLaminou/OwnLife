"""Day plans: planned blocks next to what actually happened, and reusable templates."""

from __future__ import annotations

import datetime as dt

from datetime import date, timedelta

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from app.db import utcnow
from app.deps import DB, CurrentProfile, CurrentUser
from app.models import Category, PlanBlock, PlanTemplate
from app.serializers import entry_out, plan_block_out, template_out
from app.services.ledger import entries_overlapping
from app.services import prayer
from app.services.quicklog import match_category
from app.services.timeutil import (
    day_start_utc,
    hhmm_to_minutes,
    minutes_to_hhmm,
    overlap_seconds,
    range_utc,
    tz_of,
)

router = APIRouter(prefix="/api", tags=["plan"])


class BlockIn(BaseModel):
    date: dt.date
    start: str
    end: str
    title: str = Field(min_length=1, max_length=200)
    category_id: int | None = None
    is_fixed: bool = False
    notes: str | None = None


class BlockPatch(BaseModel):
    start: str | None = None
    end: str | None = None
    title: str | None = Field(None, min_length=1, max_length=200)
    category_id: int | None = None
    is_fixed: bool | None = None
    notes: str | None = None


class TemplateBlock(BaseModel):
    start: str
    end: str
    title: str = Field(min_length=1, max_length=200)
    category: str | None = None
    category_id: int | None = None
    is_fixed: bool = False


class TemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    weekdays: list[int] = Field(default_factory=list)
    blocks: list[TemplateBlock] = Field(default_factory=list)


class ApplyIn(BaseModel):
    template_id: int
    replace: bool = True


class CopyIn(BaseModel):
    source_date: date
    replace: bool = True


def _span(start: str, end: str) -> tuple[int, int]:
    try:
        s, e = hhmm_to_minutes(start), hhmm_to_minutes(end)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if e <= s:
        e += 24 * 60  # ends after midnight
    return s, e


def _check_category(db, user_id: int, category_id: int | None) -> None:
    if category_id is not None:
        c = db.get(Category, category_id)
        if c is None or c.user_id != user_id:
            raise HTTPException(422, "Unknown category")


@router.get("/plan/{day}")
def plan_day(day: date, user: CurrentUser, profile: CurrentProfile, db: DB) -> dict:
    tz = tz_of(profile.timezone)
    now = utcnow()
    blocks = db.scalars(
        select(PlanBlock).where(PlanBlock.user_id == user.id, PlanBlock.plan_date == day).order_by(PlanBlock.start_minute)
    ).all()
    lo, _ = range_utc(day, day, tz)
    hi = day_start_utc(day + timedelta(days=2), tz)  # blocks may run past midnight
    entries = entries_overlapping(db, user.id, lo, hi)
    out_blocks = []
    planned = matched = 0.0
    for b in blocks:
        b0 = lo + timedelta(minutes=b.start_minute)
        b1 = lo + timedelta(minutes=b.end_minute)
        kind = b.category.kind if b.category else None
        hit = 0.0
        if kind:
            for e in entries:
                if e.category is not None and e.category.kind == kind:
                    hit += overlap_seconds(b0, b1, e.started_at, e.ended_at or now)
        length = (b1 - b0).total_seconds()
        hit = min(hit, length)
        if kind and kind not in ("maintenance",):
            planned += length
            matched += hit
        out_blocks.append({**plan_block_out(b), "matched_seconds": hit, "planned_seconds": length})
    return {
        "date": day.isoformat(),
        "blocks": out_blocks,
        "entries": [entry_out(e, now) for e in entries if e.started_at < day_start_utc(day + timedelta(days=1), tz)],
        "adherence": round(matched / planned, 3) if planned else None,
    }


@router.post("/plan/blocks")
def create_block(body: BlockIn, user: CurrentUser, db: DB) -> dict:
    _check_category(db, user.id, body.category_id)
    s, e = _span(body.start, body.end)
    b = PlanBlock(user_id=user.id, plan_date=body.date, start_minute=s, end_minute=e, title=body.title,
                  category_id=body.category_id, is_fixed=body.is_fixed, notes=body.notes)
    db.add(b)
    db.commit()
    db.refresh(b)
    return plan_block_out(b)


@router.patch("/plan/blocks/{block_id}")
def update_block(block_id: int, body: BlockPatch, user: CurrentUser, db: DB) -> dict:
    b = db.get(PlanBlock, block_id)
    if b is None or b.user_id != user.id:
        raise HTTPException(404, "Block not found")
    data = body.model_dump(exclude_unset=True)
    if "start" in data or "end" in data:
        s, e = _span(data.pop("start", None) or minutes_to_hhmm(b.start_minute),
                     data.pop("end", None) or minutes_to_hhmm(b.end_minute))
        b.start_minute, b.end_minute = s, e
    if "category_id" in data:
        _check_category(db, user.id, data["category_id"])
    for k, v in data.items():
        setattr(b, k, v)
    db.commit()
    db.refresh(b)
    return plan_block_out(b)


@router.delete("/plan/blocks/{block_id}")
def delete_block(block_id: int, user: CurrentUser, db: DB) -> dict:
    b = db.get(PlanBlock, block_id)
    if b is None or b.user_id != user.id:
        raise HTTPException(404, "Block not found")
    db.delete(b)
    db.commit()
    return {"ok": True}


@router.post("/plan/{day}/apply-template")
def apply_template(day: date, body: ApplyIn, user: CurrentUser, profile: CurrentProfile, db: DB) -> dict:
    t = db.get(PlanTemplate, body.template_id)
    if t is None or t.user_id != user.id:
        raise HTTPException(404, "Template not found")
    if body.replace:
        db.execute(delete(PlanBlock).where(PlanBlock.user_id == user.id, PlanBlock.plan_date == day))
    cats = list(db.scalars(select(Category).where(Category.user_id == user.id)))
    blocks = []
    for raw in t.blocks or []:
        blk = TemplateBlock.model_validate(raw)
        s, e = _span(blk.start, blk.end)
        cat_id = blk.category_id
        if cat_id is None and blk.category:
            match = match_category(blk.category, cats)
            cat_id = match.id if match else None
        blocks.append({"start_minute": s, "end_minute": e, "title": blk.title, "category_id": cat_id,
                       "is_fixed": blk.is_fixed})
    # Prayer blocks follow the sun: they move to the day's computed times.
    prefs = prayer.settings_with_defaults((profile.prefs or {}).get("prayer"))
    moved = 0
    if prefs["align_planner"]:
        aligned = prayer.align_blocks(blocks, prayer.times_for(day, prefs, tz_of(profile.timezone)))
        moved = sum(1 for b in aligned if b.get("prayer"))
        blocks = aligned
    for b in blocks:
        db.add(PlanBlock(user_id=user.id, plan_date=day, start_minute=b["start_minute"], end_minute=b["end_minute"],
                         title=b["title"], category_id=b["category_id"], is_fixed=b["is_fixed"]))
    db.commit()
    return {"created": len(blocks), "prayers_aligned": moved}


@router.post("/plan/{day}/copy-from")
def copy_from(day: date, body: CopyIn, user: CurrentUser, db: DB) -> dict:
    src = db.scalars(
        select(PlanBlock).where(PlanBlock.user_id == user.id, PlanBlock.plan_date == body.source_date)
    ).all()
    if body.replace:
        db.execute(delete(PlanBlock).where(PlanBlock.user_id == user.id, PlanBlock.plan_date == day))
    for b in src:
        db.add(PlanBlock(user_id=user.id, plan_date=day, start_minute=b.start_minute, end_minute=b.end_minute,
                         title=b.title, category_id=b.category_id, is_fixed=b.is_fixed, notes=b.notes))
    db.commit()
    return {"created": len(src)}


# ---------------------------------------------------------------- templates
@router.get("/plan-templates")
def list_templates(user: CurrentUser, db: DB) -> list[dict]:
    return [template_out(t) for t in db.scalars(select(PlanTemplate).where(PlanTemplate.user_id == user.id))]


@router.post("/plan-templates")
def create_template(body: TemplateIn, user: CurrentUser, db: DB) -> dict:
    for blk in body.blocks:
        _span(blk.start, blk.end)
    t = PlanTemplate(user_id=user.id, name=body.name, weekdays=body.weekdays,
                     blocks=[b.model_dump() for b in body.blocks])
    db.add(t)
    db.commit()
    return template_out(t)


@router.patch("/plan-templates/{template_id}")
def update_template(template_id: int, body: TemplateIn, user: CurrentUser, db: DB) -> dict:
    t = db.get(PlanTemplate, template_id)
    if t is None or t.user_id != user.id:
        raise HTTPException(404, "Template not found")
    for blk in body.blocks:
        _span(blk.start, blk.end)
    t.name, t.weekdays, t.blocks = body.name, body.weekdays, [b.model_dump() for b in body.blocks]
    db.commit()
    return template_out(t)


@router.post("/plan-templates/from-day/{day}")
def template_from_day(day: date, user: CurrentUser, db: DB, name: str = "Saved day") -> dict:
    blocks = db.scalars(
        select(PlanBlock).where(PlanBlock.user_id == user.id, PlanBlock.plan_date == day).order_by(PlanBlock.start_minute)
    ).all()
    t = PlanTemplate(
        user_id=user.id,
        name=name[:80],
        weekdays=[],
        blocks=[
            {"start": minutes_to_hhmm(b.start_minute), "end": minutes_to_hhmm(b.end_minute), "title": b.title,
             "category_id": b.category_id, "is_fixed": b.is_fixed}
            for b in blocks
        ],
    )
    db.add(t)
    db.commit()
    return template_out(t)


@router.delete("/plan-templates/{template_id}")
def delete_template(template_id: int, user: CurrentUser, db: DB) -> dict:
    t = db.get(PlanTemplate, template_id)
    if t is None or t.user_id != user.id:
        raise HTTPException(404, "Template not found")
    db.delete(t)
    db.commit()
    return {"ok": True}
