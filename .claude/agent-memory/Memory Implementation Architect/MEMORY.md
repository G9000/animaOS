# Memory Implementation Architect - MEMORY

## Heat Scoring (F2) Implementation Notes

### Heat-to-Score Normalization
- Raw heat scores are unbounded (can be 20+ for heavily accessed items)
- `_retrieval_score()` must return [0,1]-ish values for query-aware blending to work
- Solution: sigmoid-like normalization `heat / (heat + k)` with k=5.0
- Without this, high-heat items dominate regardless of query relevance (breaks `_CATEGORY_QUERY_WEIGHTS` blending)

### Recency Fallback to created_at
- `compute_heat()` accepts optional `created_at` parameter
- When `last_referenced_at` is None (never accessed), falls back to `created_at` for recency decay
- Without this, all never-accessed items have identical recency=0 regardless of age

### MemoryItem.heat Can Be None
- Test fixtures create MemoryItem objects without DB persistence -> `heat` is None, not 0.0
- Guard: `item.heat is not None and item.heat > 0.0` before using stored heat

## Codebase Patterns

### Test DB Setup
- Tests use in-memory SQLite with `StaticPool`, `Base.metadata.create_all()`
- `autoflush=False, expire_on_commit=False` are standard in test sessions

### Migration Format
- Revision IDs: `YYYYMMDD_NNNN` format (e.g., `20260319_0001`)
- Always include `from __future__ import annotations`
- down_revision chains to previous migration

### Full Test Suite
- Run: `cd apps/server && python -m pytest -x -q`
- As of F6: 748 tests passing
- Timeout: ~70 seconds typical

### Mocking Deferred Imports
- When service functions use deferred imports (inside function body), patch at the source module
- Example: `_call_llm_for_segmentation` is private in `batch_segmenter.py` -- patch as `anima_server.services.agent.batch_segmenter._call_llm_for_segmentation`
- For `settings`, import at module level if tests need to patch it

## Files I Own
- `services/agent/heat_scoring.py` - heat formula, decay, hottest/coldest queries
- Key constants: HEAT_ALPHA=1.0, HEAT_BETA=1.0, HEAT_GAMMA=1.0, HEAT_DELTA=0.5, RECENCY_TAU_HOURS=24.0
- `_HEAT_NORM_K=5.0` in memory_store.py controls sigmoid normalization steepness
- `services/agent/batch_segmenter.py` - F6 batch episode segmentation
- Latest migration: `20260319_0004` (episode segmentation columns)
