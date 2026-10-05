"""Prayer times, reminders and life events. Notifications go to a fake sender:
no test ever shows a real toast."""

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.services import prayer

NIAMEY = ZoneInfo("Africa/Niamey")


# ---------------------------------------------------------------- prayer times
def test_prayer_times_match_a_published_timetable():
    # Aladhan (Muslim World League), checked 2026-10-03 for 13.5116 N, 2.1254 E.
    t = prayer.times_for(date(2026, 10, 3), None, NIAMEY)["times"]
    assert {k: t[k] for k in ("fajr", "sunrise", "dhuhr", "maghrib", "isha")} == {
        "fajr": "05:30", "sunrise": "06:41", "dhuhr": "12:41", "maghrib": "18:40", "isha": "19:46"}
    assert t["asr"] in ("16:01", "16:02")  # Aladhan rounds this one up
    june = prayer.times_for(date(2027, 6, 21), None, NIAMEY)["times"]
    assert june == {"fajr": "05:06", "sunrise": "06:26", "dhuhr": "12:53", "asr": "16:19",
                    "maghrib": "19:21", "isha": "20:36"}


def test_offsets_and_methods():
    base = prayer.times_for(date(2026, 10, 3), None, NIAMEY)["minutes"]
    shifted = prayer.times_for(date(2026, 10, 3), {"offsets": {"dhuhr": 20}}, NIAMEY)["minutes"]
    assert shifted["dhuhr"] - base["dhuhr"] == 20 and shifted["asr"] == base["asr"]
    makkah = prayer.times_for(date(2026, 10, 3), {"method": "Makkah"}, NIAMEY)["minutes"]
    assert makkah["isha"] - makkah["maghrib"] == 90
    hanafi = prayer.times_for(date(2026, 10, 3), {"asr": "hanafi"}, NIAMEY)["minutes"]
    assert hanafi["asr"] > base["asr"] + 30


def test_template_prayers_move_to_the_real_times_and_others_give_way():
    info = prayer.times_for(date(2026, 10, 3), None, NIAMEY)  # Dhuhr 12:41, Maghrib 18:40
    blocks = [
        {"start_minute": 9 * 60 + 50, "end_minute": 13 * 60, "title": "Internship project", "is_fixed": False},
        {"start_minute": 13 * 60, "end_minute": 13 * 60 + 30, "title": "Dhuhr", "is_fixed": True},
        {"start_minute": 17 * 60 + 15, "end_minute": 18 * 60 + 15, "title": "Workout", "is_fixed": False},
        {"start_minute": 18 * 60 + 30, "end_minute": 19 * 60, "title": "Maghrib", "is_fixed": True},
    ]
    out = prayer.align_blocks(blocks, info)
    spans = [(b["title"], b["start_minute"], b["end_minute"]) for b in out]
    assert ("Dhuhr", 12 * 60 + 41, 13 * 60 + 11) in spans
    assert ("Internship project", 9 * 60 + 50, 12 * 60 + 41) in spans  # cut where Dhuhr now starts
    assert ("Maghrib", 18 * 60 + 40, 19 * 60 + 10) in spans
    assert ("Workout", 17 * 60 + 15, 18 * 60 + 15) in spans  # no overlap: untouched


def test_applying_a_template_uses_the_prayer_times(client, categories):
    t = client.post("/api/plan-templates", json={"name": "Test day", "blocks": [
        {"start": "05:40", "end": "06:10", "title": "Fajr", "category": "Prayer", "is_fixed": True},
        {"start": "06:00", "end": "08:30", "title": "Deep work", "category": "Mathematics"},
    ]}).json()
    client.post("/api/plan/2026-10-03/apply-template", json={"template_id": t["id"]})
    blocks = client.get("/api/plan/2026-10-03").json()["blocks"]
    fajr = next(b for b in blocks if b["title"] == "Fajr")
    work = next(b for b in blocks if b["title"] == "Deep work")
    assert fajr["start"] == "05:30" and fajr["end"] == "06:00"
    assert work["start"] == "06:00"
    info = client.get("/api/prayer/2026-10-03").json()
    assert info["times"]["fajr"] == "05:30" and info["city"] == "Niamey"

    client.put("/api/prefs/prayer", json={"align_planner": False})
    client.post("/api/plan/2026-10-03/apply-template", json={"template_id": t["id"]})
    blocks = client.get("/api/plan/2026-10-03").json()["blocks"]
    assert next(b for b in blocks if b["title"] == "Fajr")["start"] == "05:40"


