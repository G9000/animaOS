# anima-core Single-Writer Storage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the first Rust-only, path-based single-writer storage lifecycle for `anima-core`, with exclusive writer locking, committed generation snapshots, and directory-backed load/flush behavior.

**Architecture:** Keep `AnimaEngine` as the in-memory facade and add a dedicated path-storage module for file layout, snapshot metadata, OS-backed writer locks, and path-backed handles. Use generation-scoped data files plus `metadata.json` as the committed snapshot pointer so read-only opens can safely observe the last committed generation while a writer exists.

**Tech Stack:** Rust, Cargo workspace, serde, serde_json, tempfile, cross-platform file locking via `fs4`

---

## Scope Check

This plan intentionally covers the first Rust-only single-writer storage slice.

It does **not** include:

- Python bindings for path-backed storage
- stale-lock recovery
- heartbeats or leases
- compaction / cleanup of old generations
- `doctor` / rebuild behavior
- background sync or watchers

Those stay in later storage hardening.

---

## File Structure

### New files

| File | Responsibility |
|------|----------------|
| `packages/anima-core/src/path_engine.rs` | Path-backed handles, committed snapshot metadata, generation file naming, writer lock guard, load/flush logic, Rust tests |

### Modified files

| File | Changes |
|------|---------|
| `packages/anima-core/Cargo.toml` | Add cross-platform file locking dependency |
| `packages/anima-core/src/engine.rs` | Add thin associated functions delegating to path storage |
| `packages/anima-core/src/lib.rs` | Export `path_engine` and add explicit storage-related error variants if needed |

---

## Task 1: Add path-storage scaffolding and explicit storage errors

**Files:**
- Create: `packages/anima-core/src/path_engine.rs`
- Modify: `packages/anima-core/Cargo.toml`
- Modify: `packages/anima-core/src/lib.rs`
- Test: `packages/anima-core/src/path_engine.rs`

- [ ] **Step 1: Write the failing scaffolding tests**

Add focused tests in `packages/anima-core/src/path_engine.rs` for:

```rust
#[test]
fn initialize_empty_engine_dir_seeds_generation_zero_and_committed_metadata() {
    // internal directory initializer creates generation 0 metadata and frames
}

#[test]
fn open_path_fails_when_root_is_missing() {
    // opening a non-existent engine path is an explicit storage error
}

#[test]
fn open_path_fails_when_metadata_is_missing() {
    // committed snapshot pointer is required for a valid engine directory
}

#[test]
fn open_path_fails_when_committed_frames_generation_is_missing() {
    // metadata points at a generation but canonical frames file is absent
}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
cargo test -p anima-core path_engine::tests::initialize_empty_engine_dir
```

Expected: FAIL because `path_engine.rs` and the storage lifecycle do not exist yet.

- [ ] **Step 3: Add the minimal scaffolding**

In `packages/anima-core/Cargo.toml`, add:

```toml
fs4 = "0.8"
```

In `packages/anima-core/src/lib.rs`, add:

```rust
pub mod path_engine;

#[error("storage error: {0}")]
Storage(String),

#[error("lock conflict: {0}")]
LockConflict(String),
```

In `packages/anima-core/src/path_engine.rs`, add:

```rust
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EngineOpenMode {
    ReadOnly,
    ReadWrite,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
struct CommittedSnapshot {
    generation: u64,
    frames_file: String,
    cards_file: Option<String>,
    graph_file: Option<String>,
}

fn frames_file_name(generation: u64) -> String { ... }
fn cards_file_name(generation: u64) -> String { ... }
fn graph_file_name(generation: u64) -> String { ... }
```

Also add an internal initializer such as `initialize_empty_engine_dir(...)` that:

- creates the directory when missing
- accepts an existing empty directory and seeds it as a new engine path
- fails if the path already looks like an engine directory; callers must use `open_path(...)`
- writes generation `0` frames file with an empty serialized `FrameStore`
- writes `metadata.json` pointing at generation `0`
- fails on non-empty invalid directories
- does **not** expose a public writable handle yet

Also implement explicit early-open failures for:

- missing engine root path
- missing `metadata.json`

- [ ] **Step 4: Run the tests to verify they pass**

Run:

```bash
cargo test -p anima-core path_engine::tests::initialize_empty_engine_dir
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/anima-core/Cargo.toml packages/anima-core/src/lib.rs packages/anima-core/src/path_engine.rs
git commit -m "feat(anima-core): add path storage scaffolding"
```

---

## Task 2: Implement exclusive writer locking and open modes

