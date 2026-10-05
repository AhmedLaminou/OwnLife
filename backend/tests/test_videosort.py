"""The noise budget, live: strict mode (YouTube nothing sorted counts as noise),
the local model sorting new videos (driven here by a fake model), your own
sorting, the notifications as the budget runs out, and the extension's badge."""

from datetime import date, datetime, timedelta, timezone

from app.config import get_settings
from app.db import SessionLocal
from app.models import TimeEntry, WatchEvent
from app.services import reminders, videosort
from app.services.watchlive import UNSORTED

LOCAL = timezone(timedelta(hours=1))  # the test profile's time zone
DAY = datetime.now(LOCAL).date() - timedelta(days=1)  # recent: the sorter looks at the last 7 days


def _at(hhmm: str, d: date = DAY) -> datetime:
    h, m = map(int, hhmm.split(":"))
    return datetime(d.year, d.month, d.day, h, m, tzinfo=LOCAL)


def _hb(video: str, start: str, minutes: int, title: str, channel: str | None) -> list[dict]:
    t0 = _at(start)
    return [{"video_id": video, "ts": (t0 + timedelta(seconds=s)).isoformat(), "title": title, "channel": channel,
             "url": f"https://www.youtube.com/watch?v={video}"} for s in range(0, minutes * 60 + 1, 15)]


