# P6: pgvector Embeddings — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate vector search from in-memory brute-force over SQLCipher to pgvector in embedded PostgreSQL for O(log n) ANN search, persistent indexes, and concurrent access.

**Architecture:** Add `pgvector` Python package and enable the PG extension at startup. Define a `RuntimeEmbedding` model on `RuntimeBase`. Implement `PgVecStore` as a new `VectorStore` backend using pgvector's `<=>` cosine operator. Rewire `embeddings.py`, `bm25_index.py`, `forgetting.py`, `memory_store.py`, and `vault.py` to read/write via PG. Retain `InMemoryVectorStore` for unit tests and brute-force fallback for when PG is unavailable.

**Tech Stack:** Python, SQLAlchemy, pgvector, psycopg, Alembic (runtime), pytest

**Spec:** `docs/prds/three-tier-architecture/P6-pgvector-embeddings.md`

---

## File Structure

### New files

| File | Responsibility |
|------|----------------|
| `models/runtime_embedding.py` | `RuntimeEmbedding` SQLAlchemy model on `RuntimeBase` |
| `services/agent/pgvec_store.py` | `PgVecStore` implementing `VectorStore` ABC using pgvector operators |
| `alembic_runtime/versions/005_p6_pgvector_embeddings.py` | Runtime Alembic migration: pgvector extension + `embeddings` table + HNSW index |
| `tests/test_pgvec_store.py` | Unit tests for `PgVecStore` (mocked/in-memory where possible) |
| `tests/test_embedding_sync.py` | Tests for cold-start sync from soul to runtime |

### Modified files

| File | Changes |
|------|---------|
| `config.py` | Add `agent_embedding_dim: int = 768` setting |
| `db/runtime.py` | Add `ensure_pgvector()` call after engine init |
| `models/__init__.py` | Export `RuntimeEmbedding` |
| `models/runtime.py` | (no change — RuntimeEmbedding gets its own file) |
| `alembic_runtime/env.py` | Import `runtime_embedding` model so metadata is populated |
| `services/agent/vector_store.py` | `_get_store()` returns `PgVecStore` when runtime session available; add `_get_runtime_session()` helper |
| `services/agent/embeddings.py` | Dual-write to PG in `embed_memory_item()` and `backfill_embeddings()`; `sync_to_vector_store()` targets PG |
| `services/agent/bm25_index.py` | `get_or_build_index()` queries `RuntimeEmbedding.content_preview` instead of `MemoryVector.content` |
| `services/agent/forgetting.py` | `_forget_single_item()` deletes from `RuntimeEmbedding` |
| `services/agent/memory_store.py` | `supersede_memory_item()` deletes from `RuntimeEmbedding` |
| `services/vault.py` | `_rebuild_vector_indices()` calls PG-targeting sync |
| `pyproject.toml` | Add `pgvector>=0.3.6` dependency |

---

## Task 1: Add pgvector dependency and embedding dimension config

**Files:**
- Modify: `apps/server/pyproject.toml`
- Modify: `apps/server/src/anima_server/config.py`

- [ ] **Step 1: Add pgvector to pyproject.toml**

In `apps/server/pyproject.toml`, add `"pgvector>=0.3.6"` to the dependencies list:

```toml
  "pgvector>=0.3.6",
```

Add it after the existing `"pgserver>=0.1.4"` line.

- [ ] **Step 2: Add embedding dimension setting to config.py**

In `apps/server/src/anima_server/config.py`, add after `agent_extraction_provider`:

```python
    agent_embedding_dim: int = 768
```

- [ ] **Step 3: Install the new dependency**

Run: `cd apps/server && uv sync`

- [ ] **Step 4: Commit**

```bash
git add apps/server/pyproject.toml apps/server/uv.lock apps/server/src/anima_server/config.py
git commit -m "feat(p6): add pgvector dependency and embedding_dim config"
```

---

## Task 2: Create RuntimeEmbedding model

**Files:**
- Create: `apps/server/src/anima_server/models/runtime_embedding.py`
- Modify: `apps/server/src/anima_server/models/__init__.py`
- Modify: `apps/server/alembic_runtime/env.py`

- [ ] **Step 1: Create the RuntimeEmbedding model**

