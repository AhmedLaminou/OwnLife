"""Application settings, read from environment variables and backend/.env."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BACKEND_DIR.parent
DATA_DIR = BACKEND_DIR / "data"

# Free OpenRouter models, tried in this order (OpenRouter's `models` fallback array).
# Checked against the live catalog on 2026-10-02: all three support tool calling.
# Free variants are limited to 20 requests/minute and 50/day — 1000/day once the
# account has bought $10 of credits in total. Free endpoints come and go: if one
# disappears, replace it here or in Settings → AI.
DEFAULT_FREE_MODELS = (
    "nvidia/nemotron-3-super-120b-a12b:free,"
    "qwen/qwen3.8-27b:free,"
    "google/gemma-4-31b-it:free"
)

AiMode = Literal["cloud", "local", "cloud_then_local", "off"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    environment: Literal["development", "production", "test"] = "development"
    # Empty means the SQLite file backend/data/ownlife.db.
    database_url: str = ""
    data_dir: Path = DATA_DIR
    host: str = "127.0.0.1"
    port: int = 8000

    # --- Auth ---
    session_days: int = 30
    # Set to true only when served over HTTPS.
    cookie_secure: bool = False
    # The first account can always be created. After that, only if this is true.
    allow_registration: bool = False

    # --- Local file sync ---
    # The server only reads and writes Markdown files that live under this folder.
    import_root: Path | None = None
    # The journal file. "{year}" stands for a year, so one setting covers
    # Notes/2026/2026_JOURNAL.md, Notes/2027/2027_JOURNAL.md…
    # The other .md files in the journal's folder are synced as notes.
    journal_sync_path: Path | None = None
    # Watch the files and import changes within seconds of a save.
    file_sync: bool = True
    # Write edits made in OwnLife back to the files. Off: OwnLife only reads them.
    file_sync_writeback: bool = True
    # Previous versions kept per file in data/file-history before OwnLife writes it.
    file_history_keep: int = 50

    # --- AI ---
    # cloud: OpenRouter only. local: Ollama only. cloud_then_local: OpenRouter,
    # falling back to Ollama when offline or rate-limited. off: no LLM at all.
    ai_mode: AiMode = "cloud_then_local"
    openrouter_api_key: SecretStr | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_models: str = DEFAULT_FREE_MODELS
    # "" leaves the decision to the account's privacy settings on openrouter.ai.
    # "deny" keeps only providers that do not store prompts — free endpoints
    # usually do store them, so "deny" tends to rule them out.
    openrouter_data_collection: Literal["", "allow", "deny"] = ""
    openrouter_zdr: bool = False
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_chat_model: str = "qwen3:4b-instruct-2507-q4_K_M"
    embeddings_provider: Literal["ollama", "none"] = "ollama"
    ollama_embed_model: str = "embeddinggemma"
    llm_timeout_seconds: float = 90
    local_llm_timeout_seconds: float = 300
    # The assistant stops after this many tool rounds in a single answer.
    agent_max_tool_rounds: int = 4

    # --- Integrations ---
    activitywatch_url: str = "http://127.0.0.1:5600"
    # 0 disables background syncing; the Sync button still works.
    activitywatch_autosync_minutes: int = 0
    # Resolve YouTube channel names through the public oEmbed endpoint (no key).
    youtube_oembed: bool = True

    @field_validator("import_root", "journal_sync_path", mode="before")
    @classmethod
    def _empty_is_none(cls, v):
        # JOURNAL_SYNC_PATH= (empty) switches the sync off.
        return None if isinstance(v, str) and not v.strip() else v

    @property
    def effective_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{(self.data_dir / 'ownlife.db').as_posix()}"

    @property
    def openrouter_model_list(self) -> list[str]:
        return [m.strip() for m in self.openrouter_models.split(",") if m.strip()]

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"


@lru_cache
def get_settings() -> Settings:
    return Settings()
