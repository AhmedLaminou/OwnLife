"""People and money: who gave or received it (found, added, or — when only
close — not guessed), money linked to a person added later, gifts and moments,
merging two entries for one person, and the same through Capture."""

from datetime import date

from app.db import SessionLocal
from app.models import CaptureDraft, Transaction
from tests.conftest import iso_local

DAY = date(2026, 9, 20)


def _tx(client, amount: float, item: str, direction: str = "in", **kw) -> dict:
    r = client.post("/api/money", json={"date": DAY.isoformat(), "direction": direction, "amount": amount,
                                        "item": item, "category": "gift", **kw})
    assert r.status_code == 200, r.text
    return r.json()


def _people(client) -> dict[str, dict]:
    return {p["name"]: p for p in client.get("/api/people").json()}


def test_money_from_someone_links_them_or_adds_them(client):
    client.post("/api/people", json={"name": "Uncle Kofi", "relation": "family"})
    known = _tx(client, 1000, "gift", person="uncle  kofi")  # case and spaces aside
    assert known["person"] == "Uncle Kofi" and known["warnings"] == []

    new = _tx(client, 2000, "gift for the exams", person="Grandma Zara")
    assert new["person"] == "Grandma Zara"
    people = _people(client)
    assert people["Grandma Zara"]["money_in"] == 2000 and people["Uncle Kofi"]["money_in"] == 1000

    close = _tx(client, 500, "change", person="Kofi")  # Kofi is perhaps Uncle Kofi: not guessed
    assert close["person"] is None and close["counterparty"] == "Kofi"
    assert "Uncle Kofi" in close["warnings"][0]
    assert "Kofi" not in _people(client)

    shop = _tx(client, 300, "bread", direction="out", counterparty="the baker")  # a shop is not a person
    assert shop["person"] is None and "the baker" not in _people(client)


def test_a_new_person_takes_the_money_written_under_their_name(client):
    tx = _tx(client, 750, "gift", counterparty="Cousin Theo")
    assert tx["person"] is None
    client.post("/api/people", json={"name": "cousin theo"})
    [row] = client.get("/api/money", params={"start": DAY.isoformat(), "end": DAY.isoformat()}).json()
    assert row["person"] == "cousin theo"


def test_a_transaction_is_linked_unlinked_and_corrected(client):
    client.post("/api/people", json={"name": "Sam"})
    tx = _tx(client, 1000, "loan back", direction="in")
    r = client.patch(f"/api/money/{tx['id']}", json={"person": "Sam", "amount": 1200}).json()
    assert r["person"] == "Sam" and r["amount"] == 1200
    assert client.patch(f"/api/money/{tx['id']}", json={"person": ""}).json()["person"] is None
    sam = _people(client)["Sam"]
    assert client.patch(f"/api/money/{tx['id']}", json={"person_id": sam["id"]}).json()["person"] == "Sam"


def test_gifts_and_moments_and_the_privacy_switch(client):
    p = client.post("/api/people", json={"name": "Nadia", "relation": "friend"}).json()
    gift = client.post(f"/api/people/{p['id']}/moments",
                       json={"date": "2026-09-19", "kind": "gift_from", "text": "A book on graph theory"}).json()
    moment = client.post(f"/api/people/{p['id']}/moments", json={"text": "Told a terrifying ghost story"}).json()
    assert gift["is_private"] is False and moment["kind"] == "moment"
    full = client.get(f"/api/people/{p['id']}").json()
    assert [m["text"] for m in full["moments"]] == ["Told a terrifying ghost story", "A book on graph theory"]
    listed = _people(client)["Nadia"]
    assert listed["gifts"] == 1 and listed["moments"] == 1 and listed["last_interaction"]

    client.patch(f"/api/people/moments/{moment['id']}", json={"text": "Told a ghost story", "is_private": True})
    assert client.put("/api/people/prefs", json={"moments_private": True}).json() == {"moments_private": True}
    later = client.post(f"/api/people/{p['id']}/moments", json={"kind": "gift_to", "text": "A pen"}).json()
    assert later["is_private"] is True
    assert client.delete(f"/api/people/moments/{gift['id']}").json() == {"ok": True}
    assert len(client.get(f"/api/people/{p['id']}").json()["moments"]) == 2
    assert client.post(f"/api/people/{p['id']}/moments", json={"kind": "bribe", "text": "x"}).status_code == 422


def test_two_entries_for_one_person_are_merged(client, categories):
    a = client.post("/api/people", json={"name": "Marco", "relation": "family", "notes": "Older cousin"}).json()
    b = client.post("/api/people", json={"name": "Marco Rossi"}).json()
    client.post("/api/time/entries", json={
        "title": "Football", "category_id": categories["Friends"]["id"], "started_at": iso_local(DAY, "17:00"),
        "ended_at": iso_local(DAY, "18:00"), "person_ids": [b["id"]]})
    _tx(client, 500, "gift", person="Marco Rossi")
    client.post(f"/api/people/{b['id']}/moments", json={"text": "Lent me a bicycle"})
    r = client.post(f"/api/people/{b['id']}/merge", json={"into_id": a["id"]}).json()
    assert r["moved"] == {"time_entries": 1, "transactions": 1, "moments": 1}
    people = _people(client)
    assert "Marco Rossi" not in people and people["Marco"]["hours_together"] == 1.0
    assert people["Marco"]["money_in"] == 500 and people["Marco"]["moments"] == 1
    assert client.patch(f"/api/people/{a['id']}", json={"name": "Marco"}).status_code == 200
    c = client.post("/api/people", json={"name": "Hugo"}).json()
    assert client.patch(f"/api/people/{c['id']}", json={"name": "marco"}).status_code == 409


def test_capture_records_gifts_moments_and_money_from_people_and_undoes_them(client):
    client.post("/api/people", json={"name": "Uncle Kofi"})
    uid = client.get("/api/auth/me").json()["user"]["id"]
    with SessionLocal() as db:
        row = CaptureDraft(user_id=uid, capture_date=DAY, input_text="(test)", draft={}, model="fake")
        db.add(row)
        db.commit()
        draft_id = row.id
    body = {"date": DAY.isoformat(), "transactions": [
        {"item": "gift", "amount": 5000, "direction": "in", "category": "gift", "person": "Uncle Kofi"},
        {"item": "pocket money", "amount": 1000, "direction": "in", "category": "gift", "person": "Aunt Ines"},
    ], "moments": [
        {"person": "Aunt Ines", "kind": "gift_from", "text": "A blue notebook"},
        {"person": "Uncle Kofi", "kind": "moment", "text": "Scared me with a story about snakes", "day_offset": -1},
    ]}
    r = client.post(f"/api/ai/drafts/{draft_id}/commit", json={"draft": body}).json()
    assert r["created"]["transactions"] == 2 and r["created"]["moments"] == 2 and r["errors"] == []
    people = _people(client)
    assert people["Aunt Ines"]["money_in"] == 1000 and people["Aunt Ines"]["gifts"] == 1
    kofi = client.get(f"/api/people/{people['Uncle Kofi']['id']}").json()
    assert kofi["moments"][0]["date"] == "2026-09-19" and kofi["moments"][0]["source"] == "capture"

    undone = client.post(f"/api/ai/drafts/{draft_id}/undo").json()
    assert undone["removed"]["moments"] == 2 and undone["removed"]["people"] == 1  # Aunt Ines was new
    assert "Aunt Ines" not in _people(client) and "Uncle Kofi" in _people(client)
    with SessionLocal() as db:
        assert db.query(Transaction).count() == 0
