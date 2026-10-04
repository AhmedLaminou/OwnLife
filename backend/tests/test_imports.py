"""YouTube Takeout and ActivityWatch — no network: ActivityWatch is a mock transport."""

import json
from datetime import date, datetime, timezone

import httpx

from app.db import SessionLocal
from app.models import TimeEntry
from app.services import activitywatch as aw
from app.services.rules import load_ruleset
from tests.conftest import iso_local

TAKEOUT = [
    {"header": "YouTube", "title": "Watched Anime X Ep 5 Reaction Mashup",
     "titleUrl": "https://www.youtube.com/watch?v=aaaaaaaaaa1",
     "subtitles": [{"name": "ReactionHub", "url": "https://www.youtube.com/channel/x"}],
     "time": "2026-09-10T01:00:00.000Z", "products": ["YouTube"]},
    {"header": "YouTube", "title": "Watched Anime X Ep 6 Reaction Mashup",
     "titleUrl": "https://www.youtube.com/watch?v=aaaaaaaaaa2",
     "subtitles": [{"name": "ReactionHub", "url": "https://www.youtube.com/channel/x"}],
     "time": "2026-09-10T01:15:00.000Z", "products": ["YouTube"]},
    {"header": "YouTube", "title": "Watched Lecture 1: Course Introduction",
     "titleUrl": "https://www.youtube.com/watch?v=bbbbbbbbbb1",
     "subtitles": [{"name": "YaleCourses", "url": "https://www.youtube.com/channel/y"}],
     "time": "2026-09-10T01:30:00.000Z", "products": ["YouTube"]},
    {"header": "YouTube", "title": "Watched an ad", "titleUrl": "https://www.youtube.com/watch?v=cccccccccc1",
     "time": "2026-09-10T01:31:00.000Z", "details": [{"name": "From Google Ads"}]},
    {"header": "YouTube", "title": "Watched a video that has been removed", "time": "2026-09-10T01:40:00.000Z"},
]


def _rule(client, field, pattern, category_name, cats, is_regex=False):
    r = client.post("/api/rules", json={"field": field, "pattern": pattern, "is_regex": is_regex,
                                         "category_id": cats[category_name]["id"]})
    assert r.status_code == 200, r.text


def test_takeout_import_estimates_blocks_by_category(client, categories):
    _rule(client, "channel", "ReactionHub", "Reaction videos", categories)
    _rule(client, "channel", "YaleCourses", "Physics", categories)
    files = {"file": ("watch-history.json", json.dumps(TAKEOUT).encode(), "application/json")}
    r = client.post("/api/media/youtube/takeout", files=files).json()
    assert r["events_in_file"] == 3 and r["skipped"] == 2 and r["estimated_blocks"] == 2
    again = client.post("/api/media/youtube/takeout", files=files).json()
    assert again["new_events"] == 0  # idempotent

    entries = client.get("/api/time/entries", params={"start": "2026-09-10", "end": "2026-09-10"}).json()
    reaction = next(e for e in entries if e["kind"] == "noise")
    assert reaction["is_estimate"] and reaction["source"] == "youtube_takeout"
    assert round(reaction["duration_seconds"]) == 30 * 60  # 15 min gaps x 2
    physics = next(e for e in entries if e["kind"] == "core")
    assert round(physics["duration_seconds"]) == 8 * 60  # last video: default estimate

    summary = client.get("/api/media/youtube/summary", params={"start": "2026-09-01", "end": "2026-09-30"}).json()
    assert summary["videos"] == 3
    assert summary["channels"][0]["channel"] == "ReactionHub" and summary["channels"][0]["videos"] == 2


def test_the_search_history_file_is_refused_with_a_hint(client):
    # The same Takeout folder holds the searches: they are not videos watched.
    searches = [
        {"header": "YouTube", "title": "Vous avez recherché linear algebra",
         "titleUrl": "https://www.youtube.com/results?search_query=linear+algebra",
         "time": "2026-09-10T19:00:00.000Z", "products": ["YouTube"]},
    ]
    files = {"file": ("search-history.json", json.dumps(searches).encode(), "application/json")}
    r = client.post("/api/media/youtube/takeout", files=files)
    assert r.status_code == 422 and "search history" in r.json()["detail"]
    assert client.get("/api/media/youtube/stats").json()["events"] == 0


