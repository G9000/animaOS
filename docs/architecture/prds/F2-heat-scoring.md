# PRD: F2 — Heat-Based Memory Scoring

**Version**: 1.0
**Date**: 2026-03-18
**Status**: Draft
**Roadmap Phase**: 10.4
**Priority**: P1
**Depends on**: None (standalone), benefits from F1
**Blocks**: F5 (Async Sleep Agents uses heat thresholds)

---

## 1. Overview

Replace the fixed-weight retrieval scoring formula with a heat-based model inspired by MemoryOS. Heat combines access frequency, interaction depth, and time-decay into a single persistent score. Hot memories surface first in retrieval. Cold memories are candidates for archival. Heat thresholds gate expensive background operations (consolidation, contradiction scanning, profile synthesis).

Heat transforms memory scoring from a static formula into a living signal that reflects how the user actually engages with their memories.

---

## 2. Problem Statement

### Current Implementation

`memory_store.py` contains:

```python
def _retrieval_score(item, now):
    # Fixed weights, no configurability
    importance_weight = 0.4   # item.importance (0-10 scale)
    recency_weight = 0.35     # 30-day half-life exponential decay
    frequency_weight = 0.25   # log(1 + reference_count)
    return importance_weight * importance_score + recency_weight * recency_score + frequency_weight * frequency_score
```

Supporting functions:
- `touch_memory_items(db, items)` — increments `reference_count` and updates `last_referenced_at`
- `get_memory_items_scored(db, ...)` — fetches a pool of items, scores each in Python, optionally blends with query embedding similarity

### The Gaps

| Gap | Impact |
|-----|--------|
| **Fixed weights** | All memories scored identically regardless of actual usage patterns. A frequently-accessed work fact and a rarely-accessed childhood memory use the same 0.4/0.35/0.25 split. |
| **No persistent score** | Score is recomputed on every retrieval query. No way to query "what are the hottest memories right now?" without fetching and scoring everything. |
| **In-Python sorting** | `get_memory_items_scored()` fetches a pool of items and sorts in Python. With a persistent `heat` column, the database can do `ORDER BY heat DESC` directly. |
| **No heat-triggered behavior** | No way to gate expensive operations (contradiction scanning, profile synthesis) based on memory activity. Currently these run on fixed timers. |
| **No decay without access** | The recency component only matters during scoring. There's no batch decay that proactively cools items over time. |

### Evidence

| Source | Pattern |
|--------|---------|
| MemoryOS `mid_term.py` | `compute_segment_heat()` = `alpha * N_visit + beta * L_interaction + gamma * R_recency` |
| MemoryOS `mid_term.py` | `MidTermMemory.search_sessions()` updates heat on every access |
| MemoryOS `retriever.py` | `heapq`-based top-K selection by heat |
| MemoryOS `updater.py` | Heat-triggered promotion from short-term → mid-term → long-term |

---

## 3. Goals and Non-Goals

### Goals

1. Persistent `heat` column on `MemoryItem` — scored in the database, not recomputed on every query
2. Configurable weights (`alpha`, `beta`, `gamma`) for access count, interaction depth, and recency decay
3. Heat updated on every access (`touch_memory_items` path)
4. Batch heat decay during sleep tasks so untouched items cool over time
5. `get_hottest_items()` / `get_coldest_items()` for targeted retrieval and archival candidate identification
6. Heat as a gating signal for F5 (Async Sleep Agents)

### Non-Goals

- Heat-triggered memory promotion (short → mid → long-term tiers) — AnimaOS uses a flat memory model, not MemoryOS's three-tier hierarchy
- Eviction or deletion of cold memories — that belongs to Intentional Forgetting (Roadmap Phase 10.5)
- Changing what goes into the prompt — heat changes ranking, not content volume
- Auto-tuning of `alpha/beta/gamma` weights — manual tuning is sufficient for v1

---

## 4. Detailed Design

### 4.1 Heat Formula

```
H = alpha * access_count + beta * interaction_depth + gamma * recency_decay
```

Where:
- `access_count` = `reference_count` from `MemoryItem` (already tracked)
- `interaction_depth` = how many times the memory appeared in a prompt that led to a meaningful conversation (approximated by `reference_count` initially; can be refined later with explicit depth tracking)
- `recency_decay` = `exp(-hours_since_last_access / tau)` where `tau` defaults to 24 hours

Default weights (matching MemoryOS):
- `HEAT_ALPHA = 1.0` (access count weight)
- `HEAT_BETA = 1.0` (interaction depth weight)
- `HEAT_GAMMA = 1.0` (recency weight)
- `RECENCY_TAU_HOURS = 24.0` (time-decay half-life)

### 4.2 New File

