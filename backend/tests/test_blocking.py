"""Blocking noise on YouTube: off until switched on; a noise video blocked once
the day's noise reaches the limit (and not before, not on another day); a
category or a channel blocked all day; learning, unsorted and spared
categories never blocked; the settings cleaned; the extension's endpoint."""

from datetime import datetime, timedelta, timezone

from app.db import SessionLocal
from app.models import Profile, TimeEntry
from app.services import blocking
from app.services.rules import load_ruleset

LOCAL = timezone(timedelta(hours=1))  # the test profile's time zone
NOW = datetime(2026, 9, 21, 18, 0, tzinfo=LOCAL)

NEWS = {"video_id": "nnnnnnnnnn1", "title": "Election night: live results", "channel": "NightlyNewsTV",
        "url": "https://www.youtube.com/watch?v=nnnnnnnnnn1"}
REACTION = {"video_id": "rrrrrrrrrr1", "title": "Reacting to the strangest videos", "channel": "ReactionHub",
            "url": "https://www.youtube.com/watch?v=rrrrrrrrrr1"}
LECTURE = {"video_id": "llllllllll1", "title": "Eigenvalues, lecture 7", "channel": "SomeMathChannel",
           "url": "https://www.youtube.com/watch?v=llllllllll1"}
SONG = {"video_id": "ssssssssss1", "title": "A quiet song", "channel": "SomeMusicChannel",
        "url": "https://www.youtube.com/watch?v=ssssssssss1"}
UNSORTED = {"video_id": "uuuuuuuuuu1", "title": "Episode 112", "channel": "MysteryPodcast",
            "url": "https://www.youtube.com/watch?v=uuuuuuuuuu1"}


def _setup(client, categories) -> int:
    for channel, cat in (("NightlyNewsTV", "News & geopolitics"), ("ReactionHub", "Reaction videos"),
                         ("SomeMathChannel", "Mathematics"), ("SomeMusicChannel", "Music videos")):
        client.post("/api/rules", json={"field": "channel", "pattern": channel, "category_id": categories[cat]["id"]})
    return client.get("/api/auth/me").json()["user"]["id"]


def _noise(uid: int, categories, start: str, minutes: int, day=NOW) -> None:
    h, m = map(int, start.split(":"))
    a = day.replace(hour=h, minute=m)
    with SessionLocal() as db:
        db.add(TimeEntry(user_id=uid, title="Debate", category_id=categories["News & geopolitics"]["id"],
                         started_at=a, ended_at=a + timedelta(minutes=minutes), source="manual"))
        db.commit()


def _decide(uid: int, video: dict, now: datetime = NOW) -> dict:
    with SessionLocal() as db:
        return blocking.decide(db, uid, db.get(Profile, uid), load_ruleset(db, uid), video, now)


def _set(client, **prefs) -> dict:
    r = client.put("/api/media/youtube/blocking", json={"enabled": True, **prefs})
    assert r.status_code == 200, r.text
    return r.json()


def test_noise_is_blocked_once_the_day_reaches_the_limit(client, categories):
    uid = _setup(client, categories)
    assert _decide(uid, REACTION)["blocked"] is False  # off until switched on
    _set(client, limit_minutes=60, never=[categories["Music videos"]["id"]])
    _noise(uid, categories, "09:00", 40)
    d = _decide(uid, REACTION)
    assert d["blocked"] is False and d["noise_seconds"] == 40 * 60 and d["category"]["name"] == "Reaction videos"

    _noise(uid, categories, "12:00", 25)  # 65 minutes of noise today: past the hour
    d = _decide(uid, REACTION)
    assert (d["blocked"], d["reason"], d["active"]) == (True, "limit", True)
    assert d["until"] == "2026-09-22T00:00:00+01:00"  # until local midnight
    assert _decide(uid, NEWS)["blocked"] is True
    assert _decide(uid, LECTURE)["blocked"] is False  # learning plays
    assert _decide(uid, UNSORTED)["blocked"] is False  # not sorted yet: plays until it is
    assert _decide(uid, SONG)["blocked"] is False  # a category you spared
    assert _decide(uid, REACTION, NOW + timedelta(days=1))["blocked"] is False  # a new day starts at zero


def test_a_category_or_a_channel_can_be_blocked_all_day(client, categories):
    uid = _setup(client, categories)
    _set(client, limit_minutes=120, always=[categories["News & geopolitics"]["id"]], channels=["reactionhub "])
    news, reaction = _decide(uid, NEWS), _decide(uid, REACTION)
    assert (news["blocked"], news["reason"], news["noise_seconds"]) == (True, "category", 0)
    assert (reaction["blocked"], reaction["reason"]) == (True, "channel")  # case and spaces aside
    assert _decide(uid, LECTURE)["blocked"] is False


def test_the_settings_are_cleaned(client, categories):
    _setup(client, categories)
    news, music = categories["News & geopolitics"]["id"], categories["Music videos"]["id"]
    out = _set(client, always=[news, categories["Mathematics"]["id"], 999], never=[news, music],
               channels=["Daily  Planet News", "daily planet news", " ", "ReactionHub"])
    assert out["prefs"]["always"] == [news]  # only noise categories
    assert out["prefs"]["never"] == [music]  # not both always and never
    assert out["prefs"]["channels"] == ["Daily Planet News", "ReactionHub"]
    names = {c["name"] for c in out["categories"]}
    assert "News & geopolitics" in names and "YouTube, not sorted yet" not in names and "Mathematics" not in names
    assert out["status"]["enabled"] is True and out["status"]["limit_seconds"] == 120 * 60


def test_the_extension_asks_with_its_key(client, categories):
    _setup(client, categories)
    token = client.post("/api/tokens", json={"name": "Chrome extension"}).json()["token"]
    auth = {"Authorization": f"Bearer {token}"}
    assert client.post("/api/ingest/youtube/check", json=NEWS, headers=auth).json()["blocked"] is False
    _set(client, always=[categories["News & geopolitics"]["id"]])
    r = client.post("/api/ingest/youtube/check", json=NEWS, headers=auth).json()
    assert r["blocked"] is True and r["reason"] == "category" and r["category"]["name"] == "News & geopolitics"
    assert client.post("/api/ingest/youtube/check", json=NEWS).status_code == 401  # never without the key
    assert client.get("/api/ingest/ping", headers=auth).json()["today"]["blocking"]["enabled"] is True