Create `apps/server/src/anima_server/models/runtime_embedding.py`:

```python
"""pgvector-backed embedding storage on the Runtime (PostgreSQL) engine.

Each row stores a single embedding vector alongside metadata that enables
upsert-by-source, staleness detection, and BM25 index construction without
touching the encrypted soul database.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Index, Integer, String, func, text
from sqlalchemy.dialects.postgresql import TIMESTAMP as _PG_TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from anima_server.config import settings
from anima_server.db.runtime_base import RuntimeBase

TIMESTAMPTZ = _PG_TIMESTAMP(timezone=True)


def _vector_column() -> Any:
    """Return a pgvector Vector column with the configured dimension.

    Importing pgvector at module level would fail in environments where
    pgvector is not installed (e.g. lightweight test runs).  The lazy
    import keeps the model importable everywhere; the column type is only
    resolved when SQLAlchemy actually reflects or creates the table.
    """
    from pgvector.sqlalchemy import Vector

    return Vector(settings.agent_embedding_dim)


class RuntimeEmbedding(RuntimeBase):
    __tablename__ = "embeddings"
    __table_args__ = (
        Index(
            "ix_embeddings_user_source",
            "user_id",
            "source_type",
            "source_id",
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    source_type: Mapped[str] = mapped_column(
        String(24), nullable=False,
    )  # "memory_item" | "episode" | "entity"
    source_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False,
    )  # PK in the source table
    content_hash: Mapped[str] = mapped_column(
        String(64), nullable=False,
    )  # SHA-256 of plaintext for staleness detection
    embedding: Mapped[Any] = mapped_column(
        _vector_column(),
        nullable=False,
    )
    content_preview: Mapped[str] = mapped_column(
        String(200), nullable=False, server_default=text("''"),
    )  # first 200 chars for debugging / BM25
    category: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default=text("'fact'"),
    )
    importance: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("3"),
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now(),
    )

    @staticmethod
    def compute_content_hash(plaintext: str) -> str:
        """SHA-256 hex digest for staleness detection."""
        return hashlib.sha256(plaintext.encode()).hexdigest()
```

- [ ] **Step 2: Export RuntimeEmbedding from models/__init__.py**

In `apps/server/src/anima_server/models/__init__.py`, add:

```python
from anima_server.models.runtime_embedding import RuntimeEmbedding
```

And add `"RuntimeEmbedding"` to the `__all__` list.

- [ ] **Step 3: Import the model in alembic_runtime/env.py**

In `apps/server/alembic_runtime/env.py`, add after the existing runtime model import:

```python
import anima_server.models.runtime_embedding  # noqa: F401
```

- [ ] **Step 4: Commit**

```bash
git add apps/server/src/anima_server/models/runtime_embedding.py \
       apps/server/src/anima_server/models/__init__.py \
       apps/server/alembic_runtime/env.py
git commit -m "feat(p6): add RuntimeEmbedding model on RuntimeBase"
```

---

## Task 3: Create Alembic runtime migration for pgvector + embeddings table

**Files:**
- Create: `apps/server/alembic_runtime/versions/005_p6_pgvector_embeddings.py`

- [ ] **Step 1: Create the migration file**

Create `apps/server/alembic_runtime/versions/005_p6_pgvector_embeddings.py`:

```python
"""P6: pgvector extension + embeddings table + HNSW index

Revision ID: 005
Revises: 004
Create Date: 2026-03-28
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import TIMESTAMP as _PG_TIMESTAMP

TIMESTAMPTZ = _PG_TIMESTAMP(timezone=True)

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Enable pgvector extension (idempotent)
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "embeddings",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger, nullable=False),
        sa.Column("source_type", sa.String(24), nullable=False),
        sa.Column("source_id", sa.BigInteger, nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        # Vector column — raw SQL because Alembic doesn't natively support pgvector types.
        # Dimension is set to 768 (nomic-embed-text default).
        # Users with a different embedding model must re-embed after changing config.
        sa.Column("embedding", sa.LargeBinary, nullable=False),
        sa.Column("content_preview", sa.String(200), nullable=False, server_default=""),
        sa.Column("category", sa.String(24), nullable=False, server_default="fact"),
        sa.Column("importance", sa.Integer, nullable=False, server_default=sa.text("3")),
        sa.Column("created_at", TIMESTAMPTZ, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TIMESTAMPTZ, nullable=False, server_default=sa.func.now()),
    )

    # Replace the placeholder LargeBinary column with a real vector column.
    # Alembic's create_table doesn't support custom pgvector types, so we
    # drop the placeholder and add the actual vector column via raw SQL.
    op.drop_column("embeddings", "embedding")
    op.execute("ALTER TABLE embeddings ADD COLUMN embedding vector(768) NOT NULL")

    # Indexes
    op.create_index("ix_embeddings_user_id", "embeddings", ["user_id"])
    op.create_index(
        "ix_embeddings_user_source",
        "embeddings",
        ["user_id", "source_type", "source_id"],
        unique=True,
    )

    # HNSW approximate nearest neighbor index for cosine distance
    op.execute(
        "CREATE INDEX ix_embeddings_hnsw ON embeddings "
        "USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64)"
    )


def downgrade() -> None:
    op.drop_table("embeddings")
    op.execute("DROP EXTENSION IF EXISTS vector")
```

- [ ] **Step 2: Commit**

```bash
git add apps/server/alembic_runtime/versions/005_p6_pgvector_embeddings.py
git commit -m "feat(p6): alembic migration — pgvector extension + embeddings table + HNSW index"
```

---

## Task 4: Implement PgVecStore backend

**Files:**
- Create: `apps/server/src/anima_server/services/agent/pgvec_store.py`
- Create: `apps/server/tests/test_pgvec_store.py`

- [ ] **Step 1: Write the failing tests**

Create `apps/server/tests/test_pgvec_store.py`:

```python
"""Tests for PgVecStore — pgvector-backed VectorStore implementation.

These tests use InMemoryVectorStore to validate PgVecStore's logic
without requiring a running PostgreSQL instance. Integration tests
that require PG are marked with @pytest.mark.integration.
"""

from __future__ import annotations

import pytest
from anima_server.services.agent.vector_store import (
    InMemoryVectorStore,
    VectorSearchResult,
)


class TestPgVecStoreContractWithInMemory:
    """Validate VectorStore contract using InMemoryVectorStore as stand-in.

    PgVecStore must satisfy the same contract.  These tests document the
    expected behavior for both backends.
    """

    @pytest.fixture(autouse=True)
    def _setup(self):
        self.store = InMemoryVectorStore()

    def test_upsert_and_search(self):
        self.store.upsert(
            1, item_id=1, content="hiking in mountains",
            embedding=[1.0, 0.0, 0.0], category="preference", importance=4,
        )
        self.store.upsert(
            1, item_id=2, content="software engineer",
            embedding=[0.0, 1.0, 0.0], category="fact", importance=5,
        )
        results = self.store.search_by_vector(
            1, query_embedding=[0.9, 0.1, 0.0], limit=5,
        )
        assert len(results) == 2
        assert results[0].item_id == 1
        assert results[0].similarity > 0.9

    def test_category_filter(self):
        self.store.upsert(
            1, item_id=1, content="hiking",
            embedding=[1.0, 0.0, 0.0], category="preference", importance=4,
        )
        self.store.upsert(
            1, item_id=2, content="engineer",
            embedding=[0.0, 1.0, 0.0], category="fact", importance=5,
        )
        results = self.store.search_by_vector(
            1, query_embedding=[1.0, 0.0, 0.0], limit=5, category="fact",
        )
        assert len(results) == 1
        assert results[0].item_id == 2

    def test_upsert_updates_existing(self):
        self.store.upsert(
            1, item_id=1, content="v1",
            embedding=[1.0, 0.0, 0.0], category="fact", importance=3,
        )
        self.store.upsert(
            1, item_id=1, content="v2",
            embedding=[0.0, 1.0, 0.0], category="fact", importance=5,
        )
        assert self.store.count(1) == 1
        results = self.store.search_by_vector(
            1, query_embedding=[0.0, 1.0, 0.0], limit=1,
        )
        assert results[0].content == "v2"

    def test_delete(self):
        self.store.upsert(
            1, item_id=1, content="test",
            embedding=[1.0, 0.0], category="fact", importance=3,
        )
        assert self.store.count(1) == 1
        self.store.delete(1, item_id=1)
        assert self.store.count(1) == 0

    def test_rebuild_replaces_all(self):
        self.store.upsert(
            1, item_id=1, content="old",
            embedding=[1.0, 0.0], category="fact", importance=3,
        )
        count = self.store.rebuild(1, [
            (2, "new", [0.0, 1.0], "fact", 3),
        ])
        assert count == 1
        assert self.store.count(1) == 1
        results = self.store.search_by_vector(
            1, query_embedding=[0.0, 1.0], limit=5,
        )
        assert results[0].item_id == 2

    def test_search_empty_store(self):
        results = self.store.search_by_vector(
            99, query_embedding=[1.0, 0.0], limit=5,
        )
        assert results == []

    def test_user_isolation(self):
        self.store.upsert(
            1, item_id=1, content="user1",
            embedding=[1.0, 0.0], category="fact", importance=3,
        )
        self.store.upsert(
            2, item_id=2, content="user2",
            embedding=[0.0, 1.0], category="fact", importance=3,
        )
        assert self.store.count(1) == 1
        assert self.store.count(2) == 1


class TestContentHash:
    def test_compute_content_hash(self):
        from anima_server.models.runtime_embedding import RuntimeEmbedding

        h = RuntimeEmbedding.compute_content_hash("test content")
        assert len(h) == 64  # SHA-256 hex
        # Deterministic
        assert h == RuntimeEmbedding.compute_content_hash("test content")
        # Different input -> different hash
        assert h != RuntimeEmbedding.compute_content_hash("other content")
```

