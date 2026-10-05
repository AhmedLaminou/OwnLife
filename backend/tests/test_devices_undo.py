"""Device keys, the YouTube extension's heartbeats, and undo."""

from datetime import date, datetime, timedelta, timezone

from tests.conftest import iso_local

DAY = date(2026, 9, 20)


def _hb(video: str, start: str, minutes: int, title: str, channel: str | None):
    h, m = map(int, start.split(":"))
    t0 = datetime(DAY.year, DAY.month, DAY.day, h, m, tzinfo=timezone(timedelta(hours=1)))
    return [
        {"video_id": video, "ts": (t0 + timedelta(seconds=s)).isoformat(), "title": title, "channel": channel,
         "url": f"https://www.youtube.com/watch?v={video}"}
        for s in range(0, minutes * 60 + 1, 15)
    ]


def _key(client) -> str:
    r = client.post("/api/tokens", json={"name": "Chrome extension"}).json()
    assert r["token"].startswith("ol_") and r["prefix"] == r["token"][:10]
    return r["token"]


def test_keys_are_shown_once_scoped_and_revocable(client):
    token = _key(client)
    listed = client.get("/api/tokens").json()
    assert "token" not in listed[0] and listed[0]["name"] == "Chrome extension"
    auth = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/ingest/ping", headers=auth).json()["name"] == "Test Person"
    assert client.get("/api/ingest/ping").status_code == 401
    assert client.get("/api/ingest/ping", headers={"Authorization": "Bearer ol_wrong"}).status_code == 401
    # a key opens the ingest endpoints only, not the rest of the API
    anon_headers = {"Authorization": f"Bearer {token}", "X-OwnLife": "1"}
    client.cookies.clear()
    assert client.get("/api/journal", headers=anon_headers).status_code == 401
    assert client.get("/api/ingest/ping", headers=auth).status_code == 200


def test_heartbeats_become_one_measured_block_per_category(client, categories):
    token = _key(client)
    auth = {"Authorization": f"Bearer {token}"}
    client.post("/api/rules", json={"field": "channel", "pattern": "ReactionHub",
                                    "category_id": categories["Reaction videos"]["id"]})
    beats = _hb("aaaaaaaaaaa", "21:00", 10, "Reaction 1", "ReactionHub") + _hb("bbbbbbbbbbb", "21:11", 9, "Reaction 2", "ReactionHub")
    # sent out of order, in two batches: the result is the same
    r1 = client.post("/api/ingest/youtube", json={"heartbeats": beats[40:]}, headers=auth)
    assert r1.status_code == 200, r1.text
    client.post("/api/ingest/youtube", json={"heartbeats": beats[:40]}, headers=auth)

    entries = [e for e in client.get("/api/time/entries", params={"start": DAY.isoformat(), "end": DAY.isoformat()}).json()
               if e["source"] == "extension"]
    assert len(entries) == 1
    e = entries[0]
    assert e["title"] == "YouTube · ReactionHub — 2 videos"
    assert e["category"]["name"] == "Reaction videos"
    assert round(e["duration_seconds"] / 60) == 20  # 21:00 → 21:20: one minute between the videos merged
    assert round(sum(e["meta"]["channels"].values()) / 60) == 19  # 19 minutes actually playing

    # the same minutes seen by ActivityWatch are not counted twice
    day = client.get(f"/api/time/day/{DAY.isoformat()}").json()
    assert round(day["totals_by_kind"]["noise"] / 60) == 20


def test_a_new_rule_refiles_measured_minutes(client, categories):
    auth = {"Authorization": f"Bearer {_key(client)}"}
    client.post("/api/ingest/youtube", json={"heartbeats": _hb("ccccccccccc", "10:00", 5, "Lecture 1", "YaleCourses")},
                headers=auth)
    entry = next(e for e in client.get("/api/time/entries", params={"start": DAY.isoformat(), "end": DAY.isoformat()}).json())
    assert entry["category"]["name"] == "YouTube, not sorted yet"  # strict mode: noise until it is sorted
    client.post("/api/rules", json={"field": "channel", "pattern": "YaleCourses", "category_id": categories["Physics"]["id"]})
    assert client.post("/api/rules/reapply").json()["extension_segments_refiled"] == 1
    entry = next(e for e in client.get("/api/time/entries", params={"start": DAY.isoformat(), "end": DAY.isoformat()}).json())
    assert entry["category"]["name"] == "Physics"


