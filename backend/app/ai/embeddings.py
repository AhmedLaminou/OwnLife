"""Local embeddings through Ollama — the journal is indexed without leaving the machine.

Implements LangChain's `Embeddings` interface, so it can be dropped into any
LangChain vector store; OwnLife itself uses the numpy helpers below.
"""

from __future__ import annotations

import httpx
import numpy as np
from langchain_core.embeddings import Embeddings

from app.config import Settings


class OllamaEmbedder(Embeddings):
    def __init__(self, base_url: str, model: str, timeout: float = 180.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    @property
    def name(self) -> str:
        return f"ollama:{self.model}"

    # embeddinggemma and nomic were trained with task prefixes; using them
    # measurably improves retrieval. Other models get the raw text.
    def _doc(self, text: str, title: str | None = None) -> str:
        if "embeddinggemma" in self.model:
            return f"title: {title or 'none'} | text: {text}"
        if "nomic" in self.model:
            return f"search_document: {text}"
        return text

    def _query(self, text: str) -> str:
        if "embeddinggemma" in self.model:
            return f"task: search result | query: {text}"
        if "nomic" in self.model:
            return f"search_query: {text}"
        return text

    def _embed(self, inputs: list[str]) -> np.ndarray:
        r = httpx.post(
            f"{self.base_url}/api/embed",
            json={"model": self.model, "input": inputs},
            timeout=self.timeout,
        )
        r.raise_for_status()
        m = np.asarray(r.json()["embeddings"], dtype=np.float32)
        norms = np.linalg.norm(m, axis=1, keepdims=True)
        return m / np.maximum(norms, 1e-12)

    def embed_docs_np(self, texts: list[str], titles: list[str | None] | None = None) -> np.ndarray:
        titles = titles or [None] * len(texts)
        return self._embed([self._doc(t, ti) for t, ti in zip(texts, titles)])

    def embed_query_np(self, text: str) -> np.ndarray:
        return self._embed([self._query(text)])[0]

    # --- LangChain interface ---
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.embed_docs_np(texts).tolist()

    def embed_query(self, text: str) -> list[float]:
        return self.embed_query_np(text).tolist()

    def available(self) -> tuple[bool, str]:
        try:
            r = httpx.get(f"{self.base_url}/api/tags", timeout=3)
            r.raise_for_status()
        except httpx.HTTPError:
            return False, "Ollama is not running"
        names = {m.get("name", "") for m in r.json().get("models", [])}
        wanted = self.model if ":" in self.model else f"{self.model}:latest"
        if wanted in names or self.model in names:
            return True, "ok"
        return False, f"Model {self.model!r} is not pulled (ollama pull {self.model})"


def get_embedder(settings: Settings) -> OllamaEmbedder | None:
    if settings.embeddings_provider != "ollama":
        return None
    return OllamaEmbedder(settings.ollama_base_url, settings.ollama_embed_model)
