"""Persistent vector store for semantic memory search.

Embeddings live in the per-user ``anima.db`` via the ``MemoryVector``
SQLAlchemy model.  This means vectors are:

- Encrypted when the Core is encrypted (SQLCipher).
- Per-user isolated (each user has their own database file).
- Included automatically in vault export/import.
- Managed through the same SQLAlchemy session as everything else.

Two backends:

- ``OrmVecStore``: Production store using the per-user SQLAlchemy session.
- ``InMemoryVectorStore``: Process-local fallback for tests.

The public helper functions (``upsert_memory``, ``search_similar``, etc.)
accept an optional ``db`` session.  When provided, they use ``OrmVecStore``
directly.  When omitted, they fall back to the in-memory store (for tests
that call ``use_in_memory_store()``).
"""

from __future__ import annotations

import logging
import math
import struct
from abc import ABC, abstractmethod
from dataclasses import dataclass
from threading import Lock
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _serialize_f32(vec: list[float]) -> bytes:
    """Pack a float list into little-endian float32 bytes."""
    return struct.pack(f"<{len(vec)}f", *vec)


def _deserialize_f32(data: bytes) -> list[float]:
    """Unpack little-endian float32 bytes back to a float list."""
    n = len(data) // 4
    return list(struct.unpack(f"<{n}f", data))


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _text_similarity(query_text: str, content: str) -> float:
    query_terms = set(query_text.lower().split())
    content_terms = set(content.lower().split())
    if not query_terms or not content_terms:
        return 0.0
    intersection = query_terms & content_terms
    union = query_terms | content_terms
    return len(intersection) / len(union)


# ---------------------------------------------------------------------------
# Abstract interface
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class VectorSearchResult:
    item_id: int
    content: str
    category: str
    importance: int
    similarity: float


class VectorStore(ABC):
    """Abstract vector store that all backends must implement."""

    @abstractmethod
    def upsert(
        self,
        user_id: int,
        *,
        item_id: int,
        content: str,
        embedding: list[float],
        category: str,
        importance: int,
    ) -> None: ...

    @abstractmethod
    def delete(self, user_id: int, *, item_id: int) -> None: ...

    @abstractmethod
    def search_by_vector(
        self,
        user_id: int,
        *,
        query_embedding: list[float],
        limit: int = 10,
        category: str | None = None,
    ) -> list[VectorSearchResult]: ...

    @abstractmethod
    def search_by_text(
        self,
        user_id: int,
        *,
        query_text: str,
        limit: int = 10,
        category: str | None = None,
    ) -> list[VectorSearchResult]: ...

    @abstractmethod
    def rebuild(
        self,
        user_id: int,
        items: list[tuple[int, str, list[float], str, int]],
    ) -> int: ...

    @abstractmethod
    def count(self, user_id: int) -> int: ...

    @abstractmethod
    def reset(self) -> None: ...


# ---------------------------------------------------------------------------
# ORM-backed store (per-user anima.db)
# ---------------------------------------------------------------------------


