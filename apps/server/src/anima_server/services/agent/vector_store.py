"""Persistent and in-memory vector stores for semantic memory search.

Provides an abstract ``VectorStore`` interface with two implementations:

- ``SqliteVecStore``: Persistent SQLite-backed store using raw SQL with cosine
  similarity. Embeddings survive server restarts.
- ``InMemoryVectorStore``: Process-local fallback (the original implementation).

The active store is selected via ``get_vector_store()`` which returns a module-
level singleton.  All public helper functions (``upsert_memory``,
``search_similar``, etc.) delegate through it.
"""

from __future__ import annotations

import logging
import math
import shutil
import sqlite3
import struct
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any

from anima_server.config import settings

logger = logging.getLogger(__name__)


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
# SQLite persistent store
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


class SqliteVecStore(VectorStore):
    """Persistent vector store backed by a dedicated SQLite database.

    Stores embeddings as packed float32 blobs and computes cosine
    similarity in Python after fetching candidate rows.
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        self._lock = Lock()
        self._conn: sqlite3.Connection | None = None
        self._ensure_schema()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(
                self._db_path, check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
        return self._conn

    def _ensure_schema(self) -> None:
        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS memory_vectors (
                item_id    INTEGER NOT NULL,
                user_id    INTEGER NOT NULL,
                content    TEXT    NOT NULL,
                category   TEXT    NOT NULL DEFAULT 'fact',
                importance INTEGER NOT NULL DEFAULT 3,
                embedding  BLOB    NOT NULL,
                PRIMARY KEY (item_id)
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_mv_user "
            "ON memory_vectors (user_id)"
        )
        conn.commit()

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
        blob = _serialize_f32(embedding)
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                "INSERT INTO memory_vectors "
                "(item_id, user_id, content, category, importance, embedding) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(item_id) DO UPDATE SET "
                "content=excluded.content, category=excluded.category, "
                "importance=excluded.importance, embedding=excluded.embedding",
                (item_id, user_id, content, category, importance, blob),
            )
            conn.commit()

    def delete(self, user_id: int, *, item_id: int) -> None:
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                "DELETE FROM memory_vectors WHERE item_id = ? AND user_id = ?",
                (item_id, user_id),
            )
            conn.commit()

    def search_by_vector(
        self,
        user_id: int,
        *,
        query_embedding: list[float],
        limit: int = 10,
        category: str | None = None,
    ) -> list[VectorSearchResult]:
        with self._lock:
            conn = self._get_conn()
            if category is not None:
                rows = conn.execute(
                    "SELECT item_id, content, category, importance, embedding "
                    "FROM memory_vectors WHERE user_id = ? AND category = ?",
                    (user_id, category),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT item_id, content, category, importance, embedding "
                    "FROM memory_vectors WHERE user_id = ?",
                    (user_id,),
                ).fetchall()

        scored: list[tuple[float, VectorSearchResult]] = []
        for row in rows:
            emb = _deserialize_f32(row[4])
            sim = _cosine_similarity(query_embedding, emb)
            scored.append((sim, VectorSearchResult(
                item_id=row[0], content=row[1], category=row[2],
                importance=row[3], similarity=round(sim, 4),
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
        with self._lock:
            conn = self._get_conn()
            if category is not None:
                rows = conn.execute(
                    "SELECT item_id, content, category, importance "
                    "FROM memory_vectors WHERE user_id = ? AND category = ?",
                    (user_id, category),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT item_id, content, category, importance "
                    "FROM memory_vectors WHERE user_id = ?",
                    (user_id,),
                ).fetchall()

        scored: list[tuple[float, VectorSearchResult]] = []
        for row in rows:
            sim = _text_similarity(query_text, row[1])
            if sim > 0.0:
                scored.append((sim, VectorSearchResult(
                    item_id=row[0], content=row[1], category=row[2],
                    importance=row[3], similarity=round(sim, 4),
                )))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [r for _, r in scored[:limit]]

    def rebuild(
        self,
        user_id: int,
        items: list[tuple[int, str, list[float], str, int]],
    ) -> int:
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                "DELETE FROM memory_vectors WHERE user_id = ?", (user_id,))
            for item_id, content, embedding, category, importance in items:
                blob = _serialize_f32(embedding)
                conn.execute(
                    "INSERT INTO memory_vectors "
                    "(item_id, user_id, content, category, importance, embedding) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (item_id, user_id, content, category, importance, blob),
                )
            conn.commit()
        return len(items)

    def count(self, user_id: int) -> int:
        with self._lock:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT COUNT(*) FROM memory_vectors WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            return row[0] if row else 0

    def reset(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
            try:
                Path(self._db_path).unlink(missing_ok=True)
                Path(self._db_path + "-wal").unlink(missing_ok=True)
                Path(self._db_path + "-shm").unlink(missing_ok=True)
            except OSError:
                pass
            self._ensure_schema()


# ---------------------------------------------------------------------------
# In-memory fallback store
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
# Module-level singleton and backward-compatible public API
# ---------------------------------------------------------------------------

_store: VectorStore | None = None
_store_lock = Lock()
_legacy_cleanup_done = False
_legacy_cleanup_lock = Lock()


def _cleanup_legacy_persist_dir() -> None:
    global _legacy_cleanup_done
    if _legacy_cleanup_done:
        return
    with _legacy_cleanup_lock:
        if _legacy_cleanup_done:
            return
        legacy_dir = Path(settings.data_dir) / "chroma"
        if legacy_dir.exists():
            shutil.rmtree(legacy_dir, ignore_errors=True)
            logger.info(
                "Removed legacy plaintext vector store at %s", legacy_dir)
        _legacy_cleanup_done = True


def get_vector_store() -> VectorStore:
    """Return the singleton vector store, lazy-initializing on first call."""
    global _store
    if _store is not None:
        return _store
    with _store_lock:
        if _store is not None:
            return _store
        _cleanup_legacy_persist_dir()
        vec_db_path = Path(settings.data_dir) / "vectors.db"
        vec_db_path.parent.mkdir(parents=True, exist_ok=True)
        _store = SqliteVecStore(vec_db_path)
        logger.info("Persistent vector store initialized at %s", vec_db_path)
        return _store


def upsert_memory(
    user_id: int,
    *,
    item_id: int,
    content: str,
    embedding: list[float],
    category: str = "fact",
    importance: int = 3,
) -> None:
    get_vector_store().upsert(
        user_id, item_id=item_id, content=content,
        embedding=embedding, category=category, importance=importance,
    )


def delete_memory(user_id: int, *, item_id: int) -> None:
    try:
        get_vector_store().delete(user_id, item_id=item_id)
    except Exception:  # noqa: BLE001
        logger.debug("Failed to delete item %d from vector store", item_id)


def search_similar(
    user_id: int,
    *,
    query_embedding: list[float],
    limit: int = 10,
    category: str | None = None,
) -> list[dict[str, Any]]:
    results = get_vector_store().search_by_vector(
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
) -> list[dict[str, Any]]:
    results = get_vector_store().search_by_text(
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
) -> int:
    return get_vector_store().rebuild(user_id, items)


def get_collection(user_id: int) -> Any:
    """Legacy shim for code that calls get_collection(uid).count()."""
    class _CollectionProxy:
        def count(self) -> int:
            return get_vector_store().count(user_id)
    return _CollectionProxy()


def reset_vector_store() -> None:
    """Reset the vector store. Used in tests."""
    global _store, _legacy_cleanup_done
    with _store_lock:
        if _store is not None:
            _store.reset()
            _store = None
    _legacy_cleanup_done = False


def use_in_memory_store() -> None:
    """Force the in-memory backend (for tests that don't want disk IO)."""
    global _store
    with _store_lock:
        _store = InMemoryVectorStore()