# ---------------------------------------------------------------- reminders
def _local(d: date, hhmm: str) -> datetime:
    h, m = map(int, hhmm.split(":"))
    return datetime(d.year, d.month, d.day, h, m, tzinfo=NIAMEY).astimezone(timezone.utc)


def test_the_evening_ritual_sends_each_reminder_once(client):
    from app.config import get_settings
    from app.services import reminders

    client.put("/api/profile", json={"wake_target": "05:40", "bed_target": "23:00", "awakening_date": "2026-08-31"})
    sent: list[tuple[str, str, str]] = []

    def fake(title, body, url=None, button=None):
        sent.append((title, body, url))
        return True, "fake"

    d = date(2026, 10, 3)
    s = get_settings()
    assert reminders.tick(s, send=fake, now=_local(d, "21:00")) == ["review"]  # yesterday's, caught up
    assert reminders.tick(s, send=fake, now=_local(d, "22:31")) == ["capture"]
    assert sent[-1][0] == "Capture Day 34 before bed" and sent[-1][2].endswith("/?capture=1")
    assert reminders.tick(s, send=fake, now=_local(d, "22:40")) == []  # once a day
    assert reminders.tick(s, send=fake, now=_local(d, "23:00")) == ["bedtime"]

    # the laptop was off at 05:40: at 07:00 the morning summary is skipped, the review still written
    done = reminders.tick(s, send=fake, now=_local(d + timedelta(days=1), "07:00"))
    assert done == ["review"]
    review = client.get("/api/ai/reviews").json()
    assert review[0]["date"] == "2026-10-03" and review[0]["facts"]["date"] == "2026-10-03"
    assert review[0]["text"] is None and "facts only" in review[0]["error"]  # AI is off in tests

    # next morning on time
    assert reminders.tick(s, send=fake, now=_local(d + timedelta(days=2), "05:41")) == ["morning", "review"]
    assert sent[-1][0] == "Day 36 — good morning" and "Fajr" in sent[-1][1]


def test_reminders_can_be_switched_off(client):
    from app.config import get_settings
    from app.services import reminders

    client.put("/api/prefs/reminders", json={"enabled": False, "auto_review": False})
    sent = []
    assert reminders.tick(get_settings(), send=lambda *a, **k: (sent.append(a), (True, ""))[1],
                          now=_local(date(2026, 10, 3), "22:31")) == []
    assert sent == []
    assert client.get("/api/prefs").json()["reminders"]["enabled"] is False


def test_a_long_running_timer_asks_if_you_are_still_on_it(client, categories):
    from app.config import get_settings
    from app.db import SessionLocal
    from app.models import TimeEntry
    from app.services import reminders

    client.put("/api/prefs/reminders", json={"auto_review": False})
    d = date(2026, 10, 3)
    uid = client.get("/api/auth/me").json()["user"]["id"]
    with SessionLocal() as db:
        db.add(TimeEntry(user_id=uid, title="Linear algebra", category_id=categories["Mathematics"]["id"],
                         started_at=_local(d, "14:00"), ended_at=None, source="timer"))
        db.commit()
    titles: list[str] = []

    def fake(title, body, url=None, button=None):
        titles.append(title)
        return True, "fake"

    s = get_settings()
    assert reminders.tick(s, send=fake, now=_local(d, "15:30")) == []
    assert reminders.tick(s, send=fake, now=_local(d, "16:01")) == ["timer"]  # 2 hours
    assert titles == ["Still on “Linear algebra”?"]
    assert reminders.tick(s, send=fake, now=_local(d, "17:00")) == []  # once per step
    assert reminders.tick(s, send=fake, now=_local(d, "18:05")) == ["timer"]  # 4 hours
    client.put("/api/prefs/reminders", json={"auto_review": False, "timer_nudge_minutes": 0})
    assert reminders.tick(s, send=fake, now=_local(d, "20:10")) == []  # switched off
    assert len(titles) == 2


# ---------------------------------------------------------------- life events
def test_life_events_crud_and_overview(client):
    e = client.post("/api/life/events", json={"date": "2022-09-15", "precision": "month", "area": "move",
                                               "title": "Started CS in Exampleton", "importance": 3}).json()
    assert e["precision"] == "month"
    client.patch(f"/api/life/events/{e['id']}", json={"title": "Started Computer Science — Exampleton"})
    overview = client.get("/api/life/overview").json()
    assert [x["title"] for x in overview["events"]] == ["Started Computer Science — Exampleton"]
    assert client.delete(f"/api/life/events/{e['id']}").json() == {"ok": True}
    assert client.get("/api/life/events").json() == []
