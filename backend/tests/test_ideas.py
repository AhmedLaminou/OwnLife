"""Ideas from the journal's thought sections, with a scripted model: what is
read (and never: other sections, private pages), the writer's own words kept,
essays matched, references checked, an idea placed into an essay's file and
taken back, a new essay, and nothing proposed twice."""

from datetime import date

import pytest

from app.config import get_settings
from app.db import SessionLocal
from app.models import Idea, JournalEntry
from app.services import journal_ideas
from app.services.journal_ideas import FoundIdea, Ref

CRLF = "\r\n"
LINES = [
    "Day 1 : 31/08/2026",
    "Went to the market. Bought tomatoes.",
    "[SomeThoughts]  ",
    "1) I think boredom is the mind asking for a harder problem, not for rest.",
    "2) Children copy what adults do, never what they say; school teaches the opposite.",
    "3) Ate rice at noon with [Cousin].",
    "[SomeFacts]",
    "1) The bus was late again.",
    "",
    " Day 2 : 01/09/2026",
    "[SomeTHoughts] [SomeFacts]",
    "1) A kettle that boils with sunlight could save charcoal: a parabolic mirror and a black pot.",
    "",
    "Day 3 : 02/09/2026",
    "Walked home.",
    "Idea : maybe memory is a story we rewrite each time we remember it.",
    "",
    "Day 4 : 03/09/2026",
    "[SomeThoughts]",
    "1) A secret thought that stays here.",
]


@pytest.fixture
def folder(env):
    f = env / "MyUniverse" / "2026"
    f.mkdir(parents=True)
    (f / "2026_TEST_FILE.md").write_bytes(CRLF.join(LINES).encode())
    (f / "OnBoredom.md").write_bytes(b"Boredom as a signal.\r\n")
    (f / "OnEducation.md").write_bytes(b"")
    return f


def _sync(client) -> None:
    assert client.post("/api/sync/run").status_code == 200


def _uid(client) -> int:
    return client.get("/api/auth/me").json()["user"]["id"]


def test_thought_sections_stop_at_the_next_tag_line_and_take_idea_paragraphs():
    body = "\n".join(LINES[1:9])
    [section] = journal_ideas.thought_sections(body)
    assert section.startswith("[SomeThoughts]") and "harder problem" in section and "with [Cousin]" in section
    assert "bus was late" not in section and "tomatoes" not in section
    assert journal_ideas.thought_sections("[KeyThougtsOfTheDay]\n1) Time is the only currency we spend once.")
    assert journal_ideas.thought_sections("Idea : a phone app that counts the minutes we wait for buses.\n\nOther.") == [
        "Idea : a phone app that counts the minutes we wait for buses."]
    assert journal_ideas.thought_sections("Nothing marked here, only a long day of errands and chores.") == []


def test_anchor_keeps_the_writers_own_words():
    text = "1) Children copy what adults do, never what they say; school teaches the opposite. 2) Rain."
    assert journal_ideas.anchor("Children copy what adults do, never what they say", text) == \
        "Children copy what adults do, never what they say"
    assert journal_ideas.anchor("Kids copy what adults do, not what they say", text).startswith("1) Children copy")


