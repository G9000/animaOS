# anima-core Phase 1: Integrity and Ops Surface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish `anima-core` as a trustworthy substrate by adding integrity reporting, dedup-on-insert, capsule verification/reporting, and FFI exposure for verify/stats.

**Architecture:** Add a dedicated `integrity.rs` module that scans frames, cards, graph, and capsule sections and returns structured reports instead of boolean pass/fail. Strengthen `FrameStore` and `CardStore` with exact dedup semantics so repeated inserts stop polluting the substrate. Extend capsule verification so callers can inspect section inventory and corruption causes, then expose the new ops surface through `ffi.rs` for current Python hosts.

**Tech Stack:** Rust, Cargo workspace, serde, blake3, zstd, PyO3

**Spec:** `docs/superpowers/specs/2026-04-08-anima-core-standalone-engine-design.md`

---

## Scope Check

This plan intentionally covers **Phase 1 only**. Temporal indexing, replay expansion, state projection, and the standalone engine facade are separate sub-projects and should each get their own plan after this phase lands.

---

## File Structure

### New files

| File | Responsibility |
|------|----------------|
| `packages/anima-core/src/integrity.rs` | Integrity issue types, report generation, repair hints, core stats, unit tests |

### Modified files

| File | Changes |
|------|---------|
| `packages/anima-core/src/lib.rs` | Export `integrity` module and crate-level types |
| `packages/anima-core/src/frame.rs` | Add exact dedup support and frame-store stats hooks |
| `packages/anima-core/src/cards.rs` | Add exact dedup support and version-chain validation hooks |
| `packages/anima-core/src/capsule.rs` | Add section-inspection / verification report APIs |
| `packages/anima-core/src/ffi.rs` | Expose verify/stats functions and Python-friendly result payloads |

---

## Task 1: Define integrity report and core stats types

**Files:**
- Create: `packages/anima-core/src/integrity.rs`
- Modify: `packages/anima-core/src/lib.rs`
- Test: `packages/anima-core/src/integrity.rs`

- [ ] **Step 1: Write the failing unit tests for integrity reporting**

Create `packages/anima-core/src/integrity.rs` with test-first coverage for:

```rust
#[test]
fn frame_checksum_mismatch_is_reported() {
    // create a tampered frame and assert one checksum issue is emitted
}

#[test]
fn duplicate_active_cards_are_reported() {
    // create cards with same entity/slot/value and assert duplicate issue is emitted
}

#[test]
fn core_stats_count_active_and_superseded_records() {
    // assert stats reflect active/superseded totals
}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
cargo test -p anima-core integrity::
```

Expected: fail because `integrity.rs` types and scanners do not exist yet.

- [ ] **Step 3: Implement report types and scanners**

In `packages/anima-core/src/integrity.rs`, add:

```rust
pub enum IntegritySeverity { Info, Warning, Error }

pub enum IntegrityIssueKind {
    FrameChecksumMismatch,
    DuplicateActiveFrame,
    DuplicateActiveCard,
    InvalidSupersession,
    OrphanedGraphEdge,
}

pub struct IntegrityIssue {
    pub kind: IntegrityIssueKind,
    pub severity: IntegritySeverity,
    pub message: String,
    pub record_ids: Vec<u64>,
    pub repair_hint: Option<String>,
}

pub struct CoreStats {
    pub frame_count: usize,
    pub active_frame_count: usize,
    pub superseded_frame_count: usize,
    pub card_count: usize,
    pub active_card_count: usize,
    pub graph_node_count: usize,
    pub graph_edge_count: usize,
}

pub struct IntegrityReport {
    pub ok: bool,
    pub issues: Vec<IntegrityIssue>,
    pub stats: CoreStats,
}
```

Also add scanners for frames and cards as standalone pure functions.

- [ ] **Step 4: Export the module in `lib.rs`**

Add:

```rust
pub mod integrity;
```

- [ ] **Step 5: Run the tests to verify they pass**

Run:

```bash
cargo test -p anima-core integrity::
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/anima-core/src/integrity.rs packages/anima-core/src/lib.rs
git commit -m "feat(anima-core): add integrity report and core stats types"
```

---

## Task 2: Add exact dedup to frames and cards

**Files:**
- Modify: `packages/anima-core/src/frame.rs`
- Modify: `packages/anima-core/src/cards.rs`
- Test: `packages/anima-core/src/frame.rs`
- Test: `packages/anima-core/src/cards.rs`

- [ ] **Step 1: Write the failing dedup tests**

Add tests to `frame.rs`:

```rust
#[test]
fn duplicate_active_frame_returns_existing_id() {
    // same user + kind + content + active status => same logical insert
}
```

Add tests to `cards.rs`:

```rust
#[test]
fn duplicate_active_card_returns_existing_id() {
    // same entity + slot + value + active version => dedup
}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
cargo test -p anima-core duplicate_active
```

Expected: FAIL because stores currently append duplicates.

- [ ] **Step 3: Implement frame dedup**

In `frame.rs`, add an exact dedup index keyed by active frame identity, for example:

```rust
type FrameDedupKey = (String, FrameKind, [u8; 32]);
```

Update `FrameStore::insert` so:

- active exact duplicates return the existing frame ID
- superseded/deleted frames do not block a new active insert
- the dedup index stays consistent when a frame is superseded or deleted

- [ ] **Step 4: Implement card dedup**

In `cards.rs`, add an exact dedup key for active cards, for example:

```rust
type CardDedupKey = (String, String, String, bool);
```

Update `CardStore::put` so repeated active cards with the same `(entity, slot, value)` return the existing ID instead of appending noise.

