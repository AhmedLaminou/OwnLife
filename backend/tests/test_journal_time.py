"""Time blocks read from the journal: which lines are read, one draft per day in
the inbox, never re-read for nothing, blocks already in the ledger unticked. The
model is a fake answering from a table: nothing leaves the machine."""

from datetime import date, timedelta

from app.config import get_settings
from app.db import SessionLocal, utcnow
from app.models import JournalEntry
from app.services import journal_time as jt
from app.services.journal_time import Block, Met
from tests.conftest import iso_local

DAY = "2026-09-01"
BODY = (
    "1) Woke up late and thought about the week.\n"
    "2) From 09:00 to 11:30 linear algebra with Sam, then lunch.\n"
    "3) A long reflection about time, without any hour in it.\n"
    "4) Around 17-18h reaction videos."
)

ANSWERS = {
    "linear algebra": [Block(excerpt=1, title="Linear algebra", category="Mathematics", start="09:00", end="11:30",
                             people=["Sam"])],
    "reaction videos": [Block(excerpt=1, title="Reaction videos", category="Reaction videos", start="17:00",
                              end="18:00", approximate=True)],
}


def fake(settings, user, profile, categories, people, excerpts):  # noqa: ARG001
    blocks = [(ex, b) for ex in excerpts for needle, bs in ANSWERS.items() if needle in ex[2] for b in bs]
    met = [(ex, Met(excerpt=1, name="Sam", relation="friend")) for ex in excerpts if "with Sam" in ex[2]]
    return blocks, met, "fake"


def _setup(client) -> int:
    assert client.post("/api/journal", json={"entry_date": DAY, "body": BODY}).status_code == 200
    return client.get("/api/auth/me").json()["user"]["id"]


def _journal_drafts(client) -> list[dict]:
    return [d for d in client.get("/api/ai/drafts").json() if d["draft"].get("origin") == "journal"]


def test_only_lines_with_a_time_are_read():
    lines = jt.time_lines(BODY)
    assert len(lines) == 2 and "09:00" in lines[0] and "17-18h" in lines[1]
    long_line = "Today. " + "Nothing in particular here. " * 40 + "Then from 13:30 to 16:20 the lectures."
    [clause] = jt.time_lines(long_line)
    assert "13:30" in clause and len(clause) < 200  # only the clause with a time, and the one before


def test_a_scan_gives_one_draft_per_day_in_the_inbox(client, categories):
    uid = _setup(client)
    r = jt.scan(get_settings(), uid, extractor=fake)
    assert r.days == 1 and r.lines == 2 and r.drafts == 1
    [d] = _journal_drafts(client)
    body = d["draft"]
    assert d["date"] == DAY and body["summary"] == f"From your journal, Day {body['day_number']}."
    assert "09:00" in d["input_text"]
    assert [(t["title"], t["category_id"], t["start"], t["end"], t["approximate"]) for t in body["time_entries"]] == [
        ("Linear algebra", categories["Mathematics"]["id"], "09:00", "11:30", False),
        ("Reaction videos", categories["Reaction videos"]["id"], "17:00", "18:00", True),
    ]
    assert body["people"] == [{"include": True, "name": "Sam", "relation": "friend"}]
    assert "A long reflection" not in d["input_text"]  # lines without a time never leave


def test_a_page_is_read_again_only_when_its_time_lines_change(client):
    uid = _setup(client)
    jt.scan(get_settings(), uid, extractor=fake)
    with SessionLocal() as db:  # a new reflection, no time: nothing to read
        e = db.query(JournalEntry).filter(JournalEntry.user_id == uid).one()
        e.body += "\n5) Another thought, still no hour."
        db.commit()
    assert jt.scan(get_settings(), uid, extractor=fake).days == 0
    with SessionLocal() as db:  # the lecture really ended at 12:00: the pending draft is replaced
        e = db.query(JournalEntry).filter(JournalEntry.user_id == uid).one()
        e.body = e.body.replace("to 11:30", "to 12:00")
        db.commit()
    ANSWERS["linear algebra"][0].end = "12:00"
    try:
        assert jt.scan(get_settings(), uid, extractor=fake).drafts == 1
    finally:
        ANSWERS["linear algebra"][0].end = "11:30"
    [d] = _journal_drafts(client)
    assert d["draft"]["time_entries"][0]["end"] == "12:00"


def test_a_block_already_in_the_ledger_arrives_unticked(client, categories):
    client.post("/api/time/entries", json={"title": "Algebra", "category_id": categories["Mathematics"]["id"],
                                           "started_at": iso_local(date(2026, 9, 1), "09:05"),
                                           "ended_at": iso_local(date(2026, 9, 1), "11:30")})
    uid = _setup(client)
    jt.scan(get_settings(), uid, extractor=fake)
    [d] = _journal_drafts(client)
    algebra, videos = d["draft"]["time_entries"]
    assert algebra["include"] is False and videos["include"] is True
    assert any("already in the ledger" in w for w in d["draft"]["warnings"])


def test_saved_blocks_count_as_your_own_entries(client):
    uid = _setup(client)
    jt.scan(get_settings(), uid, extractor=fake)
    [d] = _journal_drafts(client)
    r = client.post(f"/api/ai/drafts/{d['id']}/commit", json={"draft": {**d["draft"], "date": d["date"]}})
    assert r.status_code == 200, r.text
    assert r.json()["created"]["time_entries"] == 2 and r.json()["created"]["people"] == 1
    entries = client.get("/api/time/entries", params={"start": DAY, "end": DAY}).json()
    assert {e["source"] for e in entries} == {"journal"}
    assert jt.scan(get_settings(), uid, rescan_all=True, extractor=fake).drafts == 1
    [again] = _journal_drafts(client)  # read again later: what is saved arrives unticked
    assert not any(t["include"] for t in again["draft"]["time_entries"])


def test_a_private_page_never_goes_to_a_cloud_model(client, monkeypatch):
    uid = _setup(client)
    client.post("/api/journal", json={"entry_date": "2026-09-02", "body": "From 21:00 to 23:00 a secret thing.",
                                      "is_private": True})
    seen: list[str] = []

    def spy(*args):
        seen.extend(text for _, _, text in args[-1])
        return fake(*args)

    monkeypatch.setattr(jt, "sends_to_cloud", lambda settings, profile: True)
    assert jt.scan(get_settings(), uid, extractor=spy).days == 2
    assert seen and not any("secret" in t for t in seen)


def test_the_automatic_reading_waits_for_a_quiet_page_and_can_be_switched_off(client, monkeypatch):
    uid = _setup(client)
    started: list[int] = []
    monkeypatch.setattr(jt, "effective_mode", lambda settings, profile: "cloud")
    monkeypatch.setattr(jt, "schedule", lambda settings, user_id, rescan_all=False: started.append(user_id) or True)
    now = utcnow()
    assert jt.tick(get_settings(), now=now) == []
    assert jt.tick(get_settings(), now=now + timedelta(minutes=11)) == [uid]
    assert client.put("/api/ai/journal-time/prefs", json={"scan": False}).status_code == 200
    with SessionLocal() as db:
        state = jt.get_state(db, uid, jt.PROVIDER)
        state.cursor = {}
        db.commit()
    assert jt.tick(get_settings(), now=now + timedelta(hours=1)) == []
    state = client.get("/api/ai/journal-time").json()
    assert state["prefs"] == {"scan": False} and state["ai_off"] is True and state["days_not_read"] == 1
