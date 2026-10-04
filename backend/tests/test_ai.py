"""The AI layer with a scripted fake model: no network, no API key, no Ollama."""

import json

import numpy as np
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from app.ai import rag
from app.ai.capture import Extraction, XHabitLog, XPerson, XTimeEntry, XTransaction
from app.ai.llm import ModelChoice
from app.db import SessionLocal


class ScriptedModel(BaseChatModel):
    """Answers with the next message of the script; records what it was sent."""

    script: list
    seen: list

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append(messages)
        return ChatResult(generations=[ChatGeneration(message=self.script.pop(0))])


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    events = []
    for block in text.strip().split("\n\n"):
        lines = block.split("\n")
        name = lines[0].removeprefix("event: ")
        data = json.loads(lines[1].removeprefix("data: "))
        events.append((name, data))
    return events


def test_chat_agent_calls_a_tool_and_stores_the_turn(client, monkeypatch):
    model = ScriptedModel(
        script=[
            AIMessage(content="", tool_calls=[{
                "name": "log_time", "id": "call_1", "type": "tool_call",
                "args": {"title": "Linear algebra", "category": "math", "start": "09:00", "end": "11:00",
                         "day": "2026-09-10", "people": ["Ibrahim"]},
            }]),
            AIMessage(content="Logged 2h00 of linear algebra with Ibrahim."),
        ],
        seen=[],
    )
    monkeypatch.setattr("app.routers.ai.chat_models",
                        lambda *a, **k: [ModelChoice("fake", "fake", "Fake model", False, model)])

    r = client.post("/api/ai/chat", json={"message": "I did linear algebra 9-11 with Ibrahim on 10/09"})
    assert r.status_code == 200
    events = _parse_sse(r.text)
    names = [e[0] for e in events]
    assert names[0] == "start" and names[-1] == "done"
    assert ("tool_call" in names) and ("tool_result" in names) and ("token" in names)
    done = events[-1][1]
    assert done["actions"][0]["type"] == "time_entry"
    assert done["answer"] == "Logged 2h00 of linear algebra with Ibrahim."

    # the system prompt carried the person's categories, so no lookup call was needed
    system = model.seen[0][0].content
    assert "Mathematics" in system and "Africa/Niamey" in system

    entries = client.get("/api/time/entries", params={"start": "2026-09-10", "end": "2026-09-10"}).json()
    assert entries[0]["title"] == "Linear algebra" and entries[0]["source"] == "ai"
    assert entries[0]["people"][0]["name"] == "Ibrahim"

    thread_id = done["thread_id"]
    msgs = client.get(f"/api/ai/threads/{thread_id}/messages").json()
    assert [m["role"] for m in msgs] == ["user", "assistant", "tool", "assistant"]
    assert msgs[-1]["meta"]["actions"][0]["type"] == "time_entry"


def test_tool_category_falls_back_to_rules_and_kind(client, categories):
    """A small local model passed the kind ("core") instead of a category name
    (seen with qwen3:4b on 2026-10-02). The tool must still land somewhere sensible."""
    from app.ai.tools import resolve_category

    client.post("/api/rules", json={"field": "title", "pattern": "linear algebra",
                                     "category_id": categories["Mathematics"]["id"]})
    user_id = client.get("/api/auth/me").json()["user"]["id"]
    with SessionLocal() as db:
        cat, how = resolve_category(db, user_id, "core", "Linear Algebra Study")
        assert cat.name == "Mathematics" and "rules" in how
        cat, how = resolve_category(db, user_id, "core", "Robotics kinematics chapter 2")
        assert cat.name == "Robotics" and "closest" in how
        cat, _ = resolve_category(db, user_id, "Physics", "anything")
        assert cat.name == "Physics"
        cat, _ = resolve_category(db, user_id, "astrology", "stars")
        assert cat is None


