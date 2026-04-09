# anima-core Phase 5: Engine Facade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a coherent `AnimaEngine` facade in `anima-core` so hosts can open, query, verify, replay, and export one engine object instead of orchestrating raw stores and helper functions themselves.

**Architecture:** Add a focused `engine.rs` module that owns the current in-memory working set (`FrameStore`, `CardStore`, `KnowledgeGraph`) plus optional replay registry and derived temporal index state. Keep the first engine slice storage-light: in-memory lifecycle, verify/stats, projection/query helpers, and capsule import/export that treats `Frames` as canonical while `Cards`, `Graph`, and engine metadata remain explicitly derived compatibility sections. Python will bind the Rust facade as a thin `Engine` class only after Rust behavior is proven.

**Tech Stack:** Rust, serde, Cargo workspace, PyO3, existing `capsule.rs` / `integrity.rs` / `projection.rs` / `temporal.rs` / `replay.rs`

---

## Scope Check

This plan intentionally covers **Phase 5 only**.

It does **not** include:

- OS-level single-writer file locks
- background index persistence
- repair / `doctor` mutations
- capsule migrations beyond section manifest classification
- new retrieval algorithms

Those stay in later engine-hardening work.

---

## File Structure

### New files

| File | Responsibility |
|------|----------------|
| `packages/anima-core/src/engine.rs` | Top-level `AnimaEngine` facade, lifecycle, query helpers, capsule import/export, unit tests |

### Modified files

| File | Changes |
|------|---------|
| `packages/anima-core/src/lib.rs` | Export `engine` module |
| `packages/anima-core/src/capsule.rs` | Add canonical-vs-derived section metadata helpers and manifest inspection surface used by engine export/import |
| `packages/anima-core/src/ffi.rs` | Expose `AnimaEngine` as a thin Python class plus capsule-open/export helpers |

---

## Task 1: Define the in-memory `AnimaEngine` facade

**Files:**
- Create: `packages/anima-core/src/engine.rs`
- Modify: `packages/anima-core/src/lib.rs`
- Test: `packages/anima-core/src/engine.rs`

- [ ] **Step 1: Write the failing engine lifecycle tests**

Add focused tests in `packages/anima-core/src/engine.rs` for:

```rust
#[test]
fn engine_new_starts_empty_and_reports_zero_stats() {
    let engine = AnimaEngine::new();
    assert_eq!(engine.stats().frame_count, 0);
    assert!(engine.entity_state("missing").slots.is_empty());
}

#[test]
fn engine_from_parts_exposes_projection_and_history_queries() {
    // frames/cards/graph inserted in stores are visible through engine methods
}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
cargo test -p anima-core engine::tests::engine_new
```

Expected: FAIL because `engine.rs` and `AnimaEngine` do not exist yet.

- [ ] **Step 3: Implement the base engine type**

In `packages/anima-core/src/engine.rs`, add:

```rust
pub struct AnimaEngine {
    frames: FrameStore,
    cards: CardStore,
    graph: KnowledgeGraph,
    #[cfg(feature = "replay")]
    replay_registry: ReplayRegistry,
}

impl AnimaEngine {
    pub fn new() -> Self { ... }
    pub fn from_parts(frames: FrameStore, cards: CardStore, graph: KnowledgeGraph, ...) -> Self { ... }
    pub fn frames(&self) -> &FrameStore { ... }
    pub fn cards(&self) -> &CardStore { ... }
    pub fn graph(&self) -> &KnowledgeGraph { ... }
    pub fn entity_state(&self, entity: &str) -> EntityState { ... }
    pub fn slot_history(&self, entity: &str, slot: &str) -> Vec<MemoryCard> { ... }
    pub fn verify(&self) -> IntegrityReport { ... }
    pub fn stats(&self) -> CoreStats { ... }
}
```

Rules:

- `new()` must build an empty but fully usable engine
- `from_parts()` must preserve deterministic query behavior
- `verify()` and `stats()` should delegate to existing integrity helpers instead of re-implementing them
- keep ownership simple; do not add locks or background caches in this task

- [ ] **Step 4: Export the module**

Modify `packages/anima-core/src/lib.rs`:

```rust
pub mod engine;
```

- [ ] **Step 5: Run the engine lifecycle tests to verify they pass**

Run:

```bash
cargo test -p anima-core engine::tests::engine_new
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/anima-core/src/engine.rs packages/anima-core/src/lib.rs
git commit -m "feat(anima-core): add base engine facade"
```

---

## Task 2: Add query and replay helpers to the engine surface

**Files:**
- Modify: `packages/anima-core/src/engine.rs`
- Test: `packages/anima-core/src/engine.rs`

- [ ] **Step 1: Write the failing query-helper tests**

Add tests for:

