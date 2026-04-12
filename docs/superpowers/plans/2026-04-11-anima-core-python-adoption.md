# anima-core Python Adoption Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Python server's `anima_core` integration explicit, centralized, and incrementally broader by adopting low-risk stateless Rust helpers behind existing Python APIs.

**Architecture:** Add a single server-owned `anima_core` adapter module, migrate all current direct imports through it, then adopt Rust-backed `cosine_similarity`, `compute_heat`, and `rrf_fuse` behind parity-tested Python wrappers. Keep stateful engine ownership in Python/SQLAlchemy for now and treat Rust engine bindings as deferred.

**Tech Stack:** Python, FastAPI service layer, PyO3 `anima_core` module, Rust/Cargo workspace, pytest

---

## Scope Check

This plan intentionally covers Python integration hygiene and low-risk helper adoption.

It does **not** cover:

- replacing SQLAlchemy memory storage with Rust stores
- replacing `pgvector` with Rust HNSW
- live path-engine adoption in the Python server
- capsule storage lifecycle changes beyond existing export/import use
- broad FFI removal or Rust crate API redesign

Those remain separate efforts.

---

## File Structure

### New files

| File | Responsibility |
|------|----------------|
| `apps/server/src/anima_server/services/anima_core_bindings.py` | Single Python-owned adapter for optional `anima_core` imports, capability flags, wrappers, and Python fallbacks |
| `apps/server/tests/test_anima_core_bindings.py` | Availability, fallback, and wrapper-parity tests for the new adapter |

### Modified files

| File | Changes |
|------|---------|
| `apps/server/src/anima_server/services/vault.py` | Replace direct `anima_core` imports with adapter wrappers |
| `apps/server/src/anima_server/services/agent/adaptive_retrieval.py` | Route adaptive-cutoff helpers through adapter |
| `apps/server/src/anima_server/services/agent/graph_triplets.py` | Route triplet extraction through adapter |
| `apps/server/src/anima_server/services/agent/text_processing.py` | Route text normalization and PDF spacing helpers through adapter |
| `apps/server/src/anima_server/services/agent/embeddings.py` | Route `cosine_similarity` and reciprocal-rank fusion through adapter-backed wrappers |
| `apps/server/src/anima_server/services/agent/heat_scoring.py` | Route `compute_heat` through adapter-backed wrapper |
| `apps/server/tests/test_vault.py` | Verify adapter-backed capsule paths still behave correctly |
| `apps/server/tests/test_hybrid_retrieval.py` | Add parity coverage for reciprocal-rank fusion behavior |
| `apps/server/tests/test_memory_scored_retrieval.py` | Keep cosine similarity behavior stable while switching implementation |
| `apps/server/tests/test_heat_scoring.py` | Keep heat-scoring behavior stable while switching implementation |
| `packages/anima-core/src/ffi.rs` | Clarify which bindings are server-used vs deferred/preview in docs/comments |

---

## Task 1: Add the central Python adapter and migrate existing production-used bindings

**Files:**
- Create: `apps/server/src/anima_server/services/anima_core_bindings.py`
- Modify: `apps/server/src/anima_server/services/vault.py`
- Modify: `apps/server/src/anima_server/services/agent/adaptive_retrieval.py`
- Modify: `apps/server/src/anima_server/services/agent/graph_triplets.py`
- Modify: `apps/server/src/anima_server/services/agent/text_processing.py`
- Test: `apps/server/tests/test_anima_core_bindings.py`
- Test: `apps/server/tests/test_vault.py`

- [ ] **Step 1: Write the failing adapter tests**

Add tests in `apps/server/tests/test_anima_core_bindings.py` covering:

```python
def test_adapter_reports_missing_module_when_anima_core_unavailable():
    ...

def test_adapter_exposes_current_production_capabilities():
    ...

def test_adapter_capsule_wrappers_raise_clear_error_when_module_missing():
    ...

def test_adapter_text_and_triplet_wrappers_fall_back_to_python_implementation():
    ...
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
uv run pytest apps/server/tests/test_anima_core_bindings.py -q
```

