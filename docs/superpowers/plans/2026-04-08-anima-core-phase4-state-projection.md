# anima-core Phase 4: State Projection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a stable `EntityState` projection surface in `anima-core` so hosts can ask the core for current structured knowledge about an entity instead of reconstructing it in Python.

**Architecture:** Add a focused `projection.rs` module that composes `CardStore`, `KnowledgeGraph`, and supporting `FrameStore` references into a deterministic `EntityState` view. The initial slice stays read-only and host-friendly: current slot values, version history by slot, connected entities, and supporting frame ids. Python FFI will expose thin dict/list payloads over the Rust structs after Rust-native behavior is proven.

**Tech Stack:** Rust, Cargo workspace, serde, PyO3

---

## Scope Check

This plan intentionally covers **Phase 4 only**.

It does **not** include:

- capsule packaging of projections
- repair / doctor operations
- engine lifecycle or `engine.rs`
- single-writer locking

Those stay in later phases.

---

## File Structure

### New files

| File | Responsibility |
|------|----------------|
| `packages/anima-core/src/projection.rs` | `EntityState` types, state builders, projection queries, unit tests |

### Modified files

| File | Changes |
|------|---------|
| `packages/anima-core/src/lib.rs` | Export `projection` module |
| `packages/anima-core/src/cards.rs` | Add small public helpers for active slot state / slot history access if current internals are not enough |
| `packages/anima-core/src/graph.rs` | Add small public helpers for entity lookup / neighbor extraction if current internals are not enough |
| `packages/anima-core/src/ffi.rs` | Expose entity-state projection helpers as Python-friendly payloads |

---

## Task 1: Define `EntityState` and slot-history types

**Files:**
- Create: `packages/anima-core/src/projection.rs`
- Modify: `packages/anima-core/src/lib.rs`
- Test: `packages/anima-core/src/projection.rs`

- [ ] **Step 1: Write the failing unit tests for entity-state shape**

Add tests in `packages/anima-core/src/projection.rs` for:

```rust
#[test]
fn entity_state_groups_active_slot_values_for_single_and_multiple_slots() {
    // user.employer => ["OpenAI"]
    // user.likes => ["coffee", "jazz"]
}

#[test]
fn entity_state_collects_supporting_frame_ids_without_duplicates() {
    // values and graph neighbors should surface unique frame IDs
}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
cargo test -p anima-core projection::tests::entity_state
```

Expected: FAIL because `projection.rs` and `EntityState` do not exist yet.

- [ ] **Step 3: Implement the core projection types**

In `packages/anima-core/src/projection.rs`, add:

```rust
pub struct SlotValueState {
    pub slot: String,
    pub values: Vec<String>,
    pub supporting_frame_ids: Vec<FrameId>,
}

pub struct ConnectedEntityState {
    pub relation_type: String,
    pub entity_name: String,
    pub entity_kind: EntityKind,
    pub supporting_frame_ids: Vec<FrameId>,
}

pub struct EntityState {
    pub entity: String,
    pub slots: Vec<SlotValueState>,
    pub connected_entities: Vec<ConnectedEntityState>,
    pub supporting_frame_ids: Vec<FrameId>,
}
```

Keep ordering deterministic:

- slots sorted by slot name
- values sorted lexicographically
- neighbors sorted by relation then entity
- frame ids sorted ascending and deduplicated

- [ ] **Step 4: Export the module**

Modify `packages/anima-core/src/lib.rs`:

```rust
pub mod projection;
```

- [ ] **Step 5: Run the tests to verify they pass**

Run:

```bash
cargo test -p anima-core projection::tests::entity_state
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/anima-core/src/projection.rs packages/anima-core/src/lib.rs
git commit -m "feat(anima-core): add entity state projection types"
```

---

## Task 2: Project current slot state and slot history from cards

**Files:**
- Modify: `packages/anima-core/src/projection.rs`
- Modify: `packages/anima-core/src/cards.rs`
- Test: `packages/anima-core/src/projection.rs`

- [ ] **Step 1: Write the failing tests for slot history**

Add tests for:

```rust
#[test]
fn slot_history_returns_versions_oldest_to_newest_for_entity_slot() {
    // sets -> updates -> retracts history remains visible
}

#[test]
fn entity_state_excludes_inactive_values_from_current_slot_projection() {
    // superseded/retracted cards do not appear in current slots
}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
cargo test -p anima-core projection::tests::slot_history
```

Expected: FAIL because projection cannot yet read slot history/current state from `CardStore`.

- [ ] **Step 3: Add minimal public card helpers if needed**

If current internals are not enough, add narrow helpers in `packages/anima-core/src/cards.rs` such as:

```rust
pub fn active_cards_for_entity(&self, entity: &str) -> Vec<&MemoryCard>
pub fn history_for_entity_slot(&self, entity: &str, slot: &str) -> Vec<&MemoryCard>
```

Do not add a broad query language. Keep helpers deterministic and read-only.

- [ ] **Step 4: Implement current-slot and history projection**

In `packages/anima-core/src/projection.rs`, add:

```rust
pub fn entity_state_from_cards(cards: &CardStore, entity: &str) -> EntityState
pub fn slot_history(cards: &CardStore, entity: &str, slot: &str) -> Vec<MemoryCard>
```