- [ ] **Step 5: Run the tests to verify they pass**

Run:

```bash
cargo test -p anima-core duplicate_active
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/anima-core/src/frame.rs packages/anima-core/src/cards.rs
git commit -m "feat(anima-core): dedup exact frame and card inserts"
```

---

## Task 3: Add capsule inspection and verification reporting

**Files:**
- Modify: `packages/anima-core/src/capsule.rs`
- Modify: `packages/anima-core/src/integrity.rs`
- Test: `packages/anima-core/src/capsule.rs`

- [ ] **Step 1: Write the failing verification tests**

Add tests in `capsule.rs` for:

```rust
#[test]
fn verify_capsule_reports_available_sections() {
    // assert cards/frames/metadata are listed
}

#[test]
fn verify_capsule_reports_footer_mismatch_as_error() {
    // tamper payload and assert explicit footer issue kind
}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
cargo test -p anima-core capsule::tests::verify
```

Expected: FAIL because there is no report-style API yet.

- [ ] **Step 3: Implement capsule inspection API**

In `capsule.rs`, add explicit report types and accessors, for example:

```rust
pub struct CapsuleSectionInfo {
    pub kind: SectionKind,
    pub offset: u32,
    pub size: u32,
    pub encrypted: bool,
}

pub struct CapsuleVerificationReport {
    pub ok: bool,
    pub version: u8,
    pub encrypted: bool,
    pub sections: Vec<CapsuleSectionInfo>,
    pub issues: Vec<String>,
}
```

Add a `verify` path that:

- validates footer checksum
- validates section checksums
- returns section inventory
- reports failure causes explicitly instead of only returning `Err`

- [ ] **Step 4: Bridge capsule verification into integrity helpers**

In `integrity.rs`, add a small adapter that converts capsule verification findings into `IntegrityIssue` values so hosts see one report vocabulary.

- [ ] **Step 5: Run the tests to verify they pass**

Run:

```bash
cargo test -p anima-core capsule::tests
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/anima-core/src/capsule.rs packages/anima-core/src/integrity.rs
git commit -m "feat(anima-core): add capsule verification reporting"
```

---

## Task 4: Expose verify and stats through Python FFI

**Files:**
- Modify: `packages/anima-core/src/ffi.rs`
- Test: `packages/anima-core/src/ffi.rs`

- [ ] **Step 1: Write the failing FFI-facing unit tests**

Add focused tests for conversion helpers in `ffi.rs`, for example:

```rust
#[test]
fn integrity_report_converts_to_python_json_shape() {
    // assert keys ok/issues/stats exist in the serialized mapping
}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
cargo test -p anima-core ffi::tests
```

Expected: FAIL because the conversion helpers and exported functions do not exist.

- [ ] **Step 3: Add verify/stats bindings**

Expose Rust-side functions such as:

```rust
#[pyfunction]
fn verify_frame_store(store: &PyFrameStore) -> PyResult<PyObject> { ... }

#[pyfunction]
fn frame_store_stats(store: &PyFrameStore) -> PyResult<PyObject> { ... }

#[pyfunction]
fn verify_capsule_bytes(data: Vec<u8>, password: Option<Vec<u8>>) -> PyResult<PyObject> { ... }
```

Use Python-friendly dict payloads first. Avoid designing a PyO3 class hierarchy unless needed later.

- [ ] **Step 4: Register the new functions in the module**

Add them to `anima_core_module(...)` with `m.add_function(...)`.

- [ ] **Step 5: Run Rust tests and Python import smoke**

Run:

```bash
cargo test -p anima-core
uv run python -c "import anima_core; print(hasattr(anima_core, 'verify_capsule_bytes'))"
```

Expected:

- Cargo tests pass
- Python prints `True`

- [ ] **Step 6: Commit**

```bash
git add packages/anima-core/src/ffi.rs
git commit -m "feat(anima-core): expose integrity and capsule verify through ffi"
```

---

## Task 5: Run final verification and record follow-on work

**Files:**
- Modify: `docs/superpowers/specs/2026-04-08-anima-core-standalone-engine-design.md` (only if execution changes design assumptions)
- Modify: `docs/superpowers/plans/2026-04-08-anima-core-phase1-integrity-and-ops.md` (mark any plan corrections discovered during execution)

- [ ] **Step 1: Run the full crate test suite**

Run:

```bash
cargo test -p anima-core
```

Expected: PASS.

- [ ] **Step 2: Run targeted Python smoke for bound APIs**

Run:

```bash
uv run python -c "import anima_core; print(sorted(name for name in dir(anima_core) if 'verify' in name or 'stats' in name))"
```

Expected: output includes the new verify/stats bindings.

- [ ] **Step 3: Confirm no unintended public-surface drift**

Check:

```bash
git diff -- packages/anima-core/src
```

Verify the phase only adds:

- integrity module
- dedup support
- capsule reporting
- ffi ops exposure

It should **not** introduce temporal indexing, session replay expansion, or engine facade code.

- [ ] **Step 4: Commit final cleanup**

```bash
git add packages/anima-core/src docs/superpowers/specs/2026-04-08-anima-core-standalone-engine-design.md docs/superpowers/plans/2026-04-08-anima-core-phase1-integrity-and-ops.md
git commit -m "docs(anima-core): capture phase1 integrity and ops plan"
```

---

## Follow-On Plans

After this phase is complete, write separate plans for:

1. Phase 2: temporal index and `as_of` queries
2. Phase 3: replay engine and replay summaries
3. Phase 4: `EntityState` projection over cards + graph
4. Phase 5: unified standalone engine facade
