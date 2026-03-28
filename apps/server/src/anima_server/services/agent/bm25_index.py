"""BM25 lexical search index for memory retrieval -- F1.

Replaces Jaccard-based _text_similarity() with BM25Okapi for the keyword
leg of hybrid search. Indices are per-user, cached in process memory,
and invalidated on content mutations.
"""

from __future__ import annotations

import logging
from threading import Lock

from rank_bm25 import BM25Okapi
from sqlalchemy import select
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def _tokenize(text: str) -> list[str]:
    """Simple whitespace tokenization with lowercasing."""
    return text.lower().split()


class BM25Index:
    """Per-user BM25 index built lazily from MemoryVector content."""

    def __init__(self) -> None:
        self._bm25: BM25Okapi | None = None
        self._item_ids: list[int] = []
        self._documents: list[tuple[int, str]] = []

    def build(self, documents: list[tuple[int, str]]) -> None:
        """Build index from (item_id, content) pairs."""
        self._documents = list(documents)
        self._item_ids = [doc_id for doc_id, _ in self._documents]
        tokenized = [_tokenize(content) for _, content in self._documents]
        if tokenized:
            self._bm25 = BM25Okapi(tokenized)
        else:
            self._bm25 = None

    def search(self, query: str, *, limit: int = 20) -> list[tuple[int, float]]:
        """Return (item_id, bm25_score) ranked descending."""
        if self._bm25 is None or not self._item_ids:
            return []
        tokenized_query = _tokenize(query)
        if not tokenized_query:
            return []
        scores = self._bm25.get_scores(tokenized_query)
        scored = [
            (self._item_ids[i], float(scores[i]))
            for i in range(len(self._item_ids))
            if scores[i] != 0.0
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:limit]

    def add_document(self, item_id: int, content: str) -> None:
        """Add a document. Triggers full rebuild (BM25Okapi needs corpus stats)."""
        self._documents.append((item_id, content))
        self.build(self._documents)

    def remove_document(self, item_id: int) -> None:
        """Remove a document by ID. Triggers full rebuild."""
        self._documents = [(did, c) for did, c in self._documents if did != item_id]
        self.build(self._documents)

    @property
    def document_count(self) -> int:
        return len(self._item_ids)


# -- Module-level per-user cache ---------------------------------------------

_user_indices: dict[int, BM25Index] = {}
_indices_lock: Lock = Lock()


def get_or_build_index(user_id: int, *, db: Session) -> BM25Index:
    """Lazy-load the BM25 index for a user.

    Prefers RuntimeEmbedding (PG) as the data source.
    Falls back to MemoryVector (soul DB) if PG is unavailable.
    Thread-safe via _indices_lock.
    """
    with _indices_lock:
        if user_id in _user_indices:
            return _user_indices[user_id]

    rows = None
    try:
        from anima_server.db.runtime import get_runtime_session_factory
        from anima_server.models.runtime_embedding import RuntimeEmbedding

        runtime_db = get_runtime_session_factory()()
        try:
            rows = runtime_db.execute(
                select(RuntimeEmbedding.source_id, RuntimeEmbedding.content_preview).where(
                    RuntimeEmbedding.user_id == user_id
                )
            ).all()
        finally:
            runtime_db.close()
    except Exception:
        rows = None

    if rows is None:
        from anima_server.models import MemoryVector

        rows = db.execute(
            select(MemoryVector.item_id, MemoryVector.content).where(
                MemoryVector.user_id == user_id
            )
        ).all()

    index = BM25Index()
    index.build([(row[0], row[1]) for row in rows])

    with _indices_lock:
        _user_indices[user_id] = index

    return index


def invalidate_index(user_id: int) -> None:
    """Clear cached index. Next search triggers rebuild."""
    with _indices_lock:
        _user_indices.pop(user_id, None)


def bm25_search(
    user_id: int,
    *,
    query: str,
    limit: int = 20,
    db: Session,
) -> list[tuple[int, float]]:
    """Search using BM25. Returns (item_id, score) pairs ranked descending."""
    index = get_or_build_index(user_id, db=db)
    return index.search(query, limit=limit)