- [ ] **Step 2: Run tests to verify they pass (contract tests use InMemoryVectorStore)**

Run: `cd apps/server && python -m pytest tests/test_pgvec_store.py -v`
Expected: All PASS (these validate the contract, not the PG backend)

- [ ] **Step 3: Implement PgVecStore**

Create `apps/server/src/anima_server/services/agent/pgvec_store.py`:

```python
"""pgvector-backed VectorStore implementation.

Uses PostgreSQL's pgvector extension for O(log n) approximate nearest
neighbor search via HNSW indexes.  Falls back gracefully if the runtime
PG session is unavailable.
"""

from __future__ import annotations

import hashlib
import logging

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from anima_server.models.runtime_embedding import RuntimeEmbedding
from anima_server.services.agent.vector_store import VectorSearchResult, VectorStore

logger = logging.getLogger(__name__)


class PgVecStore(VectorStore):
    """Vector store backed by pgvector in the runtime PostgreSQL."""

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
        content_hash = hashlib.sha256(content.encode()).hexdigest()
        existing = self._db.scalar(
            select(RuntimeEmbedding).where(
                RuntimeEmbedding.user_id == user_id,
                RuntimeEmbedding.source_type == "memory_item",
                RuntimeEmbedding.source_id == item_id,
            )
        )
        if existing is not None:
            existing.embedding = embedding
            existing.content_hash = content_hash
            existing.content_preview = content[:200]
            existing.category = category
            existing.importance = importance
            existing.updated_at = func.now()
        else:
            self._db.add(RuntimeEmbedding(
                user_id=user_id,
                source_type="memory_item",
                source_id=item_id,
                content_hash=content_hash,
                embedding=embedding,
                content_preview=content[:200],
                category=category,
                importance=importance,
            ))
        self._db.flush()

    def delete(self, user_id: int, *, item_id: int) -> None:
        self._db.execute(
            delete(RuntimeEmbedding).where(
                RuntimeEmbedding.user_id == user_id,
                RuntimeEmbedding.source_type == "memory_item",
                RuntimeEmbedding.source_id == item_id,
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
        distance = RuntimeEmbedding.embedding.cosine_distance(query_embedding)
        stmt = (
            select(RuntimeEmbedding, (1 - distance).label("similarity"))
            .where(RuntimeEmbedding.user_id == user_id)
            .order_by(distance)
            .limit(limit)
        )
        if category is not None:
            stmt = stmt.where(RuntimeEmbedding.category == category)
        rows = self._db.execute(stmt).all()
        return [
            VectorSearchResult(
                item_id=row.RuntimeEmbedding.source_id,
                content=row.RuntimeEmbedding.content_preview,
                category=row.RuntimeEmbedding.category,
                importance=row.RuntimeEmbedding.importance,
                similarity=round(float(row.similarity), 4),
            )
            for row in rows
        ]

    def search_by_text(
        self,
        user_id: int,
        *,
        query_text: str,
        limit: int = 10,
        category: str | None = None,
    ) -> list[VectorSearchResult]:
        # Text search is handled by BM25 index, not pgvector.
        # This method exists to satisfy the abstract interface.
        return []

    def rebuild(
        self,
        user_id: int,
        items: list[tuple[int, str, list[float], str, int]],
    ) -> int:
        self._db.execute(
            delete(RuntimeEmbedding).where(
                RuntimeEmbedding.user_id == user_id,
            )
        )
        for item_id, content, embedding, category, importance in items:
            content_hash = hashlib.sha256(content.encode()).hexdigest()
            self._db.add(RuntimeEmbedding(
                user_id=user_id,
                source_type="memory_item",
                source_id=item_id,
                content_hash=content_hash,
                embedding=embedding,
                content_preview=content[:200],
                category=category,
                importance=importance,
            ))
        self._db.flush()
        return len(items)

    def count(self, user_id: int) -> int:
        return (
            self._db.scalar(
                select(func.count())
                .select_from(RuntimeEmbedding)
                .where(RuntimeEmbedding.user_id == user_id)
            )
            or 0
        )

    def reset(self) -> None:
        self._db.execute(delete(RuntimeEmbedding))
        self._db.flush()
```

