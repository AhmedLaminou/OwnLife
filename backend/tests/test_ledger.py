from datetime import date

from tests.conftest import iso_local

DAY = date(2026, 9, 10)


def test_entry_crossing_midnight_is_split_between_days(client, categories):
    noise = categories["Reaction videos"]["id"]
    r = client.post(
        "/api/time/entries",
        json={"title": "Anime X reactions", "category_id": noise,
              "started_at": iso_local(DAY, "23:00"), "ended_at": iso_local(date(2026, 9, 11), "02:30")},
    )
    assert r.status_code == 200, r.text
    d1 = client.get("/api/time/day/2026-09-10").json()
    d2 = client.get("/api/time/day/2026-09-11").json()
    assert round(d1["totals_by_kind"]["noise"]) == 3600
    assert round(d2["totals_by_kind"]["noise"]) == 2.5 * 3600


def test_local_datetimes_without_offset_use_the_profile_zone(client, categories):
    math = categories["Mathematics"]["id"]
    r = client.post(
        "/api/time/entries",
        json={"title": "Linear algebra", "category_id": math,
              "started_at": "2026-09-10T09:00:00", "ended_at": "2026-09-10T11:00:00"},
    )
    assert r.json()["started_at"].startswith("2026-09-10T08:00:00")  # 09:00 Niamey = 08:00 UTC


def test_end_before_start_is_rejected(client, categories):
    r = client.post(
        "/api/time/entries",
        json={"title": "x", "category_id": categories["Physics"]["id"],
              "started_at": iso_local(DAY, "10:00"), "ended_at": iso_local(DAY, "09:00")},
    )
    assert r.status_code == 422


def test_stats_and_overlap_coverage(client, categories):
    math, quran = categories["Mathematics"]["id"], categories["Quran & theology"]["id"]
    client.post("/api/time/entries", json={"title": "Calculus", "category_id": math,
                                           "started_at": iso_local(DAY, "09:00"), "ended_at": iso_local(DAY, "12:00")})
    # listening to the Quran during the same hour: overlapping, not double-counted in coverage
    client.post("/api/time/entries", json={"title": "Surah", "category_id": quran,
                                           "started_at": iso_local(DAY, "11:00"), "ended_at": iso_local(DAY, "12:00")})
    day = client.get("/api/time/day/2026-09-10").json()
    assert round(day["covered_seconds"]) == 3 * 3600
    stats = client.get("/api/time/stats", params={"start": "2026-09-10", "end": "2026-09-10"}).json()
    assert round(stats["totals_by_kind"]["core"]) == 3 * 3600
    assert stats["tracked_days"] == 1


def test_timer_start_stop(client, categories):
    r = client.post("/api/time/timer/start", json={"title": "Deep work", "category_id": categories["AI & ML"]["id"]})
    assert r.json()["running"] is True
    assert client.get("/api/time/timer").json()["title"] == "Deep work"
    stopped = client.post("/api/time/timer/stop").json()
    assert stopped["running"] is False
    assert client.get("/api/time/timer").json() is None


def test_pause_then_resume_continues_the_same_session(client, categories):
    from datetime import timedelta

    from app.db import SessionLocal
    from app.models import TimeEntry
    from tests.conftest import niamey_today

    client.post("/api/time/timer/start", json={"title": "Linear algebra", "category_id": categories["Mathematics"]["id"]})
    paused = client.post("/api/time/timer/pause")
    assert paused.status_code == 200 and paused.json()["running"] is False
    state = client.get("/api/time/timer/state").json()
    assert state["running"] is None and [p["title"] for p in state["paused"]] == ["Linear algebra"]
    with SessionLocal() as db:  # 40 minutes of work before the pause
        first = db.get(TimeEntry, paused.json()["id"])
        first.started_at = first.ended_at - timedelta(minutes=40)
        db.commit()

    # something else meanwhile: the paused timer waits
    client.post("/api/time/timer/start", json={"title": "Dhuhr", "category_id": categories["Prayer"]["id"]})
    state = client.get("/api/time/timer/state").json()
    assert state["running"]["title"] == "Dhuhr" and state["paused"][0]["title"] == "Linear algebra"

    resumed = client.post("/api/time/timer/resume", json={}).json()
    assert resumed["title"] == "Linear algebra" and resumed["running"] is True
    assert round(resumed["session_seconds"] / 60) == 40  # the session carries the first segment
    state = client.get("/api/time/timer/state").json()
    assert state["paused"] == [] and state["running"]["id"] == resumed["id"]
    today = niamey_today()
    entries = client.get("/api/time/entries", params={"start": (today - timedelta(days=1)).isoformat(),
                                                      "end": today.isoformat()}).json()
    assert sorted(e["title"] for e in entries) == ["Dhuhr", "Linear algebra", "Linear algebra"]  # the pause is not counted
    assert client.post("/api/time/timer/resume", json={}).status_code == 409


