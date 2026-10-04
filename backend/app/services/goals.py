"""Goal trees, progress, and the hours invested in each goal."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Goal, TimeEntry
from app.services.ledger import effective_spans
from app.services.timeutil import day_start_utc, local_date_of, local_today, tz_of


def hours_logged(
    db: Session,
    user_id: int,
    category_ids: set[int],
    goal_ids: set[int],
    since: datetime,
    now: datetime,
) -> float:
    """Hours of entries in any of the categories, or linked to any of the goals,
    started after `since`. Each entry counts once even if it matches twice."""
    if not goal_ids and not category_ids:
        return 0.0
    # Every source since `since`: an automatic entry only counts where no
    # entry of a higher rank already covers it (see ledger.effective_spans).
    entries = list(db.scalars(select(TimeEntry).where(TimeEntry.user_id == user_id, TimeEntry.started_at >= since)))
    seconds = sum(
        (b - a).total_seconds()
        for e, a, b in effective_spans(entries, now)
        if e.category_id in category_ids or e.goal_id in goal_ids
    )
    return round(seconds / 3600, 2)


def _start(goal: Goal, tz) -> date:
    return goal.start_date or local_date_of(goal.created_at, tz)


def _own_progress(goal: Goal, hours: float) -> float | None:
    if goal.status == "done":
        return 100.0
    if goal.progress_mode == "hours" and goal.hours_target:
        return min(100.0, 100.0 * hours / goal.hours_target)
    if goal.progress_mode == "metric" and goal.metric_target is not None:
        start = goal.metric_start if goal.metric_start is not None else 0.0
        current = goal.metric_current if goal.metric_current is not None else start
        span = goal.metric_target - start
        if span == 0:
            return 100.0
        return max(0.0, min(100.0, 100.0 * (current - start) / span))
    if goal.progress_mode == "children":
        return None  # filled in from the sub-goals
    return goal.progress


def goal_tree(db: Session, user_id: int, profile_tz: str, now: datetime) -> list[dict]:
    tz = tz_of(profile_tz)
    today = local_today(tz)
    goals = db.scalars(
        select(Goal).where(Goal.user_id == user_id).order_by(Goal.sort, Goal.id)
    ).all()
    by_id = {g.id: g for g in goals}
    children: dict[int | None, list[Goal]] = {}
    for g in goals:
        children.setdefault(g.parent_id if g.parent_id in by_id else None, []).append(g)

    def build(g: Goal) -> dict:
        kids = [build(k) for k in children.get(g.id, [])]
        own_cats = {c.id for c in g.categories}
        own_hours = hours_logged(db, user_id, own_cats, {g.id}, day_start_utc(_start(g, tz), tz), now)
        # The subtree's hours: union of every category and goal below, from the
        # earliest start in the subtree.
        tree_cats, tree_goals, tree_start = set(own_cats), {g.id}, _start(g, tz)
        for k in kids:
            tree_cats |= set(k["_tree_cats"])
            tree_goals |= set(k["_tree_goals"])
            tree_start = min(tree_start, date.fromisoformat(k["_tree_start"]))
        tree_hours = (
            hours_logged(db, user_id, tree_cats, tree_goals, day_start_utc(tree_start, tz), now)
            if kids
            else own_hours
        )
        progress = _own_progress(g, own_hours)
        live_kids = [k for k in kids if k["status"] != "dropped"]
        if progress is None:
            progress = sum(k["progress"] for k in live_kids) / len(live_kids) if live_kids else 0.0
        return {
            "id": g.id,
            "parent_id": g.parent_id,
            "title": g.title,
            "description": g.description,
            "level": g.level,
            "status": g.status,
            "start_date": g.start_date.isoformat() if g.start_date else None,
            "target_date": g.target_date.isoformat() if g.target_date else None,
            "days_left": (g.target_date - today).days if g.target_date else None,
            "progress_mode": g.progress_mode,
            "progress_manual": g.progress,
            "hours_target": g.hours_target,
            "metric_unit": g.metric_unit,
            "metric_start": g.metric_start,
            "metric_target": g.metric_target,
            "metric_current": g.metric_current,
            "color": g.color,
            "sort": g.sort,
            "category_ids": sorted(own_cats),
            "invested_hours": own_hours,
            "invested_hours_tree": tree_hours,
            "progress": round(progress, 1),
            "children": kids,
            "_tree_cats": sorted(tree_cats),
            "_tree_goals": sorted(tree_goals),
            "_tree_start": tree_start.isoformat(),
        }

    def strip(node: dict) -> dict:
        for key in ("_tree_cats", "_tree_goals", "_tree_start"):
            node.pop(key, None)
        node["children"] = [strip(k) for k in node["children"]]
        return node

    return [strip(build(g)) for g in children.get(None, [])]
