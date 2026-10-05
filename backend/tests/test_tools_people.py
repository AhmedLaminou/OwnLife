"""The assistant's tools for people, money line by line, the library, notes,
ideas and rules — run directly (no model): what they answer, what a cloud model
never sees, and that each change can be undone."""

from datetime import date

from app.db import SessionLocal
from app.models import ClassificationRule, Idea, MediaItem, Note, PersonMoment, TimeEntry, Transaction
from tests.conftest import iso_local
from tests.test_devices_undo import _message_with
from tests.test_tools import _tools

DAY = date(2026, 9, 20)


def _undo(client, uid: int, actions: list[dict], index: int) -> dict:
    mid = _message_with(uid, actions)
    r = client.post(f"/api/ai/messages/{mid}/actions/{index}/undo")
    assert r.status_code == 200, r.text
    return r.json()


def test_money_from_a_person_their_page_and_a_private_moment(client):
    client.post("/api/people", json={"name": "Uncle Kofi", "relation": "family"})
    uid, ctx, tools = _tools(client, for_cloud=True)
    out = tools["log_expense"].invoke({"item": "gift", "amount": 5000, "direction": "in", "category": "gift",
                                       "day": DAY.isoformat(), "person": "uncle kofi"})
    assert "Uncle Kofi" in out and "new in People" not in out
    out = tools["log_expense"].invoke({"item": "pocket money", "amount": 1000, "direction": "in",
                                       "day": DAY.isoformat(), "person": "Aunt Ines"})
    assert "Aunt Ines (new in People)" in out
    assert "may be" in tools["log_expense"].invoke({"item": "change", "amount": 100, "direction": "in", "person": "Kofi"})

    assert "Added to Uncle Kofi's page" in tools["add_person_moment"].invoke(
        {"person": "Uncle Kofi", "text": "Gave me a watch", "kind": "gift_from", "day": DAY.isoformat()})
    with SessionLocal() as db:
        db.add(PersonMoment(user_id=uid, person_id=db.query(PersonMoment).one().person_id, occurred_on=DAY,
                            kind="moment", text="A secret he told me", is_private=True))
        db.commit()
    page = tools["get_person"].invoke({"name": "Uncle Kofi"})
    assert "received 5,000" in page and "gave you: Gave me a watch" in page
    assert "secret" not in page and "(private)" in page  # a cloud model sees that it exists, not what it says
    _, _, local = _tools(client, for_cloud=False)
    assert "A secret he told me" in local["get_person"].invoke({"name": "Uncle Kofi"})
    everyone = tools["get_person"].invoke({})
    assert "Aunt Ines" in everyone and "Uncle Kofi (family), received 5,000" in everyone

    # undo the money from the new person: she goes too, as nothing else points at her
    assert _undo(client, uid, ctx.actions, 1)["ok"]
    assert "Aunt Ines" not in {p["name"] for p in client.get("/api/people").json()}


def test_list_correct_and_delete_transactions_with_undo(client):
    for item, amount in (("dried meat", 10000), ("meat", 10000), ("bread", 250)):
        client.post("/api/money", json={"date": DAY.isoformat(), "amount": amount, "item": item, "category": "food"})
    uid, ctx, tools = _tools(client)
    listing = tools["list_transactions"].invoke({"start_date": DAY.isoformat()})
    assert listing.startswith("Sunday 20/09/2026:") and listing.count("\n- #") == 3
    with SessionLocal() as db:
        ids = {t.item: t.id for t in db.query(Transaction)}

    assert "Corrected" in tools["update_transaction"].invoke({"transaction_id": ids["bread"], "amount": 300,
                                                              "person": "Grandma Zara"})
    assert "Deleted meat 10,000" in tools["delete_transaction"].invoke({"transaction_id": ids["meat"]})
    with SessionLocal() as db:
        bread = db.get(Transaction, ids["bread"])
        assert bread.amount_minor == 300 and bread.person.name == "Grandma Zara"
        assert db.get(Transaction, ids["meat"]) is None

    _undo(client, uid, ctx.actions, 1)  # the deletion
    _undo(client, uid, ctx.actions, 0)  # the correction (and the person it added)
    with SessionLocal() as db:
        assert {(t.item, t.amount_minor, t.person_id) for t in db.query(Transaction)} == {
            ("dried meat", 10000, None), ("meat", 10000, None), ("bread", 250, None)}
    assert client.get("/api/people").json() == []
    assert "No transaction #999" in tools["delete_transaction"].invoke({"transaction_id": 999})