def test_sources_keep_only_what_the_answer_uses():
    from app.ai.tools import ToolContext

    ctx = ToolContext(user_id=1, embedder=None, for_cloud=False)
    ctx.retrieved = [
        {"chunk_id": 1, "day_number": 12, "title": "Day 12"},
        {"chunk_id": 2, "day_number": 18, "title": "Day 18"},
        {"chunk_id": 3, "day_number": None, "title": "Scale Of Life"},
    ]
    ctx.citations = [{"chunk_id": 9, "day_number": 5, "title": "Day 5"}]  # asked for by the model
    sources = ctx.sources("As you wrote on [Day 12], and in Scale Of Life…")
    assert [c["chunk_id"] for c in sources] == [9, 1, 3]
    assert ctx.sources("Today's core work: 5h50.") == [ctx.citations[0]]


def test_chat_without_any_model_explains_what_to_do(client):
    r = client.post("/api/ai/chat", json={"message": "hello"})
    events = _parse_sse(r.text)
    error = next(d for n, d in events if n == "error")
    assert "OPENROUTER_API_KEY" in error["message"]


def test_capture_draft_then_commit(client, monkeypatch):
    extraction = Extraction(
        time_entries=[
            XTimeEntry(title="Fajr prayer", category="Prayer", start="05:40", end="06:10"),
            XTimeEntry(title="Crash Course", category="Broad learning", start="10:07", end="13:15 AM"),
            XTimeEntry(title="Mystery", category="Astrology", start="14:00", end="15:00"),
            XTimeEntry(title="Late reactions", category="Reaction videos", start="23:30", end="02:00"),
        ],
        transactions=[XTransaction(item="Taxi", amount=300, category="transport"),
                      XTransaction(item="Gift from uncle", amount=1000, direction="in", counterparty="Uncle Karim")],
        habit_logs=[XHabitLog(habit="Workout", status="done"), XHabitLog(habit="Flying", status="done")],
        people=[XPerson(name="Uncle Karim", relation="family")],
        summary="Internship day.",
    )
    monkeypatch.setattr("app.routers.ai.extract", lambda *a, **k: (extraction, "Fake model"))
    draft = client.post("/api/ai/capture", json={"text": "long account of the day", "date": "2026-08-31"}).json()
    d = draft["draft"]
    assert len(d["time_entries"]) == 4
    assert d["time_entries"][1]["end"] == "13:15"
    assert d["time_entries"][2]["category_id"] is None  # unknown category flagged, not guessed
    assert d["time_entries"][3]["crosses_midnight"] is True
    assert any("Astrology" in w for w in d["warnings"])
    assert any("Flying" in w for w in d["warnings"])
    assert len(d["habit_logs"]) == 1

    d["time_entries"][2]["include"] = False  # the person unticks the doubtful line
    result = client.post(f"/api/ai/drafts/{draft['id']}/commit", json={"draft": d}).json()
    assert result["created"]["time_entries"] == 3
    assert result["created"]["transactions"] == 2
    assert result["created"]["people"] == 1
    assert client.post(f"/api/ai/drafts/{draft['id']}/commit", json={"draft": d}).status_code == 409

    entries = client.get("/api/time/entries", params={"start": "2026-08-31", "end": "2026-09-01"}).json()
    late = next(e for e in entries if e["title"] == "Late reactions")
    assert round(late["duration_seconds"]) == 2.5 * 3600


def test_capture_never_pre_ticks_a_relapse_or_overrides_computed_habits(client, monkeypatch):
    """A 4B local model once turned "a woman paid 1000 FCFA for the taxi" into a
    relapse, pre-ticked. Sensitive statuses must arrive unticked, and habits that
    are computed from the data must not receive hand-written statuses."""
    client.post("/api/habits", json={"name": "{X}-free", "kind": "quit", "is_private": True})
    extraction = Extraction(
        time_entries=[XTimeEntry(title="Taxi home", category="Commute", start="21:07", end="21:07")],
        habit_logs=[
            XHabitLog(habit="{X}-free", status="relapse"),
            XHabitLog(habit="Write the journal", status="missed"),  # evaluated from data
            XHabitLog(habit="Workout", status="missed"),
            XHabitLog(habit="Workout", status="done"),
        ],
    )
    monkeypatch.setattr("app.routers.ai.extract", lambda *a, **k: (extraction, "Fake model"))
    d = client.post("/api/ai/capture", json={"text": "a day", "date": "2026-08-31"}).json()["draft"]
    assert d["time_entries"] == []  # zero duration: left out
    logs = {(h["habit"], h["status"]): h["include"] for h in d["habit_logs"]}
    assert logs == {("{X}-free", "relapse"): False, ("Workout", "missed"): False, ("Workout", "done"): True}
    assert any("relapse" in w for w in d["warnings"])


