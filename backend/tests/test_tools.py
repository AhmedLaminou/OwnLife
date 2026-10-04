"""The assistant's newer tools, run directly (no model): what they answer, what
they keep from a cloud model, and that what they create can be undone."""

import json
from datetime import date, timedelta

from tests.conftest import niamey_today
from tests.test_devices_undo import _message_with
from tests.test_imports import TAKEOUT, _rule


def _tools(client, for_cloud: bool = False):
    from app.ai.tools import ToolContext, build_tools

    uid = client.get("/api/auth/me").json()["user"]["id"]
    ctx = ToolContext(uid, None, for_cloud)
    return uid, ctx, {t.name: t for t in build_tools(ctx)}


def test_prayer_times_for_today_and_tomorrow(client):
    _, _, tools = _tools(client)
    today = tools["get_prayer_times"].invoke({"day": "today"})
    for name in ("Fajr", "Sunrise", "Dhuhr", "Asr", "Maghrib", "Isha", "Niamey", "Next:"):
        assert name in today
    tomorrow = tools["get_prayer_times"].invoke({"day": "tomorrow"})
    assert (niamey_today() + timedelta(days=1)).strftime("%d/%m/%Y") in tomorrow
    assert "Next:" not in tomorrow


def test_spending_totals_categories_and_biggest_first(client):
    d = date(2026, 9, 14).isoformat()
    for item, amount, category, direction in [
        ("Taxi", 500, "transport", "out"), ("Lunch", 1500, "food", "out"),
        ("Book", 6000, "education", "out"), ("Gift from uncle", 10000, "gift", "in"),
    ]:
        r = client.post("/api/money", json={"date": d, "direction": direction, "amount": amount, "item": item,
                                            "category": category})
        assert r.status_code == 200, r.text
    _, _, tools = _tools(client)
    out = tools["get_spending"].invoke({"start_date": d, "end_date": d})
    assert "spent 8,000" in out and "received 10,000" in out
    assert out.split("By category: ")[1].startswith("education 6,000")
    assert out.index("Book") < out.index("Lunch") < out.index("Taxi")  # the biggest first
    assert "no money" in tools["get_spending"].invoke({"start_date": "2026-01-01", "end_date": "2026-01-02"})


def test_habit_progress_for_a_habit_to_build_and_one_to_quit(client):
    today = niamey_today()
    build = client.post("/api/habits", json={"name": "Read 20 pages", "start_date": (today - timedelta(days=10)).isoformat()}).json()
    quit_ = client.post("/api/habits", json={"name": "{X}-free", "kind": "quit",
                                             "start_date": (today - timedelta(days=20)).isoformat()}).json()
    for i in range(3):
        client.post(f"/api/habits/{build['id']}/log", json={"date": (today - timedelta(days=i)).isoformat(), "status": "done"})
    client.post(f"/api/habits/{quit_['id']}/log", json={"date": (today - timedelta(days=5)).isoformat(), "status": "relapse"})
    client.post(f"/api/habits/{quit_['id']}/log", json={"date": (today - timedelta(days=2)).isoformat(), "status": "urge"})
    stats = {h["name"]: h["stats"] for h in client.get("/api/habits").json()}

    _, _, tools = _tools(client)
    one = tools["get_habit_progress"].invoke({"habit": "Read 20 pages"})
    assert "\n" not in one and f"streak {stats['Read 20 pages']['current_streak']} days" in one
    q = tools["get_habit_progress"].invoke({"habit": "{X}-free"})
    assert "5 days clean" in q and "1 urge resisted" in q and "1 relapse" in q
    every = tools["get_habit_progress"].invoke({})
    assert every.count("\n") == len(stats) - 1
    assert "Unknown habit" in tools["get_habit_progress"].invoke({"habit": "Juggling"})


def test_private_life_events_never_reach_a_cloud_model(client):
    client.post("/api/life/events", json={"date": "2023-10-02", "title": "Started university", "area": "education"})
    client.post("/api/life/events", json={"date": "2024-05-01", "precision": "month", "title": "A hard month",
                                          "is_private": True})
    _, _, local = _tools(client)
    _, _, cloud = _tools(client, for_cloud=True)
    assert "A hard month" in local["list_life_events"].invoke({})
    out = cloud["list_life_events"].invoke({})
    assert "Started university" in out and "A hard month" not in out and "1 private event not shown" in out
    assert "05/2024" in local["list_life_events"].invoke({"year": 2024})
    assert "No life event matches" in local["list_life_events"].invoke({"query": "wedding"})