def _send(client, *beats: list[dict]) -> dict:
    token = client.post("/api/tokens", json={"name": "Chrome extension"}).json()["token"]
    r = client.post("/api/ingest/youtube", json={"heartbeats": [b for x in beats for b in x]},
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    return r.json()


def _block_list(client) -> list[tuple[str, str | None]]:
    """(title, category) of each measured block."""
    return sorted((e["title"], e["category"]["name"] if e["category"] else "")
                  for e in client.get("/api/time/entries", params={"start": DAY.isoformat(), "end": DAY.isoformat()}).json()
                  if e["source"] == "extension")


def _blocks(client) -> dict[str, str | None]:
    return {t: c or None for t, c in _block_list(client)}


def _uid(client) -> int:
    return client.get("/api/auth/me").json()["user"]["id"]


MATH = ("aaaaaaaaaa1", "Eigenvectors, visually", "SomeMathChannel")
NEWS = ("bbbbbbbbbb2", "Election night: live results", "NightlyNewsTV")
PODCAST = ("ccccccccccc", "Episode 112 with Dr X", "MysteryPodcast")
REACTION = ("ddddddddddd", "Reaction 1", "ReactionHub")


def test_strict_mode_counts_unsorted_youtube_as_noise_but_never_over_a_timer(client, categories):
    _send(client, _hb(NEWS[0], "10:00", 10, NEWS[1], NEWS[2]))
    assert _blocks(client) == {"YouTube · NightlyNewsTV": UNSORTED}
    day = client.get(f"/api/time/day/{DAY.isoformat()}").json()
    assert round(day["totals_by_kind"]["noise"] / 60) == 10

    # a timer said Linear algebra meanwhile: the unsorted video does not erase it
    with SessionLocal() as db:
        db.add(TimeEntry(user_id=_uid(client), title="Linear algebra", category_id=categories["Mathematics"]["id"],
                         started_at=_at("09:30"), ended_at=_at("10:30"), source="timer"))
        db.commit()
    day = client.get(f"/api/time/day/{DAY.isoformat()}").json()
    assert round(day["totals_by_kind"]["core"] / 60) == 60 and day["totals_by_kind"].get("noise", 0) == 0

    r = client.put("/api/media/youtube/sorting/prefs", json={"strict": False}).json()
    assert r["prefs"] == {"strict": False, "sort": True} and r["refiled"] == 1
    assert _blocks(client) == {"YouTube · NightlyNewsTV": None}


def test_the_model_sorts_what_no_rule_places_and_unsure_answers_wait_for_you(client, categories):
    client.post("/api/rules", json={"field": "channel", "pattern": "ReactionHub",
                                    "category_id": categories["Reaction videos"]["id"]})
    _send(client, *[_hb(v, t, 5, title, ch) for (v, title, ch), t in
                    zip([MATH, NEWS, PODCAST, REACTION], ["09:00", "11:00", "13:00", "15:00"])])
    asked: list = []

    def fake(cats, videos):
        asked.append(([v.title for v in videos], {c.name for c in cats}))
        by_name = {c.name: c.id for c in cats}
        answers = {MATH[0]: ("Mathematics", True), NEWS[0]: ("News & geopolitics", True),
                   PODCAST[0]: ("Broad learning", False)}
        return [(v.video_id, by_name[answers[v.video_id][0]], answers[v.video_id][1]) for v in videos]

    res = videosort.sort(get_settings(), _uid(client), asker=fake, label="fake")
    assert (res.asked, res.sorted, res.guesses) == (3, 2, 1)
    [(titles, names)] = asked
    assert sorted(titles) == sorted([MATH[1], NEWS[1], PODCAST[1]])  # the rule already places the reaction
    assert "Destructive habits" not in names and UNSORTED not in names  # never a private category
    assert _blocks(client) == {"YouTube · SomeMathChannel": "Mathematics", "YouTube · NightlyNewsTV": "News & geopolitics",
                               "YouTube · MysteryPodcast": UNSORTED, "YouTube · ReactionHub": "Reaction videos"}

    page = client.get("/api/media/youtube/sorting").json()
    [waiting] = page["to_sort"]
    assert waiting["title"] == PODCAST[1] and waiting["category"]["name"] == "Broad learning" and waiting["asked"]
    assert {v["title"] for v in page["by_model"]} == {MATH[1], NEWS[1]}

    # asked once: a second run has nothing to ask
    assert videosort.sort(get_settings(), _uid(client), asker=fake).asked == 0 and len(asked) == 1

    # you confirm the guess: the block follows
    r = client.post(f"/api/media/youtube/videos/{PODCAST[0]}/category",
                    json={"category_id": categories["Broad learning"]["id"]})
    assert r.json()["refiled"] >= 1
    assert _blocks(client)["YouTube · MysteryPodcast"] == "Broad learning"
    assert client.get("/api/media/youtube/sorting").json()["to_sort"] == []

    # your rules come before the model's answers
    client.post("/api/rules", json={"field": "title", "pattern": "Eigenvectors", "category_id": categories["Physics"]["id"]})
    client.post("/api/rules/reapply")
    assert _blocks(client)["YouTube · SomeMathChannel"] == "Physics"


def test_a_video_you_sort_wins_over_rules_and_a_channel_can_be_sorted_at_once(client, categories):
    client.post("/api/rules", json={"field": "channel", "pattern": "NightlyNewsTV",
                                    "category_id": categories["News & geopolitics"]["id"]})
    other = ("eeeeeeeeeee", "Old wars, explained by a historian", "NightlyNewsTV")
    _send(client, _hb(NEWS[0], "11:00", 5, NEWS[1], NEWS[2]), _hb(other[0], "18:00", 5, other[1], other[2]),
          _hb(PODCAST[0], "13:00", 5, PODCAST[1], PODCAST[2]))
    client.post(f"/api/media/youtube/videos/{other[0]}/category", json={"category_id": categories["Broad learning"]["id"]})
    assert _block_list(client) == [("YouTube · MysteryPodcast", UNSORTED), ("YouTube · NightlyNewsTV", "Broad learning"),
                                   ("YouTube · NightlyNewsTV", "News & geopolitics")]

    client.post(f"/api/media/youtube/videos/{PODCAST[0]}/category",
                json={"category_id": categories["Reaction videos"]["id"], "whole_channel": True})
    rule = next(r for r in client.get("/api/rules").json() if r["pattern"] == "^MysteryPodcast$")
    assert rule["is_regex"] and rule["field"] == "channel"
    assert _blocks(client)["YouTube · MysteryPodcast"] == "Reaction videos"
    assert client.post("/api/media/youtube/videos/zzzzzzzzzzz/category",
                       json={"category_id": categories["Physics"]["id"]}).status_code == 404


def test_a_video_that_came_without_its_channel_gets_it_and_the_channel_rule_applies(client, categories):
    client.post("/api/rules", json={"field": "channel", "pattern": "SomeMathChannel",
                                    "category_id": categories["Mathematics"]["id"]})
    _send(client, _hb(MATH[0], "09:00", 5, MATH[1], None))  # the page did not show it, the internet was down
    assert _blocks(client) == {"YouTube · Unknown channel": UNSORTED}
    online = get_settings().model_copy(update={"youtube_oembed": True})
    got = videosort.fill_channels(online, _uid(client), _at("00:00"),
                                  lookup=lambda vid: {"author_name": "SomeMathChannel", "title": MATH[1]})
    assert got == {MATH[0]}
    with SessionLocal() as db:
        videosort.refile(db, _uid(client), got)
    assert _blocks(client) == {"YouTube · SomeMathChannel": "Mathematics"}


def test_sorting_needs_a_model(client):
    assert client.post("/api/media/youtube/sorting/run").status_code == 400  # AI is off in the tests
    assert client.get("/api/media/youtube/sorting").json()["ai"] is False


def test_the_noise_budget_warns_before_when_and_after_it_is_spent(client, categories):
    client.put("/api/prefs/reminders", json={"auto_review": False})
    uid, news = _uid(client), categories["News & geopolitics"]["id"]
    d = date(2026, 9, 21)
    sent: list[tuple[str, str]] = []

    def fake(title, body, url=None, button=None):
        sent.append((title, body))
        return True, "fake"

    def watched(start: str, end: str) -> None:
        with SessionLocal() as db:
            db.add(TimeEntry(user_id=uid, title="Debate", category_id=news, started_at=_at(start, d), ended_at=_at(end, d),
                             source="manual"))
            db.commit()

    s = get_settings()
    watched("14:00", "14:40")
    assert reminders.tick(s, send=fake, now=_at("14:41", d)) == []  # 40 min of 60: nothing to say yet
    watched("14:40", "14:46")
    assert reminders.tick(s, send=fake, now=_at("14:47", d)) == ["noise"]
    assert sent[-1][0] == "14 min of noise left today" and "News & geopolitics 46 min" in sent[-1][1]
    assert reminders.tick(s, send=fake, now=_at("14:48", d)) == []  # once
    watched("15:00", "15:15")
    assert reminders.tick(s, send=fake, now=_at("15:16", d)) == ["noise"]
    assert sent[-1][0] == "Your hour of noise is spent"
    watched("16:00", "16:20")
    assert reminders.tick(s, send=fake, now=_at("16:21", d)) == ["noise"]
    assert sent[-1][0] == "Noise: 21 min over your budget"
    assert reminders.tick(s, send=fake, now=_at("09:00", d + timedelta(days=1))) == []  # a new day, no noise yet

    client.put("/api/prefs/reminders", json={"auto_review": False, "noise_alert": False})
    watched("17:00", "18:00")
    assert reminders.tick(s, send=fake, now=_at("18:01", d)) == []
    assert len(sent) == 3


def test_noise_levels():
    h = 3600
    assert reminders.noise_level(30, h, 900, 900) == 0
    assert reminders.noise_level(44 * 60, h, 900, 900) == 0
    assert reminders.noise_level(45 * 60, h, 900, 900) == 1
    assert reminders.noise_level(h, h, 900, 900) == 2
    assert reminders.noise_level(h + 29 * 60, h, 900, 900) == 3
    assert reminders.noise_level(h + 29 * 60, h, 0, 0) == 2  # no warning, no repeat
    assert reminders.noise_level(120, 0, 900, 900) == 2  # a budget of zero: any noise is too much


def test_the_extension_gets_todays_noise_for_its_badge(client, categories):
    client.post("/api/rules", json={"field": "channel", "pattern": "ReactionHub",
                                    "category_id": categories["Reaction videos"]["id"]})
    now = datetime.now(timezone.utc)
    beats = [{"video_id": REACTION[0], "ts": (now - timedelta(seconds=s)).isoformat(), "title": REACTION[1],
              "channel": REACTION[2], "url": f"https://www.youtube.com/watch?v={REACTION[0]}"} for s in range(150, 25, -15)]
    noise = _send(client, beats)["today"]["noise"]
    assert noise["budget_seconds"] == 3600 and 100 <= noise["seconds"] <= 140
    assert noise["categories"][0]["name"] == "Reaction videos"


def test_the_history_is_sorted_on_demand_and_its_blocks_rebuilt(client, categories):
    uid = _uid(client)
    past = [("hhhhhhhhhh1", "Eigenvalues, lecture 7", "SomeMathChannel"), ("hhhhhhhhhh2", "Election night, live", "NightlyNewsTV")]
    with SessionLocal() as db:
        for i, (vid, title, channel) in enumerate(past):
            db.add(WatchEvent(user_id=uid, source="chrome_history", video_id=vid, title=title, channel=channel,
                              occurred_at=_at(f"0{8 + 2 * i}:00"), url=f"https://www.youtube.com/watch?v={vid}"))
        db.commit()
    client.post("/api/rules/reapply")  # the estimated blocks, without a category yet
    assert client.get("/api/media/youtube/sorting").json()["history_unsorted"] == 2

    def fake(cats, videos):
        by_name = {c.name: c.id for c in cats}
        return [(v.video_id, by_name["Mathematics" if "Eigen" in v.title else "News & geopolitics"], True) for v in videos]

    res = videosort.sort(get_settings(), uid, asker=fake, label="fake", history=True)
    assert (res.asked, res.sorted) == (2, 2) and res.refiled == 2
    with SessionLocal() as db:
        cats = sorted(e.category.name for e in db.query(TimeEntry).filter(TimeEntry.source == "youtube_takeout"))
    assert cats == ["Mathematics", "News & geopolitics"]
    assert client.get("/api/media/youtube/sorting").json()["history_unsorted"] == 0

