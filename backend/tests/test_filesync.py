"""The two-way sync with the Markdown files, on files shaped like the real
journal: CRLF line endings, trailing spaces, no final newline, a leading space
before some headers. Every test works in its own temporary folder."""

import time
from datetime import date

import pytest

from app.services import mdfile

CRLF = "\r\n"
LINES = [
    "",
    "{X} = a private alias ;",
    "",
    "Day 1 : 31/08/2026",
    "Went to the internship.  ",
    "1) Talked with Uncle Karim about AI.",
    "",
    " Day 2 : 01/09/2026",
    "[SomeThoughts]  ",
    "1) Thinking about time.",
    "2) Second thought.",
    "",
    "Day 3 : 02/09/2026",
    "Reading Agatha Christie. ",
]
JOURNAL = CRLF.join(LINES)  # no final newline, like the real file


@pytest.fixture
def folder(env):
    f = env / "MyUniverse" / "2026"
    f.mkdir(parents=True)
    return f


@pytest.fixture
def journal(folder):
    p = folder / "2026_TEST_FILE.md"
    p.write_bytes(JOURNAL.encode("utf-8"))
    return p


def sync(client) -> dict:
    r = client.post("/api/sync/run")
    assert r.status_code == 200, r.text
    return r.json()


def day(client, n: int) -> dict:
    item = next(i for i in client.get("/api/journal").json()["items"] if i["day_number"] == n)
    return client.get(f"/api/journal/{item['id']}").json()


# ---------------------------------------------------------------- mdfile, pure
def test_unchanged_text_rewrites_to_identical_bytes():
    md = mdfile.MdText.decode(JOURNAL.encode())
    for s in mdfile.parse_layout(md.lines).sections:
        assert mdfile.day_replace(md, s, mdfile.section_text(md.lines, s)).encode() == JOURNAL.encode()


def test_an_edit_changes_only_the_edited_line():
    md = mdfile.MdText.decode(JOURNAL.encode())
    s = mdfile.parse_layout(md.lines).sections[1]
    new = mdfile.day_replace(md, s, "[SomeThoughts]\n1) Thinking about time, again.\n2) Second thought.")
    old_lines, new_lines = JOURNAL.split(CRLF), new.text.split(CRLF)
    changed = [(a, b) for a, b in zip(old_lines, new_lines) if a != b]
    assert changed == [("1) Thinking about time.", "1) Thinking about time, again.")]
    assert "[SomeThoughts]  " in new_lines  # trailing spaces of untouched lines survive
    assert not new.text.endswith(CRLF)


def test_header_lines_inside_a_day_are_refused():
    md = mdfile.MdText.decode(JOURNAL.encode())
    s = mdfile.parse_layout(md.lines).sections[0]
    with pytest.raises(mdfile.EditError):
        mdfile.day_replace(md, s, "text\nDay 9 : 05/09/2026\nmore")


# ---------------------------------------------------------------- file -> OwnLife
def test_file_changes_reach_ownlife_and_vanished_days_are_hidden_not_lost(client, journal):
    assert sync(client)["created"] == 3
    d2 = day(client, 2)
    client.patch(f"/api/journal/{d2['id']}", json={"is_private": True})

    journal.write_bytes(JOURNAL.replace("Thinking about time.", "Thinking about death.").encode())
    r = sync(client)
    assert r["updated"] == 1
    assert "death" in day(client, 2)["body"]

    # a typo in the header: Day 2 vanishes from the file -> hidden, not deleted
    journal.write_bytes(JOURNAL.replace(" Day 2 : 01/09/2026", " Dya 2 : 01/09/2026").encode())
    r = sync(client)
    assert len(r["missing"]) == 1
    assert [i["day_number"] for i in client.get("/api/journal").json()["items"]] == [3, 1]
    assert client.get("/api/sync").json()["missing"][0]["label"] == "Day 2"

    # fixed: the same page comes back, still private
    journal.write_bytes(JOURNAL.encode())
    r = sync(client)
    assert r["restored"] == 1
    back = day(client, 2)
    assert back["id"] == d2["id"] and back["is_private"] is True


# ---------------------------------------------------------------- OwnLife -> file
def test_edits_in_ownlife_are_written_minimally_into_the_file(client, journal, env):
    sync(client)
    d2 = day(client, 2)
    body = d2["body"].replace("Thinking about time.", "Thinking about time, again.")
    r = client.patch(f"/api/journal/{d2['id']}", json={"body": body, "expected_hash": d2["body_hash"]})
    assert r.status_code == 200 and r.json()["sync"]["state"] == "synced"

    raw = journal.read_bytes().decode()
    assert raw == JOURNAL.replace("1) Thinking about time.", "1) Thinking about time, again.")
    history = [p for p in (env / "file-history").rglob("*.md") if p.is_file()]
    assert history and history[0].read_bytes().decode() == JOURNAL  # the previous version is kept
    assert sync(client)["updated"] == 0  # its own write is not read back as a change


def test_a_new_page_goes_into_the_file_of_its_year(client, journal, env):
    sync(client)
    client.put("/api/profile", json={"awakening_date": "2026-08-31"})
    r = client.post("/api/journal", json={"entry_date": "2026-09-03", "body": "A new day, written in OwnLife."}).json()
    assert r["day_number"] == 4 and r["sync"]["state"] == "synced"
    raw = journal.read_bytes().decode()
    assert raw.endswith("Reading Agatha Christie. " + CRLF + CRLF + "Day 4 : 03/09/2026" + CRLF + "A new day, written in OwnLife.")

    # next year's page creates next year's file, in its own folder
    r = client.post("/api/journal", json={"entry_date": "2027-01-01", "body": "New year."}).json()
    nxt = env / "MyUniverse" / "2027" / "2027_TEST_FILE.md"
    assert nxt.exists() and "Day 124 : 01/01/2027" in nxt.read_text(encoding="utf-8")
    assert r["file_name"] == "2027_TEST_FILE.md"