Rules:

- current slot values come only from active cards
- multiple cards for the same slot collapse into one `SlotValueState`
- supporting frame ids are unique and sorted

- [ ] **Step 5: Run the tests to verify they pass**

Run:

```bash
cargo test -p anima-core projection::tests::slot_history
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/anima-core/src/projection.rs packages/anima-core/src/cards.rs
git commit -m "feat(anima-core): project current slot state and history from cards"
```

---

## Task 3: Compose graph neighbors into `EntityState`

**Files:**
- Modify: `packages/anima-core/src/projection.rs`
- Modify: `packages/anima-core/src/graph.rs`
- Test: `packages/anima-core/src/projection.rs`

- [ ] **Step 1: Write the failing neighbor tests**

Add tests for:

```rust
#[test]
fn entity_state_includes_connected_entities_from_graph_edges() {
    // user --employer--> OpenAI
}

#[test]
fn entity_state_handles_missing_graph_node_gracefully() {
    // cards-only entities still produce usable state
}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
cargo test -p anima-core projection::tests::neighbors
```

Expected: FAIL because graph data is not yet folded into the projection.

- [ ] **Step 3: Add minimal graph helpers if needed**

If projection cannot access graph data cleanly, add narrow read helpers in `packages/anima-core/src/graph.rs` such as:

```rust
pub fn get_node_by_name(&self, name: &str) -> Option<&GraphNode>
pub fn neighbors_for_node(&self, node_id: u64) -> Vec<&GraphEdge>
```

Keep them read-only and deterministic.

- [ ] **Step 4: Merge graph neighbors into `EntityState`**

Update `packages/anima-core/src/projection.rs` so:

- if the graph contains the entity, connected nodes are projected into `connected_entities`
- edge frame ids are folded into entity-level supporting frame ids
- cards-only entities still return valid state with empty neighbors

- [ ] **Step 5: Run the tests to verify they pass**

Run:

```bash
cargo test -p anima-core projection::tests::neighbors
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/anima-core/src/projection.rs packages/anima-core/src/graph.rs
git commit -m "feat(anima-core): compose graph neighbors into entity state"
```

---

## Task 4: Expose projection helpers through Python FFI

**Files:**
- Modify: `packages/anima-core/src/ffi.rs`
- Test: `packages/anima-core/src/ffi.rs`

- [ ] **Step 1: Write the failing FFI tests**

Add focused tests in `packages/anima-core/src/ffi.rs` for:

```rust
#[test]
fn exported_entity_state_returns_python_friendly_shape() {
    // slots / connected_entities / supporting_frame_ids keys exist
}

#[test]
fn exported_slot_history_returns_ordered_versions() {
    // oldest to newest card history
}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
cargo test -p anima-core ffi::python::tests::exported_entity_state --features python
```

Expected: FAIL because projection bindings do not exist.

- [ ] **Step 3: Add FFI bindings**

Expose thin dict/list functions first:

```rust
#[pyfunction]
fn project_entity_state(
    py: Python<'_>,
    cards: &PyCardStore,
    graph: &PyKnowledgeGraph,
    entity: &str,
) -> PyResult<PyObject> { ... }

#[pyfunction]
fn project_slot_history(
    py: Python<'_>,
    cards: &PyCardStore,
    entity: &str,
    slot: &str,
) -> PyResult<PyObject> { ... }
```

Convert Rust structs through `serde_json::to_value(...)` and the existing `json_value_to_py(...)` helper.

- [ ] **Step 4: Register the new functions**

Modify module registration in `packages/anima-core/src/ffi.rs` with `m.add_function(...)`.

- [ ] **Step 5: Run the tests to verify they pass**

Run:

```bash
$env:PATH='C:\\Users\\leoca\\AppData\\Roaming\\uv\\python\\cpython-3.12.9-windows-x86_64-none;' + $env:PATH; cargo test -p anima-core --features python ffi::python::tests::exported_entity_state
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/anima-core/src/ffi.rs
git commit -m "feat(anima-core): expose entity state projection through ffi"
```

---

## Task 5: Final verification for Phase 4

**Files:**
- Modify: `docs/superpowers/specs/2026-04-08-anima-core-standalone-engine-design.md` only if implementation changes assumptions

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
uv run python -c "import anima_core; print(sorted(name for name in dir(anima_core) if 'project_' in name or 'entity_state' in name))"
```

Expected: output includes the projection bindings.

- [ ] **Step 4: Confirm scope stayed inside Phase 4**

Check:

```bash
git diff -- packages/anima-core/src
```

Verify the diff only adds:

- `projection.rs`
- small read helpers in `cards.rs` / `graph.rs`
- projection FFI surface

It should **not** add:

- capsule changes
- engine facade
- single-writer logic

- [ ] **Step 5: Commit plan/doc updates if needed**

```bash
git add packages/anima-core/src docs/superpowers/plans/2026-04-08-anima-core-phase4-state-projection.md
git commit -m "docs(anima-core): add phase4 state projection plan"
```

---

## Follow-On Work

After this phase, the remaining major work is Phase 5:

1. `engine.rs` facade
2. host lifecycle: open/query/replay/verify/export
3. single-writer storage semantics
4. capsule-level canonical vs derived section policy
