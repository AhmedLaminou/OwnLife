"""`python -m app.doctor [--online]` — checks everything OwnLife depends on and
says what to do about each problem. Never prints secrets."""

from __future__ import annotations

import importlib
import sqlite3
import sys
from pathlib import Path

import httpx

from app.config import PROJECT_DIR, get_settings

OK, WARN, FAIL = "OK  ", "WARN", "FAIL"
COMPILED = ["pydantic_core", "uuid_utils", "orjson", "jiter", "tiktoken", "argon2", "numpy", "httptools",
            "watchfiles", "websockets", "sqlalchemy", "yaml", "xxhash", "zstandard", "ormsgpack"]


def utf8_console() -> None:
    """The Windows console defaults to cp1252, which cannot print → or ✓."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def run(online: bool = False) -> int:
    utf8_console()
    s = get_settings()
    problems = 0

    def report(level: str, what: str, hint: str = "") -> None:
        nonlocal problems
        if level == FAIL:
            problems += 1
        print(f"[{level}] {what}" + (f"\n         → {hint}" if hint else ""))

    report(OK if sys.version_info >= (3, 11) else FAIL, f"Python {sys.version.split()[0]}")

    blocked = []
    for mod in COMPILED:
        try:
            importlib.import_module(mod)
        except Exception as e:  # noqa: BLE001
            blocked.append(f"{mod} ({e.__class__.__name__})")
    if blocked:
        report(FAIL, "Compiled modules that do not load: " + ", ".join(blocked),
               "Smart App Control blocks unsigned binaries it does not know. Pin the previous "
               "version in requirements.txt, or check with: python -c \"import <module>\"")
    else:
        report(OK, f"{len(COMPILED)} compiled modules load (Smart App Control lets them through)")

    con = sqlite3.connect(":memory:")
    try:
        con.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        report(OK, f"SQLite {sqlite3.sqlite_version} with FTS5")
    except sqlite3.OperationalError:
        report(FAIL, "SQLite has no FTS5", "Use the python.org build of Python 3.11+")
    finally:
        con.close()

    db_path = s.data_dir / "ownlife.db"
    if s.database_url:
        report(OK, "Database: DATABASE_URL is set (custom)")
    elif db_path.exists():
        report(OK, f"Database: {db_path} ({db_path.stat().st_size / 1e6:.1f} MB)")
    else:
        report(WARN, f"Database not created yet ({db_path})", "Start the app once: python -m app")

    if (PROJECT_DIR / "frontend" / "dist" / "index.html").exists():
        report(OK, "Frontend is built (served at /)")
    else:
        report(WARN, "Frontend not built", "cd frontend; npm install; npm run build   (or npm run dev)")

    # --- Ollama: embeddings (search) and offline chat
    try:
        names = {m["name"] for m in httpx.get(f"{s.ollama_base_url}/api/tags", timeout=3).json().get("models", [])}
        report(OK, f"Ollama is running at {s.ollama_base_url}")
        for label, model in (("embeddings", s.ollama_embed_model), ("offline chat", s.ollama_chat_model)):
            present = model in names or f"{model}:latest" in names
            report(OK if present else WARN, f"Ollama model for {label}: {model}",
                   "" if present else f"ollama pull {model}")
    except (httpx.HTTPError, ValueError):
        report(WARN, "Ollama is not running",
               "Search falls back to keywords and the offline AI mode is unavailable. Start Ollama.")

    # --- OpenRouter
    key = s.openrouter_api_key.get_secret_value() if s.openrouter_api_key else ""
    if not key:
        report(WARN, "No OPENROUTER_API_KEY in backend/.env", "Cloud AI disabled; local mode still works.")
    else:
        report(OK, f"OPENROUTER_API_KEY is set ({len(key)} characters, not shown)")
    if online:
        try:
            catalog = {m["id"] for m in httpx.get("https://openrouter.ai/api/v1/models", timeout=20).json()["data"]}
            for m in s.openrouter_model_list:
                report(OK if m in catalog else FAIL, f"OpenRouter model {m}",
                       "" if m in catalog else "No longer in the catalog: replace it in OPENROUTER_MODELS")
        except (httpx.HTTPError, ValueError, KeyError):
            report(WARN, "Could not reach openrouter.ai", "Offline? The local fallback will be used.")
        if key:
            try:
                r = httpx.get("https://openrouter.ai/api/v1/key", headers={"Authorization": f"Bearer {key}"}, timeout=20)
                if r.status_code == 200:
                    info = r.json().get("data", {})
                    report(OK, f"OpenRouter key accepted (free tier: {info.get('is_free_tier')}, "
                               f"usage: {info.get('usage')})")
                else:
                    report(FAIL, f"OpenRouter rejected the key (HTTP {r.status_code})", "Create a new key at openrouter.ai/keys")
            except httpx.HTTPError:
                report(WARN, "Could not check the key (network)")

    # --- ActivityWatch
    try:
        info = httpx.get(f"{s.activitywatch_url}/api/0/info", timeout=3).json()
        report(OK, f"ActivityWatch {info.get('version', '')} on {info.get('hostname', '?')}")
        buckets = httpx.get(f"{s.activitywatch_url}/api/0/buckets/", timeout=3).json()
        if not any(b.startswith("aw-watcher-web") for b in buckets):
            report(WARN, "ActivityWatch has no browser watcher",
                   "Install the 'ActivityWatch Web Watcher' extension to get per-video and per-site minutes.")
    except (httpx.HTTPError, ValueError):
        report(WARN, "ActivityWatch is not running", "Optional: activitywatch.net, for automatic time tracking.")

    # --- Your files: the two-way sync
    from app.services import notify
    from app.services.filesync import SyncConfig, SyncError

    cfg = SyncConfig.from_settings(s)
    if cfg is None:
        report(WARN, "JOURNAL_SYNC_PATH or IMPORT_ROOT not set", "Set both in backend/.env to sync the journal and its notes.")
    else:
        try:
            cfg.check()
            files = cfg.journal_files()
            report(OK if files else FAIL, "Journal file(s): " + (", ".join(str(f) for f in files) or "none found"),
                   "" if files else f"Nothing matches {cfg.pattern}: fix JOURNAL_SYNC_PATH in backend/.env")
            mode = ("watched live" if s.file_sync else "not watched (FILE_SYNC=false)") + (
                ", edits written back" if s.file_sync_writeback else ", read-only (FILE_SYNC_WRITEBACK=false)")
            report(OK, f"File sync: {mode}; previous versions in {cfg.history_dir}")
        except SyncError as e:
            report(FAIL, str(e), "JOURNAL_SYNC_PATH must be inside IMPORT_ROOT")

    # --- Notifications (the evening reminders)
    if notify.available() and Path(notify._powershell()).exists():
        report(OK, "Windows notifications: through PowerShell 5.1 (test it in Settings → Rituals)")
    else:
        report(WARN, "Windows notifications unavailable", "Reminders need Windows PowerShell 5.1 (powershell.exe).")

    print("\nAll good." if problems == 0 else f"\n{problems} problem(s) to fix.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(run(online="--online" in sys.argv))