def test_a_life_event_added_by_the_assistant_can_be_undone(client):
    uid, ctx, tools = _tools(client)
    out = tools["add_life_event"].invoke({"title": "First internship", "day": "2026-07", "area": "work",
                                          "importance": 3})
    assert "First internship" in out
    [event] = client.get("/api/life/events").json()
    assert event["date"] == "2026-07-01" and event["precision"] == "month" and event["area"] == "work"
    mid = _message_with(uid, ctx.actions)
    assert client.post(f"/api/ai/messages/{mid}/actions/0/undo").json()["ok"]
    assert client.get("/api/life/events").json() == []


def test_plan_block_finds_the_category_reports_overlaps_and_undoes(client, categories):
    d = niamey_today() + timedelta(days=1)
    client.post("/api/plan/blocks", json={"date": d.isoformat(), "start": "08:00", "end": "10:00", "title": "Physics"})
    uid, ctx, tools = _tools(client)
    out = tools["plan_block"].invoke({"title": "Linear algebra", "start": "09:30", "end": "11:00", "day": "tomorrow",
                                      "category": "Mathematics"})
    assert "[Mathematics]" in out and "overlaps: 08:00–10:00 Physics" in out
    late = tools["plan_block"].invoke({"title": "Reading", "start": "23:30", "end": "00:30", "day": "tomorrow"})
    assert "23:30–00:30" in late
    titles = sorted(b["title"] for b in client.get(f"/api/plan/{d.isoformat()}").json()["blocks"])
    assert titles == ["Linear algebra", "Physics", "Reading"]

    mid = _message_with(uid, ctx.actions)
    assert client.post(f"/api/ai/messages/{mid}/actions/0/undo").json()["ok"]
    titles = sorted(b["title"] for b in client.get(f"/api/plan/{d.isoformat()}").json()["blocks"])
    assert titles == ["Physics", "Reading"]


def test_the_assistant_pauses_and_resumes_a_timer_and_can_undo_it(client, categories):
    uid, ctx, tools = _tools(client)
    tools["start_timer"].invoke({"title": "Linear algebra", "category": "Mathematics"})
    assert "Paused Linear algebra" in tools["stop_timer"].invoke({"pause": True})
    assert client.get("/api/time/timer/state").json()["paused"][0]["title"] == "Linear algebra"
    assert "Resumed Linear algebra" in tools["resume_timer"].invoke({})
    state = client.get("/api/time/timer/state").json()
    assert state["running"]["title"] == "Linear algebra" and state["paused"] == []

    mid = _message_with(uid, ctx.actions)  # actions: started, paused, resumed
    assert client.post(f"/api/ai/messages/{mid}/actions/2/undo").json()["ok"]
    state = client.get("/api/time/timer/state").json()
    assert state["running"] is None and state["paused"][0]["title"] == "Linear algebra"
    assert "Resumed Linear algebra" in tools["resume_timer"].invoke({})  # paused again, so resumable again
    assert "No paused timer" in tools["resume_timer"].invoke({})


def test_youtube_tool_lists_a_day_and_keeps_destructive_titles_local(client, categories):
    _rule(client, "channel", "YaleCourses", "Physics", categories)
    _rule(client, "channel", "ReactionHub", "Destructive habits", categories)
    files = {"file": ("watch-history.json", json.dumps(TAKEOUT).encode(), "application/json")}
    assert client.post("/api/media/youtube/takeout", files=files).status_code == 200

    _, _, local = _tools(client)
    day = local["get_youtube"].invoke({"start_date": "2026-09-10"})
    assert "3 videos (estimated from history)" in day and "Videos:" in day
    assert "- 02:00 Anime X Ep 5 Reaction Mashup — ReactionHub, 15 min" in day  # 01:00 UTC is 02:00 in Niamey
    assert "YaleCourses 0h08 (1 video, Physics)" in day
    week = local["get_youtube"].invoke({"start_date": "2026-09-08", "end_date": "2026-09-14"})
    assert "Videos:" not in week and "3 videos" in week
    assert "no YouTube recorded for Nobody" in local["get_youtube"].invoke({"start_date": "2026-09-10",
                                                                             "channel": "Nobody"})

    _, _, cloud = _tools(client, for_cloud=True)
    out = cloud["get_youtube"].invoke({"start_date": "2026-09-10"})
    assert "Anime X" not in out and "ReactionHub" not in out
    assert "a video in Destructive habits" in out and "Lecture 1: Course Introduction" in out
