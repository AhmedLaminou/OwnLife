"""The built-in window tracker, driven by a fake window and clock: samples become
ledger blocks by category; idle time, blips and private windows are not kept;
your own entries win over it; window titles never reach a cloud model."""

import sys
from datetime import date, datetime, timedelta, timezone

import pytest

from app.config import get_settings
from app.db import SessionLocal
from app.models import TimeEntry
from app.services import wintrack
from app.services.wintrack import Sample, Tracker
from tests.conftest import iso_local

T0 = datetime(2026, 9, 20, 8, 0, tzinfo=timezone.utc)  # 09:00 in Niamey


class FakeWindows:
    def __init__(self) -> None:
        self.window: Sample | None = None
        self.idle = 0.0

    def tracker(self) -> Tracker:
        return Tracker(get_settings(), probe=lambda: self.window, idle=lambda: self.idle)


def _run(tracker: Tracker, uid: int, fake: FakeWindows, t: datetime, minutes: float, window: Sample | None,
         idle: float = 0.0) -> datetime:
    """Samples every 5 seconds for `minutes`, with this window in front."""
    fake.window, fake.idle = window, idle
    with SessionLocal() as db:
        for _ in range(round(minutes * 12)):
            t += timedelta(seconds=5)
            tracker.step(db, uid, t)
    return t


def _flush(tracker: Tracker, uid: int) -> None:
    with SessionLocal() as db:
        tracker.flush(db, uid)


def _entries(uid: int) -> list[tuple]:
    with SessionLocal() as db:
        rows = db.query(TimeEntry).filter(TimeEntry.user_id == uid, TimeEntry.source == "window").order_by(
            TimeEntry.started_at).all()
        return [(e.title, e.category.name if e.category else None,
                 round((e.ended_at - e.started_at).total_seconds() / 60, 1)) for e in rows]


def _uid(client) -> int:
    return client.get("/api/auth/me").json()["user"]["id"]


def _rule(client, field: str, pattern: str, category_id: int) -> None:
    assert client.post("/api/rules", json={"field": field, "pattern": pattern, "category_id": category_id}).status_code == 200


def test_samples_become_blocks_by_category(client, categories):
    _rule(client, "app", "Code.exe", categories["Building projects"]["id"])
    _rule(client, "title", "reaction", categories["Reaction videos"]["id"])
    uid, fake = _uid(client), FakeWindows()
    tr = fake.tracker()
    t = _run(tr, uid, fake, T0, 10, Sample("Code.exe", "app.py — OwnLife — Visual Studio Code"))
    t = _run(tr, uid, fake, t, 5, Sample("chrome.exe", "Anime X reaction - YouTube - Google Chrome"))
    _run(tr, uid, fake, t, 3, Sample("Code.exe", "tests.py — OwnLife — Visual Studio Code"))
    _flush(tr, uid)
    assert _entries(uid) == [
        ("app.py — OwnLife — Visual Studio Code", "Building projects", 10.0),
        ("Anime X reaction - YouTube - Google Chrome", "Reaction videos", 5.0),
        ("tests.py — OwnLife — Visual Studio Code", "Building projects", 3.0),
    ]


def test_idle_time_and_short_blips_are_not_counted(client, categories):
    _rule(client, "title", "Calculator", categories["Mathematics"]["id"])
    uid, fake = _uid(client), FakeWindows()
    tr = fake.tracker()
    t = _run(tr, uid, fake, T0, 4, Sample("notepad.exe", "Notes"))
    t = _run(tr, uid, fake, t, 10, Sample("notepad.exe", "Notes"), idle=300)  # away from the keyboard
    t = _run(tr, uid, fake, t, 0.5, Sample("calc.exe", "Calculator"))  # 30 seconds: a blip
    _run(tr, uid, fake, t, 3, Sample("notepad.exe", "Notes"))
    _flush(tr, uid)
    assert _entries(uid) == [("Notes", None, 4.0), ("Notes", None, 3.0)]