```rust
#[test]
fn engine_temporal_queries_use_indexed_frame_ordering() {
    // temporal_range and temporal_as_of return newest-first results
}

#[cfg(feature = "replay")]
#[test]
fn engine_replay_queries_expose_session_summary_and_checkpoint_lookup() {
    // registry-backed summary and checkpoint lookup work through engine
}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
cargo test -p anima-core engine::tests::engine_temporal --features "temporal replay"
```

Expected: FAIL because engine query helpers do not exist yet.

- [ ] **Step 3: Implement temporal and replay methods**

Extend `packages/anima-core/src/engine.rs` with:

```rust
#[cfg(feature = "temporal")]
pub fn temporal_index(&self) -> TemporalIndex { ... }

#[cfg(feature = "temporal")]
pub fn temporal_range(&self, start: Option<i64>, end: Option<i64>, limit: Option<usize>) -> Vec<&Frame> { ... }

#[cfg(feature = "temporal")]
pub fn temporal_as_of(&self, timestamp: i64, limit: Option<usize>) -> Vec<&Frame> { ... }

#[cfg(feature = "replay")]
pub fn replay_session_ids(&self) -> Vec<String> { ... }

#[cfg(feature = "replay")]
pub fn replay_session_summary(&self, session_id: &str) -> Option<ReplaySummary> { ... }

#[cfg(feature = "replay")]
pub fn replay_checkpoint_by_label(&self, session_id: &str, label: &str) -> Option<ReplayCheckpoint> { ... }
```

Rules:

- temporal queries must use `TemporalIndex::from_store`
- replay methods must delegate to `ReplayRegistry`
- keep engine query methods read-only and deterministic

- [ ] **Step 4: Run the tests to verify they pass**

Run:

```bash
cargo test -p anima-core engine::tests --features "temporal replay"
```

Expected: PASS for the new engine query coverage.

- [ ] **Step 5: Commit**

```bash
git add packages/anima-core/src/engine.rs
git commit -m "feat(anima-core): add engine query and replay helpers"
```

---

## Task 3: Add capsule manifest classification and engine import/export

**Files:**
- Modify: `packages/anima-core/src/capsule.rs`
- Modify: `packages/anima-core/src/engine.rs`
- Test: `packages/anima-core/src/capsule.rs`
- Test: `packages/anima-core/src/engine.rs`

- [ ] **Step 1: Write the failing capsule classification and round-trip tests**

Add tests for:

```rust
#[test]
fn section_manifest_marks_frames_canonical_and_cards_graph_derived() {
    // the policy matches the standalone-engine spec
}

#[test]
fn engine_capsule_roundtrip_preserves_frames_and_derived_manifest() {
    // frames always survive export -> import; derived sections are explicitly labeled
}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
cargo test -p anima-core engine::tests::engine_capsule --features "temporal replay"
```

Expected: FAIL because manifest classification and engine import/export do not exist yet.

- [ ] **Step 3: Add explicit section classification helpers**

In `packages/anima-core/src/capsule.rs`, add a narrow manifest surface such as:

```rust
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum SectionStorageClass {
    Canonical,
    Derived,
}

pub fn section_storage_class(kind: SectionKind) -> SectionStorageClass { ... }

pub fn section_manifest(sections: &[SectionKind]) -> Vec<SectionManifestEntry> { ... }
```

Rules:

- `Frames` must be canonical
- `Cards`, `Graph`, and engine-owned `Metadata` must be marked derived
- do not add new section kinds in this task
- this task is about explicit policy over the existing section set, not replay persistence

- [ ] **Step 4: Implement engine capsule import/export**

In `packages/anima-core/src/engine.rs`, add:

```rust
pub fn write_capsule(&self, password: Option<&[u8]>) -> crate::Result<Vec<u8>> { ... }
pub fn read_capsule(raw: Vec<u8>, password: Option<&[u8]>) -> crate::Result<Self> { ... }
pub fn capsule_manifest(&self) -> Vec<SectionManifestEntry> { ... }
```

Rules:

- export `Frames` always as canonical state
- `Cards` and `Graph` may be written as derived compatibility sections if present, but replay must not be persisted in this task
- metadata should contain lightweight manifest data only; do not duplicate canonical stores there
- import must fail loudly on missing `Frames`
- import may accept missing derived sections and build an engine with empty derived stores rather than inventing a rebuild pipeline in this phase

- [ ] **Step 5: Run the tests to verify they pass**

Run:

```bash
cargo test -p anima-core engine::tests::engine_capsule --features "temporal replay"
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/anima-core/src/capsule.rs packages/anima-core/src/engine.rs
git commit -m "feat(anima-core): add engine capsule import export"
```

---

## Task 4: Expose the engine facade through Python FFI

**Files:**
- Modify: `packages/anima-core/src/ffi.rs`
- Test: `packages/anima-core/src/ffi.rs`

- [ ] **Step 1: Write the failing Python-facing engine tests**

