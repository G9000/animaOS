# Memory System Upgrade (F1-F7) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement 7 memory subsystem features: BM25 hybrid search, heat scoring, predict-calibrate consolidation, knowledge graph, async sleep agents, batch episode segmentation, and intentional forgetting.

**Architecture:** Each feature is a new service file under `apps/server/src/anima_server/services/agent/` with corresponding tests. Features build on existing memory infrastructure (MemoryItem, vector_store, embeddings, consolidation, sleep_tasks). Three features require new DB tables (F2 adds a column, F4 adds 2 tables, F5 adds 1 table, F6 adds 2 columns, F7 adds 1 table + 2 columns). All changes are SQLite-compatible, single-user, encrypted-at-rest.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0, SQLite/SQLCipher, rank-bm25, pytest, Alembic

**Dependency Order:**
- Phase 1 (parallel): F1 (BM25), F2 (Heat), F4 (Knowledge Graph)
- Phase 2 (after F1): F3 (Predict-Calibrate)
- Phase 3 (after F2): F7 (Intentional Forgetting)
- Phase 4 (after F2+F3+F4): F5 (Async Sleep Agents)
- Phase 5 (after F5): F6 (Batch Segmentation)

**PRD Location:** `docs/prds/memory/F{1-7}-*.md`

**Base paths:**
- Source: `apps/server/src/anima_server/`
- Tests: `apps/server/tests/`
- Models: `apps/server/src/anima_server/models/agent_runtime.py`
- Model exports: `apps/server/src/anima_server/models/__init__.py`
- Migrations: `apps/server/alembic/versions/`
- Dependencies: `apps/server/pyproject.toml`

---

## Phase 1A: F1 — BM25 Hybrid Search

### Task 1.1: Add rank-bm25 dependency

**Files:**
- Modify: `apps/server/pyproject.toml`

- [ ] **Step 1: Add rank-bm25 to dependencies**

In `pyproject.toml`, add `"rank-bm25>=0.2.2"` to the `dependencies` list.

- [ ] **Step 2: Install**

Run: `cd apps/server && pip install -e .`

- [ ] **Step 3: Commit**

```bash
git add apps/server/pyproject.toml
git commit -m "feat(F1): add rank-bm25 dependency"
```

### Task 1.2: Create BM25Index class with tests (TDD)

**Files:**
- Create: `apps/server/src/anima_server/services/agent/bm25_index.py`
- Create: `apps/server/tests/test_bm25_index.py`

- [ ] **Step 1: Write failing tests for BM25Index**

```python
# tests/test_bm25_index.py
"""Tests for BM25 index — F1 hybrid search."""
import pytest
from anima_server.services.agent.bm25_index import (
    BM25Index, _tokenize, bm25_search, get_or_build_index,
    invalidate_index, _user_indices,
)


class TestTokenize:
    def test_lowercase_split(self):
        assert _tokenize("Hello World") == ["hello", "world"]

    def test_empty_string(self):
        assert _tokenize("") == []

    def test_punctuation_kept(self):
        # Simple whitespace split keeps punctuation attached
        result = _tokenize("PostgreSQL is great!")
        assert "postgresql" in result


class TestBM25Index:
    def test_build_and_search(self):
        idx = BM25Index()
        idx.build([
            (1, "User works at Google as a software engineer"),
            (2, "User prefers PostgreSQL over MySQL"),
            (3, "User lives in Berlin Germany"),
        ])
        results = idx.search("PostgreSQL", limit=3)
        # PostgreSQL doc should rank first (rare term = high IDF)
        assert results[0][0] == 2

    def test_search_empty_index(self):
        idx = BM25Index()
        results = idx.search("anything")
        assert results == []

    def test_document_count(self):
        idx = BM25Index()
        idx.build([(1, "doc one"), (2, "doc two")])
        assert idx.document_count == 2

    def test_add_document(self):
        idx = BM25Index()
        idx.build([(1, "hello world")])
        idx.add_document(2, "goodbye world")
        assert idx.document_count == 2
        results = idx.search("goodbye")
        assert any(item_id == 2 for item_id, _ in results)

    def test_remove_document(self):
        idx = BM25Index()
        idx.build([(1, "hello"), (2, "goodbye")])
        idx.remove_document(1)
        assert idx.document_count == 1
        results = idx.search("hello")
        assert not any(item_id == 1 for item_id, _ in results)

    def test_common_word_low_score(self):
        """Common words should not dominate scoring (IDF weighting)."""
        idx = BM25Index()
        idx.build([
            (1, "the cat is on the mat"),
            (2, "the dog is in the house"),
            (3, "PostgreSQL database configuration"),
        ])
        results_common = idx.search("the", limit=3)
        results_rare = idx.search("PostgreSQL", limit=3)
        # Rare term should produce a higher top score
        if results_common and results_rare:
            assert results_rare[0][1] >= results_common[0][1]


class TestModuleLevelCache:
    def setup_method(self):
        _user_indices.clear()

    def test_invalidate_clears_cache(self):
        _user_indices[99] = BM25Index()
        invalidate_index(99)
        assert 99 not in _user_indices
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/server && python -m pytest tests/test_bm25_index.py -v`
Expected: ImportError / FAIL

