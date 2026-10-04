"""Every test runs against a fresh database in a temporary folder, with a
throwaway account. Nothing touches backend/data, the real account, OpenRouter
or Ollama: AI is off unless a test injects a fake model."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings

HEADERS = {"X-OwnLife": "1"}
PASSWORD = "throwaway-password-123"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    monkeypatch.setenv("EMBEDDINGS_PROVIDER", "none")
    monkeypatch.setenv("AI_MODE", "off")
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    monkeypatch.setenv("IMPORT_ROOT", str(tmp_path))
    # Never the real journal: a yearly journal inside this test's own folder.
    monkeypatch.setenv("JOURNAL_SYNC_PATH", str(tmp_path / "MyUniverse" / "{year}" / "{year}_TEST_FILE.md"))
    monkeypatch.setenv("YOUTUBE_OEMBED", "false")
    monkeypatch.setenv("BACKUP_MIRROR_DIR", "")  # never a real stick or cloud folder from backend/.env
    get_settings.cache_clear()
    s = get_settings()
    assert str(s.journal_sync_path).startswith(str(tmp_path)) and str(s.import_root) == str(tmp_path)
    yield tmp_path
    get_settings.cache_clear()


@pytest.fixture
def app(env):
    from app.main import create_app
    from app.security import login_throttle

    login_throttle._failures.clear()  # the throttle is process-wide
    return create_app()


@pytest.fixture
def anon(app):
    with TestClient(app) as c:
        c.headers.update(HEADERS)
        yield c


@pytest.fixture
def client(anon):
    r = anon.post(
        "/api/auth/setup",
        json={
            "email": "throwaway@example.test",
            "display_name": "Test Person",
            "password": PASSWORD,
            "birth_date": "2001-06-15",
            "timezone": "Africa/Niamey",
        },
    )
    assert r.status_code == 200, r.text
    return anon


@pytest.fixture
def categories(client) -> dict[str, dict]:
    return {c["name"]: c for c in client.get("/api/categories").json()}


def iso_local(d: date, hhmm: str, offset_hours: int = 1) -> str:
    """A local Niamey time (UTC+1) as an ISO string with offset."""
    h, m = map(int, hhmm.split(":"))
    return datetime(d.year, d.month, d.day, h, m, tzinfo=timezone(timedelta(hours=offset_hours))).isoformat()


def niamey_today() -> date:
    return datetime.now(timezone(timedelta(hours=1))).date()