```
apps/server/src/anima_server/services/agent/heat_scoring.py
```

```python
HEAT_ALPHA: float = 1.0
HEAT_BETA: float = 1.0
HEAT_GAMMA: float = 1.0
RECENCY_TAU_HOURS: float = 24.0

def compute_heat(
    *,
    access_count: int,
    interaction_depth: int,
    last_accessed_at: datetime | None,
    now: datetime | None = None,
) -> float:
    """Compute heat score: H = alpha * access + beta * depth + gamma * recency_decay."""
    ...

def compute_time_decay(
    last_accessed: datetime,
    now: datetime,
    *,
    tau_hours: float = RECENCY_TAU_HOURS,
) -> float:
    """Exponential time decay: exp(-hours_since / tau)."""
    ...

def update_heat_on_access(
    db: Session,
    items: list[MemoryItem],
    *,
    now: datetime | None = None,
) -> None:
    """Increment access_count, update last_referenced_at, recompute and persist heat."""
    ...

def decay_all_heat(
    db: Session,
    *,
    user_id: int,
    now: datetime | None = None,
) -> int:
    """Batch-update heat for all active items. Called during sleep tasks.
    Returns count of items updated.
    """
    ...

def get_hottest_items(
    db: Session,
    *,
    user_id: int,
    limit: int = 20,
    category: str | None = None,
) -> list[MemoryItem]:
    """Return items sorted by heat descending."""
    ...

def get_coldest_items(
    db: Session,
    *,
    user_id: int,
    limit: int = 20,
    heat_threshold: float = 0.1,
) -> list[MemoryItem]:
    """Return items below heat threshold (candidates for archival)."""
    ...
```

### 4.3 Modified Files

| File | Function | Change |
|------|----------|--------|
| `memory_store.py` | `_retrieval_score()` | Replace body with call to `compute_heat()` for the base score, then blend with query embedding similarity as before |
| `memory_store.py` | `touch_memory_items()` | After updating `reference_count` and `last_referenced_at`, call `update_heat_on_access()` to recompute and persist heat |
| `memory_store.py` | `get_memory_items_scored()` | Use `ORDER BY heat DESC` from the database instead of fetching a large pool and sorting in Python |
| `sleep_tasks.py` | `run_sleep_tasks()` | Add step 0: `decay_all_heat(db, user_id=user_id)` to refresh heat scores before other operations |
| `models/agent_runtime.py` | `MemoryItem` | Add `heat` column and composite index |

### 4.4 Data Model Changes

**Add column to `MemoryItem`:**

```python
heat: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
```

**Add composite index:**

```python
Index("ix_memory_items_user_heat", "user_id", "heat")
```

### 4.5 Migration Strategy for Existing Data

All existing items get `heat=0.0` from the migration default. On the first `run_sleep_tasks()` call after migration, `decay_all_heat()` recomputes heat for all items using their existing `reference_count` and `last_referenced_at`. No manual backfill needed.

### 4.6 Integration Points

- **Retrieval**: `get_memory_items_scored()` uses heat as the primary sort. Query-embedding blending is a secondary adjustment applied on top.
- **Prompt assembly**: `build_facts_memory_block()` etc. in `memory_blocks.py` call `get_memory_items_scored()` — no changes needed at this layer.
- **Sleep tasks**: Heat decay runs as step 0 of `run_sleep_tasks()`, so items cool before other operations assess them.
- **F5 integration**: Heat thresholds will gate whether consolidation agents fire (e.g., only consolidate when max heat > threshold). This wiring happens in F5, not here.
- **Token budget**: No impact. Heat changes ranking, not content volume.

---

## 5. Requirements

| ID | Requirement | Priority |
|----|-------------|----------|
| F2.1 | `heat` column (Float, default 0.0) on `MemoryItem` with composite index `(user_id, heat)` | Must |
| F2.2 | `compute_heat()` implementing the formula with configurable `alpha`, `beta`, `gamma`, `tau` | Must |
| F2.3 | `compute_time_decay()` implementing exponential decay `exp(-hours / tau)` | Must |
| F2.4 | `update_heat_on_access()` called from `touch_memory_items()` to recompute heat on every memory access | Must |
| F2.5 | `decay_all_heat()` batch-updating all items during sleep tasks | Must |
| F2.6 | `get_hottest_items()` returning items sorted by heat descending | Should |
| F2.7 | `get_coldest_items()` returning items below a heat threshold | Should |
| F2.8 | `_retrieval_score()` replaced with `compute_heat()` as the base score | Must |
| F2.9 | `get_memory_items_scored()` uses `ORDER BY heat DESC` from the database | Should |
| F2.10 | First `run_sleep_tasks()` after migration backfills heat for all existing items | Must |
| F2.11 | Weights `HEAT_ALPHA`, `HEAT_BETA`, `HEAT_GAMMA`, `RECENCY_TAU_HOURS` are module-level constants (tunable without code changes beyond the constant definition) | Should |