def test_the_library_read_and_updated_with_undo(client):
    uid, ctx, tools = _tools(client)
    assert "Nothing in the library yet" in tools["list_library"].invoke({})
    out = tools["update_library"].invoke({"title": "Introduction to Algorithms", "kind": "book", "progress": 120,
                                          "total": 1300, "unit": "pages", "creator": "Cormen"})
    assert out.startswith("Added Introduction to Algorithms [book]: in_progress, 120/1300 pages")
    assert "Updated" in tools["update_library"].invoke({"title": "algorithms", "progress": 180})
    assert "180/1300 pages" in tools["list_library"].invoke({"kind": "book"})
    _undo(client, uid, ctx.actions, 1)
    with SessionLocal() as db:
        assert db.query(MediaItem).one().progress_current == 120
    _undo(client, uid, ctx.actions, 0)
    with SessionLocal() as db:
        assert db.query(MediaItem).count() == 0


def test_notes_are_read_in_parts_and_private_ones_stay_here(client):
    uid, _, tools = _tools(client, for_cloud=True)
    with SessionLocal() as db:
        db.add_all([Note(user_id=uid, title="On Time", kind="essay", body="Time is " + "long " * 2000),
                    Note(user_id=uid, title="Diary Of Doubts", kind="essay", body="Very personal.", is_private=True),
                    Note(user_id=uid, title="On Silence", kind="essay", body="")])
        db.commit()
    listing = tools["read_note"].invoke({})
    assert "On Time (essay, 2002 words)" in listing and "Diary Of Doubts (essay, 2 words) — private" in listing
    first = tools["read_note"].invoke({"title": "on time"})
    assert first.startswith("“On Time” (essay), part 1 of 2") and "Time is long" in first
    assert "part 2 of 2" in tools["read_note"].invoke({"title": "On Time", "part": 2})
    assert "is private" in tools["read_note"].invoke({"title": "Diary"})
    assert "empty so far" in tools["read_note"].invoke({"title": "On Silence"})


def test_ideas_are_listed_by_words_and_domain(client):
    uid, _, tools = _tools(client)
    with SessionLocal() as db:
        essay = Note(user_id=uid, title="On Time", kind="essay", body="")
        db.add(essay)
        db.flush()
        db.add_all([
            Idea(user_id=uid, entry_date=DAY, day_number=21, title="Time as a resource", statement="Time is spent once.",
                 quote="time is spent once", domain="philosophy", note_id=essay.id, fingerprint="a", refs=[]),
            Idea(user_id=uid, entry_date=DAY, day_number=21, title="A solar kettle", statement="A kettle on sunlight.",
                 quote="a kettle", domain="invention", new_essay="Invention Notes", fingerprint="b", refs=[]),
            Idea(user_id=uid, entry_date=DAY, day_number=21, title="Dismissed one", statement="x", quote="x",
                 domain="other", status="dismissed", fingerprint="c", refs=[]),
        ])
        db.commit()
    out = tools["list_ideas"].invoke({})
    assert "Time as a resource (philosophy; for On Time)" in out and "Dismissed" not in out
    assert "new essay “Invention Notes”" in tools["list_ideas"].invoke({"domain": "invention"})
    assert tools["list_ideas"].invoke({"query": "kettle"}).count("\n") == 0


def test_a_rule_from_the_assistant_refiles_and_undoes(client, categories):
    uid, ctx, tools = _tools(client)
    with SessionLocal() as db:
        db.add(TimeEntry(user_id=uid, title="Lecture 3 — SomeMathChannel - YouTube - Google Chrome",
                         started_at=iso_dt("10:00"), ended_at=iso_dt("11:00"), source="window",
                         meta={"apps": {"chrome.exe": 3600}, "titles": ["Lecture 3 — SomeMathChannel - YouTube"]}))
        db.commit()
    out = tools["add_rule"].invoke({"pattern": "SomeMathChannel", "category": "Mathematics", "field": "title"})
    assert "Rule added: title “SomeMathChannel” → Mathematics" in out
    with SessionLocal() as db:
        assert db.query(TimeEntry).one().category.name == "Mathematics"
    _undo(client, uid, ctx.actions, 0)
    with SessionLocal() as db:
        assert db.query(ClassificationRule).count() == 0 and db.query(TimeEntry).one().category_id is None
    assert "Unknown category" in tools["add_rule"].invoke({"pattern": "x", "category": "Astrology"})


def test_a_small_local_model_gets_the_short_list_of_tools(client):
    from app.ai.tools import LOCAL_TOOLS

    _, _, tools = _tools(client)
    assert set(LOCAL_TOOLS) <= set(tools) and len(tools) >= 31
    assert {"get_person", "read_note", "add_rule"}.isdisjoint(LOCAL_TOOLS)


def iso_dt(hhmm: str):
    from datetime import datetime

    return datetime.fromisoformat(iso_local(DAY, hhmm))
