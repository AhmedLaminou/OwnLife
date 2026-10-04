from pathlib import Path

from app.services.journal_import import parse_virtual_memory

SAMPLE = """Intro line about the file.

{X} = a private alias ;
[BigVision] = the roadmap :

Day 1 : 31/08/2026
Went to the internship. [SomeFacts]
1) Talked with Uncle Karim about AI.

Day 2 : 01/09/2026
[SomeThought]
1) Thinking about time.

 Day 3 : 02-09-2026
[SomeThoughts] Reading Agatha Christie.

Day 28 : 27/009/2026
[SomeThoughts] A typo in the month did not stop it.
"""


def _no_file_sync(monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("JOURNAL_SYNC_PATH", "")
    get_settings.cache_clear()


def test_parser_handles_the_virtual_memory_format():
    p = parse_virtual_memory(SAMPLE)
    assert [d.day_number for d in p.days] == [1, 2, 3, 28]
    assert str(p.days[-1].entry_date) == "2026-09-27"
    assert p.glossary[0] == {"term": "{X}", "meaning": "a private alias", "private": True}
    assert p.glossary[1]["private"] is False
    # [SomeThought] and [SomeThoughts] are folded into one tag
    tags = {t for d in p.days for t in d.tags}
    assert "[SomeThought]" not in tags and "[SomeThoughts]" in tags


def test_one_way_import_is_idempotent_and_respects_in_app_edits(client, monkeypatch):
    _no_file_sync(monkeypatch)  # the upload import is for people without a synced file
    first = client.post("/api/journal/import-text", json={"text": SAMPLE}).json()
    assert first["created"] == 4 and first["glossary_added"] == 2
    again = client.post("/api/journal/import-text", json={"text": SAMPLE}).json()
    assert again["created"] == 0 and again["unchanged"] == 4

    items = client.get("/api/journal").json()["items"]
    day1 = next(i for i in items if i["day_number"] == 1)
    client.patch(f"/api/journal/{day1['id']}", json={"body": "Edited inside OwnLife."})

    changed = SAMPLE.replace("Went to the internship.", "Went to the internship early.")
    changed = changed.replace("Thinking about time.", "Thinking about time, again.")
    report = client.post("/api/journal/import-text", json={"text": changed}).json()
    assert report["updated"] == 1  # Day 2
    assert len(report["conflicts"]) == 1  # Day 1 was edited in the app: kept
    assert client.get(f"/api/journal/{day1['id']}").json()["body"] == "Edited inside OwnLife."


def test_one_way_import_is_refused_while_the_file_is_synced(client):
    r = client.post("/api/journal/import-text", json={"text": SAMPLE})
    assert r.status_code == 400 and "synced live" in r.json()["detail"]


def test_keyword_search_without_embeddings(client, env):
    journal = env / "MyUniverse" / "2026" / "2026_TEST_FILE.md"
    journal.parent.mkdir(parents=True)
    journal.write_text(SAMPLE, encoding="utf-8")
    client.post("/api/sync/run")
    r = client.get("/api/search", params={"q": "karim"}).json()
    assert r["mode"] == "keyword"
    assert r["hits"] and r["hits"][0]["day_number"] == 1


def test_sync_reads_the_file_and_the_notes_beside_it(client, env):
    folder = env / "MyUniverse" / "2026"
    folder.mkdir(parents=True)
    (folder / "2026_TEST_FILE.md").write_text(SAMPLE, encoding="utf-8")
    (folder / "ReadingNotes.md").write_text("Notes on a book.", encoding="utf-8")
    (folder / "GardenPlans.md").write_text("", encoding="utf-8")  # a placeholder: listed, to be written

    r = client.post("/api/sync/run").json()
    assert r["created"] == 4 and r["notes_created"] == 2 and r["glossary_added"] == 2
    titles = sorted(n["title"] for n in client.get("/api/notes").json())
    assert titles == ["Garden Plans", "Reading Notes"]
    again = client.post("/api/sync/run").json()
    assert again["created"] == 0 and again["updated"] == 0 and again["unchanged"] == 4


def test_sync_refuses_files_outside_import_root(client, monkeypatch, tmp_path_factory):
    from app.config import get_settings

    outside = Path(tmp_path_factory.mktemp("elsewhere")) / "x.md"
    outside.write_text(SAMPLE, encoding="utf-8")
    monkeypatch.setenv("JOURNAL_SYNC_PATH", str(outside))
    get_settings.cache_clear()
    assert client.post("/api/sync/run").status_code == 403


def test_journal_crud_and_tags(client, env):
    e = client.post("/api/journal", json={"entry_date": "2026-10-02", "body": "Today [Sprint] started."}).json()
    assert e["tags"] == ["[Sprint]"]
    assert e["sync"]["state"] == "synced"  # written into the test's own yearly file
    assert client.get("/api/journal/tags").json() == [{"tag": "[Sprint]", "count": 1}]
    assert client.delete(f"/api/journal/{e['id']}").json() == {"ok": True}
    assert client.get("/api/search", params={"q": "Sprint"}).json()["hits"] == []
    text = (env / "MyUniverse" / "2026" / "2026_TEST_FILE.md").read_text(encoding="utf-8")
    assert "Sprint" not in text
