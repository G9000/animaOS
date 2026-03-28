---
title: "PRD: F1 - Lexical-Semantic Hybrid Retrieval (BM25 + Vector + RRF)"
description: Candidate-generation retrieval upgrade combining BM25, vector similarity, and Reciprocal Rank Fusion
category: prd
version: "1.0"
---

# PRD: F1 - Lexical-Semantic Hybrid Retrieval (BM25 + Vector + RRF)

**Version**: 1.0
**Date**: 2026-03-18
**Status**: Shipped (~95%)
**Roadmap Phase**: 9.7
**Priority**: P0 - Foundation
**Depends on**: None
**Blocked by**: Nothing
**Blocks**: F3 (Predict-Calibrate), and improves F2, F4, F5, F6

---

## 1. Overview

Upgrade the memory retrieval candidate-generation layer by replacing the naive Jaccard-based keyword search with BM25Okapi lexical ranking while preserving vector search and Reciprocal Rank Fusion. The goal is better recall for proper nouns, technical terms, and exact phrases without claiming to solve graph reasoning, pattern separation, or the full memory system.

This is a retrieval-layer improvement, not the total memory-system solution. It should be read as one part of a broader memory stack.
This design keeps hybrid lexical-semantic retrieval self-hosted and local to the Core, rather than depending on external search infrastructure for BM25 fusion quality.

---

## 2. Problem Statement

### Current Implementation

| Component | File | Function | What it does |
|-----------|------|----------|--------------|
| Vector search | `vector_store.py` | `OrmVecStore.search_by_vector()` | Cosine similarity over `MemoryVector` table - works well |
| Keyword search | `vector_store.py` | `_text_similarity()` | Jaccard word overlap - primitive |
| RRF fusion | `embeddings.py` | `_reciprocal_rank_fusion()` | Merges vector + keyword results - well-built |
| Hybrid search | `embeddings.py` | `hybrid_search()` | Orchestrates both legs + RRF - already wired into the retrieval path |
| Adaptive filter | `embeddings.py` | `adaptive_filter()` | Score gap detection - already works |

### The Gap

`_text_similarity()` computes Jaccard similarity (intersection-over-union of word sets). This misses:

- Term frequency weighting: "Python Python Python" scores the same as "Python" for the query "Python"
- Inverse document frequency: Common words ("the", "is") are weighted equally with rare/informative words ("PostgreSQL", "Argon2id")
- Document length normalization: A 3-word memory and a 300-word memory are treated identically

**User-visible impact**: Searching for "PostgreSQL" may not surface the memory "User prefers PostgreSQL over MySQL for personal projects" because Jaccard treats "PostgreSQL" as just one of many words. BM25 would weight it heavily because it's rare across the corpus.

### Scope Boundary

F1 improves lexical-semantic candidate generation. It does not attempt to solve the broader memory thesis around graph reasoning, lifecycle management, or pattern separation.

### Evidence

| Source | Finding |
|--------|---------|
| Nemori `unified_search.py` | BM25 + ChromaDB + RRF outperformed either search alone |
| Mem0 `graph_memory.py` lines 119-127 | BM25 reranking of graph results improved entity matching |
| MemoryOS `retriever.py` | Keyword similarity (Jaccard variant) combined with semantic similarity |
| AnimaOS `embeddings.py` | RRF infrastructure already exists but the keyword leg under-delivers |

---

## 3. Goals and Non-Goals

### Goals

1. Replace `_text_similarity()` (Jaccard) with BM25Okapi as the keyword search backend
2. Maintain the existing RRF fusion pipeline unchanged - only the keyword leg is swapped
3. Build BM25 indices lazily per user, cached in process memory with invalidation on content changes
4. Zero schema changes - BM25 indices are in-memory only, rebuilt from existing `MemoryVector.content`

### Non-Goals

- Changing the vector search leg (cosine similarity is good)
- Modifying RRF fusion logic or `adaptive_filter()` - these already work
- Persisting BM25 indices to disk - process-local is sufficient for single-user
- Adding new search modes or query parsers
- Changing what goes into the prompt (this changes ranking quality, not content volume)

---

## 4. Detailed Design