NBSP = "\N{NO-BREAK SPACE}"


def _entry(inner: str) -> str:
    """One entry of a My Activity / Takeout HTML page, in the shape Google writes."""
    return (
        '<div class="outer-cell mdl-cell mdl-cell--12-col mdl-shadow--2dp"><div class="mdl-grid">'
        '<div class="header-cell mdl-cell mdl-cell--12-col"><p class="mdl-typography--title">YouTube<br></p></div>'
        f'<div class="content-cell mdl-cell mdl-cell--6-col mdl-typography--body-1">{inner}</div>'
        '<div class="content-cell mdl-cell mdl-cell--6-col mdl-typography--body-1 mdl-typography--text-right"></div>'
        "</div></div>"
    )


WATCHED = _entry(
    f'Vous avez regardé{NBSP}<a href="https://www.youtube.com/watch?v=dddddddddd1">Lecture 2: Vectors</a><br>'
    f'<a href="https://www.youtube.com/channel/y">YaleCourses</a><br>3 oct. 2026, 03:19:37 WAT<br>'
)
NOT_WATCHED = [
    _entry(f'A aimé{NBSP}<a href="https://www.youtube.com/watch?v=dddddddddd2">A liked video</a><br>'
           f'<a href="https://www.youtube.com/channel/z">Someone</a><br>2 oct. 2026, 23:39:31 WAT<br>'),
    _entry(f'Vous vous êtes abonné à{NBSP}<a href="https://www.youtube.com/channel/z">Someone</a><br>'
           "1 oct. 2026, 10:00:00 WAT<br>"),
    _entry(f'Vous avez recherché{NBSP}<a href="https://www.youtube.com/results?search_query=vectors">vectors</a><br>'
           "1 oct. 2026, 09:59:00 WAT<br>"),
    _entry(f'<a href="https://www.youtube.com/watch?v=dddddddddd3">A shared video</a>{NBSP}a été fermé<br>'
           "30 sept. 2026, 08:00:00 WAT<br>"),
]


def _page(*entries: str) -> dict:
    html = f"<html><body>{''.join(entries)}</body></html>"
    return {"file": ("MonActivité.html", html.encode(), "text/html")}


def test_french_my_activity_html_keeps_only_the_videos_watched(client):
    r = client.post("/api/media/youtube/takeout", files=_page(*NOT_WATCHED, WATCHED))
    assert r.status_code == 200, r.text
    r = r.json()
    assert r["events_in_file"] == 1
    assert r["ignored"] == {"likes": 1, "subscriptions": 1, "searches": 1, "other": 1}
    [ev] = client.get("/api/media/youtube/events").json()
    assert ev["title"] == "Lecture 2: Vectors" and ev["channel"] == "YaleCourses"
    assert ev["occurred_at"].startswith("2026-10-03T02:19:37")  # 03:19:37 WAT is 02:19:37 UTC


def test_a_file_without_any_video_watched_says_why(client):
    r = client.post("/api/media/youtube/takeout", files=_page(*NOT_WATCHED))
    assert r.status_code == 422
    assert "1 likes" in r.json()["detail"] and "not keeping the videos you watch" in r.json()["detail"]


def _visit(local: str, url: str, title: str) -> dict:
    t = datetime.fromisoformat(local)
    return {"time_usec": int(t.timestamp() * 1_000_000), "url": url, "title": title, "favicon_url": "",
            "page_transition_qualifier": "CLIENT_QUALIFIER_UNSET", "client_id": "test"}