class FakeEmbedder:
    """Deterministic bag-of-words vectors: enough to test the ranking plumbing."""

    name = "fake:bow"
    vocab = ["uncle", "karim", "ai", "robotics", "physics", "lecture", "death", "fear", "family"]

    def _vec(self, text: str) -> np.ndarray:
        t = text.lower()
        v = np.array([t.count(w) for w in self.vocab], dtype=np.float32) + 1e-3
        return v / np.linalg.norm(v)

    def embed_docs_np(self, texts, titles=None):
        return np.vstack([self._vec(t) for t in texts])

    def embed_query_np(self, text):
        return self._vec(text)


def test_hybrid_search_fuses_keyword_and_semantic(client):
    for i, body in enumerate([
        "Spoke with my uncle Karim about AI and robotics.",
        "Watched the Yale physics lecture for an hour.",
        "Thinking about death made me afraid; fear of the end.",
    ]):
        client.post("/api/journal", json={"entry_date": f"2026-09-0{i + 1}", "day_number": i + 1, "body": body})
    user_id = client.get("/api/auth/me").json()["user"]["id"]
    embedder = FakeEmbedder()
    with SessionLocal() as db:
        assert rag.embed_pending(db, user_id, embedder) == 3
        hits, mode = rag.search(db, user_id, "conversation with family about robotics", embedder, k=3)
    assert mode == "hybrid"
    assert hits[0].day_number == 1
    assert "semantic" in hits[0].via


def test_chunking_follows_numbered_thoughts():
    body = "\n".join(f"{i}) " + "word " * 120 for i in range(1, 6))
    chunks = rag.split_journal_body(body)
    assert len(chunks) >= 3
    assert all(len(c) <= rag.CHUNK_CHARS for c in chunks)
    assert chunks[0].startswith("1)")


def test_daily_review_facts_need_no_ai(client, categories):
    client.post("/api/time/entries", json={"title": "AI", "category_id": categories["AI & ML"]["id"],
                                           "started_at": "2026-09-10T08:00:00", "ended_at": "2026-09-10T12:00:00"})
    r = client.get("/api/ai/review/2026-09-10").json()
    assert r["facts"]["core_hours"] == 4.0
    assert "Core work 4.0h" in r["facts_text"]
    assert client.post("/api/ai/review/2026-09-10").status_code == 400  # no model configured


def test_status_reports_mode_and_chain(client):
    s = client.get("/api/ai/status").json()
    assert s["mode"] == "off" and s["chain"] == []
    client.put("/api/profile", json={"ai_settings": {"mode": "local"}})
    s = client.get("/api/ai/status").json()
    assert s["mode"] == "local" and s["chain"][0].startswith("Ollama")


def test_redaction_applies_before_cloud(client):
    from app.models import Profile
    from app.services.privacy import glossary_lines, redact

    client.put("/api/profile", json={
        "redactions": [{"pattern": r"chocolate\w*", "replace": "{X}"}],
        "glossary": [{"term": "{X}", "meaning": "explicit meaning", "private": True},
                     {"term": "[Sprint]", "meaning": "study style", "private": False}],
    })
    with SessionLocal() as db:
        p = db.query(Profile).first()
        assert redact("I ate CHOCOLATES again", p) == "I ate {X} again"
        cloud = "\n".join(glossary_lines(p, for_cloud=True))
        assert "explicit meaning" not in cloud and "study style" in cloud
        assert "explicit meaning" in "\n".join(glossary_lines(p, for_cloud=False))