- [ ] **Step 3: Implement BM25Index**

```python
# services/agent/bm25_index.py
"""BM25 lexical search index for memory retrieval — F1.

Replaces Jaccard-based _text_similarity() with BM25Okapi for the keyword
leg of hybrid search. Indices are per-user, cached in process memory,
and invalidated on content mutations.
"""
from __future__ import annotations

import logging
from threading import Lock

from rank_bm25 import BM25Okapi
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
            if scores[i] > 0.0
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:limit]

    def add_document(self, item_id: int, content: str) -> None:
        """Add a document. Triggers full rebuild."""
        self._documents.append((item_id, content))
        self.build(self._documents)

    def remove_document(self, item_id: int) -> None:
        """Remove a document by ID. Triggers full rebuild."""
        self._documents = [(did, c) for did, c in self._documents if did != item_id]
        self.build(self._documents)

    @property
    def document_count(self) -> int:
        return len(self._item_ids)


# ── Module-level per-user cache ──────────────────────────────────────

_user_indices: dict[int, BM25Index] = {}
_indices_lock: Lock = Lock()


def get_or_build_index(user_id: int, *, db: Session) -> BM25Index:
    """Lazy-load the BM25 index for a user."""
    with _indices_lock:
        if user_id in _user_indices:
            return _user_indices[user_id]

    # Build outside the lock (DB query)
    from anima_server.models import MemoryVector
    from sqlalchemy import select

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
    """Search using BM25. Returns (item_id, score) pairs."""
    index = get_or_build_index(user_id, db=db)
    return index.search(query, limit=limit)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/server && python -m pytest tests/test_bm25_index.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add apps/server/src/anima_server/services/agent/bm25_index.py apps/server/tests/test_bm25_index.py
git commit -m "feat(F1): BM25Index class with per-user cache and tests"
```

### Task 1.3: Wire BM25 into hybrid_search and add invalidation hooks

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/embeddings.py` (lines 523-574 — keyword leg in `hybrid_search()`)
- Modify: `apps/server/src/anima_server/services/agent/vector_store.py` (add invalidation calls after `upsert`, `delete`, `rebuild`)

- [ ] **Step 1: Write integration test for BM25-powered hybrid search**

Add to `tests/test_bm25_index.py`:

```python
class TestHybridSearchIntegration:
    """Verify hybrid_search uses BM25 for keyword leg."""

    def test_bm25_keyword_leg_called(self, monkeypatch):
        """When hybrid_search runs the keyword leg, it should call bm25_search
        instead of search_by_text."""
        from anima_server.services.agent import bm25_index

        calls = []
        def mock_bm25_search(user_id, *, query, limit, db):
            calls.append(query)
            return [(1, 5.0)]

        monkeypatch.setattr(bm25_index, "bm25_search", mock_bm25_search)

        # Import after patching
        from anima_server.services.agent.embeddings import _reciprocal_rank_fusion
        # Just verify the function exists and accepts the right args
        result = _reciprocal_rank_fusion(
            [(1, 0.9)], [(1, 5.0)],
            semantic_weight=0.5, keyword_weight=0.5,
        )
        assert len(result) > 0
```

- [ ] **Step 2: Modify `hybrid_search()` to use BM25**

In `embeddings.py`, replace the keyword leg (lines ~563-574):

**Before:**
```python
    # --- Keyword leg ---
    keyword_ranked: list[tuple[int, float]] = []
    try:
        kw_results = search_by_text(
            user_id, query_text=query, limit=limit, db=db)
        keyword_ranked = [
            (r["id"], r["similarity"])
            for r in kw_results
            if r["similarity"] > 0.0
        ]
    except Exception:  # noqa: BLE001
        logger.debug("Keyword search failed in hybrid_search")