---

## 6. Data Model Changes

**Migration**: `20260319_0001_add_heat_column_to_memory_items.py`

```python
# Alembic migration
def upgrade():
    op.add_column("memory_items", sa.Column("heat", sa.Float, nullable=False, server_default="0.0"))
    op.create_index("ix_memory_items_user_heat", "memory_items", ["user_id", "heat"])

def downgrade():
    op.drop_index("ix_memory_items_user_heat")
    op.drop_column("memory_items", "heat")
```

- New columns: 1 (`heat`)
- New indices: 1 (`ix_memory_items_user_heat`)
- New tables: 0

---

## 7. Acceptance Criteria

| # | Criterion | Verification |
|---|-----------|--------------|
| AC1 | A memory accessed 10 times in the last hour has higher heat than one accessed once 7 days ago | Unit test with known inputs |
| AC2 | After 48 hours without access, a memory's heat decays by at least 75% | Unit test: `compute_time_decay()` with 48h delta |
| AC3 | `update_heat_on_access()` increases heat monotonically with each access | Unit test: access item 5 times, verify increasing heat |
| AC4 | `decay_all_heat()` reduces heat for all untouched items | Integration test: set items with known `last_referenced_at`, run decay, verify decrease |
| AC5 | `get_hottest_items()` returns items in descending heat order | Unit test with varied-heat items |
| AC6 | `get_coldest_items()` returns only items below threshold | Unit test |
| AC7 | First `run_sleep_tasks()` after migration backfills heat from `reference_count` + `last_referenced_at` | Integration test: create items with known access history, verify non-zero heat after backfill |
| AC8 | `get_memory_items_scored()` returns the same memories (different order) as before the change | Regression test |
| AC9 | All 602 existing tests pass | CI |

---

## 8. Test Plan

| # | Type | Test | Details |
|---|------|------|---------|
| T1 | Unit | `compute_heat()` | Verify formula with known inputs: access=5, depth=3, last_access=2h ago → expected value |
| T2 | Unit | `compute_time_decay()` | Verify: 0h → 1.0, 24h → ~0.37, 48h → ~0.14 (with tau=24) |
| T3 | Unit | `update_heat_on_access()` | Access item 5 times, verify heat increases each time |
| T4 | Unit | `decay_all_heat()` | Set items with known `last_accessed_at`, run decay, verify heat decreases |
| T5 | Unit | `get_hottest_items()` | Create items with heat 1.0, 5.0, 3.0; verify returned order is 5.0, 3.0, 1.0 |
| T6 | Unit | `get_coldest_items()` | Create items with heat 0.05, 0.5, 5.0; threshold=0.1; verify only 0.05 returned |
| T7 | Integration | Prompt relevance | Create items, access some heavily, verify high-heat items appear first in memory blocks |
| T8 | Regression | `_retrieval_score()` callers | Verify `get_memory_items_scored()` still returns sensible results with the new formula |
| T9 | Regression | Full suite | All 602 tests pass |

---

## 9. Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| **Heat staleness between sleep tasks** | Low | `update_heat_on_access()` recomputes on every access. Decay only affects untouched items. Active items stay hot regardless of decay schedule. |
| **Migration on existing data** | Low | All items get `heat=0.0`. First `run_sleep_tasks()` backfills using existing `reference_count` and `last_referenced_at`. |
| **Formula tuning** | Low | Weights are module-level constants, easy to adjust. Default `alpha=beta=gamma=1.0` matches MemoryOS's validated configuration. |
| **interaction_depth approximation** | Low | Initially proxied by `reference_count`. Can be refined later with explicit depth tracking if needed. |

---

## 10. Rollout

1. Create `heat_scoring.py` with all functions
2. Write unit tests for `heat_scoring.py`
3. Create Alembic migration for `heat` column + index
4. Modify `memory_store.py` (`_retrieval_score`, `touch_memory_items`, `get_memory_items_scored`)
5. Modify `sleep_tasks.py` (add `decay_all_heat` as step 0)
6. Add `heat` column to `MemoryItem` model in `models/agent_runtime.py`
7. Write integration tests
8. Run full test suite (602+ tests)
9. Ship as single PR

---

## 11. References

- MemoryOS `mid_term.py` — `compute_segment_heat()` formula and implementation
- MemoryOS `mid_term.py` — `MidTermMemory.search_sessions()` heat update on access
- MemoryOS `retriever.py` — heap-based top-K selection by heat
- [Implementation Plan Phase 2](../memory-implementation-plan.md) — detailed function signatures