# ---------------------------------------------------------------- undo
def _message_with(user_id: int, actions: list[dict]) -> int:
    from app.db import SessionLocal
    from app.models import ChatMessage, ChatThread

    with SessionLocal() as db:
        t = ChatThread(user_id=user_id, title="t")
        db.add(t)
        db.flush()
        m = ChatMessage(thread_id=t.id, user_id=user_id, role="assistant", content="Done.", meta={"actions": actions})
        db.add(m)
        db.commit()
        return m.id


def test_undo_an_assistant_action(client, categories):
    e = client.post("/api/time/entries", json={"title": "Linear algebra", "category_id": categories["Mathematics"]["id"],
                                               "started_at": iso_local(DAY, "09:00"), "ended_at": iso_local(DAY, "10:00")}).json()
    uid = client.get("/api/auth/me").json()["user"]["id"]
    mid = _message_with(uid, [{"type": "time_entry", "id": e["id"], "label": "Linear algebra"}])
    r = client.post(f"/api/ai/messages/{mid}/actions/0/undo").json()
    assert r["ok"] and r["actions"][0]["undone"] is True
    assert client.get("/api/time/entries", params={"start": DAY.isoformat(), "end": DAY.isoformat()}).json() == []
    assert client.post(f"/api/ai/messages/{mid}/actions/0/undo").status_code == 409


def test_undo_removes_appended_text_from_the_journal_file(client, env):
    from app.ai.tools import ToolContext, build_tools

    client.post("/api/journal", json={"entry_date": "2026-09-02", "body": "A day of reading."})
    uid = client.get("/api/auth/me").json()["user"]["id"]
    ctx = ToolContext(uid, None, False)
    tool = next(t for t in build_tools(ctx) if t.name == "add_to_journal")
    assert "Added" in tool.invoke({"text": "Note from the assistant.", "day": "2026-09-02"})
    path = env / "MyUniverse" / "2026" / "2026_TEST_FILE.md"
    assert "Note from the assistant." in path.read_text(encoding="utf-8")

    mid = _message_with(uid, ctx.actions)
    r = client.post(f"/api/ai/messages/{mid}/actions/0/undo").json()
    assert r["ok"]
    text = path.read_text(encoding="utf-8")
    assert "Note from the assistant." not in text and "A day of reading." in text


def test_undo_a_whole_capture(client, categories, env):
    from app.db import SessionLocal
    from app.models import CaptureDraft

    uid = client.get("/api/auth/me").json()["user"]["id"]
    with SessionLocal() as db:
        d = CaptureDraft(user_id=uid, capture_date=DAY, input_text="Studied, paid a taxi.", draft={"warnings": []})
        db.add(d)
        db.commit()
        draft_id = d.id
    body = {"draft": {
        "date": DAY.isoformat(),
        "time_entries": [{"title": "Linear algebra", "category_id": categories["Mathematics"]["id"], "start": "09:00",
                          "end": "11:00", "people": ["Somebody New"]}],
        "transactions": [{"item": "taxi", "amount": 400, "category": "transport"}],
        "people": [],
        "journal_text": "Studied, paid a taxi.",
    }}
    r = client.post(f"/api/ai/drafts/{draft_id}/commit", json=body).json()
    assert r["created"]["time_entries"] == 1 and r["records"]["people_created"]
    path = env / "MyUniverse" / "2026" / "2026_TEST_FILE.md"
    assert "paid a taxi" in path.read_text(encoding="utf-8")

    undone = client.post(f"/api/ai/drafts/{draft_id}/undo").json()
    assert undone["removed"] == {"time_entries": 1, "transactions": 1, "habit_logs": 0, "media": 0, "people": 1,
                                 "moments": 0}
    assert client.get(f"/api/ai/drafts/{draft_id}").json()["status"] == "pending"  # back in the inbox
    assert client.get("/api/money", params={"start": DAY.isoformat(), "end": DAY.isoformat()}).json() == []
    assert "paid a taxi" not in path.read_text(encoding="utf-8")
    assert client.post(f"/api/ai/drafts/{draft_id}/undo").status_code == 409