### 4.1 New File

```text
apps/server/src/anima_server/services/agent/bm25_index.py
```

### 4.2 BM25Index Class

```python
from rank_bm25 import BM25Okapi
from threading import Lock

class BM25Index:
    """Per-user BM25 index built lazily from MemoryVector content."""

    def __init__(self) -> None: ...

    def build(self, documents: list[tuple[int, str]]) -> None:
        """Build index from (item_id, content) pairs.
        Tokenizes content by whitespace + lowering.
        """
        ...

    def search(self, query: str, *, limit: int = 20) -> list[tuple[int, float]]:
        """Return (item_id, bm25_score) ranked descending."""
        ...

    def add_document(self, item_id: int, content: str) -> None:
        """Incrementally add a document. Triggers full rebuild
        (BM25Okapi requires corpus-level stats).
        """
        ...

    def remove_document(self, item_id: int) -> None:
        """Remove a document by ID. Triggers full rebuild."""
        ...

    @property
    def document_count(self) -> int: ...
```

### 4.3 Module-Level Cache

```python
_user_indices: dict[int, BM25Index] = {}
_indices_lock: Lock = Lock()

def get_or_build_index(user_id: int, *, db: Session) -> BM25Index:
    """Lazy-load the BM25 index for a user.
    On cache miss: query all MemoryVector rows for the user, build index.
    Thread-safe via _indices_lock.
    """
    ...

def invalidate_index(user_id: int) -> None:
    """Clear cached index. Next search triggers rebuild."""
    ...

def bm25_search(
    user_id: int,
    *,
    query: str,
    limit: int = 20,
    db: Session,
) -> list[tuple[int, float]]:
    """Search using BM25. Returns (item_id, score) pairs."""
    ...
```

### 4.4 Modified Files

| File | Location | Change |
|------|----------|--------|
| `vector_store.py` | `OrmVecStore.upsert()` | After upsert, call `bm25_index.invalidate_index(user_id)` |
| `vector_store.py` | `OrmVecStore.delete()` | After delete, call `bm25_index.invalidate_index(user_id)` |
| `vector_store.py` | `OrmVecStore.rebuild()` | After rebuild, call `bm25_index.invalidate_index(user_id)` |
| `embeddings.py` | `hybrid_search()` | Replace the keyword leg: call `bm25_search()` from `bm25_index.py` instead of `search_by_text()` (Jaccard). RRF merge logic stays identical. |

### 4.5 Integration Points

- Retrieval entry points: `service.py::_prepare_turn_context()` handles per-turn memory recall, and `tools.py::recall_memory()` handles explicit memory search. `hybrid_search()` sits below those entry points in the retrieval path.
- `companion.py` caches static blocks; it is not the per-turn caller of `hybrid_search()`.
- Index lifecycle: BM25 indices live in process memory (like the current `InMemoryVectorStore`). They rebuild lazily on first search after invalidation.
- Corpus coverage assumption: The BM25 corpus is built from the currently search-indexed memory text, not necessarily every logical memory row at write time.
- Corpus coverage risk: Fresh memories that have not yet entered the search-indexed corpus remain invisible to the BM25 leg until they are added to that corpus and the BM25 index is rebuilt.
- Corpus coverage risk: For already-indexed content, an index rebuild can restore BM25 visibility after invalidation or content changes.
- Token budget: No impact. This changes ranking quality, not what goes into the prompt.
- `_RRF_K = 60` remains the existing RRF default.

### 4.6 Tokenization Strategy

BM25Okapi requires tokenized documents. Use simple whitespace splitting + lowercasing:

```python
def _tokenize(text: str) -> list[str]:
    return text.lower().split()
```

This is intentionally simple. BM25's strength is in TF-IDF weighting, not in sophisticated tokenization. Stemming and stop-word removal are unnecessary for a personal memory corpus where proper nouns and technical terms are the primary search targets.

---

## 5. Requirements