Expected: FAIL because the adapter module does not exist yet.

- [ ] **Step 3: Add the adapter module**

Create `apps/server/src/anima_server/services/anima_core_bindings.py` with:

```python
try:
    import anima_core as _anima_core
except Exception:
    _anima_core = None

HAS_ANIMA_CORE = _anima_core is not None

def capability_map() -> dict[str, bool]:
    return {
        "read_capsule": HAS_ANIMA_CORE and hasattr(_anima_core, "read_capsule"),
        "write_capsule": HAS_ANIMA_CORE and hasattr(_anima_core, "write_capsule"),
        "find_adaptive_cutoff": HAS_ANIMA_CORE and hasattr(_anima_core, "find_adaptive_cutoff"),
        "normalize_scores": HAS_ANIMA_CORE and hasattr(_anima_core, "normalize_scores"),
        "extract_triplets": HAS_ANIMA_CORE and hasattr(_anima_core, "extract_triplets"),
        "fix_pdf_spacing": HAS_ANIMA_CORE and hasattr(_anima_core, "fix_pdf_spacing"),
        "normalize_text": HAS_ANIMA_CORE and hasattr(_anima_core, "normalize_text"),
        "cosine_similarity": HAS_ANIMA_CORE and hasattr(_anima_core, "cosine_similarity"),
        "compute_heat": HAS_ANIMA_CORE and hasattr(_anima_core, "compute_heat"),
        "rrf_fuse": HAS_ANIMA_CORE and hasattr(_anima_core, "rrf_fuse"),
    }
```

Also add wrapper functions for the current Tier 1 bindings so `vault.py`, `adaptive_retrieval.py`, `graph_triplets.py`, and `text_processing.py` no longer import `anima_core` directly.

- [ ] **Step 4: Migrate the existing import sites**

Update these modules to import wrappers from `anima_core_bindings.py`:

- `vault.py`
- `adaptive_retrieval.py`
- `graph_triplets.py`
- `text_processing.py`

Do **not** change behavior in this step. This is a no-functional-change migration.

- [ ] **Step 5: Run adapter and vault tests**

Run:

```bash
uv run pytest apps/server/tests/test_anima_core_bindings.py apps/server/tests/test_vault.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/server/src/anima_server/services/anima_core_bindings.py apps/server/src/anima_server/services/vault.py apps/server/src/anima_server/services/agent/adaptive_retrieval.py apps/server/src/anima_server/services/agent/graph_triplets.py apps/server/src/anima_server/services/agent/text_processing.py apps/server/tests/test_anima_core_bindings.py apps/server/tests/test_vault.py
git commit -m "refactor(server): centralize anima_core bindings"
```

---

## Task 2: Adopt Rust cosine similarity behind the existing Python API

**Files:**
- Modify: `apps/server/src/anima_server/services/anima_core_bindings.py`
- Modify: `apps/server/src/anima_server/services/agent/embeddings.py`
- Test: `apps/server/tests/test_memory_scored_retrieval.py`

- [ ] **Step 1: Write the failing parity tests**

Extend `apps/server/tests/test_memory_scored_retrieval.py` with a focused parity test:

```python
def test_cosine_similarity_matches_python_fallback(monkeypatch):
    from anima_server.services import anima_core_bindings

    monkeypatch.setattr(
        anima_core_bindings,
        "rust_cosine_similarity",
        lambda a, b: 0.5,
    )
    assert anima_core_bindings.cosine_similarity([1.0, 0.0], [1.0, 0.0]) == 0.5
```

Also keep the existing semantic behavior assertions intact:

- identical vectors return `1.0`
- orthogonal vectors are near `0`
- opposite vectors are near `-1`
- empty or mismatched vectors return `0.0`

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
uv run pytest apps/server/tests/test_memory_scored_retrieval.py -q
```

Expected: FAIL because the adapter does not yet own cosine similarity.

- [ ] **Step 3: Add the adapter-backed cosine wrapper**

In `anima_core_bindings.py`, add:

```python
def _python_cosine_similarity(a: list[float], b: list[float]) -> float:
    ...