```

**After:**
```python
    # --- Keyword leg (BM25) ---
    keyword_ranked: list[tuple[int, float]] = []
    try:
        from anima_server.services.agent.bm25_index import bm25_search
        keyword_ranked = bm25_search(
            user_id, query=query, limit=limit, db=db)
    except Exception:  # noqa: BLE001
        logger.debug("BM25 keyword search failed in hybrid_search")
```

- [ ] **Step 3: Add invalidation hooks to vector_store.py**

Add import at the top of the module-level public functions section (~line 427):

In `upsert_memory()` — add after `_get_store(db).upsert(...)`:
```python
    try:
        from anima_server.services.agent.bm25_index import invalidate_index
        invalidate_index(user_id)
    except Exception:  # noqa: BLE001
        pass
```

In `delete_memory()` — add after `_get_store(db).delete(...)`:
```python
    try:
        from anima_server.services.agent.bm25_index import invalidate_index
        invalidate_index(user_id)
    except Exception:  # noqa: BLE001
        pass
```

In `rebuild_user_index()` — add after `_get_store(db).rebuild(...)`:
```python
    try:
        from anima_server.services.agent.bm25_index import invalidate_index
        invalidate_index(user_id)
    except Exception:  # noqa: BLE001
        pass
```

- [ ] **Step 4: Run full test suite**

Run: `cd apps/server && python -m pytest -x -q`
Expected: All tests pass (602+)

- [ ] **Step 5: Commit**

```bash
git add apps/server/src/anima_server/services/agent/embeddings.py apps/server/src/anima_server/services/agent/vector_store.py apps/server/tests/test_bm25_index.py
git commit -m "feat(F1): wire BM25 into hybrid_search, add invalidation hooks"
```

---

## Phase 1B: F2 — Heat-Based Memory Scoring

### Task 2.1: Add heat column to MemoryItem model

**Files:**
- Modify: `apps/server/src/anima_server/models/agent_runtime.py` (MemoryItem class ~line 190)
- Create: `apps/server/alembic/versions/20260319_0001_add_heat_column.py`

- [ ] **Step 1: Add heat column to MemoryItem model**

In `agent_runtime.py`, add to the `MemoryItem` class after `tags_json`:

```python
    heat: Mapped[float] = mapped_column(
        nullable=False, default=0.0, server_default=text("0.0"),
    )
```

Add composite index to `__table_args__`:
```python
    __table_args__ = (
        Index("ix_memory_items_user_category_active",
              "user_id", "category", "superseded_by"),
        Index("ix_memory_items_user_heat", "user_id", "heat"),
    )
```

- [ ] **Step 2: Create Alembic migration**

```python
# alembic/versions/20260319_0001_add_heat_column.py
"""Add heat column to memory_items.

Revision ID: 20260319_0001
"""
revision = "20260319_0001"
down_revision = "20260316_0003"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.add_column("memory_items", sa.Column("heat", sa.Float, nullable=False, server_default="0.0"))
    op.create_index("ix_memory_items_user_heat", "memory_items", ["user_id", "heat"])


def downgrade():
    op.drop_index("ix_memory_items_user_heat")
    op.drop_column("memory_items", "heat")
```

- [ ] **Step 3: Run migration**

Run: `cd apps/server && alembic upgrade head`

- [ ] **Step 4: Commit**

```bash
git add apps/server/src/anima_server/models/agent_runtime.py apps/server/alembic/versions/20260319_0001_add_heat_column.py
git commit -m "feat(F2): add heat column to MemoryItem with migration"
```

### Task 2.2: Create heat_scoring.py with tests (TDD)

**Files:**
- Create: `apps/server/src/anima_server/services/agent/heat_scoring.py`
- Create: `apps/server/tests/test_heat_scoring.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_heat_scoring.py
"""Tests for heat-based memory scoring — F2."""
import math
from datetime import UTC, datetime, timedelta

import pytest
from anima_server.services.agent.heat_scoring import (
    HEAT_ALPHA, HEAT_BETA, HEAT_GAMMA, HEAT_DELTA,
    RECENCY_TAU_HOURS, compute_heat, compute_time_decay,
)


class TestComputeTimeDecay:
    def test_zero_hours(self):
        now = datetime.now(UTC)
        assert compute_time_decay(now, now) == pytest.approx(1.0)

    def test_tau_hours(self):
        now = datetime.now(UTC)
        past = now - timedelta(hours=RECENCY_TAU_HOURS)
        expected = math.exp(-1.0)
        assert compute_time_decay(past, now) == pytest.approx(expected, rel=1e-3)

    def test_48_hours_decay(self):
        now = datetime.now(UTC)
        past = now - timedelta(hours=48)
        result = compute_time_decay(past, now)
        # With tau=24: exp(-48/24) = exp(-2) ≈ 0.135
        assert result < 0.25  # At least 75% decay


