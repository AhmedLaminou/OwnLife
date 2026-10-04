"""Retrieval over the written memory: chunking, indexing, hybrid search.

Search combines two rankings with Reciprocal Rank Fusion (RRF):
- keyword: SQLite FTS5 with BM25 — exact names, places, rare words ("Karim");
- semantic: cosine similarity between local embeddings — meaning without the
  same words ("when did I feel like giving up?").
RRF needs no score calibration between the two: each list contributes
1 / (60 + rank). For a few thousand chunks, a brute-force numpy dot product is
faster than any vector database would be to set up.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field

import numpy as np
from langchain_text_splitters import RecursiveCharacterTextSplitter
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.ai.embeddings import OllamaEmbedder
from app.models import Chunk, JournalEntry, Note

CHUNK_CHARS = 1200
RRF_K = 60
_ITEM_START = re.compile(r"^\s*(?:\d{1,2}\s*\)|//|-{2,}\s*>|Note\s*:|Idea\s*:|Question\s*:)", re.I)
_splitter = RecursiveCharacterTextSplitter(chunk_size=CHUNK_CHARS, chunk_overlap=120)

_STOP = set(
    "the a an and or of to in on at for with is are was were be been it this that i me my you "
    "your he she we they them what when why how who did do does have has had not no so as by "
    "from about into than then there their le la les de des du un une et ou en au aux que qui "
    "est pour pas sur avec dans".split()
)


# ---------------------------------------------------------------- chunking
def split_journal_body(body: str) -> list[str]:
    """Cut along the author's own structure — paragraphs and numbered thoughts
    ("1)", "//", "-->") — then pack those pieces into chunks of ~1200 characters."""
    blocks: list[str] = []
    cur: list[str] = []
    for line in body.split("\n"):
        if not line.strip():
            if cur:
                blocks.append("\n".join(cur))
                cur = []
            continue
        if _ITEM_START.match(line) and cur:
            blocks.append("\n".join(cur))
            cur = []
        cur.append(line.rstrip())
    if cur:
        blocks.append("\n".join(cur))

    chunks: list[str] = []
    buf = ""
    for b in blocks:
        b = b.strip()
        if not b:
            continue
        if len(b) > CHUNK_CHARS:
            if buf:
                chunks.append(buf)
                buf = ""
            chunks.extend(_splitter.split_text(b))
        elif not buf:
            buf = b
        elif len(buf) + len(b) + 2 <= CHUNK_CHARS:
            buf = f"{buf}\n\n{b}"
        else:
            chunks.append(buf)
            buf = b
    if buf:
        chunks.append(buf)
    return chunks


def journal_title(e: JournalEntry) -> str:
    d = e.entry_date.strftime("%d/%m/%Y")
    return f"Day {e.day_number} — {d}" if e.day_number else d


# ---------------------------------------------------------------- indexing
_index_lock = threading.Lock()
_index_version = 0


@dataclass
class _VectorIndex:
    version: int
    model: str
    ids: np.ndarray
    matrix: np.ndarray
    private: np.ndarray


_vector_cache: dict[int, _VectorIndex] = {}


def _bump() -> None:
    global _index_version
    with _index_lock:
        _index_version += 1


def delete_source_chunks(db: Session, user_id: int, source_type: str, source_id: int) -> None:
    db.execute(
        delete(Chunk).where(
            Chunk.user_id == user_id, Chunk.source_type == source_type, Chunk.source_id == source_id
        )
    )
    _bump()


def index_journal_entry(db: Session, e: JournalEntry) -> int:
    delete_source_chunks(db, e.user_id, "journal", e.id)
    title = journal_title(e)
    pieces = split_journal_body(e.body or "")
    for i, piece in enumerate(pieces):
        db.add(
            Chunk(
                user_id=e.user_id,
                source_type="journal",
                source_id=e.id,
                chunk_index=i,
                text=piece,
                meta={"title": title, "date": e.entry_date.isoformat(), "day_number": e.day_number},
                is_private=e.is_private,
            )
        )
    db.flush()
    return len(pieces)


def index_note(db: Session, n: Note) -> int:
    delete_source_chunks(db, n.user_id, "note", n.id)
    pieces = _splitter.split_text(n.body or "") if n.body else []
    for i, piece in enumerate(pieces):
        db.add(
            Chunk(
                user_id=n.user_id,
                source_type="note",
                source_id=n.id,
                chunk_index=i,
                text=piece,
                meta={"title": n.title, "date": None, "kind": n.kind},
                is_private=n.is_private,
            )
        )
    db.flush()
    return len(pieces)


def reindex_all(db: Session, user_id: int) -> int:
    total = 0
    for e in db.scalars(select(JournalEntry).where(JournalEntry.user_id == user_id, JournalEntry.visible())):
        total += index_journal_entry(db, e)
    for n in db.scalars(select(Note).where(Note.user_id == user_id, Note.visible())):
        total += index_note(db, n)
    return total


def embed_pending(
    db: Session, user_id: int, embedder: OllamaEmbedder, batch: int = 16, on_progress=None
) -> int:
    """Embeds chunks that have no vector yet (or one from another model)."""
    todo = db.scalars(
        select(Chunk)
        .where(
            Chunk.user_id == user_id,
            (Chunk.embedding.is_(None)) | (Chunk.embedding_model != embedder.name),
        )
        .order_by(Chunk.id)
    ).all()
    done = 0
    for i in range(0, len(todo), batch):
        part = todo[i : i + batch]
        vectors = embedder.embed_docs_np([c.text for c in part], [c.meta.get("title") for c in part])
        for c, v in zip(part, vectors):
            c.embedding = v.astype(np.float32).tobytes()
            c.embedding_model = embedder.name
        db.commit()
        done += len(part)
        if on_progress:
            on_progress(done, len(todo))
    if done:
        _bump()
    return done


# ---------------------------------------------------------------- search
@dataclass
class Hit:
    chunk_id: int
    source_type: str
    source_id: int
    title: str
    date: str | None
    day_number: int | None
    text: str
    score: float
    via: list[str] = field(default_factory=list)
    similarity: float | None = None

    def as_dict(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "source_type": self.source_type,
            "source_id": self.source_id,
            "title": self.title,
            "date": self.date,
            "day_number": self.day_number,
            "text": self.text,
            "score": round(self.score, 5),
            "via": self.via,
            "similarity": None if self.similarity is None else round(self.similarity, 3),
        }

    def citation(self) -> str:
        return f"[Day {self.day_number}]" if self.day_number else f"[{self.title}]"


def fts_query(q: str) -> str | None:
    words = [w for w in re.findall(r"\w+", q.casefold()) if len(w) >= 2 and w not in _STOP]
    uniq = list(dict.fromkeys(words))[:16]
    return " OR ".join(f'"{w}"' for w in uniq) if uniq else None


def _keyword_ranks(db: Session, user_id: int, q: str, include_private: bool, limit: int = 50) -> list[int]:
    match = fts_query(q)
    if not match:
        return []
    sql = (
        "SELECT c.id FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid "
        "WHERE chunks_fts MATCH :m AND c.user_id = :u "
        + ("" if include_private else "AND c.is_private = 0 ")
        + "ORDER BY bm25(chunks_fts) LIMIT :lim"
    )
    return [row[0] for row in db.execute(text(sql), {"m": match, "u": user_id, "lim": limit})]


def _vectors(db: Session, user_id: int, model: str) -> _VectorIndex:
    cached = _vector_cache.get(user_id)
    if cached and cached.version == _index_version and cached.model == model:
        return cached
    rows = db.execute(
        select(Chunk.id, Chunk.embedding, Chunk.is_private).where(
            Chunk.user_id == user_id, Chunk.embedding_model == model, Chunk.embedding.is_not(None)
        )
    ).all()
    if rows:
        index = _VectorIndex(
            _index_version,
            model,
            np.array([r[0] for r in rows], dtype=np.int64),
            np.vstack([np.frombuffer(r[1], dtype=np.float32) for r in rows]),
            np.array([bool(r[2]) for r in rows], dtype=bool),
        )
    else:
        index = _VectorIndex(
            _index_version, model, np.zeros(0, np.int64), np.zeros((0, 1), np.float32), np.zeros(0, bool)
        )
    _vector_cache[user_id] = index
    return index


def _semantic_ranks(
    db: Session, user_id: int, q: str, embedder: OllamaEmbedder, include_private: bool, limit: int = 50
) -> list[tuple[int, float]]:
    index = _vectors(db, user_id, embedder.name)
    if len(index.ids) == 0:
        return []
    sims = index.matrix @ embedder.embed_query_np(q)
    if not include_private:
        sims = np.where(index.private, -np.inf, sims)
    order = np.argsort(-sims)[:limit]
    return [(int(index.ids[i]), float(sims[i])) for i in order if np.isfinite(sims[i])]


def search(
    db: Session,
    user_id: int,
    q: str,
    embedder: OllamaEmbedder | None = None,
    k: int = 8,
    include_private: bool = False,
) -> tuple[list[Hit], str]:
    """Returns (hits, mode): mode is "keyword" when semantic search was unavailable."""
    q = (q or "").strip()
    if not q:
        return [], "none"
    keyword = _keyword_ranks(db, user_id, q, include_private)
    semantic: list[tuple[int, float]] = []
    semantic_error = False
    if embedder is not None:
        try:
            semantic = _semantic_ranks(db, user_id, q, embedder, include_private)
        except Exception:  # Ollama down: keyword search still answers
            semantic_error = True

    scores: dict[int, float] = {}
    via: dict[int, list[str]] = {}
    sims: dict[int, float] = {}
    for rank, cid in enumerate(keyword):
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (RRF_K + rank + 1)
        via.setdefault(cid, []).append("keyword")
    for rank, (cid, sim) in enumerate(semantic):
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (RRF_K + rank + 1)
        via.setdefault(cid, []).append("semantic")
        sims[cid] = sim
    top = sorted(scores, key=lambda c: -scores[c])[:k]
    chunks = {c.id: c for c in db.scalars(select(Chunk).where(Chunk.id.in_(top)))} if top else {}
    hits = [
        Hit(
            chunk_id=cid,
            source_type=chunks[cid].source_type,
            source_id=chunks[cid].source_id,
            title=chunks[cid].meta.get("title") or "",
            date=chunks[cid].meta.get("date"),
            day_number=chunks[cid].meta.get("day_number"),
            text=chunks[cid].text,
            score=scores[cid],
            via=via[cid],
            similarity=sims.get(cid),
        )
        for cid in top
        if cid in chunks
    ]
    mode = "hybrid" if embedder is not None and not semantic_error else "keyword"
    return hits, mode