**Files:**
- Modify: `packages/anima-core/src/path_engine.rs`
- Test: `packages/anima-core/src/path_engine.rs`

- [ ] **Step 1: Write the failing lock-behavior tests**

Add tests for:

```rust
#[test]
fn create_path_holds_lock_until_close() {
    // create_path returns a writer handle that blocks a second writer
}

#[test]
fn second_writable_open_fails_with_lock_conflict() {
    // first writer holds exclusive lock on .lock
}

#[test]
fn read_only_open_succeeds_while_writer_exists() {
    // reader can still load committed snapshot
}

#[test]
fn dropping_writer_releases_lock_for_next_writer() {
    // second writer can open after first handle closes
}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
cargo test -p anima-core path_engine::tests::lock -- --nocapture
```

Expected: FAIL because no lock guard or open mode handling exists yet.

- [ ] **Step 3: Implement path-backed handles and lock guard**

In `packages/anima-core/src/path_engine.rs`, add:

```rust
pub struct ReadOnlyPathEngineHandle {
    engine: AnimaEngine,
    root: PathBuf,
}

pub struct ReadWritePathEngineHandle {
    engine: AnimaEngine,
    root: PathBuf,
    lock: WriterLockGuard,
}

pub enum EnginePathHandle {
    ReadOnly(ReadOnlyPathEngineHandle),
    ReadWrite(ReadWritePathEngineHandle),
}

struct WriterLockGuard {
    file: std::fs::File,
}
```

Implement:

- `create_path(path) -> ReadWritePathEngineHandle`
- non-blocking exclusive OS file lock on `<engine-dir>/.lock` using `fs4`
- `open_path(path, EngineOpenMode::ReadOnly)`
- `open_path(path, EngineOpenMode::ReadWrite)`
- `EnginePathHandle::close(self)` delegating to the concrete handle variants
- lock-conflict errors mapped to `crate::Error::LockConflict`
- `close(self)` as an explicit lifecycle endpoint on both handle types, with drop also releasing the lock

Rules:

- `create_path(...)` must acquire the writer lock before returning the writable handle
- read-only open never takes the writer lock
- writable open must fail fast if lock acquisition fails
- do not expose `engine_mut()` or `flush()` on the read-only handle

- [ ] **Step 4: Run the tests to verify they pass**

Run:

```bash
cargo test -p anima-core path_engine::tests::lock -- --nocapture
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/anima-core/src/path_engine.rs
git commit -m "feat(anima-core): add single-writer lock handling"
```

---

## Task 3: Implement committed snapshot load/flush behavior

**Files:**
- Modify: `packages/anima-core/src/engine.rs`
- Modify: `packages/anima-core/src/path_engine.rs`
- Test: `packages/anima-core/src/path_engine.rs`
- Test: `packages/anima-core/src/engine.rs`

- [ ] **Step 1: Write the failing mutation-surface and snapshot tests**

Add tests for:

```rust
#[test]
fn writable_handle_can_get_mutable_engine_access() {
    // path-backed writer can mutate engine state before flush
}

#[test]
fn open_path_fails_when_metadata_is_malformed() {
    // corrupt committed snapshot pointer is an explicit failure
}

#[test]
fn flush_persists_new_generation_and_reload_roundtrips_state() {
    // writer mutates engine, flushes, then read-only open sees committed generation
}

#[test]
fn missing_derived_files_load_as_empty_state() {
    // cards/graph payloads can be absent for a committed generation
}

#[test]
fn malformed_derived_file_fails_instead_of_loading_as_missing() {
    // corrupt cards/graph file is explicit failure
}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
cargo test -p anima-core engine::tests::writable_handle_can_get_mutable_engine_access
cargo test -p anima-core path_engine::tests::snapshot
```

Expected: FAIL because the mutable engine surface and snapshot semantics are not implemented yet.

- [ ] **Step 3: Expose the minimal mutable engine surface**

In `packages/anima-core/src/engine.rs`, add crate-visible accessors needed by the path layer:

```rust
pub(crate) fn frames_mut(&mut self) -> &mut FrameStore { ... }
pub(crate) fn cards_mut(&mut self) -> &mut CardStore { ... }
pub(crate) fn graph_mut(&mut self) -> &mut KnowledgeGraph { ... }
```

Keep this surface `pub(crate)` only. Do not add public mutation APIs for hosts in this slice.

- [ ] **Step 4: Implement snapshot metadata and flush**

Extend `packages/anima-core/src/path_engine.rs` with:

```rust
impl ReadWritePathEngineHandle {
    pub fn engine(&self) -> &AnimaEngine { ... }
    pub fn engine_mut(&mut self) -> &mut AnimaEngine { ... }
    pub fn flush(&mut self) -> crate::Result<()> { ... }
}

impl ReadOnlyPathEngineHandle {
    pub fn engine(&self) -> &AnimaEngine { ... }
}
```

Implement the first-slice commit model:

- load committed snapshot by reading `metadata.json`
- `flush()` writes a fresh generation of files beside the current generation
- `metadata.json` is replaced last
- old generation files are retained
- canonical frames file must exist for the committed generation
- missing derived files load as empty stores
- malformed derived files stay fatal

Use the current engine serialization surfaces:

- `FrameStore::serialize` / `deserialize`
- `CardStore::serialize` / `deserialize`
- `KnowledgeGraph::serialize` / `deserialize`

- [ ] **Step 5: Run the tests to verify they pass**

Run:

```bash
cargo test -p anima-core engine::tests::writable_handle_can_get_mutable_engine_access
cargo test -p anima-core path_engine::tests::snapshot
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/anima-core/src/engine.rs packages/anima-core/src/path_engine.rs
git commit -m "feat(anima-core): add committed snapshot load and flush"
```

---

## Task 4: Wire path storage into the engine facade

**Files:**
- Modify: `packages/anima-core/src/engine.rs`
- Modify: `packages/anima-core/src/path_engine.rs`
- Test: `packages/anima-core/src/engine.rs`

- [ ] **Step 1: Write the failing engine delegation tests**

Add focused tests in `packages/anima-core/src/engine.rs` for:

```rust
#[test]
fn engine_create_path_returns_writable_handle_with_empty_engine() {
    // associated constructor delegates to path storage
}

#[test]
fn engine_open_path_read_only_loads_committed_snapshot() {
    // thin engine-facing open delegates without duplicating storage logic
}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
cargo test -p anima-core engine::tests::engine_open_path
```

Expected: FAIL because `AnimaEngine` does not expose path-backed lifecycle yet.

- [ ] **Step 3: Add thin associated functions only**

In `packages/anima-core/src/engine.rs`, add thin delegation:

```rust
pub fn create_path(path: impl AsRef<std::path::Path>) -> crate::Result<ReadWritePathEngineHandle> {
    crate::path_engine::create_path(path)
}

pub fn open_path(
    path: impl AsRef<std::path::Path>,
    mode: EngineOpenMode,
) -> crate::Result<EnginePathHandle> {
    crate::path_engine::open_path(path, mode)
}
```

Do not duplicate storage logic in `engine.rs`.

- [ ] **Step 4: Run the tests to verify they pass**

Run:

```bash
cargo test -p anima-core engine::tests::engine_open_path
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/anima-core/src/engine.rs packages/anima-core/src/path_engine.rs
git commit -m "feat(anima-core): expose path-backed engine lifecycle"
```

---

## Task 5: Final verification for the single-writer Rust slice

**Files:**
- Modify: `docs/superpowers/specs/2026-04-09-anima-core-single-writer-storage-design.md` only if implementation changes the snapshot contract

- [ ] **Step 1: Run the focused Rust verification**

Run:

```bash
cargo test -p anima-core path_engine::tests
cargo test -p anima-core engine::tests
```

Expected: PASS.

- [ ] **Step 2: Run the full crate verification**

Run:

```bash
cargo test -p anima-core --features "temporal replay"
cargo test -p anima-core --features python
```

Expected: PASS.

If the local shell does not already expose the uv-managed Python runtime needed by PyO3, prepend that interpreter directory first using the same shell setup used in earlier `anima-core` Python-feature verification.

- [ ] **Step 3: Confirm scope stayed Rust-only**

Check:

```bash
git diff --name-only -- packages/anima-core
```

Verify the diff only includes:

- `packages/anima-core/Cargo.toml`
- `packages/anima-core/src/path_engine.rs`
- `packages/anima-core/src/engine.rs`
- `packages/anima-core/src/lib.rs`

It should **not** include:

- `packages/anima-core/src/ffi.rs`
- Python-facing storage APIs
- cleanup / compaction logic
- stale-lock recovery

- [ ] **Step 4: Commit plan/doc updates if needed**

```bash
git add packages/anima-core docs/superpowers/plans/2026-04-09-anima-core-single-writer-storage.md
git commit -m "docs(anima-core): add single-writer storage plan"
```

---

## Follow-On Work

After this slice, the next storage-hardening steps are:

1. Python exposure for path-backed lifecycle
2. explicit generation cleanup / compaction
3. degraded-open or rebuild behavior for corrupt derived state
4. stale-lock recovery or lease semantics if needed
