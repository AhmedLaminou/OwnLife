"""ActivityWatch sync: real minutes per app, website and YouTube video.

ActivityWatch (activitywatch.net, open source) runs locally and records the
active window, whether you are at the keyboard (AFK), and — with its browser
extension — the active tab's URL and title. It serves all of that on
http://127.0.0.1:5600. OwnLife asks it, through its query API, for the
non-AFK window events and the browser events that overlap them, classifies them
with the same rules as YouTube history, merges neighbours into blocks, and
writes those blocks to the ledger.

Each sync covers the time since the previous one, so nothing is imported twice.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import utcnow
from app.models import IntegrationState, TimeEntry
from app.services.rules import Activity, RuleSet, domain_of, youtube_video_id
from app.services.youtube import channel_for_video

BROWSER_APPS = (
    "chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe",
    "Google Chrome", "Microsoft Edge", "Firefox", "Brave Browser",
)
MERGE_GAP_SECONDS = 120
MIN_BLOCK_SECONDS = 60
FIRST_SYNC_DAYS = 3
SETTLE_SECONDS = 120  # leave the last two minutes: ActivityWatch may still extend them


class ActivityWatchError(RuntimeError):
    pass


class AWClient:
    def __init__(self, base_url: str, transport: httpx.BaseTransport | None = None) -> None:
        self.http = httpx.Client(
            base_url=base_url.rstrip("/") + "/api/0", timeout=30, transport=transport
        )

    def close(self) -> None:
        self.http.close()

    def _get(self, path: str):
        try:
            r = self.http.get(path)
            r.raise_for_status()
            return r.json()
        except httpx.HTTPError as e:
            raise ActivityWatchError(f"ActivityWatch is not reachable ({e.__class__.__name__})") from e

    def info(self) -> dict:
        return self._get("/info")

    def buckets(self) -> dict:
        return self._get("/buckets/")

    def query(self, start: datetime, end: datetime, lines: list[str]) -> list[dict]:
        body = {"timeperiods": [f"{start.isoformat()}/{end.isoformat()}"], "query": lines}
        try:
            r = self.http.post("/query/", json=body)
            r.raise_for_status()
            result = r.json()
        except httpx.HTTPError as e:
            raise ActivityWatchError(f"ActivityWatch query failed ({e.__class__.__name__})") from e
        return result[0] if result else []


def _q(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def build_queries(buckets: dict) -> tuple[list[str] | None, list[str] | None]:
    ids = list(buckets)
    win = next((b for b in ids if b.startswith("aw-watcher-window")), None)
    afk = next((b for b in ids if b.startswith("aw-watcher-afk")), None)
    web = [b for b in ids if b.startswith("aw-watcher-web")]
    if win is None:
        return None, None
    base = [f"win = flood(query_bucket({_q(win)}));"]
    if afk:
        base += [
            f"afk = flood(query_bucket({_q(afk)}));",
            'active = filter_keyvals(afk, "status", ["not-afk"]);',
            "win = filter_period_intersect(win, active);",
        ]
    window_query = [*base, "RETURN = win;"]
    if not web:
        return window_query, None
    apps = ", ".join(_q(a) for a in BROWSER_APPS)
    web_lines = [*base, f"browser = filter_keyvals(win, \"app\", [{apps}]);", "web = [];"]
    for b in web:
        web_lines.append(f"web = concat(web, flood(query_bucket({_q(b)})));")
    web_lines += ["web = filter_period_intersect(web, browser);", "RETURN = sort_by_timestamp(web);"]
    return window_query, web_lines


@dataclass
class Segment:
    start: datetime
    end: datetime
    activity: Activity
    label: str
    category_id: int | None = None
    titles: dict[str, float] = field(default_factory=dict)


def _parse_events(events: list[dict]) -> list[tuple[datetime, datetime, dict]]:
    out = []
    for e in events:
        try:
            start = datetime.fromisoformat(str(e["timestamp"]).replace("Z", "+00:00")).astimezone(timezone.utc)
        except (KeyError, ValueError):
            continue
        dur = float(e.get("duration") or 0)
        if dur <= 0:
            continue
        out.append((start, start + timedelta(seconds=dur), e.get("data") or {}))
    return out


def build_segments(
    db: Session, user_id: int, window_events: list[dict], web_events: list[dict] | None, oembed: bool
) -> list[Segment]:
    segments: list[Segment] = []
    for start, end, data in _parse_events(web_events or []):
        url, title = data.get("url"), data.get("title") or ""
        vid = youtube_video_id(url)
        if vid:
            channel = channel_for_video(db, user_id, vid, enabled=oembed)
            act = Activity(title=title, channel=channel, url=url)
            label = f"YouTube · {channel}" if channel else "YouTube"
        else:
            act = Activity(title=title, url=url)
            label = domain_of(url) or title or "Browser"
        segments.append(Segment(start, end, act, label, titles={title: (end - start).total_seconds()}))
    have_web = bool(web_events)
    for start, end, data in _parse_events(window_events):
        app = data.get("app") or ""
        if have_web and app in BROWSER_APPS:
            continue  # covered by the finer-grained browser events
        title = data.get("title") or ""
        label = app.removesuffix(".exe") or "Unknown app"
        segments.append(
            Segment(start, end, Activity(title=title, app=app), label, titles={title: (end - start).total_seconds()})
        )
    segments.sort(key=lambda s: s.start)
    return segments


def merge_segments(segments: list[Segment]) -> list[Segment]:
    merged: list[Segment] = []
    for s in segments:
        last = merged[-1] if merged else None
        if (
            last is not None
            and last.category_id == s.category_id
            and last.label == s.label
            and (s.start - last.end).total_seconds() <= MERGE_GAP_SECONDS
        ):
            last.end = max(last.end, s.end)
            for t, secs in s.titles.items():
                last.titles[t] = last.titles.get(t, 0.0) + secs
        else:
            merged.append(s)
    return [m for m in merged if (m.end - m.start).total_seconds() >= MIN_BLOCK_SECONDS]


def get_state(db: Session, user_id: int, provider: str = "activitywatch") -> IntegrationState:
    state = db.scalar(
        select(IntegrationState).where(
            IntegrationState.user_id == user_id, IntegrationState.provider == provider
        )
    )
    if state is None:
        state = IntegrationState(user_id=user_id, provider=provider, cursor={}, settings={})
        db.add(state)
        db.flush()
    return state


def sync(
    db: Session,
    user_id: int,
    client: AWClient,
    ruleset: RuleSet,
    oembed: bool = True,
    now: datetime | None = None,
) -> dict:
    now = now or utcnow()
    state = get_state(db, user_id)
    cursor = (state.cursor or {}).get("until")
    start = datetime.fromisoformat(cursor) if cursor else now - timedelta(days=FIRST_SYNC_DAYS)
    end = now - timedelta(seconds=SETTLE_SECONDS)
    if end <= start:
        return {"created": 0, "from": start.isoformat(), "to": end.isoformat(), "skipped": "nothing new"}

    try:
        window_q, web_q = build_queries(client.buckets())
        if window_q is None:
            raise ActivityWatchError("No window watcher bucket: is aw-watcher-window running?")
        window_events = client.query(start, end, window_q)
        web_events = client.query(start, end, web_q) if web_q else None
    except ActivityWatchError as e:
        state.last_error = str(e)
        db.flush()
        raise

    segments = build_segments(db, user_id, window_events, web_events, oembed)
    for s in segments:
        s.category_id = ruleset.classify(s.activity)
    blocks = merge_segments(segments)
    for b in blocks:
        top_titles = [t for t, _ in sorted(b.titles.items(), key=lambda kv: -kv[1]) if t][:3]
        db.add(
            TimeEntry(
                user_id=user_id,
                title=b.label[:300],
                category_id=b.category_id,
                started_at=b.start,
                ended_at=b.end,
                source="activitywatch",
                source_ref=f"aw:{b.start.isoformat()}",
                meta={
                    "app": b.activity.app,
                    "domain": domain_of(b.activity.url),
                    "channel": b.activity.channel,
                    "titles": top_titles,
                },
            )
        )
    state.cursor = {"until": end.isoformat()}
    state.last_synced_at = now
    state.last_error = None
    db.flush()
    return {"created": len(blocks), "from": start.isoformat(), "to": end.isoformat()}
