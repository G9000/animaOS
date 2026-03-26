---
title: "Phase 6: pgvector Embeddings"
description: Migrate vector search from in-memory brute-force over SQLCipher to pgvector in embedded PostgreSQL, giving persistent similarity search that survives process restarts without index rebuilds.
category: prd
version: "1.0"
---

# Phase 6: pgvector Embeddings

**Status**: Proposed
**Depends on**: P2 (Runtime Messages -- PostgreSQL infrastructure must be operational)
**Parallel with**: P3, P4, P5, P7 (no ordering dependency beyond P2)
**Estimated scope**: 1 PR

---

## Overview

AnimaOS currently stores embeddings in two places: a `MemoryVector` table in per-user SQLCipher (binary-packed float32 blobs) and a redundant `embedding_json` JSON column on `MemoryItem`. Search is brute-force cosine similarity -- every query loads all vectors into Python and computes dot products in a loop. The `OrmVecStore` in `vector_store.py` wraps this pattern behind an abstract interface, while `InMemoryVectorStore` provides a process-local fallback for tests.

This works at small scale (hundreds of vectors) but has three problems:

1. **No native ANN indexing.** Every search is O(n) over the full corpus, with float deserialization overhead from `struct.unpack`.
2. **SQLCipher single-writer.** Vector upserts compete with all other writes to `anima.db`, and concurrent search + write is constrained by WAL mode's single-writer limitation.
3. **Rebuild on restart.** The `BM25Index` (process-local) must be rebuilt from `MemoryVector` rows on first access. While BM25 rebuild is fast, vector search has no equivalent warm-up -- it simply scans everything every time.

Phase 6 migrates vector storage and similarity search to pgvector in the embedded PostgreSQL instance established in P1. pgvector provides native cosine distance operators, approximate nearest neighbor (ANN) indexes, and concurrent read/write access. The BM25 keyword index stays in-memory (it is fast to rebuild and process-local is acceptable for single-user).

### Why pgvector

| Concern | Current (SQLCipher brute-force) | pgvector |
|---------|-------------------------------|----------|
| Search complexity | O(n) Python loop per query | O(log n) with HNSW index; O(sqrt(n)) with IVFFlat |
| Index persistence | None -- scan on every query | Native PostgreSQL indexes survive restarts |
| Concurrent access | SQLite single-writer constraint | Row-level locking, connection pooling |
| Operator support | Manual `cosine_similarity()` in Python | `<=>` (cosine), `<->` (L2), `<#>` (inner product) |
| Dimension flexibility | Implicit (float32 blob length) | Explicit `vector(dim)` column type with validation |

---

## Scope

### In scope

- Install and enable the `pgvector` extension in the embedded PostgreSQL lifecycle (P1 infrastructure).
- Define a `RuntimeEmbedding` SQLAlchemy model on the runtime (PG) engine.
- Implement a `PgVecStore` backend that uses pgvector's `<=>` operator for cosine distance.
- Rewire `embeddings.py` to use `PgVecStore` as the primary search backend.
- Rewire `bm25_index.py` to build from `RuntimeEmbedding` rows instead of `MemoryVector`.
- Create an ANN index (HNSW preferred, IVFFlat as fallback) on the embedding column.
- Add a startup sync path: on first access, if `RuntimeEmbedding` is empty but `MemoryItem.embedding_json` has data, bulk-insert from the soul store.
- Retain `MemoryItem.embedding_json` as a portable cache for `.anima/` transfers.
- Deprecate `MemoryVector` in SQLCipher (stop writing; leave table for backward compat).
- Add configuration for embedding dimension.
- Maintain the `InMemoryVectorStore` test backend (no pgvector required in unit tests).

### Out of scope

- Changing the embedding generation pipeline (`generate_embedding`, provider selection, LRU cache). That stays as-is.
- Moving `MemoryItem` itself out of SQLCipher. Soul data stays in the soul store.
- BM25 migration to PostgreSQL full-text search. BM25 stays in-memory (fast rebuild, process-local).
- Multi-user partitioning or row-level security in PostgreSQL. Single-user embedded instance.
- Changes to the hybrid search algorithm (RRF merge, adaptive filtering, BM25 reranking). The algorithm stays; only the vector retrieval backend changes.

---

## Implementation Details