def test_a_paused_timer_can_be_finished_without_resuming(client, categories):
    client.post("/api/time/timer/start", json={"title": "Reading", "category_id": categories["Physics"]["id"]})
    p = client.post("/api/time/timer/pause").json()
    assert client.post(f"/api/time/timer/finish/{p['id']}").status_code == 200
    assert client.get("/api/time/timer/state").json() == {"running": None, "paused": []}
    assert client.post("/api/time/timer/pause").status_code == 409


def test_quick_log_preview_then_commit(client):
    text = "\n".join([
        "05:40-06:10 Fajr #prayer",
        "09:00-11:30 Linear algebra #math @Ibrahim !Office",
        "23:10-01:40 reactions #reaction",
        "-400 taxi #transport",
        "+1000 gift from uncle #gift",
        "!! Workout done",
        "this line means nothing",
    ])
    preview = client.post("/api/time/quick", json={"text": text, "date": "2026-09-10"}).json()
    assert preview["committed"] is False
    assert [t["category"] for t in preview["time_entries"]] == ["Prayer", "Mathematics", "Reaction videos"]
    assert preview["time_entries"][1]["new_people"] == ["Ibrahim"]
    assert preview["time_entries"][1]["location"] == "Office"
    assert len(preview["transactions"]) == 2 and len(preview["habit_logs"]) == 1
    assert any("Not understood" in e for e in preview["errors"])

    done = client.post("/api/time/quick", json={"text": text, "date": "2026-09-10", "commit": True}).json()
    assert done["committed"] is True
    entries = client.get("/api/time/entries", params={"start": "2026-09-10", "end": "2026-09-11"}).json()
    assert len(entries) == 3
    late = next(e for e in entries if e["title"] == "reactions")
    assert round(late["duration_seconds"]) == 2.5 * 3600  # crossed midnight
    money = client.get("/api/money/summary", params={"start": "2026-09-10", "end": "2026-09-10"}).json()
    assert money["total_out"] == 400 and money["total_in"] == 1000
    people = client.get("/api/people").json()
    assert people[0]["name"] == "Ibrahim" and people[0]["hours_together"] == 2.5


def test_editing_category_locks_it(client, categories):
    e = client.post("/api/time/entries", json={
        "title": "x", "started_at": iso_local(DAY, "08:00"), "ended_at": iso_local(DAY, "09:00")}).json()
    assert e["category_locked"] is False
    p = client.patch(f"/api/time/entries/{e['id']}", json={"category_id": categories["Physics"]["id"]}).json()
    assert p["category_locked"] is True and p["kind"] == "core"