- [ ] **Step 4: Run tests**

Run: `cd apps/server && python -m pytest tests/test_pgvec_store.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add apps/server/src/anima_server/services/agent/pgvec_store.py \
       apps/server/tests/test_pgvec_store.py
git commit -m "feat(p6): implement PgVecStore backend with pgvector cosine search"
```

---

## Task 5: Rewire vector_store.py to use PgVecStore as primary backend

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/vector_store.py`

- [ ] **Step 1: Add runtime session helper and update _get_store()**

In `vector_store.py`, add a helper to obtain a runtime PG session, and update `_get_store()` to prefer `PgVecStore` when a runtime session is available:

Replace the `_get_store` function and add a new `_get_runtime_session` helper. The key change: when a soul `db` session is provided, also try to get a runtime PG session for `PgVecStore`. If PG is unavailable, fall back to `OrmVecStore` (existing brute-force).

```python
def _try_get_runtime_session() -> Session | None:
    """Attempt to obtain a runtime PG session. Returns None if unavailable."""
    try:
        from anima_server.db.runtime import get_runtime_session_factory
        factory = get_runtime_session_factory()
        return factory()
    except (RuntimeError, Exception):
        return None


def _get_store(db: Session | None, *, runtime_db: Session | None = None) -> tuple[VectorStore, Session | None]:
    """Return (store, owned_runtime_session).

    If runtime PG is available, returns PgVecStore.  The caller must close
    the owned_runtime_session if it is not None.
    Falls back to OrmVecStore (soul DB) or InMemoryVectorStore (tests).
    """
    if runtime_db is not None:
        from anima_server.services.agent.pgvec_store import PgVecStore
        return PgVecStore(runtime_db), None

    # Try to get a PG session for PgVecStore
    rt_session = _try_get_runtime_session()
    if rt_session is not None:
        try:
            from anima_server.services.agent.pgvec_store import PgVecStore
            return PgVecStore(rt_session), rt_session
        except Exception:
            rt_session.close()

    # Fallback to soul DB or in-memory
    if db is not None:
        return OrmVecStore(db), None
    return _get_fallback_store(), None
```

- [ ] **Step 2: Update all public API functions to use new _get_store()**

Update `upsert_memory`, `delete_memory`, `search_similar`, `search_by_text`, `rebuild_user_index`, and `get_collection` to handle the owned session:

```python
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