class OrmVecStore(VectorStore):
    """Vector store backed by the per-user SQLAlchemy database.

    Each method receives a ``Session`` bound to the correct
    per-user ``anima.db``, so vectors are encrypted, isolated,
    and exported together with everything else.
    """

    def __init__(self, db: Session) -> None:
        self._db = db

    def upsert(
        self,
        user_id: int,
        *,
        item_id: int,
        content: str,
        embedding: list[float],
        category: str = "fact",
        importance: int = 3,
    ) -> None:
        from anima_server.models import MemoryVector

        blob = _serialize_f32(embedding)
        existing = self._db.get(MemoryVector, item_id)
        if existing is not None:
            existing.content = content
            existing.category = category
            existing.importance = importance
            existing.embedding = blob
        else:
            self._db.add(MemoryVector(
                item_id=item_id,
                user_id=user_id,
                content=content,
                category=category,
                importance=importance,
                embedding=blob,
            ))
        self._db.flush()

    def delete(self, user_id: int, *, item_id: int) -> None:
        from anima_server.models import MemoryVector

        self._db.execute(
            delete(MemoryVector).where(
                MemoryVector.item_id == item_id,
                MemoryVector.user_id == user_id,
            )
        )
        self._db.flush()

    def search_by_vector(
        self,
        user_id: int,
        *,
        query_embedding: list[float],
        limit: int = 10,
        category: str | None = None,
    ) -> list[VectorSearchResult]:
        from anima_server.models import MemoryVector

        stmt = select(MemoryVector).where(MemoryVector.user_id == user_id)
        if category is not None:
            stmt = stmt.where(MemoryVector.category == category)
        rows = self._db.scalars(stmt).all()

        scored: list[tuple[float, VectorSearchResult]] = []
        for row in rows:
            emb = _deserialize_f32(row.embedding)
            sim = _cosine_similarity(query_embedding, emb)
            scored.append((sim, VectorSearchResult(
                item_id=row.item_id, content=row.content,
                category=row.category, importance=row.importance,
                similarity=round(sim, 4),
            )))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [r for _, r in scored[:limit]]

    def search_by_text(
        self,
        user_id: int,
        *,
        query_text: str,
        limit: int = 10,
        category: str | None = None,
    ) -> list[VectorSearchResult]:
        from anima_server.models import MemoryVector

        stmt = select(MemoryVector).where(MemoryVector.user_id == user_id)
        if category is not None:
            stmt = stmt.where(MemoryVector.category == category)
        rows = self._db.scalars(stmt).all()

        scored: list[tuple[float, VectorSearchResult]] = []
        for row in rows:
            sim = _text_similarity(query_text, row.content)
            if sim > 0.0:
                scored.append((sim, VectorSearchResult(
                    item_id=row.item_id, content=row.content,
                    category=row.category, importance=row.importance,
                    similarity=round(sim, 4),
                )))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [r for _, r in scored[:limit]]

    def rebuild(
        self,
        user_id: int,
        items: list[tuple[int, str, list[float], str, int]],
    ) -> int:
        from anima_server.models import MemoryVector

        self._db.execute(
            delete(MemoryVector).where(MemoryVector.user_id == user_id))
        for item_id, content, embedding, category, importance in items:
            blob = _serialize_f32(embedding)
            self._db.add(MemoryVector(
                item_id=item_id,
                user_id=user_id,
                content=content,
                category=category,
                importance=importance,
                embedding=blob,
            ))
        self._db.flush()
        return len(items)

    def count(self, user_id: int) -> int:
        from sqlalchemy import func as sa_func
        from anima_server.models import MemoryVector

        return self._db.scalar(
            select(sa_func.count()).select_from(MemoryVector).where(
                MemoryVector.user_id == user_id)
        ) or 0

    def reset(self) -> None:
        from anima_server.models import MemoryVector

        self._db.execute(delete(MemoryVector))
        self._db.flush()


# ---------------------------------------------------------------------------
# In-memory fallback store (for tests)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _VectorRecord:
    item_id: int
    content: str
    embedding: list[float]
    category: str
    importance: int


class InMemoryVectorStore(VectorStore):
    """Process-local dict-based vector store. No persistence."""

    def __init__(self) -> None:
        self._data: dict[int, dict[int, _VectorRecord]] = {}

    def upsert(
        self,
        user_id: int,
        *,
        item_id: int,
        content: str,
        embedding: list[float],
        category: str = "fact",
        importance: int = 3,
    ) -> None:
        self._data.setdefault(user_id, {})[item_id] = _VectorRecord(
            item_id=item_id, content=content, embedding=embedding,
            category=category, importance=importance,
        )

    def delete(self, user_id: int, *, item_id: int) -> None:
        user_store = self._data.get(user_id)
        if user_store:
            user_store.pop(item_id, None)

    def search_by_vector(
        self,
        user_id: int,
        *,
        query_embedding: list[float],
        limit: int = 10,
        category: str | None = None,
    ) -> list[VectorSearchResult]:
        user_store = self._data.get(user_id, {})
        scored: list[tuple[float, VectorSearchResult]] = []
        for record in user_store.values():
            if category is not None and record.category != category:
                continue
            sim = _cosine_similarity(query_embedding, record.embedding)
            scored.append((sim, VectorSearchResult(
                item_id=record.item_id, content=record.content,
                category=record.category, importance=record.importance,
                similarity=round(sim, 4),
            )))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [r for _, r in scored[:limit]]

    def search_by_text(
        self,
        user_id: int,
        *,
        query_text: str,
        limit: int = 10,
        category: str | None = None,
    ) -> list[VectorSearchResult]:
        user_store = self._data.get(user_id, {})
        scored: list[tuple[float, VectorSearchResult]] = []
        for record in user_store.values():
            if category is not None and record.category != category:
                continue
            sim = _text_similarity(query_text, record.content)
            if sim > 0.0:
                scored.append((sim, VectorSearchResult(
                    item_id=record.item_id, content=record.content,
                    category=record.category, importance=record.importance,
                    similarity=round(sim, 4),
                )))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [r for _, r in scored[:limit]]

    def rebuild(
        self,
        user_id: int,
        items: list[tuple[int, str, list[float], str, int]],
    ) -> int:
        self._data[user_id] = {}
        for item_id, content, embedding, category, importance in items:
            self._data[user_id][item_id] = _VectorRecord(
                item_id=item_id, content=content, embedding=embedding,
                category=category, importance=importance,
            )
        return len(items)

    def count(self, user_id: int) -> int:
        return len(self._data.get(user_id, {}))

    def reset(self) -> None:
        self._data.clear()