CHROME = {"Browser History": [
    _visit("2026-09-20T20:30:00+01:00", "https://www.youtube.com/", "YouTube"),
    _visit("2026-09-20T20:00:00+01:00", "https://www.youtube.com/watch?v=eeeeeeeeee1&t=10s",
           "(3) Anime X Ep 7 Reaction - YouTube"),
    _visit("2026-09-20T20:01:00+01:00", "https://www.youtube.com/watch?v=eeeeeeeeee1",
           "Anime X Ep 7 Reaction - YouTube"),  # a reload: the same watch
    _visit("2026-09-20T20:05:00+01:00", "https://www.khanacademy.org/math", "Khan Academy"),
    _visit("2026-09-20T20:20:00+01:00", "https://www.youtube.com/watch?v=eeeeeeeeee2", "YouTube"),  # title not loaded
], "Typed Url": [], "Session": []}


def test_chrome_history_gives_the_youtube_videos_opened(client):
    files = {"file": ("Historique.json", json.dumps(CHROME).encode(), "application/json")}
    r = client.post("/api/media/youtube/takeout", files=files).json()
    assert r["source"] == "chrome_history" and r["events_in_file"] == 2 and r["skipped"] == 2
    assert r["channel_lookup"] is False  # oEmbed is off in tests
    assert client.post("/api/media/youtube/takeout", files=files).json()["new_events"] == 0  # idempotent

    events = sorted(client.get("/api/media/youtube/events").json(), key=lambda e: e["occurred_at"])
    assert [e["title"] for e in events] == ["Anime X Ep 7 Reaction", "eeeeeeeeee2"]
    assert events[0]["url"] == "https://www.youtube.com/watch?v=eeeeeeeeee1"
    assert {e["source"] for e in events} == {"chrome_history"}
    [block] = client.get("/api/time/entries", params={"start": "2026-09-20", "end": "2026-09-20"}).json()
    assert round(block["duration_seconds"] / 60) == 28  # 20 min until the next video, 8 for the last one


def test_channel_lookup_names_chrome_videos_and_refiles_them(client, categories, monkeypatch):
    from app.services import youtube

    files = {"file": ("Historique.json", json.dumps(CHROME).encode(), "application/json")}
    client.post("/api/media/youtube/takeout", files=files)
    _rule(client, "channel", "ReactionHub", "Reaction videos", categories)
    found = {"eeeeeeeeee1": {"author_name": "ReactionHub", "title": "Anime X Ep 7 Reaction"},
             "eeeeeeeeee2": {"author_name": "ReactionHub", "title": "Anime X Ep 8 Reaction"}}
    monkeypatch.setattr(youtube, "oembed", found.get)
    uid = client.get("/api/auth/me").json()["user"]["id"]
    youtube._lookup_channels(uid)  # what the background thread runs
    assert youtube.lookup_status(uid)["state"] == "idle"

    events = sorted(client.get("/api/media/youtube/events").json(), key=lambda e: e["occurred_at"])
    assert [e["channel"] for e in events] == ["ReactionHub", "ReactionHub"]
    assert events[1]["title"] == "Anime X Ep 8 Reaction"  # the title Chrome did not have yet
    [block] = client.get("/api/time/entries", params={"start": "2026-09-20", "end": "2026-09-20"}).json()
    assert block["category"]["name"] == "Reaction videos" and block["title"].startswith("YouTube · ReactionHub")


def test_an_estimate_counts_on_a_day_that_has_your_own_entries(client, categories):
    # Your own entry no longer drops the day's YouTube estimate: it wins where they overlap.
    d = date(2026, 9, 10)
    client.post("/api/time/entries", json={"title": "Linear algebra", "category_id": categories["Mathematics"]["id"],
                                           "started_at": iso_local(d, "02:20"), "ended_at": iso_local(d, "03:00")})
    _rule(client, "channel", "ReactionHub", "Reaction videos", categories)
    files = {"file": ("watch-history.json", json.dumps(TAKEOUT).encode(), "application/json")}
    assert client.post("/api/media/youtube/takeout", files=files).json()["estimated_blocks"] == 2
    totals = client.get(f"/api/time/day/{d.isoformat()}").json()["totals_by_kind"]
    assert round(totals["noise"] / 60) == 20  # reaction videos 02:00-02:30, your entry from 02:20
    assert round(totals["core"] / 60) == 40
    assert round(totals.get("uncategorized", 0) / 60) == 0  # the lecture at 02:30 is inside your entry