### 1. pgvector extension lifecycle

During P1, the embedded PostgreSQL startup sequence (`db/runtime.py`) initializes the database. Phase 6 adds a step to ensure the pgvector extension is created.

```python
# db/runtime.py -- added to ensure_runtime_database() or equivalent
async def _ensure_pgvector(engine: AsyncEngine) -> None:
    """Enable pgvector extension. Idempotent."""
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
```

This runs once at startup, before any embedding operations. If the extension is not available (e.g., pgvector not installed in the embedded PG), the system logs a warning and falls back to brute-force search via `embedding_json` -- the existing fallback path in `embeddings.py` already handles this.

### 2. RuntimeEmbedding model

A new model on the runtime (PostgreSQL) base, separate from the soul SQLCipher models.

```python
from pgvector.sqlalchemy import Vector

class RuntimeEmbedding(RuntimeBase):
    __tablename__ = "embeddings"
    __table_args__ = (
        Index(
            "ix_embeddings_user_source",
            "user_id", "source_type", "source_id",
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    source_type: Mapped[str] = mapped_column(
        String(24), nullable=False,
    )  # "memory_item" | "episode" | "entity"
    source_id: Mapped[int] = mapped_column(
        Integer, nullable=False,
    )  # PK in the source table
    content_hash: Mapped[str] = mapped_column(
        String(64), nullable=False,
    )  # SHA-256 of plaintext, for staleness detection
    embedding: Mapped[Any] = mapped_column(
        Vector(dim=None),  # dim set at table creation from config
        nullable=False,
    )
    content_preview: Mapped[str] = mapped_column(
        String(200), nullable=False, server_default=text("''"),
    )  # first 200 chars for debugging
    category: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default=text("'fact'"),
    )
    importance: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("3"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
```

**Design decisions:**

- **`source_type` + `source_id`** instead of a direct FK to `memory_items`. Memory items live in SQLCipher; we cannot create a cross-database foreign key. The composite `(user_id, source_type, source_id)` unique index prevents duplicates and enables upsert-by-source.
- **`content_hash`** enables staleness detection without decrypting and comparing full content. When a memory item is updated (superseded), the new item gets a new embedding; the old one's `content_hash` will not match and can be pruned.
- **`content_preview`** stores the first 200 characters of plaintext for debugging and BM25 index construction. This is plaintext in the runtime store, which is acceptable because the runtime is ephemeral and local-only (not portable).
- **`Vector(dim=None)`** -- the dimension is determined at table creation time from `settings.agent_embedding_dim`. The pgvector extension validates dimension consistency on insert.

### 3. Embedding dimension configuration

Add a new setting to `config.py`:

```python
agent_embedding_dim: int = 768  # nomic-embed-text default; override for other models
```

The dimension must match the embedding model output. Common values:
- `nomic-embed-text` (Ollama default): 768
- `text-embedding-3-small` (OpenRouter): 1536
- `all-MiniLM-L6-v2`: 384

The table DDL uses this value. Changing the embedding model after data exists requires a re-embed (clearing and regenerating all embeddings).

### 4. PgVecStore backend

A new `VectorStore` implementation that uses pgvector operators.

```python
class PgVecStore(VectorStore):
    """Vector store backed by pgvector in the runtime PostgreSQL."""

    def __init__(self, session: Session) -> None:
        self._db = session

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
    # ... delete, search_by_text, rebuild, count, reset follow same pattern
```

**Key change:** `search_by_vector` is no longer O(n). With an HNSW index, pgvector returns approximate nearest neighbors in O(log n) without loading all vectors into Python.

### 5. ANN index creation

Created during startup or via Alembic migration on the runtime database:

```sql
-- HNSW index for cosine distance (preferred)
CREATE INDEX IF NOT EXISTS ix_embeddings_hnsw
ON embeddings
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);
```

**Index type decision:**

| Index | Build time | Query time | Memory | Best for |
|-------|-----------|------------|--------|----------|
| HNSW | Slower | Faster (O(log n)) | Higher | < 100K vectors, query-heavy |
| IVFFlat | Faster | Slower (O(sqrt(n))) | Lower | > 100K vectors, write-heavy |

AnimaOS is single-user with a personal knowledge base. Expected corpus: hundreds to low thousands of vectors. HNSW is the right default. The index is created programmatically at startup alongside the `CREATE EXTENSION` call.

