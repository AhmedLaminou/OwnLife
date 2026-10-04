"""Life overview, habits, goals and plans."""

from datetime import date, timedelta

from tests.conftest import iso_local, niamey_today


def test_life_overview_counts_weeks_and_projects_habits(client, categories):
    client.put("/api/profile", json={"awakening_date": "2026-08-31", "noise_budget_hours": 1})
    yesterday = niamey_today() - timedelta(days=1)
    client.post("/api/time/entries", json={"title": "Linear algebra", "category_id": categories["Mathematics"]["id"],
                                           "started_at": iso_local(yesterday, "06:00"), "ended_at": iso_local(yesterday, "12:00")})
    client.post("/api/time/entries", json={"title": "Reactions", "category_id": categories["Reaction videos"]["id"],
                                           "started_at": iso_local(yesterday, "20:00"), "ended_at": iso_local(yesterday, "23:00")})
    o = client.get("/api/life/overview").json()
    assert o["configured"] is True
    assert o["total_weeks"] == (date(2091, 6, 15) - date(2001, 6, 15)).days // 7
    assert o["weeks_alive"] + o["weeks_left"] == o["total_weeks"]
    assert o["measured"]["tracked_days"] == 1
    core = next(p for p in o["projections"] if p["kind"] == "core")
    assert core["avg_hours_per_day"] == 6.0
    assert o["what_if"]["reclaimed_hours_per_day"] == 2.0
    assert o["awakening"]["day_number"] == (niamey_today() - date(2026, 8, 31)).days + 1

    weeks = client.get("/api/life/weeks").json()["weeks"]
    week_index = str((yesterday - date(2001, 6, 15)).days // 7)
    assert weeks[week_index]["hours"]["core"] == 6.0


def test_rule_based_habit_is_evaluated_from_the_ledger(client, categories):
    habits = {h["name"]: h for h in client.get("/api/habits").json()}
    minimum = habits["Minimum day: 4h of core work"]
    start = niamey_today() - timedelta(days=3)
    client.patch(f"/api/habits/{minimum['id']}", json={"start_date": start.isoformat()})
    for offset in (3, 2, 1):
        d = niamey_today() - timedelta(days=offset)
        client.post("/api/time/entries", json={"title": "Physics", "category_id": categories["Physics"]["id"],
                                               "started_at": iso_local(d, "08:00"), "ended_at": iso_local(d, "13:00")})
    h = next(x for x in client.get("/api/habits").json() if x["id"] == minimum["id"])
    assert h["stats"]["current_streak"] == 3
    assert h["stats"]["today"] == "pending"


def test_quit_habit_counts_clean_days_and_urges(client):
    h = client.post("/api/habits", json={"name": "{X}-free", "kind": "quit", "is_private": True,
                                         "start_date": (niamey_today() - timedelta(days=20)).isoformat()}).json()
    relapse_day = niamey_today() - timedelta(days=4)
    r = client.post(f"/api/habits/{h['id']}/log", json={"date": relapse_day.isoformat(), "status": "relapse", "time": "02:30"})
    assert r.status_code == 200
    stats = client.post(f"/api/habits/{h['id']}/log", json={"status": "urge"}).json()["stats"]
    assert stats["current_streak"] == 4
    assert stats["best_streak"] == 16
    assert stats["urges_resisted_total"] == 1
    assert stats["relapse_hours"][2] == 1  # 02:30 local
    assert stats["next_milestone"] == 7
    # a build-habit status is refused on a quit habit
    assert client.post(f"/api/habits/{h['id']}/log", json={"status": "done"}).status_code == 422


def test_goal_tree_progress_and_invested_hours(client, categories):
    vision = client.post("/api/goals", json={"title": "[BigVision]", "level": "vision",
                                             "progress_mode": "children"}).json()
    math = client.post("/api/goals", json={
        "title": "Mathematics for ML", "parent_id": vision["id"], "progress_mode": "hours", "hours_target": 10,
        "category_ids": [categories["Mathematics"]["id"]], "start_date": "2026-01-01"}).json()
    client.post("/api/goals", json={"title": "Read 12 books", "parent_id": vision["id"], "progress_mode": "metric",
                                    "metric_start": 0, "metric_target": 12, "metric_current": 3})
    d = date(2026, 9, 10)
    client.post("/api/time/entries", json={"title": "Probability", "category_id": categories["Mathematics"]["id"],
                                           "started_at": iso_local(d, "08:00"), "ended_at": iso_local(d, "10:00")})
    tree = client.get("/api/goals").json()
    root = tree[0]
    assert root["title"] == "[BigVision]"
    kids = {k["title"]: k for k in root["children"]}
    assert kids["Mathematics for ML"]["invested_hours"] == 2.0
    assert kids["Mathematics for ML"]["progress"] == 20.0
    assert kids["Read 12 books"]["progress"] == 25.0
    assert root["progress"] == 22.5  # average of the children
    assert root["invested_hours_tree"] == 2.0
    # a goal cannot become its own ancestor
    assert client.patch(f"/api/goals/{vision['id']}", json={"parent_id": math["id"]}).status_code == 422


def test_plan_template_and_adherence(client, categories):
    templates = client.get("/api/plan-templates").json()
    d = "2026-09-10"
    assert client.post(f"/api/plan/{d}/apply-template", json={"template_id": templates[0]["id"]}).json()["created"] > 3
    plan = client.get(f"/api/plan/{d}").json()
    first = plan["blocks"][0]
    assert (first["start"], first["category"]) == ("06:00", "Mathematics")
    sleep = next(b for b in plan["blocks"] if b["title"] == "Sleep")
    assert sleep["end_minute"] == 30 * 60  # 23:00 → 06:00 next day
    # do half of the first block (06:00-08:30 planned)
    client.post("/api/time/entries", json={"title": "Linear algebra", "category_id": categories["Mathematics"]["id"],
                                           "started_at": iso_local(date(2026, 9, 10), "06:00"),
                                           "ended_at": iso_local(date(2026, 9, 10), "07:15")})
    plan = client.get(f"/api/plan/{d}").json()
    assert round(plan["blocks"][0]["matched_seconds"]) == 75 * 60
    assert 0 < plan["adherence"] < 1


def test_money_summary(client):
    for body in ({"date": "2026-09-10", "item": "Taxi", "amount": 300, "category": "transport"},
                 {"date": "2026-09-10", "item": "Biscuits", "amount": 50, "category": "food"},
                 {"date": "2026-09-10", "item": "Gift", "amount": 5000, "direction": "in"}):
        assert client.post("/api/money", json=body).status_code == 200
    s = client.get("/api/money/summary", params={"start": "2026-09-10", "end": "2026-09-10"}).json()
    assert (s["total_out"], s["total_in"], s["net"]) == (350, 5000, 4650)
    assert s["out_by_category"][0] == {"category": "transport", "amount": 300}


def test_export_contains_everything(client):
    client.post("/api/journal", json={"entry_date": "2026-10-02", "body": "hello"})
    r = client.get("/api/system/export")
    assert r.status_code == 200
    data = r.json()
    assert data["journal"][0]["body"] == "hello"
    assert {"categories", "time_entries", "habits", "goals", "people", "chat_threads"} <= set(data)


def test_backup(client, env):
    r = client.post("/api/system/backup").json()
    assert r["bytes"] > 0
    assert client.get("/api/system/backups").json()[0]["name"].startswith("ownlife-")