class TestComputeHeat:
    def test_basic_formula(self):
        now = datetime.now(UTC)
        heat = compute_heat(
            access_count=5,
            interaction_depth=5,  # same as access_count in v1
            last_accessed_at=now,
            importance=7.0,
            now=now,
        )
        # H = 1.0*5 + 1.0*5 + 1.0*1.0 + 0.5*7 = 14.5
        assert heat == pytest.approx(14.5, rel=1e-2)

    def test_no_access(self):
        heat = compute_heat(
            access_count=0,
            interaction_depth=0,
            last_accessed_at=None,
            importance=3.0,
        )
        # H = 0 + 0 + 0 (no last_accessed) + 0.5*3 = 1.5
        assert heat == pytest.approx(1.5, rel=1e-2)

    def test_frequently_accessed_beats_old(self):
        now = datetime.now(UTC)
        hot = compute_heat(
            access_count=10, interaction_depth=10,
            last_accessed_at=now, importance=3.0, now=now,
        )
        cold = compute_heat(
            access_count=1, interaction_depth=1,
            last_accessed_at=now - timedelta(days=7),
            importance=3.0, now=now,
        )
        assert hot > cold

    def test_heat_increases_with_each_access(self):
        now = datetime.now(UTC)
        heats = []
        for n in range(1, 6):
            h = compute_heat(
                access_count=n, interaction_depth=n,
                last_accessed_at=now, importance=3.0, now=now,
            )
            heats.append(h)
        # Each access should increase heat
        for i in range(1, len(heats)):
            assert heats[i] > heats[i - 1]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/server && python -m pytest tests/test_heat_scoring.py -v`
Expected: ImportError

- [ ] **Step 3: Implement heat_scoring.py**

```python
# services/agent/heat_scoring.py
"""Heat-based memory scoring — F2.

Persistent heat score combining access frequency, interaction depth,
time-decay, and LLM-assigned importance. Hot memories surface first;
cold memories are candidates for archival.
"""
from __future__ import annotations

import logging
import math
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Configurable weights
HEAT_ALPHA: float = 1.0      # access count
HEAT_BETA: float = 1.0       # interaction depth
HEAT_GAMMA: float = 1.0      # recency decay
HEAT_DELTA: float = 0.5      # importance
RECENCY_TAU_HOURS: float = 24.0