def test_a_header_line_typed_in_ownlife_is_refused(client, journal):
    sync(client)
    d1 = day(client, 1)
    r = client.patch(f"/api/journal/{d1['id']}", json={"body": "ok\nDay 50 : 05/10/2026\nnot ok"})
    assert r.status_code == 422 and "day header" in r.json()["detail"]
    assert journal.read_bytes().decode() == JOURNAL


def test_a_stale_editor_cannot_overwrite_newer_text(client, journal):
    sync(client)
    d1 = day(client, 1)
    journal.write_bytes(JOURNAL.replace("Went to the internship.", "Went to the internship, early.").encode())
    sync(client)  # the change from the editor arrives first
    r = client.patch(f"/api/journal/{d1['id']}", json={"body": "Old text, typed long ago.", "expected_hash": d1["body_hash"]})
    assert r.status_code == 409
    assert "early" in journal.read_text(encoding="utf-8")


def test_changes_on_both_sides_become_a_conflict_and_nothing_is_lost(client, journal):
    sync(client)
    d3 = day(client, 3)
    # the file changes, and OwnLife writes before the watcher has seen it
    journal.write_bytes(JOURNAL.replace("Reading Agatha Christie.", "Reading Agatha Christie, chapter 4.").encode())
    r = client.patch(f"/api/journal/{d3['id']}", json={"body": "Finished the book."}).json()
    assert r["sync"]["state"] == "conflict"
    assert "chapter 4" in r["conflict_body"] and r["body"] == "Finished the book."
    assert "chapter 4" in journal.read_text(encoding="utf-8")  # the file was not overwritten
    assert client.get("/api/sync").json()["conflicts"][0]["label"] == "Day 3"

    merged = "Reading Agatha Christie, chapter 4.\nFinished the book."
    r = client.post(f"/api/journal/{d3['id']}/resolve", json={"keep": "text", "text": merged}).json()
    assert r["sync"]["state"] == "synced" and r["body"] == merged
    # the untouched line keeps its trailing space; only the new line is added
    assert journal.read_bytes().decode().endswith("chapter 4. " + CRLF + "Finished the book.")


def test_deleting_a_page_removes_it_from_the_file(client, journal):
    sync(client)
    d2 = day(client, 2)
    assert client.delete(f"/api/journal/{d2['id']}").status_code == 200
    text = journal.read_text(encoding="utf-8")
    assert "Day 2" not in text and "Day 1 : 31/08/2026" in text and "Day 3 : 02/09/2026" in text


# ---------------------------------------------------------------- notes
def test_notes_sync_both_ways(client, folder, journal, env):
    (folder / "InventionIdeas.md").write_bytes(b"A solar dryer.\r\nA cheap tutor.\r\n")
    sync(client)
    note = next(n for n in client.get("/api/notes").json() if n["title"] == "Invention Ideas")
    full = client.get(f"/api/notes/{note['id']}").json()

    r = client.patch(f"/api/notes/{note['id']}", json={"body": full["body"] + "\nA water filter.",
                                                       "expected_hash": full["body_hash"]}).json()
    assert r["sync"]["state"] == "synced"
    assert (folder / "InventionIdeas.md").read_bytes() == b"A solar dryer.\r\nA cheap tutor.\r\nA water filter.\r\n"

    (folder / "InventionIdeas.md").write_bytes(b"A solar dryer.\r\nA water filter.\r\n")
    assert sync(client)["notes_updated"] == 1
    assert client.get(f"/api/notes/{note['id']}").json()["body"] == "A solar dryer.\nA water filter."

    created = client.post("/api/notes", json={"title": "On the nature of time", "body": "Time is the wealth."}).json()
    assert created["file_name"] == "OnTheNatureOfTime.md"
    assert (folder / "OnTheNatureOfTime.md").read_text(encoding="utf-8").strip() == "Time is the wealth."

    gone = client.delete(f"/api/notes/{created['id']}").json()
    assert not (folder / "OnTheNatureOfTime.md").exists()
    assert gone["file_copy"] and "deleted" in gone["file_copy"]  # recoverable from the history


# ---------------------------------------------------------------- the watcher
def test_the_watcher_imports_a_save_within_seconds(client, journal):
    from app.config import get_settings
    from app.services.filesync import Watcher

    w = Watcher(get_settings())
    w.start()
    try:
        deadline = time.time() + 10
        while w.revision == 0 and time.time() < deadline:
            time.sleep(0.2)
        assert w.revision >= 1  # the startup sync imported the three days
        before = w.revision
        journal.write_bytes((JOURNAL + CRLF + CRLF + "Day 4 : 03/09/2026" + CRLF + "Saved in the editor.").encode())
        deadline = time.time() + 10
        while w.revision == before and time.time() < deadline:
            time.sleep(0.2)
        assert any(i["day_number"] == 4 for i in client.get("/api/journal").json()["items"])
    finally:
        w.stop()
    assert date(2026, 9, 3).isoformat() in {i["entry_date"] for i in client.get("/api/journal").json()["items"]}