**Tuning parameters:**
- `m = 16` -- connections per node (default, good for < 100K vectors)
- `ef_construction = 64` -- build-time quality (default)
- Query-time `ef_search` left at PostgreSQL default (40); can be tuned per-session if needed

### 6. Rewiring embeddings.py

The primary changes to `embeddings.py`:

**`semantic_search()`**: Replace the `search_similar()` call chain. Instead of routing through the old `_get_store()` dispatcher, call `PgVecStore.search_by_vector()` directly via a runtime session. The brute-force fallback over `embedding_json` stays as a degraded-mode path for when pgvector is unavailable.

**`hybrid_search()`**: The semantic leg switches from `search_similar()` (which was brute-force) to `PgVecStore.search_by_vector()`. The keyword leg (BM25) stays in-memory. The RRF merge, BM25 reranking, and adaptive filtering are unchanged.

**`embed_memory_item()`**: Dual-write stays. On embedding a memory item:
1. Write `embedding_json` on the `MemoryItem` in SQLCipher (portable cache).
2. Upsert `RuntimeEmbedding` in PostgreSQL (search index).

**`backfill_embeddings()`**: Same logic, but upserts go to PostgreSQL instead of `MemoryVector`.

**`sync_to_vector_store()`**: Rewired to bulk-insert `RuntimeEmbedding` rows from `embedding_json`. Used after vault import and on cold start.

### 7. Rewiring bm25_index.py

`get_or_build_index()` currently queries `MemoryVector` from SQLCipher. After this phase, it queries `RuntimeEmbedding` from PostgreSQL instead:

```python
rows = runtime_db.execute(
    select(
        RuntimeEmbedding.source_id,
        RuntimeEmbedding.content_preview,
    ).where(RuntimeEmbedding.user_id == user_id)
).all()
```

This is a data source change, not a logic change. The BM25 index itself remains process-local.

### 8. Cold-start sync

When the runtime PostgreSQL starts fresh (new machine, cleared runtime data), `RuntimeEmbedding` is empty but `MemoryItem.embedding_json` in the soul store may have cached vectors. A sync function restores them:

```python
async def sync_embeddings_from_soul(
    soul_db: Session,
    runtime_db: Session,
    *,
    user_id: int,
) -> int:
    """Restore RuntimeEmbedding rows from soul's embedding_json cache.

    Called on cold start when runtime PG is empty but soul has cached vectors.
    """
    items = soul_db.scalars(
        select(MemoryItem).where(
            MemoryItem.user_id == user_id,
            MemoryItem.superseded_by.is_(None),
            MemoryItem.embedding_json.isnot(None),
        )
    ).all()

    count = 0
    for item in items:
        embedding = _parse_embedding(item.embedding_json)
        if embedding is None:
            continue
        plaintext = df(user_id, item.content, table="memory_items", field="content")
        content_hash = hashlib.sha256(plaintext.encode()).hexdigest()
        runtime_db.add(RuntimeEmbedding(
            user_id=user_id,
            source_type="memory_item",
            source_id=item.id,
            content_hash=content_hash,
            embedding=embedding,
            content_preview=plaintext[:200],
            category=item.category,
            importance=item.importance,
        ))
        count += 1

    if count > 0:
        runtime_db.flush()
    return count
```

This runs lazily on first embedding search if the `RuntimeEmbedding` table is empty for the user. It does not re-compute embeddings -- it restores from the soul cache. Items without cached embeddings are picked up by the existing `backfill_embeddings()` on the next consolidation cycle.

### 9. Deprecating MemoryVector

`MemoryVector` in SQLCipher (`models/agent_runtime.py`) is deprecated:

- Stop writing to it. All new embedding writes go to `RuntimeEmbedding` in PostgreSQL.
- Stop reading from it. BM25 index and vector search read from `RuntimeEmbedding`.
- Leave the table definition in the model file (backward compat for existing databases).
- Remove imports and references from `vector_store.py`, `bm25_index.py`, `forgetting.py`.
- The `OrmVecStore` class is replaced by `PgVecStore`. `InMemoryVectorStore` stays for tests.

### 10. Forgetting integration

`forgetting.py` calls `delete_memory()` from `vector_store.py` when items are forgotten. This must be updated to delete from `RuntimeEmbedding` in PostgreSQL:

```python
runtime_db.execute(
    delete(RuntimeEmbedding).where(
        RuntimeEmbedding.user_id == user_id,
        RuntimeEmbedding.source_type == "memory_item",
        RuntimeEmbedding.source_id == item_id,
    )
)
```

The BM25 invalidation call stays the same.

---

## Files to Create/Modify

### New files

| File | Purpose |
|------|---------|
| `models/runtime_embedding.py` | `RuntimeEmbedding` model definition on `RuntimeBase` |
| `services/agent/pgvec_store.py` | `PgVecStore` implementation using pgvector operators |
| `tests/test_pgvec_store.py` | Integration tests for `PgVecStore` (requires PG fixture) |
| `tests/test_embedding_sync.py` | Tests for cold-start sync from soul to runtime |

### Modified files

| File | Changes |
|------|---------|
| `config.py` | Add `agent_embedding_dim: int = 768` |
| `db/runtime.py` | Add `_ensure_pgvector()` call in startup sequence |
| `models/__init__.py` | Export `RuntimeEmbedding`; keep `MemoryVector` export for backward compat |
| `services/agent/vector_store.py` | Replace `OrmVecStore` dispatch with `PgVecStore`; keep `InMemoryVectorStore` for tests; update `_get_store()` to accept runtime session |
| `services/agent/embeddings.py` | Rewire `semantic_search()`, `hybrid_search()`, `embed_memory_item()`, `backfill_embeddings()`, `sync_to_vector_store()` to use runtime PG session |
| `services/agent/bm25_index.py` | `get_or_build_index()` queries `RuntimeEmbedding` instead of `MemoryVector` |
| `services/agent/forgetting.py` | `_forget_single_item()` deletes from `RuntimeEmbedding` instead of `MemoryVector` |
| `services/agent/memory_store.py` | `supersede_memory_item()` deletes old embedding from `RuntimeEmbedding` |
| `services/vault.py` | `_rebuild_vector_indices()` calls `sync_embeddings_from_soul()` targeting PG |
| `services/agent/consolidation.py` | `_backfill_user_embeddings()` upserts to PG via `PgVecStore` |
| `tests/test_vector_store.py` | Update to test both `PgVecStore` (integration) and `InMemoryVectorStore` (unit) |
| `tests/test_bm25_index.py` | Update data source mock to use `RuntimeEmbedding` |

---

## Models / Schemas

### RuntimeEmbedding (PostgreSQL)

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | `Integer` | PK, autoincrement | |
| `user_id` | `Integer` | NOT NULL, indexed | No FK (users table is in SQLCipher) |
| `source_type` | `String(24)` | NOT NULL | `"memory_item"`, `"episode"`, `"entity"` |
| `source_id` | `Integer` | NOT NULL | PK of the source record in soul store |
| `content_hash` | `String(64)` | NOT NULL | SHA-256 hex digest of plaintext content |
| `embedding` | `Vector(dim)` | NOT NULL | pgvector column; dim from `agent_embedding_dim` |
| `content_preview` | `String(200)` | NOT NULL, default `''` | First 200 chars of plaintext for debugging/BM25 |
| `category` | `String(24)` | NOT NULL, default `'fact'` | Mirrors `MemoryItem.category` |
| `importance` | `Integer` | NOT NULL, default `3` | Mirrors `MemoryItem.importance` |
| `created_at` | `DateTime(tz)` | NOT NULL, default `now()` | |
| `updated_at` | `DateTime(tz)` | NOT NULL, default `now()` | |

**Indexes:**

| Name | Columns | Type | Purpose |
|------|---------|------|---------|
| `ix_embeddings_user_source` | `(user_id, source_type, source_id)` | UNIQUE B-tree | Dedup / upsert by source |
| `ix_embeddings_user_id` | `(user_id)` | B-tree | Filter by user |
| `ix_embeddings_hnsw` | `(embedding)` | HNSW `vector_cosine_ops` | ANN similarity search |

### Configuration additions

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `agent_embedding_dim` | `int` | `768` | Embedding vector dimension (must match model output) |

### Deprecated model

`MemoryVector` in `models/agent_runtime.py` is retained for backward compatibility but no longer written to or read from. A future migration can drop the table.

---

## Migration Strategy