def fake_model(sent: list[str]):
    def extractor(settings, user, profile, notes, excerpts):
        assert {n.title for n in notes} >= {"On Boredom", "On Education"}
        out = []
        for ex in excerpts:
            text = ex[3]
            sent.append(text)
            if "boredom" in text:
                out.append((ex, FoundIdea(excerpt=1, title="Boredom calls for harder problems", domain="psychology",
                                          statement="Boredom asks for a harder problem, not for rest.",
                                          quote="I think boredom is the mind asking for a harder problem, not for rest.",
                                          essay="On Boredom", references=[
                                              Ref(author="Arthur Schopenhauer", work="Parerga and Paralipomena", why="Boredom and striving."),
                                              Ref(author="A. Nobody", work="A Book That Does Not Exist", why="Invented.")])))
                out.append((ex, FoundIdea(excerpt=1, title="Children learn by imitation", domain="society",
                                          statement="Children imitate actions, not words.",
                                          quote="Kids copy what adults do, not what they say", essay="On education")))
            if "kettle" in text:
                out.append((ex, FoundIdea(excerpt=1, title="A solar kettle", domain="invention", statement="A solar kettle.",
                                          quote="A kettle that boils with sunlight could save charcoal", new_essay="Invention Notes")))
            if "memory" in text:
                out.append((ex, FoundIdea(excerpt=1, title="Memory as a retold story", domain="philosophy",
                                          statement="Remembering rewrites the memory.",
                                          quote="maybe memory is a story we rewrite each time we remember it.",
                                          new_essay="On Memory")))
        return out, "fake · nemotron"
    return extractor


def fake_open_library(url, params=None, **kw):
    class R:
        status_code = 200

        def json(self):
            found = params["author"] == "Arthur Schopenhauer"
            return {"docs": [{"key": "/works/OL1W", "title": params["title"]}] if found else []}
    return R()


def _read(client, sent):
    return journal_ideas.scan(get_settings(), _uid(client), extractor=fake_model(sent), get=fake_open_library)


def test_ideas_are_read_matched_checked_and_never_proposed_twice(client, folder):
    _sync(client)
    with SessionLocal() as db:
        db.query(JournalEntry).filter(JournalEntry.day_number == 4).update({"is_private": True})
        db.commit()
    sent: list[str] = []
    r = _read(client, sent)
    assert (r.days, r.new) == (4, 4)  # the private page counts as read, nothing of it was sent
    assert not any("secret" in s or "tomatoes" in s or "bus was late" in s for s in sent)

    page = client.get("/api/ideas").json()
    assert page["counts"]["new"] == 4 and page["prefs"] == {"scan": False} and 0 < page["thought_share"] < 1
    ideas = {i["title"]: i for i in page["ideas"]}
    boredom = ideas["Boredom calls for harder problems"]
    assert boredom["essay"]["title"] == "On Boredom" and boredom["day_number"] == 1
    assert [(x["author"], x["verified"]) for x in boredom["references"]] == [
        ("Arthur Schopenhauer", True), ("A. Nobody", False)]
    children = ideas["Children learn by imitation"]
    assert children["essay"]["title"] == "On Education"  # "On education" is the same essay
    assert children["quote"].startswith("2) Children copy what adults do")  # the page's words, not the model's
    assert ideas["A solar kettle"]["essay"] is None and ideas["A solar kettle"]["new_essay"] == "Invention Notes"

    assert _read(client, sent).new == 0  # nothing changed: nothing read again
    client.post(f"/api/ideas/{ideas['A solar kettle']['id']}/status", json={"status": "dismissed"})
    assert journal_ideas.scan(get_settings(), _uid(client), rescan_all=True, extractor=fake_model([]),
                              get=fake_open_library).new == 0  # dismissed stays dismissed


