"""Vector store for semantic memory search.

Primary backend: ``PgVecStore`` (pgvector in embedded PostgreSQL).
Test backend: ``InMemoryVectorStore`` (process-local, no persistence).

``OrmVecStore`` (MemoryVector in SQLCipher) was removed in P6.
"""

from __future__ import annotations

import logging
import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
from threading import Lock
from typing import Any

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------



def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
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
# In-memory store (for tests)
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
            item_id=item_id,
            content=content,
            embedding=embedding,
            category=category,
            importance=importance,
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
            scored.append(
                (
                    sim,
                    VectorSearchResult(
                        item_id=record.item_id,
                        content=record.content,
                        category=record.category,
                        importance=record.importance,
                        similarity=round(sim, 4),
                    ),
                )
            )
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
                scored.append(
                    (
                        sim,
                        VectorSearchResult(
                            item_id=record.item_id,
                            content=record.content,
                            category=record.category,
                            importance=record.importance,
                            similarity=round(sim, 4),
                        ),
                    )
                )
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
                item_id=item_id,
                content=content,
                embedding=embedding,
                category=category,
                importance=importance,
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
_force_in_memory = False
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


def _try_get_runtime_session() -> Session | None:
    """Attempt to obtain a runtime PG session. Returns ``None`` if unavailable."""
    try:
        from anima_server.db.runtime import get_runtime_session_factory

        return get_runtime_session_factory()()
    except Exception:
        return None


def _get_store(
    db: Session | None,
    *,
    runtime_db: Session | None = None,
) -> tuple[VectorStore, Session | None]:
    """Return ``(store, owned_runtime_session)`` for the active backend.

    Priority order:
    1. Explicit ``runtime_db`` → PgVecStore (caller manages session)
    2. PG available → PgVecStore via auto-obtained runtime session
    3. Degraded / tests → InMemoryVectorStore fallback

    OrmVecStore (MemoryVector in SQLCipher) is deprecated as of P6.
    """
    if runtime_db is not None:
        from anima_server.services.agent.pgvec_store import PgVecStore

        return PgVecStore(runtime_db), None

    if _force_in_memory:
        return _get_fallback_store(), None

    rt_session = _try_get_runtime_session()
    if rt_session is not None:
        try:
            from anima_server.services.agent.pgvec_store import PgVecStore

            return PgVecStore(rt_session), rt_session
        except Exception:
            rt_session.close()

    if db is not None:
        logger.debug("PG unavailable; vector store degraded to in-memory fallback")
    return _get_fallback_store(), None


def upsert_memory(
    user_id: int,
    *,
    item_id: int,
    content: str,
    embedding: list[float],
    category: str = "fact",
    importance: int = 3,
    db: Session | None = None,
    runtime_db: Session | None = None,
) -> None:
    store, owned_session = _get_store(db, runtime_db=runtime_db)
    try:
        store.upsert(
            user_id,
            item_id=item_id,
            content=content,
            embedding=embedding,
            category=category,
            importance=importance,
        )
        if owned_session is not None:
            owned_session.commit()
    except Exception:
        if owned_session is not None:
            owned_session.rollback()
        raise
    finally:
        if owned_session is not None:
            owned_session.close()
    try:
        from anima_server.services.agent.bm25_index import invalidate_index

        invalidate_index(user_id)
    except Exception:
        pass


def delete_memory(
    user_id: int,
    *,
    item_id: int,
    db: Session | None = None,
    runtime_db: Session | None = None,
) -> None:
    store, owned_session = _get_store(db, runtime_db=runtime_db)
    try:
        store.delete(user_id, item_id=item_id)
        if owned_session is not None:
            owned_session.commit()
    except Exception:
        if owned_session is not None:
            owned_session.rollback()
        logger.debug("Failed to delete item %d from vector store", item_id)
    finally:
        if owned_session is not None:
            owned_session.close()
    try:
        from anima_server.services.agent.bm25_index import invalidate_index

        invalidate_index(user_id)
    except Exception:
        pass