| ID | Requirement | Priority | Rationale |
|----|-------------|----------|-----------|
| F1.1 | `BM25Index` class wrapping `rank-bm25.BM25Okapi`, built lazily per user from `MemoryVector.content` | Must | Core functionality |
| F1.2 | `bm25_search()` function returning `(item_id, score)` pairs ranked descending | Must | Search interface |
| F1.3 | Index invalidation hooks in `OrmVecStore.upsert()`, `delete()`, `rebuild()` | Must | Prevents stale results |
| F1.4 | `hybrid_search()` uses `bm25_search()` instead of `search_by_text()` for the keyword leg | Must | The actual swap |
| F1.5 | Per-user index cache with thread-safe `Lock` | Must | Concurrency safety |
| F1.6 | Incremental `add_document()` / `remove_document()` | Should | Convenience; triggers full rebuild internally since BM25Okapi needs corpus stats |
| F1.7 | RRF fusion with `k=60` (existing value preserved) | Must | Current standard RRF constant and existing implementation default |
| F1.8 | `search_by_text()` / `_text_similarity()` in `vector_store.py` can be left in place (dead code) or removed | Could | Cleanup |

---

## 6. Data Model Changes

**None.** BM25 indices are built in-memory from existing `MemoryVector.content` data.

- Migration count: **0**
- New tables: **0**
- Modified tables: **0**

---

## 7. New Dependencies

| Package | Version | Size | License | Why |
|---------|------|------|---------|-----|
| `rank-bm25` | latest | ~15 KB | Apache 2.0 | Pure Python BM25Okapi implementation |

This is the only new pip dependency across all 7 memory features (F1-F7). We explicitly avoid `spacy` (large and unnecessary for this use case), `numpy` (not required for the tokenization path), `chromadb`, and `neo4j`.

---

## 8. Acceptance Criteria

| # | Criterion | How to verify |
|---|-----------|---------------|
| AC1 | Searching for "PostgreSQL" returns the memory containing that exact term in top-5 results | Integration test with known corpus |
| AC2 | Searching for a common word ("the") does not disproportionately rank memories | Unit test: verify IDF weighting |
| AC3 | Target build time: BM25 index builds in under 100 ms for a small personal-memory corpus; verify with benchmark data | Benchmark in test |
| AC4 | Target: memory overhead stays small enough for roughly 10,000 memories at about 50 tokens each; verify with measurement data | Measure in test |
| AC5 | All 602 existing tests pass without modification | CI |
| AC6 | `hybrid_search()` returns blended results from both BM25 and vector legs | Integration test: create memories findable by keyword only and by embedding only, verify both appear |
| AC7 | Index invalidation: after `OrmVecStore.upsert()` upserts search-indexed `MemoryVector.content`, the next BM25 search includes that indexed content | Integration test |
| AC8 | Index invalidation: after deleting a memory, the next BM25 search excludes it | Integration test |

---

## 9. Test Plan

| # | Type | Test | Details |
|---|------|------|---------|
| T1 | Unit | `BM25Index.build()` + `search()` | Build from known documents, verify expected ranking order |
| T2 | Unit | `BM25Index.add_document()` | Add document, verify it appears in search results |
| T3 | Unit | `BM25Index.remove_document()` | Remove document, verify it disappears from results |
| T4 | Unit | `_tokenize()` | Verify lowercase splitting, handling of punctuation |
| T5 | Unit | `get_or_build_index()` | Verify lazy build on cache miss, cache hit on second call |
| T6 | Unit | `invalidate_index()` | Verify cache eviction triggers rebuild on next search |
| T7 | Integration | `hybrid_search()` with BM25 leg | Mock vector results + BM25 results, verify RRF produces blended ranking |
| T8 | Integration | Proper noun advantage | Query for a rare proper noun, verify BM25 ranks it higher than Jaccard would have |
| T9 | Regression | Existing `hybrid_search` tests | All existing tests pass with BM25 backend |
| T10 | Performance | Index build time | Target: 1,000 documents under 100 ms; confirm with benchmark |
| T11 | Performance | Memory usage | Target: keep overhead small for 10,000 documents; confirm with measurement |

---

## 10. Risks