def test_an_idea_goes_into_an_essay_and_comes_back_out(client, folder):
    _sync(client)
    _read(client, [])
    ideas = {i["title"]: i for i in client.get("/api/ideas").json()["ideas"]}
    boredom, essay = ideas["Boredom calls for harder problems"], ideas["Boredom calls for harder problems"]["essay"]
    r = client.post(f"/api/ideas/{boredom['id']}/place", json={"note_id": essay["id"]}).json()
    assert r["idea"]["status"] == "placed" and r["sync"]["state"] == "synced"
    text = (folder / "OnBoredom.md").read_text(encoding="utf-8")
    assert text.startswith("Boredom as a signal.")
    assert "## Boredom calls for harder problems" in text and "I think boredom is the mind asking" in text
    assert "*From Day 1, 31 August 2026.* See also: Arthur Schopenhauer, *Parerga and Paralipomena*." in text
    assert "A Book That Does Not Exist" not in text  # unverified references stay out of the essay
    assert client.post(f"/api/ideas/{boredom['id']}/place", json={"note_id": essay["id"]}).status_code == 409

    assert client.post(f"/api/ideas/{boredom['id']}/unplace").json()["idea"]["status"] == "new"
    assert (folder / "OnBoredom.md").read_text(encoding="utf-8").strip() == "Boredom as a signal."

    # placed again, then the essay is edited by hand: undo refuses rather than cut your text
    client.post(f"/api/ideas/{boredom['id']}/place", json={"note_id": essay["id"], "text": "Boredom wants a harder problem."})
    with open(folder / "OnBoredom.md", "a", encoding="utf-8") as f:
        f.write("\nA line written by hand.\n")
    _sync(client)
    assert client.post(f"/api/ideas/{boredom['id']}/unplace").status_code == 409

    memory = ideas["Memory as a retold story"]
    r = client.post(f"/api/ideas/{memory['id']}/place", json={"new_title": "On Memory"}).json()
    assert r["note"]["title"] == "On Memory"
    assert "## Memory as a retold story" in (folder / "OnMemory.md").read_text(encoding="utf-8")
    assert client.post(f"/api/ideas/{ideas['A solar kettle']['id']}/place", json={"new_title": "on memory"}).status_code == 409


def test_an_essay_changed_in_its_file_meanwhile_is_not_overwritten(client, folder):
    _sync(client)
    _read(client, [])
    boredom = next(i for i in client.get("/api/ideas").json()["ideas"] if i["title"].startswith("Boredom"))
    edited = "Boredom as a signal, rewritten in the editor.\r\n".encode()
    (folder / "OnBoredom.md").write_bytes(edited)  # not synced yet
    r = client.post(f"/api/ideas/{boredom['id']}/place", json={"note_id": boredom["essay"]["id"]})
    assert r.status_code == 409 and "changed in its file" in r.json()["detail"]
    assert (folder / "OnBoredom.md").read_bytes() == edited
    assert client.get("/api/ideas").json()["counts"]["placed"] == 0


def test_a_failure_halfway_keeps_what_was_read(client, folder, monkeypatch):
    _sync(client)
    monkeypatch.setattr(journal_ideas, "BATCH_CHARS", 150)  # one page per request
    calls: list[int] = []

    def flaky(settings, user, profile, notes, batch):
        calls.append(batch[0][0])
        if len(calls) == 2:
            raise RuntimeError("rate limit")
        return fake_model([])(settings, user, profile, notes, batch)

    with pytest.raises(RuntimeError):
        journal_ideas.scan(get_settings(), _uid(client), extractor=flaky, get=fake_open_library)
    assert client.get("/api/ideas").json()["counts"]["new"] == 2  # the first page's two ideas are kept
    sent: list[str] = []
    journal_ideas.scan(get_settings(), _uid(client), extractor=fake_model(sent), get=fake_open_library)
    assert sent and not any("harder problem" in s for s in sent)  # the page already read is not sent again
    assert client.get("/api/ideas").json()["counts"]["new"] == 4


def test_reading_needs_an_online_model_and_is_off_until_switched_on(client, folder):
    assert client.post("/api/ideas/scan", json={}).status_code == 400  # AI is off in the tests
    assert client.put("/api/ideas/prefs", json={"scan": True}).json() == {"scan": True}
    assert journal_ideas.tick(get_settings()) == []  # switched on, but no online model: nothing starts
    with SessionLocal() as db:
        assert db.query(Idea).count() == 0
    assert journal_ideas.section_text(
        Idea(title="T", entry_date=date(2026, 9, 5), day_number=6, refs=[{"author": "A", "work": "W", "verified": True}],
             quote="q", statement="s", domain="other", fingerprint="f"), "Words.") == \
        "## T\n\nWords.\n\n*From Day 6, 5 September 2026.* See also: A, *W*."