Add focused tests in `packages/anima-core/src/ffi.rs` for:

```rust
#[test]
fn exported_engine_class_supports_verify_project_and_temporal_queries() {
    // Python Engine exposes verify/stats/entity_state/temporal methods
}

#[test]
fn exported_engine_capsule_roundtrip_restores_state() {
    // Engine.from_capsule_bytes(...) restores exported data
}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
$env:PATH='C:\\Users\\leoca\\AppData\\Roaming\\uv\\python\\cpython-3.12.9-windows-x86_64-none;' + $env:PATH; cargo test -p anima-core --features python ffi::python::tests::exported_engine
```

Expected: FAIL because no engine class is exported yet.

- [ ] **Step 3: Add a thin `Engine` PyO3 class**

In `packages/anima-core/src/ffi.rs`, add:

```rust
#[pyclass(name = "Engine")]
struct PyAnimaEngine {
    inner: crate::engine::AnimaEngine,
}
```

Expose only thin wrappers first:

```rust
#[pymethods]
impl PyAnimaEngine {
    #[new]
    fn new() -> Self { ... }
    #[staticmethod]
    fn from_capsule_bytes(data: Vec<u8>, password: Option<Vec<u8>>) -> PyResult<Self> { ... }
    fn to_capsule_bytes(&self, password: Option<Vec<u8>>) -> PyResult<Vec<u8>> { ... }
    fn verify(&self, py: Python<'_>) -> PyResult<PyObject> { ... }
    fn stats(&self, py: Python<'_>) -> PyResult<PyObject> { ... }
    fn project_entity_state(&self, py: Python<'_>, entity: &str) -> PyResult<PyObject> { ... }
    fn project_slot_history(&self, py: Python<'_>, entity: &str, slot: &str) -> PyResult<PyObject> { ... }
    fn temporal_range(&self, py: Python<'_>, start: Option<i64>, end: Option<i64>, limit: Option<usize>) -> PyResult<PyObject> { ... }
}
```

Rules:

- reuse existing JSON-to-Python helpers
- do not duplicate business logic from `engine.rs`
- do not expose raw mutable internals in this task

- [ ] **Step 4: Register the new class and helpers**

Modify module registration in `packages/anima-core/src/ffi.rs`:

```rust
m.add_class::<PyAnimaEngine>()?;
```

- [ ] **Step 5: Run the tests to verify they pass**

Run:

```bash
$env:PATH='C:\\Users\\leoca\\AppData\\Roaming\\uv\\python\\cpython-3.12.9-windows-x86_64-none;' + $env:PATH; cargo test -p anima-core --features python ffi::python::tests::exported_engine
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/anima-core/src/ffi.rs
git commit -m "feat(anima-core): expose engine facade through ffi"
```

---

## Task 5: Final verification for Phase 5

**Files:**
- Modify: `docs/superpowers/specs/2026-04-08-anima-core-standalone-engine-design.md` only if implementation changes section-policy assumptions

- [ ] **Step 1: Run the full Rust verification**

Run:

```bash
cargo test -p anima-core --features "temporal replay"
$env:PATH='C:\\Users\\leoca\\AppData\\Roaming\\uv\\python\\cpython-3.12.9-windows-x86_64-none;' + $env:PATH; cargo test -p anima-core --features python
```

Expected: PASS.

- [ ] **Step 2: Refresh the editable package**

Run:

```bash
uv sync --all-packages --reinstall-package anima-core --refresh-package anima-core
```

Expected: editable `anima-core` rebuild completes.

- [ ] **Step 3: Run installed-module smoke**

Run:

```bash
uv run python -c "import anima_core; print(hasattr(anima_core, 'Engine')); print(sorted(name for name in dir(anima_core.Engine) if 'capsule' in name or 'project' in name or 'temporal' in name))"
```

Expected:

- `True`
- method list includes the engine lifecycle/query helpers

- [ ] **Step 4: Confirm scope stayed inside Phase 5**

Check:

```bash
git diff -- packages/anima-core/src
```

Verify the diff only adds:

- `engine.rs`
- `lib.rs` export
- capsule manifest / classification helpers
- engine FFI class and tests

It should **not** add:

- OS-level locking
- new retrieval algorithms
- replay capsule persistence
- repair / `doctor`

- [ ] **Step 5: Commit plan/doc updates if needed**

```bash
git add packages/anima-core/src docs/superpowers/plans/2026-04-09-anima-core-phase5-engine-facade.md
git commit -m "docs(anima-core): add phase5 engine facade plan"
```

---

## Follow-On Work

After this phase, the remaining major work is engine hardening:

1. single-writer storage semantics
2. explicit derived-section persistence and rebuild hooks
3. `doctor` / repair operations
4. storage-backed engine open/create semantics beyond in-memory capsule round-trip