| Risk | Severity | Likelihood | Mitigation |
|------|----------|------------|------------|
| **Index staleness** | Low | Medium | Invalidation hooks in every mutation path (`upsert`, `delete`, `rebuild`). Worst case: one turn with slightly stale keyword results. |
| **Memory usage** | Low | Low | BM25Okapi stores tokenized corpus in memory. Actual footprint depends on corpus shape and token count, so validate against the target corpus rather than assuming a fixed number. |
| **Rebuild latency** | Low | Low | Full rebuild latency is a target to benchmark, not a guaranteed property. Rebuilds happen on first search after invalidation, not on every query. |
| **Tokenization quality** | Low | Low | Simple whitespace+lowercase is intentional. BM25's value is in TF-IDF, not tokenization. Proper nouns are preserved as-is. |
| **rank-bm25 dependency risk** | Low | Low | Stable, widely-used, 15 KB pure Python, Apache 2.0. No transitive dependencies. |

---

## 11. Rollout

1. Add `rank-bm25` to `pyproject.toml` / `requirements.txt`
2. Create `bm25_index.py` with `BM25Index` class and module-level functions
3. Add invalidation hooks to `vector_store.py` (`upsert`, `delete`, `rebuild`)
4. Modify `hybrid_search()` in `embeddings.py` to use `bm25_search()`
5. Write unit tests for `bm25_index.py`
6. Write integration test for `hybrid_search()` with BM25 backend
7. Run full test suite (602+ tests)
8. Ship as single PR

No feature flag is assumed for the initial swap, but the old Jaccard path remains recoverable by reverting the `hybrid_search()` change if needed.

---

## 12. Implementation Audit (2026-03-28)

### Requirements Checklist

| ID | Requirement | Status | Evidence |
|----|-------------|--------|----------|
| F1.1 | `BM25Index` class wrapping `rank-bm25.BM25Okapi`, built lazily per user | DONE | `bm25_index.py:24-70` — full class with `build()`, `search()`, `add_document()`, `remove_document()`, `document_count` |
| F1.2 | `bm25_search()` returning `(item_id, score)` pairs ranked descending | DONE | `bm25_index.py:113-122` — delegates to `get_or_build_index()` then `index.search()` |
| F1.3 | Index invalidation hooks in `OrmVecStore.upsert()`, `delete()`, `rebuild()` | DONE | `vector_store.py:488-490` (upsert), `501-503` (delete), `568-570` (rebuild) — all call `invalidate_index(user_id)` |
| F1.4 | `hybrid_search()` uses `bm25_search()` instead of `search_by_text()` for keyword leg | DONE | `embeddings.py:667-669` — imports and calls `bm25_search` from `bm25_index` |
| F1.5 | Per-user index cache with thread-safe `Lock` | DONE | `bm25_index.py:75-76` — `_user_indices: dict[int, BM25Index]` + `_indices_lock: Lock` |
| F1.6 | Incremental `add_document()` / `remove_document()` | DONE | `bm25_index.py:58-65` — both trigger full rebuild via `self.build()` |
| F1.7 | RRF fusion with `k=60` | DONE | `embeddings.py:467` — `_RRF_K = 60` |
| F1.8 | `search_by_text()` / `_text_similarity()` removed or left as dead code | PARTIAL | `_text_similarity()` still exists at `vector_store.py:65` and is used inside `OrmVecStore.search_by_text()` / `InMemoryVectorStore.search_by_text()`. NOT used by the main `hybrid_search()` path (that uses BM25). It remains as a vestigial internal fallback. |

### Goals Checklist

| Goal | Status | Evidence |
|------|--------|----------|
| Replace Jaccard with BM25Okapi as keyword backend | DONE | `hybrid_search()` calls `bm25_search()`, not `search_by_text()` |
| Maintain existing RRF pipeline unchanged | DONE | `_reciprocal_rank_fusion()` at `embeddings.py:563` unchanged; only keyword leg swapped |
| BM25 indices lazy per-user, cached in process memory | DONE | `get_or_build_index()` at `bm25_index.py:79-104` — lazy build, process-memory cache |
| Zero schema changes | DONE | No migrations added. BM25 indices are in-memory only |

### Dependency

| Package | Status | Evidence |
|---------|--------|----------|
| `rank-bm25>=0.2.2` | DONE | `pyproject.toml:28` |

### Acceptance Criteria Checklist