# ---------------------------------------------------------------------------
# Module-level state and public API
# ---------------------------------------------------------------------------

_fallback_store: InMemoryVectorStore | None = None
_fallback_lock = Lock()


def _get_fallback_store() -> InMemoryVectorStore:
    """Return the in-memory fallback store (created on first call)."""
    global _fallback_store
    if _fallback_store is not None:
        return _fallback_store
    with _fallback_lock:
        if _fallback_store is not None:
            return _fallback_store
        _fallback_store = InMemoryVectorStore()
        return _fallback_store


def _get_store(db: Session | None) -> VectorStore:
    """Return the appropriate store for the given session.

    If a SQLAlchemy session is provided, wraps it in ``OrmVecStore``
    so vectors go into the per-user anima.db.  Otherwise falls back
    to the in-memory store (used in tests via ``use_in_memory_store``).
    """
    if db is not None:
        return OrmVecStore(db)
    return _get_fallback_store()


def upsert_memory(
    user_id: int,
    *,
    item_id: int,
    content: str,
    embedding: list[float],
    category: str = "fact",
    importance: int = 3,
    db: Session | None = None,
) -> None:
    _get_store(db).upsert(
        user_id, item_id=item_id, content=content,
        embedding=embedding, category=category, importance=importance,
    )
    try:
        from anima_server.services.agent.bm25_index import invalidate_index
        invalidate_index(user_id)
    except Exception:  # noqa: BLE001
        pass


def delete_memory(user_id: int, *, item_id: int, db: Session | None = None) -> None:
    try:
        _get_store(db).delete(user_id, item_id=item_id)
    except Exception:  # noqa: BLE001
        logger.debug("Failed to delete item %d from vector store", item_id)
    try:
        from anima_server.services.agent.bm25_index import invalidate_index
        invalidate_index(user_id)
    except Exception:  # noqa: BLE001
        pass


def search_similar(
    user_id: int,
    *,
    query_embedding: list[float],
    limit: int = 10,
    category: str | None = None,
    db: Session | None = None,
) -> list[dict[str, Any]]:
    results = _get_store(db).search_by_vector(
        user_id, query_embedding=query_embedding, limit=limit, category=category,
    )
    return [
        {
            "id": r.item_id,
            "content": r.content,
            "category": r.category,
            "importance": r.importance,
            "similarity": r.similarity,
        }
        for r in results
    ]


def search_by_text(
    user_id: int,
    *,
    query_text: str,
    limit: int = 10,
    category: str | None = None,
    db: Session | None = None,
) -> list[dict[str, Any]]:
    results = _get_store(db).search_by_text(
        user_id, query_text=query_text, limit=limit, category=category,
    )
    return [
        {
            "id": r.item_id,
            "content": r.content,
            "category": r.category,
            "importance": r.importance,
            "similarity": r.similarity,
        }
        for r in results
    ]


def rebuild_user_index(
    user_id: int,
    items: list[tuple[int, str, list[float], str, int]],
    *,
    db: Session | None = None,
) -> int:
    result = _get_store(db).rebuild(user_id, items)
    try:
        from anima_server.services.agent.bm25_index import invalidate_index
        invalidate_index(user_id)
    except Exception:  # noqa: BLE001
        pass
    return result


def get_collection(user_id: int, *, db: Session | None = None) -> Any:
    """Legacy shim for code that calls get_collection(uid).count()."""
    store = _get_store(db)

    class _CollectionProxy:
        def count(self) -> int:
            return store.count(user_id)
    return _CollectionProxy()


def reset_vector_store() -> None:
    """Reset the in-memory fallback store. Used in tests."""
    global _fallback_store
    with _fallback_lock:
        if _fallback_store is not None:
            _fallback_store.reset()
            _fallback_store = None


def use_in_memory_store() -> None:
    """Force the in-memory backend (for tests that don't want disk IO)."""
    global _fallback_store
    with _fallback_lock:
        _fallback_store = InMemoryVectorStore()