def test_private_windows_keep_no_title_and_the_lock_screen_counts_nothing(client):
    uid, fake = _uid(client), FakeWindows()
    tr = fake.tracker()
    t = _run(tr, uid, fake, T0, 2, Sample("msedge.exe", "Something secret - InPrivate - Microsoft Edge"))
    _run(tr, uid, fake, t, 5, Sample("LockApp.exe", "Windows Default Lock Screen"))
    _flush(tr, uid)
    assert _entries(uid) == [("msedge · private window", None, 2.0)]
    with SessionLocal() as db:
        meta = db.query(TimeEntry).filter(TimeEntry.source == "window").one().meta
    assert meta["titles"] == ["(private window)"] and "secret" not in str(meta)


def test_a_block_deleted_from_the_ledger_while_open_stays_deleted(client):
    uid, fake = _uid(client), FakeWindows()
    tr = fake.tracker()
    t = _run(tr, uid, fake, T0, 2, Sample("notepad.exe", "Notes"))
    [entry] = [e for e in client.get("/api/time/entries", params={"start": "2026-09-20", "end": "2026-09-20"}).json()
               if e["source"] == "window"]
    client.delete(f"/api/time/entries/{entry['id']}")
    _run(tr, uid, fake, t, 3, Sample("notepad.exe", "Notes"))
    _flush(tr, uid)
    assert _entries(uid) == []


def test_your_timer_wins_over_the_window_tracker(client, categories):
    _rule(client, "title", "reaction", categories["Reaction videos"]["id"])
    uid, fake = _uid(client), FakeWindows()
    with SessionLocal() as db:  # a timer said Linear algebra, 09:00-10:00
        db.add(TimeEntry(user_id=uid, title="Linear algebra", category_id=categories["Mathematics"]["id"],
                         started_at=T0, ended_at=T0 + timedelta(hours=1), source="timer"))
        db.commit()
    tr = fake.tracker()
    _run(tr, uid, fake, T0, 30, Sample("chrome.exe", "Some reaction video - Google Chrome"))
    _flush(tr, uid)
    day = client.get("/api/time/day/2026-09-20").json()
    minutes = {k: round(v / 60) for k, v in day["totals_by_kind"].items()}
    assert minutes["core"] == 60 and minutes.get("noise", 0) == 0
    assert [o["counted"] for o in day["overlaps"]] == ["yours"]


def test_window_titles_never_reach_a_cloud_model(client):
    from app.ai.tools import ToolContext, build_tools

    uid = _uid(client)
    with SessionLocal() as db:
        db.add(TimeEntry(user_id=uid, title="letter-to-the-bank.docx — Word",
                         started_at=datetime.fromisoformat(iso_local(date(2026, 9, 20), "10:00")),
                         ended_at=datetime.fromisoformat(iso_local(date(2026, 9, 20), "10:30")),
                         source="window", meta={"apps": {"WINWORD.EXE": 1800}, "titles": ["letter-to-the-bank.docx — Word"]}))
        db.commit()
    tools = {t.name: t for t in build_tools(ToolContext(uid, None, for_cloud=True))}
    out = tools["get_day"].invoke({"day": "2026-09-20"})
    assert "WINWORD" in out and "bank" not in out
    local = {t.name: t for t in build_tools(ToolContext(uid, None, for_cloud=False))}
    assert "letter-to-the-bank" in local["get_day"].invoke({"day": "2026-09-20"})


def test_status_and_the_switch(client):
    r = client.get("/api/integrations/window-tracker").json()
    assert r["enabled"] is True and r["running"] is False  # the tests never start the thread
    r = client.put("/api/integrations/window-tracker", json={"enabled": False}).json()
    assert r["enabled"] is False


@pytest.mark.skipif(sys.platform != "win32", reason="the Windows API exists only on Windows")
def test_the_windows_api_answers():
    s = wintrack.foreground()
    assert s is None or isinstance(s.app, str) and isinstance(s.title, str)
    assert wintrack.idle_seconds() >= 0