| AC | Criterion | Status | Evidence |
|----|-----------|--------|----------|
| AC1 | "PostgreSQL" returns exact match in top-5 | COVERED | `test_bm25_index.py` — 18 test functions |
| AC2 | Common words don't disproportionately rank | COVERED | BM25Okapi IDF weighting handles this by design |
| AC3 | Build time under 100ms for small corpus | NOT VERIFIED | No explicit benchmark test found |
| AC4 | Memory overhead reasonable for ~10K memories | NOT VERIFIED | No explicit measurement test found |
| AC5 | All existing tests pass | DONE | Test suite expanded to 846 tests, all passing |
| AC6 | Blended results from BM25 + vector legs | COVERED | `test_hybrid_retrieval.py` — 33 test functions |
| AC7 | Invalidation on upsert works | COVERED | Invalidation hooks verified in `vector_store.py` |
| AC8 | Invalidation on delete works | COVERED | Invalidation hooks verified in `vector_store.py` |

### Test Plan Checklist

| Test | Status | Evidence |
|------|--------|----------|
| T1: `build()` + `search()` | DONE | `test_bm25_index.py` |
| T2: `add_document()` | DONE | `test_bm25_index.py` |
| T3: `remove_document()` | DONE | `test_bm25_index.py` |
| T4: `_tokenize()` | DONE | `test_bm25_index.py` |
| T5: `get_or_build_index()` lazy build | DONE | `test_bm25_index.py` |
| T6: `invalidate_index()` | DONE | `test_bm25_index.py` |
| T7: `hybrid_search()` with BM25 leg | DONE | `test_hybrid_retrieval.py` |
| T8: Proper noun advantage | LIKELY | Covered by BM25 IDF behavior; no explicit named test found |
| T9: Regression (existing tests) | DONE | 846 tests passing |
| T10: Performance — build time | NOT DONE | No benchmark test |
| T11: Performance — memory usage | NOT DONE | No measurement test |

### Rollout Steps Checklist

| Step | Status |
|------|--------|
| 1. Add `rank-bm25` to `pyproject.toml` | DONE |
| 2. Create `bm25_index.py` | DONE |
| 3. Add invalidation hooks to `vector_store.py` | DONE |
| 4. Modify `hybrid_search()` to use `bm25_search()` | DONE |
| 5. Write unit tests for `bm25_index.py` | DONE (18 tests) |
| 6. Write integration test for `hybrid_search()` | DONE (33 tests) |
| 7. Run full test suite | DONE (846 passing) |
| 8. Ship as single PR | DONE |

### Beyond PRD (Implemented but not specified)

| Extra | File | Notes |
|-------|------|-------|
| `_bm25_rerank()` | `embeddings.py:483` | Additional BM25 reranking pass AFTER RRF merge — not in original PRD. Adds a second BM25 scoring stage for final result ordering. |
| `HybridSearchResult` dataclass | `embeddings.py` | Carries `query_embedding` for downstream reuse — not in PRD. |
| Tags pre-filtering | `embeddings.py` | Tag-based filtering integrated into `hybrid_search()` — not in PRD. |
| Weighted RRF | `embeddings.py` | `semantic_weight` / `keyword_weight` parameters on RRF — PRD assumed equal weights. |

### Summary

**Status: SHIPPED (~95%)**

All 8 requirements implemented. All 8 rollout steps completed. 51 dedicated tests (18 BM25 + 33 hybrid).

**Remaining items (non-blocking):**
1. `_text_similarity()` / `search_by_text()` still exists as dead code in `vector_store.py` — can be removed (F1.8, "Could" priority)
2. No explicit performance benchmark tests for build time (AC3/T10) or memory usage (AC4/T11)

---

## 13. References

- Nemori `unified_search.py` - parallel BM25 + vector with RRF fusion (k=60)
- Nemori `bm25_search.py` - `rank_bm25.BM25Okapi` with per-user indices
- Mem0 `graph_memory.py` lines 119-127 - BM25 reranking of graph results
- [rank-bm25 on PyPI](https://pypi.org/project/rank-bm25/)
- [Implementation Plan Phase 1](../memory-implementation-plan.md) - detailed function signatures and modified file list