def search_similar(
    user_id: int,
    *,
    query_embedding: list[float],
    limit: int = 10,
    category: str | None = None,
    db: Session | None = None,
    runtime_db: Session | None = None,
) -> list[dict[str, Any]]:
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
```

Apply the same owned-session pattern to `search_by_text`, `rebuild_user_index`, and `get_collection`.

- [ ] **Step 3: Run existing vector store tests**

Run: `cd apps/server && python -m pytest tests/test_vector_store.py -v`
Expected: All PASS (existing tests use `use_in_memory_store()` which bypasses PG)

- [ ] **Step 4: Commit**

```bash
git add apps/server/src/anima_server/services/agent/vector_store.py
git commit -m "feat(p6): rewire vector_store.py to prefer PgVecStore over OrmVecStore"
```

---

## Task 6: Add pgvector extension lifecycle to db/runtime.py

**Files:**
- Modify: `apps/server/src/anima_server/db/runtime.py`

- [ ] **Step 1: Add ensure_pgvector() function**

In `db/runtime.py`, add after `ensure_runtime_tables()`:

```python
def ensure_pgvector() -> None:
    """Enable the pgvector extension. Idempotent.

    Called during startup after engine init but before table creation.
    If pgvector is not available, logs a warning — the system falls
    back to brute-force search.
    """
    engine = get_runtime_engine()
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        logger.info("pgvector extension enabled.")
    except Exception:
        logger.warning(
            "pgvector extension not available. "
            "Vector search will fall back to brute-force."
        )
```

Add `text` to the sqlalchemy imports at the top of the file:

```python
from sqlalchemy import create_engine, text
```

- [ ] **Step 2: Wire ensure_pgvector into startup**

In `apps/server/src/anima_server/main.py`, call `ensure_pgvector()` after `init_runtime_engine()` and before `ensure_runtime_tables()`:

```python
from .db.runtime import dispose_runtime_engine, ensure_pgvector, ensure_runtime_tables, init_runtime_engine
```

In the `lifespan` function, after `init_runtime_engine(...)`:

```python
            ensure_pgvector()
            ensure_runtime_tables()
```

- [ ] **Step 3: Commit**

```bash
git add apps/server/src/anima_server/db/runtime.py \
       apps/server/src/anima_server/main.py
git commit -m "feat(p6): enable pgvector extension during startup lifecycle"
```

---

## Task 7: Rewire bm25_index.py to read from RuntimeEmbedding

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/bm25_index.py`

- [ ] **Step 1: Update get_or_build_index() data source**

Replace the `MemoryVector` query in `get_or_build_index()` with a `RuntimeEmbedding` query. Add a fallback to `MemoryVector` if the runtime session is unavailable.

```python
def get_or_build_index(user_id: int, *, db: Session) -> BM25Index:
    """Lazy-load the BM25 index for a user.

    Prefers RuntimeEmbedding (PG) as the data source.
    Falls back to MemoryVector (soul DB) if PG is unavailable.
    """
    with _indices_lock:
        if user_id in _user_indices:
            return _user_indices[user_id]

    # Try RuntimeEmbedding in PG first
    rows = None
    try:
        from anima_server.db.runtime import get_runtime_session_factory
        from anima_server.models.runtime_embedding import RuntimeEmbedding

        rt_session = get_runtime_session_factory()()
        try:
            rows = rt_session.execute(
                select(RuntimeEmbedding.source_id, RuntimeEmbedding.content_preview)
                .where(RuntimeEmbedding.user_id == user_id)
            ).all()
        finally:
            rt_session.close()
    except (RuntimeError, Exception):
        pass

    # Fallback to MemoryVector in soul DB
    if rows is None:
        from anima_server.models import MemoryVector

        rows = db.execute(
            select(MemoryVector.item_id, MemoryVector.content)
            .where(MemoryVector.user_id == user_id)
        ).all()

    index = BM25Index()
    index.build([(row[0], row[1]) for row in rows])

    with _indices_lock:
        _user_indices[user_id] = index

    return index
```

Add `select` import if not present.

- [ ] **Step 2: Run BM25 tests**

Run: `cd apps/server && python -m pytest tests/test_bm25_index.py -v`
Expected: All PASS (tests use the `BM25Index` class directly, not `get_or_build_index`)

- [ ] **Step 3: Commit**

```bash
git add apps/server/src/anima_server/services/agent/bm25_index.py
git commit -m "feat(p6): bm25_index reads from RuntimeEmbedding, falls back to MemoryVector"
```