_synced_users: set[int] = set()
_synced_users_lock = Lock()


def _maybe_cold_start_sync(user_id: int, db: Session | None) -> None:
    """Lazily sync embeddings from soul to PG on first search per user.

    If the RuntimeEmbedding table is empty for this user but the soul DB
    has cached embedding_json data, bulk-insert into PG so pgvector
    search works without waiting for a consolidation cycle.
    """
    if _force_in_memory or db is None:
        return
    with _synced_users_lock:
        if user_id in _synced_users:
            return
    try:
        from anima_server.services.agent.embeddings import sync_embeddings_to_runtime

        synced = sync_embeddings_to_runtime(db, user_id=user_id)
        if synced < 0:
            return  # PG unavailable — don't mark synced, retry next time
        if synced > 0:
            logger.info("Cold-start sync: %d embeddings for user %d", synced, user_id)
        with _synced_users_lock:
            _synced_users.add(user_id)
    except Exception:
        logger.debug("Cold-start embedding sync failed for user %d", user_id)


def search_similar(
    user_id: int,
    *,
    query_embedding: list[float],
    limit: int = 10,
    category: str | None = None,
    db: Session | None = None,
    runtime_db: Session | None = None,
) -> list[dict[str, Any]]:
    _maybe_cold_start_sync(user_id, db)
    store, owned_session = _get_store(db, runtime_db=runtime_db)
    try:
        results = store.search_by_vector(
            user_id,
            query_embedding=query_embedding,
            limit=limit,
            category=category,
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
    finally:
        if owned_session is not None:
            owned_session.close()


def search_by_text(
    user_id: int,
    *,
    query_text: str,
    limit: int = 10,
    category: str | None = None,
    db: Session | None = None,
    runtime_db: Session | None = None,
) -> list[dict[str, Any]]:
    store, owned_session = _get_store(db, runtime_db=runtime_db)
    try:
        results = store.search_by_text(
            user_id,
            query_text=query_text,
            limit=limit,
            category=category,
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
    finally:
        if owned_session is not None:
            owned_session.close()


def rebuild_user_index(
    user_id: int,
    items: list[tuple[int, str, list[float], str, int]],
    *,
    db: Session | None = None,
    runtime_db: Session | None = None,
) -> int:
    store, owned_session = _get_store(db, runtime_db=runtime_db)
    try:
        result = store.rebuild(user_id, items)
        if owned_session is not None:
            owned_session.commit()
    except Exception:
        if owned_session is not None:
            owned_session.rollback()
        raise
    finally:
        if owned_session is not None:
            owned_session.close()
    try:
        from anima_server.services.agent.bm25_index import invalidate_index

        invalidate_index(user_id)
    except Exception:
        pass
    return result


def get_collection(
    user_id: int,
    *,
    db: Session | None = None,
    runtime_db: Session | None = None,
) -> Any:
    """Legacy shim for code that calls get_collection(uid).count()."""
    class _CollectionProxy:
        def count(self) -> int:
            store, owned_session = _get_store(db, runtime_db=runtime_db)
            try:
                return store.count(user_id)
            finally:
                if owned_session is not None:
                    owned_session.close()

    return _CollectionProxy()


def reset_vector_store() -> None:
    """Reset the in-memory fallback store. Used in tests."""
    global _fallback_store, _force_in_memory
    with _fallback_lock:
        if _fallback_store is not None:
            _fallback_store.reset()
            _fallback_store = None
        _force_in_memory = False
    with _synced_users_lock:
        _synced_users.clear()


def use_in_memory_store() -> None:
    """Force the in-memory backend (for tests that don't want disk IO)."""
    global _fallback_store, _force_in_memory
    with _fallback_lock:
        _force_in_memory = True
        _fallback_store = InMemoryVectorStore()
