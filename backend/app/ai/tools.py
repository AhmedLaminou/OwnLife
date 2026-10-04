"""The assistant's tools.

Each tool opens its own database session: LangGraph runs synchronous tools in
worker threads. Tools return short plain-text results — the model reads them,
and every token counts on a free model. Records created are collected in
`ctx.actions` so the UI can show (and undo) exactly what the assistant did.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from langchain_core.tools import BaseTool, tool
from sqlalchemy import select

from app.ai import rag
from app.ai.embeddings import OllamaEmbedder
from app.config import get_settings
from app.db import SessionLocal, utcnow
from app.models import Category, Goal, Habit, LifeEvent, Person, PlanBlock, Profile, Transaction
from app.services import filesync, prayer, records
from app.services.goals import goal_tree
from app.services.habits import habit_stats
from app.services.ledger import KIND_ORDER, daily_breakdown, effective_spans, entries_overlapping
from app.services.life import life_overview
from app.services.privacy import redact
from app.services.quicklog import match_category, match_habit
from app.services.rules import Activity, load_ruleset
from app.services.timeutil import (
    hhmm_to_minutes,
    local_instant,
    local_today,
    minutes_to_hhmm,
    range_utc,
    tz_of,
)
from app.services.youtube import watched

LIFE_AREAS = ("education", "family", "faith", "health", "work", "move", "travel", "achievement",
              "turning_point", "other")


@dataclass
class ToolContext:
    user_id: int
    embedder: OllamaEmbedder | None
    for_cloud: bool
    actions: list[dict] = field(default_factory=list)
    # Passages the model asked for with search_memory: always shown as sources.
    citations: list[dict] = field(default_factory=list)
    # Passages put in the prompt automatically: shown only if the answer uses them.
    retrieved: list[dict] = field(default_factory=list)

    def sources(self, answer: str, limit: int = 12) -> list[dict]:
        used = [
            c for c in self.retrieved
            if (c.get("day_number") and f"Day {c['day_number']}" in answer)
            or (c.get("title") and not c.get("day_number") and c["title"] in answer)
        ]
        seen: set[int] = set()
        out = []
        for c in [*self.citations, *used]:
            if c["chunk_id"] not in seen:
                seen.add(c["chunk_id"])
                out.append(c)
        return out[:limit]


def _h(seconds: float) -> str:
    m = int(round(seconds / 60))
    return f"{m // 60}h{m % 60:02d}"


def _parse_date(value: str | None, today: date) -> date:
    if not value or value.lower() == "today":
        return today
    if value.lower() == "yesterday":
        return today - timedelta(days=1)
    if value.lower() == "tomorrow":
        return today + timedelta(days=1)
    return date.fromisoformat(value)


def _n(count: int, word: str) -> str:
    return f"{count} {word}{'s' if count != 1 else ''}"


def _money(amount: float) -> str:
    return f"{amount:,.2f}".rstrip("0").rstrip(".")


_KINDS = {"core", "growth", "work", "spirit", "social", "body", "maintenance", "noise", "destructive"}


def resolve_category(db, user_id: int, category: str, title: str) -> tuple[Category | None, str]:
    """Small local models sometimes pass a kind ("core") or a near miss instead of a
    category name. Try, in order: the name itself, the person's classification
    rules on the title, then the best category of the named kind."""
    cats = list(db.scalars(select(Category).where(Category.user_id == user_id, Category.archived.is_(False))))
    cat = match_category(category, cats)
    if cat is not None:
        return cat, ""
    by_rule = load_ruleset(db, user_id).classify(Activity(title=title))
    if by_rule is not None:
        cat = next((c for c in cats if c.id == by_rule), None)
        if cat is not None:
            return cat, f" (category taken from your rules: {cat.name})"
    kind = (category or "").strip().lower()
    if kind in _KINDS:
        same_kind = [c for c in cats if c.kind == kind]
        words = title.casefold()
        for c in same_kind:
            if any(w[:5] in words for w in c.name.casefold().split() if len(w) >= 4):
                return c, f" (closest {kind} category: {c.name})"
        if same_kind:
            return same_kind[0], f" (first {kind} category: {same_kind[0].name} — change it if wrong)"
    return None, ""


def build_tools(ctx: ToolContext) -> list[BaseTool]:
    uid = ctx.user_id

    def env(db):
        profile = db.get(Profile, uid)
        tz = tz_of(profile.timezone)
        return profile, tz, local_today(tz)

    @tool
    def search_memory(query: str, k: int = 6) -> str:
        """Search the journal and notes by meaning and by keyword. Use it for any
        question about the past: what happened, who was met, what was watched,
        thought or felt. Returns passages with their day."""
        with SessionLocal() as db:
            profile, _, _ = env(db)
            hits, mode = rag.search(
                db, uid, query, ctx.embedder, k=max(1, min(k, 10)), include_private=not ctx.for_cloud
            )
            if not hits:
                return "No matching passage."
            out = []
            for h in hits:
                ctx.citations.append(h.as_dict())
                body = redact(h.text, profile) if ctx.for_cloud else h.text
                out.append(f"{h.citation()} {h.date or ''}\n{body}")
            return f"({mode} search)\n\n" + "\n\n---\n\n".join(out)

    @tool
    def get_time_summary(start_date: str, end_date: str) -> str:
        """Hours per kind and per category between two dates (YYYY-MM-DD, inclusive).
        Use it for "how much did I study this week", "where did my time go"."""
        with SessionLocal() as db:
            profile, tz, today = env(db)
            s, e = _parse_date(start_date, today), _parse_date(end_date, today)
            if e < s:
                s, e = e, s
            now = utcnow()
            days = daily_breakdown(db, uid, s, e, tz, now)
            kinds: dict[str, float] = {}
            tracked = 0
            for d in days.values():
                if sum(d.values()) >= 7200:
                    tracked += 1
                for k, v in d.items():
                    kinds[k] = kinds.get(k, 0.0) + v
            lo, hi = range_utc(s, e, tz)
            cats: dict[str, float] = {}
            for entry, a, b in effective_spans(entries_overlapping(db, uid, lo, hi), now):
                if ctx.for_cloud and entry.is_private:
                    continue
                secs = (min(b, hi) - max(a, lo)).total_seconds()
                name = entry.category.name if entry.category else "Uncategorised"
                cats[name] = cats.get(name, 0.0) + max(0.0, secs)
            lines = [f"{s} → {e}: {len(days)} days, {tracked} with ≥2h logged."]
            by_kind = ", ".join(
                f"{k} {_h(kinds[k])}" for k in (*KIND_ORDER, "uncategorized") if kinds.get(k)
            )
            lines.append("By kind: " + (by_kind or "nothing logged"))
            top = sorted(cats.items(), key=lambda kv: -kv[1])[:10]
            if top:
                lines.append("Top categories: " + ", ".join(f"{n} {_h(v)}" for n, v in top))
            target = profile.focus_target_hours
            if tracked:
                lines.append(
                    f"Core average per tracked day: {_h(kinds.get('core', 0) / tracked)} (target {target:g}h)."
                )
            return "\n".join(lines)

    @tool
    def get_day(day: str = "today") -> str:
        """Everything logged on one day (YYYY-MM-DD, 'today' or 'yesterday'):
        time entries, planned blocks and habits."""
        with SessionLocal() as db:
            profile, tz, today = env(db)
            d = _parse_date(day, today)
            lo, hi = range_utc(d, d, tz)
            now = utcnow()
            lines = [f"{d:%A %d/%m/%Y}"]
            for e in entries_overlapping(db, uid, lo, hi):
                if ctx.for_cloud and e.is_private:
                    lines.append("- (private entry)")
                    continue
                end = e.ended_at
                span = f"{e.started_at.astimezone(tz):%H:%M}–{end.astimezone(tz):%H:%M}" if end else (
                    f"{e.started_at.astimezone(tz):%H:%M}–now (running)"
                )
                cat = e.category.name if e.category else "uncategorised"
                lines.append(f"- {span} {e.title} [{cat}] {_h(((end or now) - e.started_at).total_seconds())}")
            blocks = db.scalars(
                select(PlanBlock).where(PlanBlock.user_id == uid, PlanBlock.plan_date == d).order_by(PlanBlock.start_minute)
            ).all()
            if blocks:
                lines.append("Planned: " + "; ".join(
                    f"{minutes_to_hhmm(b.start_minute)}–{minutes_to_hhmm(b.end_minute)} {b.title}" for b in blocks
                ))
            habits = db.scalars(select(Habit).where(Habit.user_id == uid, Habit.archived.is_(False))).all()
            hs = []
            for h in habits:
                st = habit_stats(db, h, profile.timezone, now, days=max(1, (today - d).days + 1))
                status = next((c["status"] for c in st["calendar"] if c["date"] == d.isoformat()), None)
                hs.append(f"{h.name}: {status or '—'}")
            if hs:
                lines.append("Habits: " + "; ".join(hs))
            return "\n".join(lines) if len(lines) > 1 else f"{d}: nothing logged."

    @tool
    def log_time(
        title: str,
        category: str,
        start: str,
        end: str,
        day: str = "today",
        people: list[str] | None = None,
        location: str | None = None,
        notes: str | None = None,
    ) -> str:
        """Log a block of time. start/end are 24-hour HH:MM local times on `day`
        (YYYY-MM-DD, 'today' or 'yesterday'); an end earlier than the start means
        the block ended after midnight. `category` is a category NAME such as
        'Mathematics' or 'Reaction videos' — not a kind such as 'core'."""
        with SessionLocal() as db:
            profile, tz, today = env(db)
            cat, how = resolve_category(db, uid, category, title)
            if cat is None:
                cats = db.scalars(select(Category).where(Category.user_id == uid, Category.archived.is_(False))).all()
                return f"Unknown category {category!r}. Use one of: " + ", ".join(c.name for c in cats)
            d = _parse_date(day, today)
            s = local_instant(d, start, tz)
            e = local_instant(d, end, tz)
            if e <= s:
                e = local_instant(d, end, tz, day_offset=1)
            entry = records.create_time_entry(
                db,
                uid,
                title=title,
                category_id=cat.id,
                started_at=s,
                ended_at=e,
                source="ai",
                people=records.resolve_people(db, uid, people or []),
                location=location,
                notes=notes,
            )
            db.commit()
            ctx.actions.append({"type": "time_entry", "id": entry.id, "label": f"{title} · {cat.name}"})
            return f"Logged #{entry.id}: {title} {start}–{end} ({_h((e - s).total_seconds())}) in {cat.name}{how}."

    @tool
    def start_timer(title: str, category: str) -> str:
        """Start a live timer now (stops any running one). `category` is a category name."""
        with SessionLocal() as db:
            cat, _ = resolve_category(db, uid, category, title)
            if cat is None:
                cats = db.scalars(select(Category).where(Category.user_id == uid, Category.archived.is_(False))).all()
                return f"Unknown category {category!r}. Use one of: " + ", ".join(c.name for c in cats)
            previous = records.running_timer(db, uid)
            entry = records.start_timer(db, uid, title, cat.id, utcnow())
            db.commit()
            ctx.actions.append({"type": "timer_started", "id": entry.id, "label": title,
                                "stopped_id": previous.id if previous else None})
            return f"Timer started: {title} [{cat.name}]."

    @tool
    def stop_timer(pause: bool = False) -> str:
        """Stop the running timer. pause=true when the person takes a break or
        switches for a while and will come back to it (resume_timer)."""
        with SessionLocal() as db:
            now = utcnow()
            entry = records.pause_timer(db, uid, now) if pause else records.stop_timer(db, uid, now)
            db.commit()
            if entry is None:
                return "No timer was running."
            ctx.actions.append({"type": "timer_paused" if pause else "timer_stopped", "id": entry.id,
                                "label": f"{'Paused' if pause else 'Stopped'} {entry.title}"})
            took = _h(records.session_seconds(db, uid, entry, now))
            return f"Paused {entry.title} at {took}." if pause else f"Stopped {entry.title}: {took}."

    @tool
    def resume_timer() -> str:
        """Resume the most recently paused timer (stops any other running one)."""
        with SessionLocal() as db:
            now = utcnow()
            previous = records.running_timer(db, uid)
            paused = records.paused_timers(db, uid, now)
            entry = records.resume_timer(db, uid, now)
            if entry is None:
                return "No paused timer."
            db.commit()
            ctx.actions.append({"type": "timer_resumed", "id": entry.id, "label": f"Resumed {entry.title}",
                                "paused_id": paused[0].id, "stopped_id": previous.id if previous else None})
            return f"Resumed {entry.title} ({_h(records.session_seconds(db, uid, entry, now))} so far)."

    @tool
    def log_expense(
        item: str,
        amount: float,
        direction: str = "out",
        category: str = "other",
        day: str = "today",
        counterparty: str | None = None,
    ) -> str:
        """Record money spent (direction 'out') or received ('in'), in the profile currency."""
        with SessionLocal() as db:
            profile, _, today = env(db)
            try:
                tx = records.create_transaction(
                    db, uid, profile.currency,
                    occurred_on=_parse_date(day, today), direction=direction, amount=amount,
                    item=item, category=category, counterparty=counterparty, source="ai",
                )
            except ValueError as e:
                return f"Not recorded: {e}"
            db.commit()
            ctx.actions.append({"type": "transaction", "id": tx.id, "label": f"{item} {amount:g}"})
            sign = "-" if direction == "out" else "+"
            return f"Recorded {sign}{amount:g} {profile.currency}: {item}."

    @tool
    def log_habit(habit: str, status: str, day: str = "today", time: str | None = None, note: str | None = None) -> str:
        """Log a habit. For habits to build: done | missed | skip.
        For habits to quit: urge (an urge resisted) | relapse."""
        with SessionLocal() as db:
            profile, tz, today = env(db)
            habits = db.scalars(select(Habit).where(Habit.user_id == uid, Habit.archived.is_(False))).all()
            h = match_habit(habit, list(habits))
            if h is None:
                return "Unknown habit. Valid: " + "; ".join(x.name for x in habits)
            d = _parse_date(day, today)
            when = local_instant(d, time, tz) if time else None
            replaced: list[dict] = []
            try:
                log = records.log_habit(db, h, d, status.lower(), occurred_at=when, note=note, replaced=replaced)
            except ValueError as e:
                return f"Not logged: {e}"
            db.commit()
            ctx.actions.append({"type": "habit_log", "id": log.id, "label": f"{h.name}: {status}",
                                "habit_id": h.id, "replaced": replaced})
            return f"{h.name}: {status} on {d}."

    @tool
    def add_to_journal(text: str, day: str = "today") -> str:
        """Append text to the journal entry of a day (creates it if needed).
        Only use when the person asks to write something down."""
        with filesync.LOCK, SessionLocal() as db:
            _, _, today = env(db)
            d = _parse_date(day, today)
            entry, previous = records.append_to_journal(db, uid, d, text)
            try:
                sync = filesync.push_day(db, uid, filesync.config(get_settings()), entry)
            except filesync.SyncError as e:
                db.rollback()
                return f"Not added: {e}"
            rag.index_journal_entry(db, entry)
            db.commit()
            ctx.actions.append({"type": "journal", "id": entry.id, "label": f"Journal {d}",
                                "appended": text.strip(), "created": not previous.strip()})
            if sync["state"] == "conflict":
                return f"Added to the journal of {d} in OwnLife; the file changed at the same time (a conflict to resolve)."
            return f"Added to the journal of {d}."

    @tool
    def list_goals() -> str:
        """The goal tree with progress, invested hours and deadlines."""
        with SessionLocal() as db:
            profile = db.get(Profile, uid)
            tree = goal_tree(db, uid, profile.timezone, utcnow())
            lines: list[str] = []

            def walk(nodes, depth=0):
                for n in nodes:
                    due = f", due {n['target_date']} ({n['days_left']}d)" if n["target_date"] else ""
                    lines.append(
                        f"{'  ' * depth}- [{n['level']}] {n['title']} — {n['progress']:.0f}%, "
                        f"{n['invested_hours_tree']:g}h invested, {n['status']}{due}"
                    )
                    walk(n["children"], depth + 1)

            walk(tree)
            return "\n".join(lines) or "No goals yet."

    @tool
    def update_goal(
        goal: str, progress: float | None = None, metric_current: float | None = None, status: str | None = None
    ) -> str:
        """Update a goal found by (part of) its title: manual progress 0-100,
        current metric value, or status (active | done | paused | dropped)."""
        with SessionLocal() as db:
            goals = db.scalars(select(Goal).where(Goal.user_id == uid)).all()
            g = next((x for x in goals if x.title.casefold() == goal.casefold()), None) or next(
                (x for x in goals if goal.casefold() in x.title.casefold()), None
            )
            if g is None:
                return "No goal matches. Goals: " + "; ".join(x.title for x in goals)
            before = {"progress": g.progress, "metric_current": g.metric_current, "status": g.status,
                      "completed_at": g.completed_at.isoformat() if g.completed_at else None}
            if progress is not None:
                g.progress = max(0.0, min(100.0, progress))
            if metric_current is not None:
                g.metric_current = metric_current
            if status in ("active", "done", "paused", "dropped"):
                g.status = status
                g.completed_at = utcnow() if status == "done" else None
            db.commit()
            ctx.actions.append({"type": "goal_updated", "id": g.id, "label": g.title, "before": before})
            return f"Updated goal {g.title}."

    @tool
    def create_goal(
        title: str,
        level: str = "objective",
        parent: str | None = None,
        target_date: str | None = None,
        description: str | None = None,
    ) -> str:
        """Create a goal. level: vision | objective | milestone. parent: title of the parent goal."""
        with SessionLocal() as db:
            parent_goal = None
            if parent:
                parent_goal = db.scalar(select(Goal).where(Goal.user_id == uid, Goal.title.ilike(f"%{parent}%")))
            g = Goal(
                user_id=uid,
                title=title[:200],
                level=level if level in ("vision", "objective", "milestone") else "objective",
                parent_id=parent_goal.id if parent_goal else None,
                target_date=date.fromisoformat(target_date) if target_date else None,
                description=description,
            )
            db.add(g)
            db.commit()
            ctx.actions.append({"type": "goal_created", "id": g.id, "label": title})
            return f"Created goal #{g.id} {title}" + (f" under {parent_goal.title}" if parent_goal else "") + "."

    @tool
    def get_life_numbers() -> str:
        """Age, weeks lived and left, and where the last 30 days' habits lead
        (hours and continuous years until 60 and over the remaining life)."""
        with SessionLocal() as db:
            profile = db.get(Profile, uid)
            o = life_overview(db, uid, profile, utcnow())
            if not o.get("configured"):
                return "Birth date not set in the profile."
            lines = [
                f"Age {o['age_years']:.2f}; week {o['weeks_alive']} of {o['total_weeks']}; "
                f"{o['weeks_left']} weeks left (horizon {o['life_expectancy_years']} years).",
                f"Waking hours left: {o['waking_hours_left']:,}; discretionary ≈ {o['discretionary_hours_left']:,}.",
                f"Tracked days in the last {o['measured']['window_days']}: {o['measured']['tracked_days']}.",
            ]
            for p in o["projections"]:
                lines.append(
                    f"- {p['kind']}: {p['avg_hours_per_day']:.1f}h/day → {p['continuous_years_until_60']} "
                    f"continuous years by 60"
                )
            if o.get("what_if"):
                w = o["what_if"]
                lines.append(
                    f"Bringing noise from {w['noise_avg_hours']}h to {w['noise_budget_hours']}h/day frees "
                    f"{w['reclaimed_continuous_years_until_60']} continuous years by 60."
                )
            return "\n".join(lines)

    @tool
    def get_youtube(start_date: str = "today", end_date: str | None = None, channel: str | None = None) -> str:
        """YouTube watched between two dates (YYYY-MM-DD, 'today' or 'yesterday';
        end_date defaults to start_date): hours per channel and per category. For a
        single day, also each video with its time. `channel` filters by channel name."""
        with SessionLocal() as db:
            profile, tz, today = env(db)
            s = _parse_date(start_date, today)
            e = _parse_date(end_date, today) if end_date else s
            if e < s:
                s, e = e, s
            lo, hi = range_utc(s, e, tz)
            rows = watched(db, uid, lo, hi)
            if channel:
                rows = [r for r in rows if channel.casefold() in (r[0].channel or "").casefold()]
            span = f"{s}" if s == e else f"{s} → {e}"
            if not rows:
                return f"{span}: no YouTube recorded" + (f" for {channel}." if channel else ".")
            rules = load_ruleset(db, uid)
            cats = {c.id: c for c in db.scalars(select(Category).where(Category.user_id == uid))}
            total = measured = 0.0
            by_channel: dict[str, list] = {}
            by_cat: dict[str, float] = {}
            videos = []
            for ev, secs, is_measured in rows:
                cat = cats.get(rules.classify(Activity(title=ev.title, channel=ev.channel, url=ev.url)))
                cat_name = cat.name if cat else "uncategorised"
                # Titles in a destructive category stay on this machine.
                hide = ctx.for_cloud and cat is not None and cat.kind == "destructive"
                total += secs
                measured += secs if is_measured else 0.0
                by_cat[cat_name] = by_cat.get(cat_name, 0.0) + secs
                row = by_channel.setdefault("(private)" if hide else ev.channel or "unknown channel", [0.0, 0, cat_name])
                row[0] += secs
                row[1] += 1
                at = f"{ev.occurred_at.astimezone(tz):%H:%M}"
                if hide:
                    videos.append(f"- {at} a video in {cat_name}, {round(secs / 60)} min")
                else:
                    title = redact(ev.title, profile) if ctx.for_cloud else ev.title
                    videos.append(f"- {at} {title} — {ev.channel or '?'}, {round(secs / 60)} min"
                                  + (" (measured)" if is_measured else ""))
            how = ("measured by the extension" if measured >= total else "estimated from history" if not measured
                   else f"{_h(measured)} measured, {_h(total - measured)} estimated")
            lines = [f"{span}: {_h(total)} of YouTube, {_n(len(rows), 'video')} ({how})."]
            top = sorted(by_channel.items(), key=lambda kv: -kv[1][0])[:12]
            lines.append("By channel: " + "; ".join(f"{n} {_h(v[0])} ({_n(v[1], 'video')}, {v[2]})" for n, v in top))
            lines.append("By category: " + ", ".join(
                f"{k} {_h(v)}" for k, v in sorted(by_cat.items(), key=lambda kv: -kv[1])))
            if s == e:
                lines += ["Videos:", *videos[:40]]
                if len(videos) > 40:
                    lines.append(f"… and {len(videos) - 40} more.")
            return "\n".join(lines)

    @tool
    def get_prayer_times(day: str = "today") -> str:
        """Prayer times of a day (YYYY-MM-DD, 'today' or 'tomorrow') in the person's
        city, and for today the next prayer."""
        with SessionLocal() as db:
            profile, tz, today = env(db)
            d = _parse_date(day, today)
            prefs = (profile.prefs or {}).get("prayer")
            info = prayer.times_for(d, prefs, tz)
            times = " · ".join(f"{prayer.LABELS[k]} {info['times'][k]}" for k in prayer.PRAYERS if info["times"][k])
            line = f"{d:%A %d/%m/%Y}, {info['city']} ({info['method_name']}): {times}."
            if d == today:
                nxt = prayer.next_prayer(datetime.now(tz), prefs, tz)
                if nxt:
                    line += f" Next: {nxt['label']} at {nxt['time']}, in {_h(nxt['minutes_left'] * 60)}."
            return line

    @tool
    def get_spending(start_date: str, end_date: str) -> str:
        """Money spent and received between two dates (YYYY-MM-DD, 'today' or
        'yesterday', inclusive): totals, per category, and the biggest expenses."""
        with SessionLocal() as db:
            profile, _, today = env(db)
            s, e = _parse_date(start_date, today), _parse_date(end_date, today)
            if e < s:
                s, e = e, s
            rows = db.scalars(select(Transaction).where(
                Transaction.user_id == uid, Transaction.occurred_on >= s, Transaction.occurred_on <= e)).all()
            if not rows:
                return f"{s} → {e}: no money recorded."
            spent = [(t, records.from_minor(t.amount_minor, t.currency)) for t in rows if t.direction == "out"]
            received = sum(records.from_minor(t.amount_minor, t.currency) for t in rows if t.direction == "in")
            total = sum(a for _, a in spent)
            days = (e - s).days + 1
            by_cat: dict[str, float] = {}
            for t, a in spent:
                by_cat[t.category] = by_cat.get(t.category, 0.0) + a
            cur = profile.currency
            lines = [f"{s} → {e} ({_n(days, 'day')}): spent {_money(total)} {cur} "
                     f"({_money(round(total / days, 2))}/day), received {_money(received)} {cur}."]
            if by_cat:
                lines.append("By category: " + ", ".join(
                    f"{c} {_money(a)}" for c, a in sorted(by_cat.items(), key=lambda kv: -kv[1])))
                biggest = sorted(spent, key=lambda ta: -ta[1])[:5]
                lines.append("Biggest: " + "; ".join(f"{t.occurred_on:%d/%m} {t.item} {_money(a)}" for t, a in biggest))
            return "\n".join(lines)

    @tool
    def get_habit_progress(habit: str | None = None) -> str:
        """Streaks and the last 30 days of one habit (by name), or of every habit."""
        with SessionLocal() as db:
            profile = db.get(Profile, uid)
            habits = list(db.scalars(
                select(Habit).where(Habit.user_id == uid, Habit.archived.is_(False)).order_by(Habit.id)))
            if habit:
                h = match_habit(habit, habits)
                if h is None:
                    return "Unknown habit. Valid: " + "; ".join(x.name for x in habits)
                habits = [h]
            now = utcnow()
            lines = []
            for h in habits:
                st = habit_stats(db, h, profile.timezone, now, days=30)
                if st["kind"] == "quit":
                    nxt = f"; next milestone at {st['next_milestone']} days" if st.get("next_milestone") else ""
                    lines.append(
                        f"- {h.name} (to quit): {_n(st['current_streak'], 'day')} clean since {st['clean_since']} "
                        f"(best {st['best_streak']}); last 30 days: {_n(st['urges_resisted_last_30'], 'urge')} "
                        f"resisted, {_n(st['relapses_last_30'], 'relapse')}{nxt}."
                    )
                else:
                    rate = f" ({st['rate_last_30']:.0%} of the days judged)" if st.get("rate_last_30") is not None else ""
                    lines.append(
                        f"- {h.name} (to build): streak {_n(st['current_streak'], 'day')} (best {st['best_streak']}); "
                        f"done {st['done_last_30']} of the last 30 days{rate}; today: {st['today'] or 'not yet'}."
                    )
            return "\n".join(lines) or "No habits yet."

    @tool
    def list_life_events(query: str | None = None, year: int | None = None) -> str:
        """The big dates of the person's life (school, moves, work, turning points),
        oldest first. Optional filters: words of the title, or a year."""
        with SessionLocal() as db:
            profile = db.get(Profile, uid)
            rows = db.scalars(
                select(LifeEvent).where(LifeEvent.user_id == uid).order_by(LifeEvent.event_date, LifeEvent.id)
            ).all()
            if year:
                rows = [r for r in rows if r.event_date.year == int(year)]
            if query:
                rows = [r for r in rows if query.casefold() in f"{r.title} {r.description or ''}".casefold()]
            lines, hidden = [], 0
            for r in rows:
                if ctx.for_cloud and r.is_private:
                    hidden += 1
                    continue
                when = {"year": f"{r.event_date:%Y}", "month": f"{r.event_date:%m/%Y}"}.get(
                    r.precision, f"{r.event_date:%d/%m/%Y}")
                text = f"{r.title} — {r.description[:240]}" if r.description else r.title
                lines.append(f"- {when} [{r.area}] {redact(text, profile) if ctx.for_cloud else text}")
            if hidden:
                lines.append(f"({_n(hidden, 'private event')} not shown)")
            return "\n".join(lines) or ("No life event matches." if query or year else "No life events recorded yet.")

    @tool
    def add_life_event(title: str, day: str, precision: str = "day", area: str = "other", importance: int = 2,
                       description: str | None = None) -> str:
        """Record a life event: a date that matters in the long run (a new school or
        job, a move, a turning point). day: YYYY-MM-DD, YYYY-MM or YYYY, or 'today'.
        area: education, family, faith, health, work, move, travel, achievement,
        turning_point or other. importance 1-3."""
        with SessionLocal() as db:
            _, _, today = env(db)
            raw = day.strip()
            if re.fullmatch(r"\d{4}", raw):
                raw, precision = f"{raw}-01-01", "year"
            elif re.fullmatch(r"\d{4}-\d{2}", raw):
                raw, precision = f"{raw}-01", "month"
            try:
                d = _parse_date(raw, today)
            except ValueError:
                return "Not added: the day must be YYYY-MM-DD, YYYY-MM or YYYY."
            ev = LifeEvent(
                user_id=uid, event_date=d, title=title.strip()[:200], description=description,
                precision=precision if precision in ("day", "month", "year") else "day",
                area=area if area in LIFE_AREAS else "other", importance=max(1, min(3, int(importance))),
            )
            db.add(ev)
            db.commit()
            ctx.actions.append({"type": "life_event", "id": ev.id, "label": f"Life event: {ev.title}"})
            return f"Added the life event “{ev.title}” ({d}, {ev.area})."

    @tool
    def plan_block(title: str, start: str, end: str, day: str = "today", category: str | None = None) -> str:
        """Add a block to a day's plan (the Planner): start/end are 24-hour HH:MM,
        an end before the start runs past midnight. day: YYYY-MM-DD, 'today' or
        'tomorrow'. category: a category NAME, optional."""
        with SessionLocal() as db:
            _, _, today = env(db)
            d = _parse_date(day, today)
            try:
                s, e = hhmm_to_minutes(start), hhmm_to_minutes(end)
            except ValueError:
                return "Not planned: start and end must be HH:MM."
            if e <= s:
                e += 24 * 60
            cat, how = resolve_category(db, uid, category, title) if category else (None, "")
            if category and cat is None:
                how = f" (no category named {category!r}: planned without one)"
            b = PlanBlock(user_id=uid, plan_date=d, start_minute=s, end_minute=e, title=title.strip()[:200],
                          category_id=cat.id if cat else None)
            db.add(b)
            db.commit()
            others = db.scalars(select(PlanBlock).where(
                PlanBlock.user_id == uid, PlanBlock.plan_date == d, PlanBlock.id != b.id)).all()
            clash = [o for o in others if o.start_minute < e and s < o.end_minute]
            span = f"{minutes_to_hhmm(s)}–{minutes_to_hhmm(e)}"
            ctx.actions.append({"type": "plan_block", "id": b.id, "label": f"Plan {d:%d/%m} {span} {b.title}"})
            out = f"Planned {b.title} {span} on {d}" + (f" [{cat.name}]" if cat else "") + how + "."
            if clash:
                out += " It overlaps: " + "; ".join(
                    f"{minutes_to_hhmm(o.start_minute)}–{minutes_to_hhmm(o.end_minute)} {o.title}" for o in clash) + "."
            return out

    return [
        search_memory,
        get_time_summary,
        get_day,
        log_time,
        start_timer,
        stop_timer,
        resume_timer,
        log_expense,
        log_habit,
        add_to_journal,
        list_goals,
        update_goal,
        create_goal,
        get_life_numbers,
        get_youtube,
        get_prayer_times,
        get_spending,
        get_habit_progress,
        list_life_events,
        add_life_event,
        plan_block,
    ]


def today_summary(db, user_id: int, for_cloud: bool) -> str:
    """Compact text of today's numbers, embedded in the system prompt."""
    profile = db.get(Profile, user_id)
    tz = tz_of(profile.timezone)
    today = local_today(tz)
    now = utcnow()
    kinds = daily_breakdown(db, user_id, today, today, tz, now).get(today, {})
    parts = [f"{k} {_h(v)}" for k, v in sorted(kinds.items(), key=lambda kv: -kv[1])]
    timer = records.running_timer(db, user_id)
    line = "Logged: " + (", ".join(parts) if parts else "nothing yet")
    if timer is not None:
        line += f". Running timer: {timer.title} since {timer.started_at.astimezone(tz):%H:%M}"
    return line


def known_people(db, user_id: int) -> list[Person]:
    return list(db.scalars(select(Person).where(Person.user_id == user_id).order_by(Person.name)))


def is_today(d: date, tz) -> bool:
    return d == datetime.now(tz).date()