### Runtime database (PostgreSQL)

The runtime database is ephemeral -- it is rebuilt when the PostgreSQL data directory is missing. No Alembic migration is needed for the runtime schema. Table creation uses `RuntimeBase.metadata.create_all()` during startup. The pgvector extension is created before table creation.

Startup sequence after this phase:

1. Start embedded PostgreSQL (P1)
2. `CREATE EXTENSION IF NOT EXISTS vector` (P6)
3. `RuntimeBase.metadata.create_all()` -- creates `embeddings` table with `vector` column
4. Create HNSW index if not exists
5. Check if `RuntimeEmbedding` is empty for each active user
6. If empty, run `sync_embeddings_from_soul()` to restore from `embedding_json` cache

### Soul database (SQLCipher)

No schema changes. `MemoryItem.embedding_json` stays. `MemoryVector` table stays (dead weight, no writes).

A future Alembic migration can drop `MemoryVector` if desired, but this is not required for Phase 6.

### Data flow during transition

During development, the system supports both paths:

1. **pgvector available:** `PgVecStore` is primary. Brute-force fallback disabled.
2. **pgvector unavailable:** Falls back to brute-force over `embedding_json` (existing behavior). A warning is logged at startup.

This is achieved by wrapping `PgVecStore` construction in a try/except. If the runtime session or pgvector extension is unavailable, `_get_store()` returns the `InMemoryVectorStore` fallback (which is populated from `embedding_json` via `sync_to_vector_store()`).

### Vault import/export

- **Export:** `embedding_json` on `MemoryItem` is included in vault exports (existing behavior, unchanged).
- **Import:** After importing a vault, `_rebuild_vector_indices()` calls `sync_embeddings_from_soul()` to populate `RuntimeEmbedding` from the imported `embedding_json` data.

---

## Test Plan

### Unit tests (no PostgreSQL required)

| Test | File | What it validates |
|------|------|-------------------|
| `InMemoryVectorStore` upsert/search/delete | `test_vector_store.py` | Existing tests continue to pass with in-memory backend |
| `InMemoryVectorStore` rebuild | `test_vector_store.py` | Rebuild replaces index correctly |
| `BM25Index` build/search | `test_bm25_index.py` | BM25 logic unchanged (data source mocked) |
| `_parse_embedding` handles all formats | `test_embeddings.py` | JSON string, list, None |
| `content_hash` dedup logic | `test_pgvec_store.py` | SHA-256 computed correctly, upsert updates on hash match |
| Embedding dimension config | `test_config.py` | `agent_embedding_dim` defaults to 768, overridable |

### Integration tests (require PostgreSQL fixture)

| Test | File | What it validates |
|------|------|-------------------|
| pgvector extension loads | `test_pgvec_store.py` | `CREATE EXTENSION IF NOT EXISTS vector` succeeds |
| Insert and cosine search | `test_pgvec_store.py` | Insert 3 vectors, query returns correct ranked order |
| Category filter | `test_pgvec_store.py` | Search with `category="fact"` excludes non-fact vectors |
| Upsert (content_hash dedup) | `test_pgvec_store.py` | Re-inserting same source updates embedding, does not create duplicate |
| Delete removes from index | `test_pgvec_store.py` | After delete, vector no longer appears in search results |
| Rebuild replaces all | `test_pgvec_store.py` | Rebuild with subset replaces previous index |
| HNSW index creation | `test_pgvec_store.py` | Index exists after startup sequence |
| Cold-start sync | `test_embedding_sync.py` | Empty PG + soul with `embedding_json` -> PG populated correctly |
| Cold-start sync idempotent | `test_embedding_sync.py` | Running sync twice does not create duplicates |
| Hybrid search end-to-end | `test_embeddings.py` | `hybrid_search()` returns results using pgvector backend |
| Forgetting deletes embedding | `test_forgetting.py` | `_forget_single_item()` removes from `RuntimeEmbedding` |

### Performance tests

| Test | Target | Method |
|------|--------|--------|
| Vector search over 1K embeddings | < 20ms | Insert 1,000 random 768-dim vectors, measure p99 query latency |
| Vector search over 10K embeddings | < 100ms | Insert 10,000 random 768-dim vectors, measure p99 query latency |
| Cold-start sync of 1K items | < 5s | Measure time to bulk-insert 1,000 embeddings from soul cache |
| HNSW index build on 10K vectors | < 30s | Measure index creation time |