---

## Task 8: Add cold-start sync function

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/embeddings.py`
- Create: `apps/server/tests/test_embedding_sync.py`

- [ ] **Step 1: Write sync tests**

Create `apps/server/tests/test_embedding_sync.py`:

```python
"""Tests for cold-start embedding sync from soul to runtime."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from anima_server.services.agent.embeddings import sync_embeddings_to_runtime


def test_sync_returns_zero_when_no_items():
    soul_db = MagicMock()
    soul_db.scalars.return_value.all.return_value = []
    count = sync_embeddings_to_runtime(soul_db, user_id=1)
    assert count == 0


def test_sync_skips_items_without_embeddings():
    mock_item = MagicMock()
    mock_item.id = 1
    mock_item.user_id = 1
    mock_item.embedding_json = None
    mock_item.content = "test"
    mock_item.category = "fact"
    mock_item.importance = 3

    soul_db = MagicMock()
    soul_db.scalars.return_value.all.return_value = [mock_item]
    count = sync_embeddings_to_runtime(soul_db, user_id=1)
    assert count == 0
```

- [ ] **Step 2: Add sync_embeddings_to_runtime function**

In `apps/server/src/anima_server/services/agent/embeddings.py`, add:

```python
def sync_embeddings_to_runtime(
    soul_db: Session,
    *,
    user_id: int,
) -> int:
    """Restore RuntimeEmbedding rows from soul's embedding_json cache.

    Called on cold start when runtime PG is empty but soul has cached
    vectors.  Idempotent — uses upsert via PgVecStore.
    Returns count of synced items.
    """
    items = list(
        soul_db.scalars(
            select(MemoryItem).where(
                MemoryItem.user_id == user_id,
                MemoryItem.superseded_by.is_(None),
                MemoryItem.embedding_json.isnot(None),
            )
        ).all()
    )

    if not items:
        return 0

    # Get a runtime PG session
    try:
        from anima_server.db.runtime import get_runtime_session_factory
        rt_session = get_runtime_session_factory()()
    except RuntimeError:
        logger.debug("Runtime PG unavailable for embedding sync")
        return 0

    try:
        from anima_server.services.agent.pgvec_store import PgVecStore
        store = PgVecStore(rt_session)

        count = 0
        for item in items:
            embedding = _parse_embedding(item.embedding_json)
            if embedding is None:
                continue
            plaintext = df(user_id, item.content, table="memory_items", field="content")
            store.upsert(
                user_id,
                item_id=item.id,
                content=plaintext,
                embedding=embedding,
                category=item.category,
                importance=item.importance,
            )
            count += 1

        if count > 0:
            rt_session.commit()
        return count
    except Exception:
        rt_session.rollback()
        logger.exception("Failed to sync embeddings to runtime PG for user %d", user_id)
        return 0
    finally:
        rt_session.close()
```

- [ ] **Step 3: Run sync tests**

Run: `cd apps/server && python -m pytest tests/test_embedding_sync.py -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add apps/server/src/anima_server/services/agent/embeddings.py \
       apps/server/tests/test_embedding_sync.py
git commit -m "feat(p6): add sync_embeddings_to_runtime for cold-start PG population"
```

---

## Task 9: Update forgetting.py and memory_store.py to delete from RuntimeEmbedding

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/forgetting.py`
- Modify: `apps/server/src/anima_server/services/agent/memory_store.py`

- [ ] **Step 1: Update forgetting.py**

In `forgetting.py`, in `_forget_single_item()` (around line 315-322), the existing code already calls `delete_memory()` from `vector_store.py`. Since `vector_store.py` now routes through PgVecStore, this should work automatically. However, add an explicit `runtime_db` pass-through if the function has access to a runtime session.

The existing code:
```python
    try:
        from anima_server.services.agent.vector_store import delete_memory
        for item_id in chain_ids:
            delete_memory(user_id, item_id=item_id, db=db)
    except Exception:
        logger.debug("Vector store cleanup failed for chain %s", chain_ids)
```

This already delegates to `_get_store()` which now prefers PgVecStore. No code change needed — the rewired `vector_store.py` handles routing.

Verify by reading the function — if it only passes `db=db` (soul session), the `_get_store()` function will still try to get a runtime session via `_try_get_runtime_session()`.

- [ ] **Step 2: Verify memory_store.py**

In `memory_store.py`, `supersede_memory_item()` calls:
```python
        from anima_server.services.agent.vector_store import delete_memory
        delete_memory(old_item.user_id, item_id=old_item_id, db=db)
```

Same situation — `_get_store()` routing handles this. No code change needed.

- [ ] **Step 3: Run forgetting tests**

Run: `cd apps/server && python -m pytest tests/ -k "forget" -v`
Expected: All PASS

- [ ] **Step 4: Commit (if any changes were needed)**

No commit needed if no changes — the routing is handled by Task 5.

---

## Task 10: Update vault.py to sync embeddings to PG after import

**Files:**
- Modify: `apps/server/src/anima_server/services/vault.py`

- [ ] **Step 1: Update _rebuild_vector_indices()**

In `vault.py`, update `_rebuild_vector_indices()` to call both the existing `sync_to_vector_store()` (for backward compat with in-memory fallback) AND `sync_embeddings_to_runtime()` (for PG):

```python
def _rebuild_vector_indices(db: Session, snapshot: dict[str, Any]) -> None:
    """Rebuild vector indices from imported embedding data."""
    try:
        from anima_server.services.agent.embeddings import (
            sync_embeddings_to_runtime,
            sync_to_vector_store,
        )

        user_ids = {int(u["id"]) for u in snapshot.get("users", []) if isinstance(u, dict)}
        for uid in user_ids:
            # Sync to PG (primary path)
            sync_embeddings_to_runtime(db, user_id=uid)
            # Also sync to legacy in-memory store (fallback path)
            sync_to_vector_store(db, user_id=uid)
    except Exception:
        import logging
        logging.getLogger(__name__).exception("Failed to rebuild vector indices")
```

- [ ] **Step 2: Run vault tests**

Run: `cd apps/server && python -m pytest tests/ -k "vault" -v`
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add apps/server/src/anima_server/services/vault.py
git commit -m "feat(p6): vault import syncs embeddings to runtime PG"
```

---

## Task 11: Run full test suite and fix regressions

**Files:**
- Various (fix any failures)

- [ ] **Step 1: Run the complete test suite**

Run: `cd apps/server && python -m pytest tests/ -x -v --timeout=120`
Expected: 846+ tests PASS, no regressions

- [ ] **Step 2: Fix any failures**

Address any import errors, session issues, or test isolation problems. Common issues:
- Tests that import `MemoryVector` directly should still work (model is retained)
- Tests using `use_in_memory_store()` should bypass PG entirely
- Tests that mock `vector_store.py` functions may need updated signatures

- [ ] **Step 3: Commit fixes**

```bash
git add -A
git commit -m "fix(p6): address test regressions from pgvector migration"
```

---

## Task 12: Final verification and cleanup

**Files:**
- None (verification only)

- [ ] **Step 1: Verify acceptance criteria**

Check each criterion from the PRD:

1. pgvector operational — migration creates extension + table
2. HNSW index — migration creates it
3. Embedding upsert — dual-write in `embed_memory_item()` (soul + PG)
4. Vector search uses pgvector — `PgVecStore.search_by_vector()` uses `<=>` operator
5. Brute-force fallback — `_get_store()` falls back to `OrmVecStore` / `InMemoryVectorStore`
6. Cold-start sync — `sync_embeddings_to_runtime()` restores from soul cache
7. BM25 reads from PG — `get_or_build_index()` queries `RuntimeEmbedding`
8. Forgetting cleans up — `delete_memory()` routes through `PgVecStore`
9. Vault import — `_rebuild_vector_indices()` calls sync
10. Performance — HNSW index provides O(log n)
11. MemoryVector deprecated — no code writes to it
12. No test regression — full suite passes

- [ ] **Step 2: Run full test suite one final time**

Run: `cd apps/server && python -m pytest tests/ -v --timeout=120`
Expected: All PASS

- [ ] **Step 3: Final commit if needed**

```bash
git add -A
git commit -m "feat(p6): pgvector embeddings migration complete"
```