def test_reapply_rules_rebuilds_youtube_blocks(client, categories):
    files = {"file": ("watch-history.json", json.dumps(TAKEOUT).encode(), "application/json")}
    client.post("/api/media/youtube/takeout", files=files)
    entries = client.get("/api/time/entries", params={"start": "2026-09-10", "end": "2026-09-10"}).json()
    assert all(e["kind"] == "uncategorized" for e in entries)
    _rule(client, "title", r"reaction", "Reaction videos", categories, is_regex=True)
    client.post("/api/rules/reapply")
    entries = client.get("/api/time/entries", params={"start": "2026-09-10", "end": "2026-09-10"}).json()
    assert {e["kind"] for e in entries} == {"noise", "uncategorized"}


def _aw_transport():
    window = [
        {"timestamp": "2026-09-10T08:00:00+00:00", "duration": 3600, "data": {"app": "Code.exe", "title": "OwnLife — VS Code"}},
        {"timestamp": "2026-09-10T09:00:00+00:00", "duration": 1800, "data": {"app": "chrome.exe", "title": "x"}},
        {"timestamp": "2026-09-10T09:30:00+00:00", "duration": 30, "data": {"app": "explorer.exe", "title": "tiny"}},
    ]
    web = [
        {"timestamp": "2026-09-10T09:00:00+00:00", "duration": 900,
         "data": {"url": "https://www.youtube.com/watch?v=abcdefghij1", "title": "Anime X reaction - YouTube"}},
        {"timestamp": "2026-09-10T09:15:00+00:00", "duration": 900,
         "data": {"url": "https://www.khanacademy.org/math/linear-algebra", "title": "Linear algebra"}},
    ]
    seen = {"queries": []}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/0/buckets/":
            return httpx.Response(200, json={"aw-watcher-window_PC": {"type": "currentwindow"},
                                             "aw-watcher-afk_PC": {"type": "afkstatus"},
                                             "aw-watcher-web-chrome": {"type": "web.tab.current"}})
        if request.url.path == "/api/0/query/":
            body = json.loads(request.content)
            seen["queries"].append(body)
            is_web = any("aw-watcher-web" in line for line in body["query"])
            return httpx.Response(200, json=[web if is_web else window])
        return httpx.Response(404)

    return httpx.MockTransport(handler), seen


def test_activitywatch_sync_classifies_and_does_not_repeat(client, categories):
    _rule(client, "app", "Code.exe", "Building projects", categories)
    _rule(client, "domain", "khanacademy.org", "Mathematics", categories)
    _rule(client, "title", "reaction", "Reaction videos", categories)
    user_id = client.get("/api/auth/me").json()["user"]["id"]
    transport, seen = _aw_transport()
    now = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)
    with SessionLocal() as db:
        client_aw = aw.AWClient("http://aw.test", transport=transport)
        result = aw.sync(db, user_id, client_aw, load_ruleset(db, user_id), oembed=False, now=now)
        db.commit()
        assert result["created"] == 3  # VS Code hour, YouTube 15 min, Khan Academy 15 min; the 30 s blip dropped
        rows = db.query(TimeEntry).filter(TimeEntry.source == "activitywatch").order_by(TimeEntry.started_at).all()
        assert [r.category.name for r in rows] == ["Building projects", "Reaction videos", "Mathematics"]
        assert rows[0].meta["titles"] == ["OwnLife — VS Code"]
        # the window query filtered AFK time
        assert any("not-afk" in line for line in seen["queries"][0]["query"])
        again = aw.sync(db, user_id, client_aw, load_ruleset(db, user_id), oembed=False, now=now)
        assert again["created"] == 0


def test_activitywatch_status_when_not_running(client):
    r = client.get("/api/integrations/activitywatch").json()
    # nothing listens on the test machine's ActivityWatch port, or something does: both are fine,
    # the endpoint must answer either way
    assert "reachable" in r and "buckets" in r