### Regression

- All existing 846+ tests pass.
- `test_vector_store.py` existing tests pass against `InMemoryVectorStore` (unchanged).
- `test_bm25_index.py` existing tests pass (data source change is transparent).
- Vault export/import round-trip preserves embeddings.

---

## Acceptance Criteria

1. **pgvector operational.** The embedded PostgreSQL starts with the `vector` extension enabled. `RuntimeEmbedding` table is created with a `vector` column of the configured dimension.

2. **HNSW index exists.** An HNSW index on the `embedding` column is created at startup and used by cosine similarity queries (verified via `EXPLAIN ANALYZE`).

3. **Embedding upsert works.** `embed_memory_item()` writes to both `MemoryItem.embedding_json` (soul, portable cache) and `RuntimeEmbedding` (runtime, search index). Duplicate source `(user_id, source_type, source_id)` updates rather than inserts.

4. **Vector search uses pgvector.** `semantic_search()` and the semantic leg of `hybrid_search()` use pgvector's `<=>` cosine distance operator. No Python-side cosine computation for the primary path.

5. **Brute-force fallback retained.** If pgvector is unavailable (extension missing, PG not running), the system falls back to brute-force over `embedding_json` and logs a warning. No crash, no silent failure.

6. **Cold-start sync works.** When `RuntimeEmbedding` is empty but `MemoryItem.embedding_json` has cached data, `sync_embeddings_from_soul()` populates the PG table without re-computing embeddings. This is idempotent.

7. **BM25 index builds from PG.** `bm25_index.py` reads `content_preview` from `RuntimeEmbedding` in PostgreSQL, not from `MemoryVector` in SQLCipher.

8. **Forgetting cleans up.** When a memory item is forgotten, the corresponding `RuntimeEmbedding` row is deleted.

9. **Vault import restores embeddings.** After importing a vault, `RuntimeEmbedding` is populated from the imported `embedding_json` data via `sync_embeddings_from_soul()`.

10. **Performance.** Cosine similarity search over 10,000 768-dimensional vectors completes in under 100ms (p99).

11. **MemoryVector deprecated.** No code path writes to or reads from the `MemoryVector` table in SQLCipher. The table definition remains for backward compatibility.

12. **No test regression.** All existing tests pass. The `InMemoryVectorStore` backend remains available for unit tests that do not need PostgreSQL.

---

## Out of Scope

- **Embedding model changes.** The embedding generation pipeline (`generate_embedding`, `_embed_ollama`, `_embed_openai_compatible`, provider selection, LRU cache) is unchanged.
- **BM25 to PostgreSQL full-text search.** The BM25 index stays process-local. PostgreSQL `tsvector`/`tsquery` is a possible future optimization but not part of this phase.
- **Multi-source embedding.** The `source_type` field supports `"episode"` and `"entity"` for future use, but this phase only implements `"memory_item"` embeddings. Episode and KG entity embeddings are deferred.
- **Dropping MemoryVector table.** The table stays in the SQLCipher schema for backward compat. A future Alembic migration can remove it.
- **Quantized vectors.** pgvector supports `halfvec` (float16) for reduced storage. Not needed at current scale; revisit if storage becomes a concern.
- **Re-ranking model.** The BM25 reranking in `_bm25_rerank()` is a lightweight lexical boost, not a neural reranker. Adding a cross-encoder reranker is a separate effort.
- **Context-aware memory gating.** The Engram paper (Cheng et al., 2026) demonstrates that retrieved knowledge should be gated by the full conversation context, not just the query — suppressing topically related but situationally irrelevant memories. After pgvector enables fast retrieval (this phase), a second-stage contextual gate becomes the natural next enhancement. See master PRD Section 11.2.
- **Frequency-aware promotion.** Tracking per-memory access frequency to dynamically promote high-frequency memories to always-loaded status (Tier 0). See master PRD Section 11.3.
- **Embedding versioning.** If the user changes their embedding model (different dimensions or semantic space), existing embeddings become invalid. A "re-embed all" command is a useful future feature but out of scope here. For now, changing `agent_embedding_dim` after data exists requires manually clearing `RuntimeEmbedding` and `embedding_json`.