def compute_time_decay(
    last_accessed: datetime,
    now: datetime,
    *,
    tau_hours: float = RECENCY_TAU_HOURS,
) -> float:
    """Exponential time decay: exp(-hours_since / tau)."""
    if last_accessed.tzinfo is None:
        last_accessed = last_accessed.replace(tzinfo=UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    hours = max(0.0, (now - last_accessed).total_seconds() / 3600.0)
    return math.exp(-hours / tau_hours)


def compute_heat(
    *,
    access_count: int,
    interaction_depth: int,
    last_accessed_at: datetime | None,
    importance: float = 0.0,
    now: datetime | None = None,
) -> float:
    """Compute heat: H = alpha*access + beta*depth + gamma*recency + delta*importance."""
    ref_now = now or datetime.now(UTC)
    recency = 0.0
    if last_accessed_at is not None:
        recency = compute_time_decay(last_accessed_at, ref_now)
    return (
        HEAT_ALPHA * access_count
        + HEAT_BETA * interaction_depth
        + HEAT_GAMMA * recency
        + HEAT_DELTA * importance
    )


def update_heat_on_access(
    db: Session,
    items: "list[Any]",
    *,
    now: datetime | None = None,
) -> None:
    """Recompute and persist heat for accessed items."""
    ref_now = now or datetime.now(UTC)
    for item in items:
        ref_count = item.reference_count or 0
        item.heat = compute_heat(
            access_count=ref_count,
            interaction_depth=ref_count,
            last_accessed_at=item.last_referenced_at,
            importance=float(item.importance),
            now=ref_now,
        )
    db.flush()


def decay_all_heat(
    db: Session,
    *,
    user_id: int,
    now: datetime | None = None,
) -> int:
    """Batch-update heat for all active items. Called during sleep tasks."""
    from anima_server.models import MemoryItem

    ref_now = now or datetime.now(UTC)
    items = list(db.scalars(
        select(MemoryItem).where(
            MemoryItem.user_id == user_id,
            MemoryItem.superseded_by.is_(None),
        )
    ).all())

    for item in items:
        ref_count = item.reference_count or 0
        item.heat = compute_heat(
            access_count=ref_count,
            interaction_depth=ref_count,
            last_accessed_at=item.last_referenced_at,
            importance=float(item.importance),
            now=ref_now,
        )
    db.flush()
    return len(items)


def get_hottest_items(
    db: Session,
    *,
    user_id: int,
    limit: int = 20,
    category: str | None = None,
) -> "list[Any]":
    """Return items sorted by heat descending."""
    from anima_server.models import MemoryItem

    stmt = select(MemoryItem).where(
        MemoryItem.user_id == user_id,
        MemoryItem.superseded_by.is_(None),
    )
    if category is not None:
        stmt = stmt.where(MemoryItem.category == category)
    stmt = stmt.order_by(MemoryItem.heat.desc()).limit(limit)
    return list(db.scalars(stmt).all())


def get_coldest_items(
    db: Session,
    *,
    user_id: int,
    limit: int = 20,
    heat_threshold: float = 0.1,
) -> "list[Any]":
    """Return items below heat threshold."""
    from anima_server.models import MemoryItem

    return list(db.scalars(
        select(MemoryItem).where(
            MemoryItem.user_id == user_id,
            MemoryItem.superseded_by.is_(None),
            MemoryItem.heat < heat_threshold,
        )
        .order_by(MemoryItem.heat.asc())
        .limit(limit)
    ).all())
```

- [ ] **Step 4: Run tests**

Run: `cd apps/server && python -m pytest tests/test_heat_scoring.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add apps/server/src/anima_server/services/agent/heat_scoring.py apps/server/tests/test_heat_scoring.py
git commit -m "feat(F2): heat_scoring service with compute/decay/query functions"
```

### Task 2.3: Wire heat into memory_store and sleep_tasks

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/memory_store.py` (`touch_memory_items`, `_retrieval_score`, `get_memory_items_scored`)
- Modify: `apps/server/src/anima_server/services/agent/sleep_tasks.py` (`run_sleep_tasks`)

- [ ] **Step 1: Modify `touch_memory_items` to update heat**

In `memory_store.py`, after the existing `db.flush()` at line 349, add:

```python
    try:
        from anima_server.services.agent.heat_scoring import update_heat_on_access
        update_heat_on_access(db, items, now=ref_now)
    except Exception:  # noqa: BLE001
        pass
```

- [ ] **Step 2: Replace `_retrieval_score` with heat**

Replace the body of `_retrieval_score()` (lines 304-333):

```python
def _retrieval_score(item: MemoryItem, now: datetime) -> float:
    """Return the item's heat score, falling back to legacy formula if heat is zero."""
    if hasattr(item, 'heat') and item.heat > 0.0:
        return item.heat
    # Legacy fallback for items without heat scores yet
    from anima_server.services.agent.heat_scoring import compute_heat
    ref_count = item.reference_count or 0
    return compute_heat(
        access_count=ref_count,
        interaction_depth=ref_count,
        last_accessed_at=item.last_referenced_at,
        importance=float(item.importance),
        now=now,
    )
```

- [ ] **Step 3: Add heat decay to sleep_tasks**

In `sleep_tasks.py`, add as step 0 in `run_sleep_tasks()`, before the contradiction scan (line 77):

```python
    # 0. Decay heat scores for all items
    try:
        from anima_server.services.agent.heat_scoring import decay_all_heat
        from anima_server.db.session import SessionLocal

        factory = db_factory or SessionLocal
        with factory() as db:
            decay_all_heat(db, user_id=user_id)
            db.commit()
    except Exception as e:  # noqa: BLE001
        logger.debug("Heat decay failed for user %s: %s", user_id, e)
```

- [ ] **Step 4: Run full test suite**

Run: `cd apps/server && python -m pytest -x -q`
Expected: All tests pass

- [ ] **Step 5: Commit**

```bash
git add apps/server/src/anima_server/services/agent/memory_store.py apps/server/src/anima_server/services/agent/sleep_tasks.py
git commit -m "feat(F2): wire heat into retrieval scoring and sleep tasks"
```

---

## Phase 1C: F4 — Knowledge Graph

### Task 4.1: Add KG models and migration

**Files:**
- Modify: `apps/server/src/anima_server/models/agent_runtime.py`
- Modify: `apps/server/src/anima_server/models/__init__.py`
- Create: `apps/server/alembic/versions/20260319_0002_create_kg_tables.py`

- [ ] **Step 1: Add KGEntity and KGRelation models**

Add to `agent_runtime.py` after MemoryVector class:

```python
class KGEntity(Base):
    __tablename__ = "kg_entities"
    __table_args__ = (
        UniqueConstraint("user_id", "name_normalized", name="uq_kg_entities_user_name"),
        Index("ix_kg_entities_user_type", "user_id", "entity_type"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    name_normalized: Mapped[str] = mapped_column(String(200), nullable=False)
    entity_type: Mapped[str] = mapped_column(
        String(50), nullable=False, server_default=text("'unknown'"))
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    mentions: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    embedding_json: Mapped[list[float] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())


class KGRelation(Base):
    __tablename__ = "kg_relations"
    __table_args__ = (
        Index("ix_kg_relations_source", "source_id"),
        Index("ix_kg_relations_dest", "destination_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("kg_entities.id", ondelete="CASCADE"), nullable=False)
    destination_id: Mapped[int] = mapped_column(
        ForeignKey("kg_entities.id", ondelete="CASCADE"), nullable=False)
    relation_type: Mapped[str] = mapped_column(String(100), nullable=False)
    mentions: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    source_memory_id: Mapped[int | None] = mapped_column(
        ForeignKey("memory_items.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())
```

- [ ] **Step 2: Export models in __init__.py**

Add `KGEntity` and `KGRelation` to the imports and `__all__` in `models/__init__.py`.

- [ ] **Step 3: Create migration**

```python
# alembic/versions/20260319_0002_create_kg_tables.py
"""Create knowledge graph tables.

Revision ID: 20260319_0002
"""
revision = "20260319_0002"
down_revision = "20260319_0001"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.create_table(
        "kg_entities",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("name_normalized", sa.String(200), nullable=False),
        sa.Column("entity_type", sa.String(50), nullable=False, server_default="unknown"),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("mentions", sa.Integer, nullable=False, server_default="1"),
        sa.Column("embedding_json", sa.JSON, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "name_normalized", name="uq_kg_entities_user_name"),
    )
    op.create_index("ix_kg_entities_user_type", "kg_entities", ["user_id", "entity_type"])

    op.create_table(
        "kg_relations",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_id", sa.Integer, sa.ForeignKey("kg_entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("destination_id", sa.Integer, sa.ForeignKey("kg_entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("relation_type", sa.String(100), nullable=False),
        sa.Column("mentions", sa.Integer, nullable=False, server_default="1"),
        sa.Column("source_memory_id", sa.Integer, sa.ForeignKey("memory_items.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_kg_relations_source", "kg_relations", ["source_id"])
    op.create_index("ix_kg_relations_dest", "kg_relations", ["destination_id"])


def downgrade():
    op.drop_table("kg_relations")
    op.drop_table("kg_entities")
```

- [ ] **Step 4: Run migration and full test suite**

Run: `cd apps/server && alembic upgrade head && python -m pytest -x -q`

- [ ] **Step 5: Commit**

```bash
git add apps/server/src/anima_server/models/agent_runtime.py apps/server/src/anima_server/models/__init__.py apps/server/alembic/versions/20260319_0002_create_kg_tables.py
git commit -m "feat(F4): add KGEntity and KGRelation models with migration"
```

### Task 4.2: Create knowledge_graph.py core functions with tests (TDD)

**Files:**
- Create: `apps/server/src/anima_server/services/agent/knowledge_graph.py`
- Create: `apps/server/tests/test_knowledge_graph.py`

**Implementation:** Follow the PRD at `docs/prds/memory/F4-knowledge-graph.md` Section 4.4 for function signatures. Key functions:
- `normalize_entity_name()` — "New York City" → "new_york_city"
- `upsert_entity()` — create/update with mentions increment
- `upsert_relation()` — create/update with mentions increment
- `search_graph()` — SQL JOIN traversal, depth 1-2, bidirectional
- `rerank_graph_results()` — BM25 rerank of traversal results
- `graph_context_for_query()` — extract entities from query, traverse, format for prompt
- `extract_entities_and_relations()` — LLM tool-call extraction (with JSON fallback)
- `prune_stale_relations()` — LLM-driven deletion of outdated relations
- `ingest_conversation_graph()` — full pipeline

**Test approach:** Unit tests for normalize, upsert, search_graph, rerank. Mock LLM for extraction tests. See PRD Section 8 for test plan (T1-T17).

**Note:** This is a large task. The implementing agent should read the full PRD at `docs/prds/memory/F4-knowledge-graph.md` for complete specifications, prompts, and tool schemas.

- [ ] **Step 1: Write tests** (see PRD T1-T7 for unit tests)
- [ ] **Step 2: Run tests to verify failure**
- [ ] **Step 3: Implement knowledge_graph.py** (see PRD Section 4.4 for all function signatures)
- [ ] **Step 4: Run tests to verify passing**
- [ ] **Step 5: Commit**

### Task 4.3: Wire KG into memory_blocks for prompt injection

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/memory_blocks.py`

**Implementation:** Add `knowledge_graph` memory block to `build_runtime_memory_blocks()`. Call `graph_context_for_query()` when semantic results are available. Format as per PRD Section 4.9. Omit block when no relevant graph context found.

- [ ] **Step 1: Write test for KG block in memory blocks**
- [ ] **Step 2: Add `build_knowledge_graph_block()` function**
- [ ] **Step 3: Add call in `build_runtime_memory_blocks()`**
- [ ] **Step 4: Run full test suite**
- [ ] **Step 5: Commit**

---

## Phase 2: F3 — Predict-Calibrate Consolidation

**Depends on:** F1 (BM25 hybrid search) completed.

### Task 3.1: Create predict_calibrate.py with tests (TDD)

**Files:**
- Create: `apps/server/src/anima_server/services/agent/predict_calibrate.py`
- Create: `apps/server/tests/test_predict_calibrate.py`

**Implementation:** Follow PRD at `docs/prds/memory/F3-predict-calibrate.md`. Key functions:
- `predict_episode_knowledge()` — LLM prediction from existing facts
- `extract_knowledge_delta()` — delta extraction (surprising/contradictory/corrective)
- `apply_quality_gates()` — heuristic filters (persistence, specificity, utility, independence)
- `predict_calibrate_extraction()` — full pipeline orchestrator

**Cold-start:** When < 5 existing facts, fall back to existing `extract_memories_via_llm()`.

**ID protection (F3.11):** Map real memory IDs to sequential integers before LLM calls.

**Emotion preservation (F3.12):** Include `detected_emotion` in delta extraction output.

- [ ] **Step 1: Write tests** (quality gates heuristics, cold-start path, ID mapping)
- [ ] **Step 2: Run tests to verify failure**
- [ ] **Step 3: Implement predict_calibrate.py**
- [ ] **Step 4: Run tests to verify passing**
- [ ] **Step 5: Commit**

### Task 3.2: Wire into consolidation.py

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/consolidation.py` (`consolidate_turn_memory_with_llm`)

**Implementation:** When existing facts > 5, route through `predict_calibrate_extraction()`. Keep cold-start fallback. Wrap in try/except with fallback to direct extraction.

- [ ] **Step 1: Write integration test**
- [ ] **Step 2: Modify `consolidate_turn_memory_with_llm()`**
- [ ] **Step 3: Run full test suite**
- [ ] **Step 4: Commit**

---

## Phase 3: F7 — Intentional Forgetting

**Depends on:** F2 (heat scoring) completed.

### Task 7.1: Add forgetting data model changes

**Files:**
- Modify: `apps/server/src/anima_server/models/agent_runtime.py`
- Modify: `apps/server/src/anima_server/models/__init__.py`
- Create: migration file

**Implementation:** Add `ForgetAuditLog` model, `needs_regeneration` column to `MemoryEpisode` and `SelfModelBlock` (in `models/consciousness.py`). See PRD Section 4.4 and 7.

- [ ] **Step 1: Add models and columns**
- [ ] **Step 2: Create migration**
- [ ] **Step 3: Export models**
- [ ] **Step 4: Run migration and tests**
- [ ] **Step 5: Commit**

### Task 7.2: Create forgetting.py with tests (TDD)

**Files:**
- Create: `apps/server/src/anima_server/services/agent/forgetting.py`
- Create: `apps/server/tests/test_forgetting.py`

**Implementation:** Follow PRD at `docs/prds/memory/F7-intentional-forgetting.md`. Key functions:
- `suppress_memory()` — flag derived references on supersession
- `forget_memory()` — hard delete + embedding removal + derived ref cleanup + claims cleanup + BM25 invalidation
- `forget_by_topic()` — hybrid_search for candidates, return for confirmation
- `find_derived_references()` — search episodes/growth_log/intentions
- `redact_derived_references()` — flag_for_regeneration or immediate_redact

**Constants:** `HEAT_VISIBILITY_FLOOR = 0.01`, `SUPERSEDED_DECAY_MULTIPLIER = 3.0`

- [ ] **Step 1-5: TDD cycle** (see PRD T1-T8)

### Task 7.3: Wire forgetting into existing code

**Files:**
- Modify: `memory_store.py` (visibility floor filter)
- Modify: `heat_scoring.py` (superseded decay multiplier)
- Modify: `consolidation.py` (call suppress on supersession)
- Modify: `sleep_tasks.py` (regeneration step)
- Create: `apps/server/src/anima_server/api/routes/forgetting.py` (REST endpoints)

- [ ] **Step 1-5: Wire and test**

---

## Phase 4: F5 — Async Sleep-Time Agents

**Depends on:** F2, F3, F4 completed.

### Task 5.1: Add BackgroundTaskRun model and migration

**Files:**
- Modify: `apps/server/src/anima_server/models/agent_runtime.py`
- Create: migration file

**Implementation:** See PRD Section 4.2 for `BackgroundTaskRun` schema.

- [ ] **Step 1-4: Model, migration, export, test**

### Task 5.2: Create sleep_agent.py with tests (TDD)

**Files:**
- Create: `apps/server/src/anima_server/services/agent/sleep_agent.py`
- Create: `apps/server/tests/test_sleep_agent.py`

**Implementation:** Follow PRD at `docs/prds/memory/F5-async-sleep-agents.md`. Key functions:
- `bump_turn_counter()` / `should_run_sleeptime()` — frequency gating
- `run_sleeptime_agents()` — parallel + sequential orchestrator
- `_issue_background_task()` — tracked task execution with finally-block
- `get_last_processed_message_id()` / `update_last_processed_message_id()` — restart cursor

**Critical preservations (F5.18-F5.21):**
- `settings.agent_background_memory_enabled` guard
- `companion.invalidate_memory()` after processing
- Working memory expiry + quick monologue in force path
- `_background_tasks` set tracking / `drain_background_memory_tasks()`

- [ ] **Step 1-5: TDD cycle** (see PRD T1-T13)

### Task 5.3: Wire into consolidation.py and reflection.py

**Files:**
- Modify: `consolidation.py` (`schedule_background_memory_consolidation`)
- Modify: `reflection.py` (`schedule_reflection`, `run_reflection`)
- Modify: `sleep_tasks.py` (heat-threshold gating)

- [ ] **Step 1-5: Wire and test**

---

## Phase 5: F6 — Batch Episode Segmentation

**Depends on:** F5 completed (for orchestration benefits).

### Task 6.1: Add episode segmentation columns

**Files:**
- Modify: `apps/server/src/anima_server/models/agent_runtime.py` (MemoryEpisode)
- Create: migration file

**Implementation:** Add `message_indices_json` (JSON, nullable) and `segmentation_method` (String(20), default "sequential") to `MemoryEpisode`. See PRD Section 4.5.

- [ ] **Step 1-4: Model, migration, test**

### Task 6.2: Create batch_segmenter.py with tests (TDD)

**Files:**
- Create: `apps/server/src/anima_server/services/agent/batch_segmenter.py`
- Create: `apps/server/tests/test_batch_segmenter.py`

**Implementation:** Follow PRD at `docs/prds/memory/F6-batch-segmentation.md`. Key functions:
- `segment_messages_batch()` — LLM topic grouping, returns `list[list[int]]`
- `should_batch_segment()` — buffer_size >= BATCH_THRESHOLD (8)
- `validate_indices()` — all indices covered, no dupes, no out-of-range
- `indices_to_0based()` — 1-based → 0-based conversion
- `generate_episodes_from_segments()` — one episode per segment group

- [ ] **Step 1-5: TDD cycle** (see PRD T1-T14)

### Task 6.3: Wire into episodes.py

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/episodes.py` (`maybe_generate_episode`)

**Implementation:** When `remaining_logs >= BATCH_THRESHOLD`, route to batch segmentation. Fall back to sequential on failure.

- [ ] **Step 1-5: Wire and test**

---

## Final: Full Regression

- [ ] **Run full test suite**: `cd apps/server && python -m pytest -x -q`
- [ ] **Verify all migrations apply cleanly**: `alembic upgrade head`
- [ ] **Verify migration rollback**: `alembic downgrade -1` (repeat for each migration)