def test_sources_do_not_double_count_and_disagreements_are_flagged(client, categories):
    """Your own entry wins over ActivityWatch; the browser extension wins over
    ActivityWatch; ActivityWatch seen during your 'Sleep' is flagged."""
    from datetime import datetime, timezone

    from app.db import SessionLocal
    from app.models import TimeEntry

    d = date(2026, 9, 12)
    client.post("/api/time/entries", json={"title": "Linear algebra", "category_id": categories["Mathematics"]["id"],
                                           "started_at": iso_local(d, "09:00"), "ended_at": iso_local(d, "10:00")})
    user_id = client.get("/api/auth/me").json()["user"]["id"]

    def at(hhmm: str) -> datetime:
        return datetime.fromisoformat(iso_local(d, hhmm)).astimezone(timezone.utc)

    with SessionLocal() as db:
        db.add_all([
            # ActivityWatch saw VS Code 09:30-10:30 (core): half of it is already your entry
            TimeEntry(user_id=user_id, title="Code", category_id=categories["Building projects"]["id"],
                      started_at=at("09:30"), ended_at=at("10:30"), source="activitywatch", source_ref="aw:1"),
            # the extension and ActivityWatch both saw YouTube 11:00-11:30
            TimeEntry(user_id=user_id, title="YouTube · ReactionHub", category_id=categories["Reaction videos"]["id"],
                      started_at=at("11:00"), ended_at=at("11:30"), source="extension", source_ref="ext:1"),
            TimeEntry(user_id=user_id, title="youtube.com", category_id=categories["Reaction videos"]["id"],
                      started_at=at("11:00"), ended_at=at("11:40"), source="activitywatch", source_ref="aw:2"),
        ])
        db.commit()
    day = client.get(f"/api/time/day/{d.isoformat()}").json()
    hours = {k: round(v / 3600, 2) for k, v in day["totals_by_kind"].items()}
    assert hours["core"] == 1.5  # 1h yours + the 30 min ActivityWatch adds after it
    assert hours["noise"] == round(40 / 60, 2)  # 30 min from the extension + 10 min only ActivityWatch saw
    aw_code = next(e for e in day["entries"] if e["title"] == "Code")
    assert aw_code["counted_seconds"] == 1800
    assert day["overlaps"] == []  # same kind: no disagreement

    client.post("/api/time/entries", json={"title": "Sleep", "category_id": categories["Sleep"]["id"],
                                           "started_at": iso_local(d, "11:00"), "ended_at": iso_local(d, "12:00")})
    day = client.get(f"/api/time/day/{d.isoformat()}").json()
    assert len(day["overlaps"]) == 2  # you logged sleep while YouTube played: one of them is wrong
    assert {o["counted"] for o in day["overlaps"]} == {"yours"}


def test_measured_youtube_during_a_running_timer_counts_as_youtube(client, categories):
    """A timer left running while you watched something else: the extension's
    measured minutes win, if the video has a category. An entry you typed
    yourself still wins over everything."""
    from datetime import datetime, timezone

    from app.db import SessionLocal
    from app.models import TimeEntry

    d = date(2026, 9, 13)
    user_id = client.get("/api/auth/me").json()["user"]["id"]

    def at(hhmm: str) -> datetime:
        return datetime.fromisoformat(iso_local(d, hhmm)).astimezone(timezone.utc)

    math, reaction = categories["Mathematics"]["id"], categories["Reaction videos"]["id"]
    with SessionLocal() as db:
        db.add_all([
            TimeEntry(user_id=user_id, title="Linear algebra", category_id=math,
                      started_at=at("20:00"), ended_at=at("22:00"), source="timer"),
            TimeEntry(user_id=user_id, title="YouTube · ReactionHub", category_id=reaction,
                      started_at=at("20:30"), ended_at=at("21:00"), source="extension", source_ref="ext:1"),
            TimeEntry(user_id=user_id, title="YouTube · a channel without a rule", category_id=None,
                      started_at=at("21:10"), ended_at=at("21:30"), source="extension", source_ref="ext:2"),
            TimeEntry(user_id=user_id, title="Exercises", category_id=math,
                      started_at=at("22:30"), ended_at=at("23:00"), source="manual"),
            TimeEntry(user_id=user_id, title="YouTube · ReactionHub", category_id=reaction,
                      started_at=at("22:30"), ended_at=at("23:00"), source="extension", source_ref="ext:3"),
        ])
        db.commit()
    day = client.get(f"/api/time/day/{d.isoformat()}").json()
    minutes = {k: round(v / 60) for k, v in day["totals_by_kind"].items()}
    assert minutes["noise"] == 30  # measured during the timer; the typed entry kept 22:30-23:00
    assert minutes["core"] == 90 + 30  # the timer minus the reaction videos, plus the typed entry
    assert minutes.get("uncategorized", 0) == 0  # no category: the timer keeps those minutes
    assert sorted(o["counted"] for o in day["overlaps"]) == ["automatic", "yours", "yours"]