def cosine_similarity(a: list[float], b: list[float]) -> float:
    if HAS_ANIMA_CORE and hasattr(_anima_core, "cosine_similarity"):
        return float(_anima_core.cosine_similarity(a, b))
    return _python_cosine_similarity(a, b)
```

In `embeddings.py`, keep the public function surface stable by delegating:

```python
from anima_server.services.anima_core_bindings import cosine_similarity
```

Remove the local implementation once the imported wrapper is in place.

- [ ] **Step 4: Run the tests to verify they pass**

Run:

```bash
uv run pytest apps/server/tests/test_memory_scored_retrieval.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/server/src/anima_server/services/anima_core_bindings.py apps/server/src/anima_server/services/agent/embeddings.py apps/server/tests/test_memory_scored_retrieval.py
git commit -m "feat(server): route cosine similarity through anima_core"
```

---

## Task 3: Adopt Rust heat scoring behind the existing Python API

**Files:**
- Modify: `apps/server/src/anima_server/services/anima_core_bindings.py`
- Modify: `apps/server/src/anima_server/services/agent/heat_scoring.py`
- Test: `apps/server/tests/test_heat_scoring.py`

- [ ] **Step 1: Write the failing heat parity tests**

Extend `apps/server/tests/test_heat_scoring.py` with tests that prove:

- the public `compute_heat(...)` API remains unchanged
- adapter fallback still returns the same values as the current Python implementation
- Rust-backed execution can be selected without changing callers

Example test:

```python
def test_compute_heat_delegates_through_adapter(monkeypatch):
    from anima_server.services import anima_core_bindings

    monkeypatch.setattr(anima_core_bindings, "rust_compute_heat", lambda **kwargs: 12.5)
    assert anima_core_bindings.compute_heat(... ) == 12.5
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
uv run pytest apps/server/tests/test_heat_scoring.py -q
```

Expected: FAIL because the adapter does not yet expose heat scoring.

- [ ] **Step 3: Add the adapter-backed heat wrapper**

In `anima_core_bindings.py`, add:

```python
def _python_compute_time_decay(... ) -> float:
    ...

def _python_compute_heat(... ) -> float:
    ...

def compute_heat(... ) -> float:
    if HAS_ANIMA_CORE and hasattr(_anima_core, "compute_heat"):
        return float(_anima_core.compute_heat(...))
    return _python_compute_heat(...)
```

In `heat_scoring.py`, preserve the public function names but route implementation through the adapter.

Keep `compute_time_decay(...)` in Python if the Rust module does not expose an equivalent helper directly.

- [ ] **Step 4: Run the tests to verify they pass**

Run:

```bash
uv run pytest apps/server/tests/test_heat_scoring.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/server/src/anima_server/services/anima_core_bindings.py apps/server/src/anima_server/services/agent/heat_scoring.py apps/server/tests/test_heat_scoring.py
git commit -m "feat(server): route heat scoring through anima_core"
```

---

## Task 4: Adopt Rust reciprocal-rank fusion behind the hybrid retrieval surface

**Files:**
- Modify: `apps/server/src/anima_server/services/anima_core_bindings.py`
- Modify: `apps/server/src/anima_server/services/agent/embeddings.py`
- Test: `apps/server/tests/test_hybrid_retrieval.py`

- [ ] **Step 1: Write the failing RRF parity tests**

Extend `apps/server/tests/test_hybrid_retrieval.py` with tests proving that the existing `_reciprocal_rank_fusion(...)` behavior is preserved when implementation is moved behind the adapter:

```python
def test_rrf_wrapper_preserves_rank_based_merge():
    ...

def test_rrf_wrapper_preserves_custom_weights():
    ...

