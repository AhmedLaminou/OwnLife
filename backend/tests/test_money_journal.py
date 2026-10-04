"""Money read from the journal: which sentences are read, suggestions stored,
added or dismissed, and never proposed twice. The model is a fake that answers
from a table, so nothing leaves the machine."""

from datetime import timedelta

from app.config import get_settings
from app.db import SessionLocal, utcnow
from app.models import JournalEntry
from app.services import journal_money as jm
from app.services.journal_money import Payment

DAY = "2026-09-01"
BODY = (
    "I woke up at 06:10 and studied for 2 hours ;\n"
    "I took the bus , which I paid 300 FCFA to the driver ; "
    "I bought some bread of 50FCFA and tea for 50 FCFA\n"
    "Uncle Karim gives me 1000FCFA ; a man gave 1000 FCFA to the conductor\n"
    "I bought juice for 250 , 1 cake , 2 bottles of water ;"
)


def pay(item: str, amount: float, direction: str = "out", category: str = "other") -> Payment:
    return Payment(excerpt=1, item=item, amount=amount, direction=direction, category=category)


ANSWERS = {
    "paid 300 FCFA": [pay("bus", 300, category="transport")],
    "paid 400 FCFA": [pay("bus", 400, category="transport")],
    "bread": [pay("bread", 50, category="food"), pay("tea", 50, category="food")],
    "gives me 1000FCFA": [pay("gift from Uncle Karim", 1000, "in", "gift")],
    "juice": [pay("juice", 250, category="food")],
}


def fake(settings, user, profile, excerpts):  # noqa: ARG001
    """Stands for the model: each excerpt containing a known phrase gets its payments."""
    found = [(ex, p) for ex in excerpts for needle, ps in ANSWERS.items() if needle in ex[2] for p in ps]
    return found, "fake"


def _setup(client) -> int:
    assert client.post("/api/journal", json={"entry_date": DAY, "body": BODY}).status_code == 200
    return client.get("/api/auth/me").json()["user"]["id"]


def _pending(client) -> dict[str, dict]:
    return {s["item"]: s for s in client.get("/api/money/journal").json()["suggestions"]}


def test_only_sentences_about_money_are_read():
    clauses = jm.money_clauses(BODY)
    assert not any("06:10" in c for c in clauses)  # a time is not money
    assert any("paid 300 FCFA" in c for c in clauses) and any("juice for 250" in c for c in clauses)
    assert any("bread" in c for c in clauses) and not any("bus" in c and "bread" in c for c in clauses)


def test_a_scan_proposes_each_payment_once(client):
    uid = _setup(client)
    r = jm.scan(get_settings(), uid, extractor=fake)
    assert r.days == 1 and r.new == 5
    pending = _pending(client)
    assert {(k, s["amount"], s["direction"]) for k, s in pending.items()} == {
        ("bus", 300, "out"), ("bread", 50, "out"), ("tea", 50, "out"),
        ("gift from Uncle Karim", 1000, "in"), ("juice", 250, "out"),
    }
    assert "paid 300 FCFA" in pending["bus"]["quote"] and pending["bus"]["date"] == DAY
    assert client.get("/api/money/journal").json()["days_not_read"] == 0
    assert jm.scan(get_settings(), uid, extractor=fake).days == 0  # nothing changed: nothing read
    again = jm.scan(get_settings(), uid, rescan_all=True, extractor=fake)
    assert again.found == 5 and again.new == 0  # read again: nothing proposed twice


def test_added_and_dismissed_suggestions_never_come_back(client):
    uid = _setup(client)
    jm.scan(get_settings(), uid, extractor=fake)
    p = _pending(client)
    assert client.post("/api/money/journal/accept", json={"ids": [p["bus"]["id"], p["gift from Uncle Karim"]["id"]]}).json() == {"added": 2}
    assert client.post("/api/money/journal/dismiss", json={"ids": [p["tea"]["id"]]}).json() == {"dismissed": 1}
    summary = client.get("/api/money/summary", params={"start": DAY, "end": DAY}).json()
    assert summary["total_out"] == 300 and summary["total_in"] == 1000
    bus = next(t for t in client.get("/api/money", params={"start": DAY, "end": DAY}).json() if t["item"] == "bus")
    assert bus["source"] == "journal" and "paid 300 FCFA" in bus["note"]

    assert jm.scan(get_settings(), uid, rescan_all=True, extractor=fake).new == 0
    assert set(_pending(client)) == {"bread", "juice"}


def test_an_amount_entered_by_hand_is_not_proposed(client):
    client.post("/api/money", json={"date": DAY, "direction": "out", "amount": 300, "item": "Bus", "category": "transport"})
    uid = _setup(client)
    jm.scan(get_settings(), uid, extractor=fake)
    assert "bus" not in _pending(client)


def test_add_without_asking(client):
    uid = _setup(client)
    client.put("/api/money/journal/prefs", json={"journal_scan": True, "auto_add": True})
    assert jm.scan(get_settings(), uid, extractor=fake).added == 5
    assert _pending(client) == {}
    summary = client.get("/api/money/summary", params={"start": DAY, "end": DAY}).json()
    assert summary["total_out"] == 650 and summary["total_in"] == 1000


def test_an_edited_sentence_replaces_its_pending_suggestion(client):
    uid = _setup(client)
    jm.scan(get_settings(), uid, extractor=fake)
    with SessionLocal() as db:  # the bus was 400, not 300
        e = db.query(JournalEntry).filter(JournalEntry.user_id == uid).one()
        e.body = e.body.replace("paid 300 FCFA", "paid 400 FCFA")
        db.commit()
    r = jm.scan(get_settings(), uid, extractor=fake)
    assert r.days == 1 and r.new == 1
    assert _pending(client)["bus"]["amount"] == 400 and len(_pending(client)) == 5


def test_the_automatic_scan_waits_until_the_page_is_quiet(client, monkeypatch):
    uid = _setup(client)
    started: list[int] = []
    monkeypatch.setattr(jm, "effective_mode", lambda settings, profile: "cloud")  # AI is off in tests
    monkeypatch.setattr(jm, "schedule", lambda settings, user_id, rescan_all=False: started.append(user_id) or True)
    now = utcnow()
    assert jm.tick(get_settings(), now=now) == []  # just written: you may still be writing
    assert jm.tick(get_settings(), now=now + timedelta(minutes=11)) == [uid]
    with SessionLocal() as db:  # a scan was attempted then: the next one waits half an hour
        state = jm.get_state(db, uid, jm.PROVIDER)
        state.cursor = {**(state.cursor or {}), "attempted_at": (now + timedelta(minutes=11)).isoformat()}
        db.commit()
    assert jm.tick(get_settings(), now=now + timedelta(minutes=20)) == []
    assert jm.tick(get_settings(), now=now + timedelta(minutes=45)) == [uid]
    client.put("/api/money/journal/prefs", json={"journal_scan": False, "auto_add": False})
    assert jm.tick(get_settings(), now=now + timedelta(hours=2)) == []  # switched off


def test_scanning_needs_a_model(client):
    r = client.post("/api/money/journal/scan", json={})
    assert r.status_code == 400 and "AI is off" in r.json()["detail"]
    assert client.get("/api/money/journal").json()["ai_off"] is True