def test_rrf_wrapper_handles_empty_inputs():
    ...
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
uv run pytest apps/server/tests/test_hybrid_retrieval.py -q
```

Expected: FAIL once the new delegation tests are added and before the wrapper exists.

- [ ] **Step 3: Add the adapter-backed RRF wrapper**

In `anima_core_bindings.py`, add:

```python
def _python_rrf_fuse(
    semantic: list[tuple[int, float]],
    keyword: list[tuple[int, float]],
    *,
    semantic_weight: float,
    keyword_weight: float,
    k: int,
) -> list[tuple[int, float]]:
    ...

def rrf_fuse(... ) -> list[tuple[int, float]]:
    if HAS_ANIMA_CORE and hasattr(_anima_core, "rrf_fuse"):
        return [
            (int(item_id), float(score))
            for item_id, score in _anima_core.rrf_fuse(...)
        ]
    return _python_rrf_fuse(...)
```

In `embeddings.py`, keep `_reciprocal_rank_fusion(...)` as the stable server surface, but implement it by delegating to the adapter wrapper.

- [ ] **Step 4: Run the tests to verify they pass**

Run:

```bash
uv run pytest apps/server/tests/test_hybrid_retrieval.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/server/src/anima_server/services/anima_core_bindings.py apps/server/src/anima_server/services/agent/embeddings.py apps/server/tests/test_hybrid_retrieval.py
git commit -m "feat(server): route hybrid fusion through anima_core"
```

---

## Task 5: Clarify binding tiers in Rust FFI docs without changing runtime ownership

**Files:**
- Modify: `packages/anima-core/src/ffi.rs`
- Modify: `docs/superpowers/specs/2026-04-11-anima-core-python-adoption-design.md`
- Modify: `docs/superpowers/plans/2026-04-11-anima-core-python-adoption.md`

- [ ] **Step 1: Write the failing documentation review checklist**

Create a checklist for `ffi.rs` comments that confirms:

- Tier 1 production server bindings are clearly identifiable
- Tier 2 stateless helper candidates are grouped as adoption targets
- Tier 3 stateful engine bindings are described as deferred/preview, not implied server dependencies

- [ ] **Step 2: Review current `ffi.rs` and confirm the checklist fails**

Inspect:

```bash
rg -n "Tier 1|Tier 2|Tier 3|preview|server-used" packages/anima-core/src/ffi.rs
```

Expected: no explicit binding-tier documentation exists yet.

- [ ] **Step 3: Add grouping comments/docstrings in `ffi.rs`**

Add section comments such as:

```rust
// Tier 1: production-used Python server bindings
// Tier 2: stateless helper candidates for near-term server adoption
// Tier 3: deferred or preview engine bindings; not current Python runtime dependencies
```

Do not remove preview bindings in this task. This is clarity work, not API amputation.

- [ ] **Step 4: Re-read the spec and plan for consistency**

Check that:

- the design and plan still match the implementation sequencing
- the docs do not imply the Python server already uses Rust-owned state

- [ ] **Step 5: Commit**

```bash
git add packages/anima-core/src/ffi.rs docs/superpowers/specs/2026-04-11-anima-core-python-adoption-design.md docs/superpowers/plans/2026-04-11-anima-core-python-adoption.md
git commit -m "docs(anima-core): clarify python adoption tiers"
```

---

## Verification

After all tasks are complete, run:

```bash
uv run pytest apps/server/tests/test_anima_core_bindings.py apps/server/tests/test_vault.py apps/server/tests/test_memory_scored_retrieval.py apps/server/tests/test_heat_scoring.py apps/server/tests/test_hybrid_retrieval.py -q
```

Then run:

```bash
cargo test -p anima-core
```

Expected:

- server-side adapter and parity tests pass
- Rust crate tests still pass

---

## Execution Notes

- Preserve Python fallbacks throughout the migration.
- Do not change public Python call signatures for `compute_heat`, `cosine_similarity`, or hybrid retrieval helpers.
- Do not start integrating `FrameStore`, `CardStore`, `KnowledgeGraph`, or path-engine handles into the live server runtime under this plan.
- If parity differences appear between Python and Rust implementations, stop and document them instead of silently changing behavior.
