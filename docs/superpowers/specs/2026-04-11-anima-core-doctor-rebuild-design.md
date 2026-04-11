# anima-core Doctor and Rebuild Design

**Date:** 2026-04-11
**Status:** Draft for review
**Scope:** First explicit `doctor` / rebuild slice for path-backed `anima-core` engines

---

## Goal

Add an explicit `doctor` surface to `anima-core` so a path-backed engine can:

- diagnose storage and integrity issues beyond basic open-time validation
- distinguish canonical from derived damage
- report which problems are repairable
- rebuild repairable derived state from canonical frames
- publish repairs through the existing committed-generation model

This is an operational layer over the engine and storage work that already exists in `engine.rs`, `path_engine.rs`, and `integrity.rs`.

---

## Why Now

The core now has:

- a top-level engine facade
- path-backed single-writer storage
- explicit canonical-vs-derived storage rules
- integrity and stats reporting
- deterministic extraction logic for cards and graph relations

What it does not yet have is a safe operational answer to:

- "this engine path is damaged, what is wrong?"
- "can I still recover the Core?"
- "which parts can be rebuilt from canonical data?"
- "how do I repair derived state without mutating the committed snapshot in place?"

That is the gap this slice closes.

---

## Non-Goals

This slice does **not** include:

- auto-repair during `open_path(...)`
- in-place mutation of the currently committed generation
- canonical frame repair
- replay/session reconstruction
- LLM-assisted repair
- capsule byte mutation or in-place capsule repair
- garbage collection of older generations
- background repair or self-healing daemons

This is an explicit operator/tooling surface, not hidden recovery behavior.

---

## Design Principles

### 1. `doctor` is explicit

Normal path opens stay strict.

- `open_path(...)` continues to fail on corrupt named derived files
- `doctor(...)` is the explicit degraded-entry path
- repair only happens when the caller asks for repair mode

This keeps normal reads honest and avoids silent state changes during load.

### 2. Frames remain canonical

The first repair boundary is simple:

- `frames` are canonical
- `cards`, `graph`, and committed metadata are derived

Missing or corrupt canonical frames are not repairable in this slice. They are reported and stop repair.

### 3. Rebuilds are deterministic

Repair must be driven only by logic already owned by the Rust core.

- cards rebuild from deterministic extraction rules in `enrich.rs`
- graph rebuild from deterministic triplet extraction in `triplet.rs`
- metadata rebuild from the committed snapshot contract in `path_engine.rs`

No fuzzy heuristics and no LLM involvement.

### 4. Repair publishes a new generation

Repairs must not mutate the currently committed snapshot in place.

Instead:

- load the committed canonical frames
- rebuild repairable derived state in memory
- write a fresh derived snapshot
- publish it through the existing generation and metadata-last commit path

This preserves the single-writer and committed-generation semantics already established.

---

## Repair Boundary

### Repairable

- missing cards file for the committed generation
- corrupt cards file for the committed generation
- missing graph file for the committed generation
- corrupt graph file for the committed generation
- missing or stale metadata that can be rewritten from known committed files

### Not Repairable in this slice

- missing committed frames file
- corrupt committed frames file
- malformed canonical frame payload
- repairs that would require inventing canonical facts from previously derived state

The first slice deliberately prefers explicit failure over pretending the Core can recover more than it actually can.

---

## Rebuild Semantics

## Cards Rebuild

Cards rebuild from canonical frames using the rules engine already present in `enrich.rs`.

Process:

1. iterate active frames from `FrameStore`
2. run deterministic extraction over each frame's content
3. map extractions into `MemoryCard` values
4. insert through `CardStore::put(...)`

Using `CardStore::put(...)` is important because it preserves the existing:

- cardinality rules
- active/superseded behavior
- exact dedup behavior

This keeps repaired cards aligned with the rest of the engine model.

## Graph Rebuild

Graph rebuild from canonical frames uses triplet extraction from `triplet.rs`.

Process:

1. iterate active frames from `FrameStore`
2. extract deterministic subject/predicate/object triplets
3. map triplet subject/object types to `EntityKind`
4. insert through `KnowledgeGraph::upsert_node(...)` and `upsert_edge(...)`

This preserves current graph dedup and mention-count behavior.

## Metadata Rebuild

The first metadata repair scope is narrow:

- rewrite committed snapshot metadata so it points at the newly published generation
- preserve the existing filename and generation conventions from `path_engine.rs`

This slice does **not** add a new semantic metadata layer.

---

## API Shape

The first API should be explicit and path-oriented.

Proposed Rust surface:

```rust
pub enum DoctorMode {
    ReportOnly,
    Repair,
}

pub fn doctor_engine_path(
    path: impl AsRef<Path>,
    mode: DoctorMode,
) -> crate::Result<DoctorReport>;
```

This can live in `path_engine.rs` or a dedicated module such as `doctor.rs`, but it should remain storage-aware rather than pretending to be a pure in-memory engine method.

### Mode Behavior

`ReportOnly`:

- does not require the writer lock
- loads committed canonical frames
- tolerates missing or corrupt derived files
- analyzes repairability
- returns a report without writing

`Repair`:

- requires writer lock ownership
- loads committed canonical frames
- rebuilds repairable derived state in memory
- publishes a fresh committed generation
- returns before/after stats and performed actions

---

## Report Model

The first slice needs structured outputs, not just `bool`.

### `DoctorScope`

```rust
pub enum DoctorScope {
    Canonical,
    Derived,
    Metadata,
    Lock,
    Storage,
}
```

### `DoctorIssue`

Each doctor issue should carry:

- issue kind
- severity
- scope
- message
- `repairable: bool`
- optional repair hint

Doctor issues may wrap or map existing integrity findings, but they also need storage-aware findings that integrity scans alone do not currently represent.

### `RepairAction`

First-slice actions:

- `rebuild_cards`
- `rebuild_graph`
- `rewrite_metadata`
- `publish_generation`

### `DoctorReport`

Suggested fields:

- `ok`
- `repair_attempted`
- `repair_succeeded`
- `issues`
- `planned_actions`
- `performed_actions`
- `stats_before`
- `stats_after`
- `published_generation`

This gives hosts enough information to show both diagnosis and repair outcomes.

---

## Load and Validation Behavior

The existing open-path contract stays strict.

`doctor` introduces a separate tolerant load path:

- frames must still load successfully
- missing derived files become repairable findings
- corrupt derived files become repairable findings
- metadata problems become storage findings when enough canonical information still exists to continue

This means:

- `open_path(...)` stays safe and unsurprising
- `doctor(...)` becomes the explicit path for degraded inspection and repair

---

## Lock and Publication Semantics

`ReportOnly` should work without the writer lock.

`Repair` must follow the path-backed storage rules already established:

- acquire exclusive writer lock
- never mutate the currently committed generation in place
- write repair output as a new generation
- publish via metadata-last snapshot replacement
- retain older generations after repair

If repair cannot acquire the writer lock, the result should be an explicit lock-conflict error rather than a degraded report pretending repair happened.

---

## Relationship to Capsules

This first slice is intentionally engine-path only.

Capsules continue to support:

- verification
- import/export
- diagnosis of corruption

But capsule repair is deferred because it has different semantics:

- engine doctor repairs a live working store
- capsule doctor would need to produce a new repaired artifact, not patch the exported artifact in place

That can be added later without weakening this first API.

---

## Testing Strategy

Required first-slice tests:

1. Missing cards file in committed generation:
   - `ReportOnly` marks issue as repairable
   - `Repair` publishes a new generation with rebuilt cards

2. Corrupt graph file in committed generation:
   - `ReportOnly` marks issue as repairable
   - `Repair` publishes a new generation with rebuilt graph

3. Missing committed frames file:
   - report shows unrecoverable canonical failure
   - repair does not proceed

4. `ReportOnly` does not require writer lock:
   - report works while another writer handle exists

5. `Repair` respects lock semantics:
   - repair fails with explicit lock conflict when another writer owns the path

6. Successful repair uses a new committed generation:
   - old generation remains on disk
   - metadata points at the new generation

7. Rebuilt cards and graph are deterministic:
   - repeated repair against the same frames yields equivalent derived state

---

## Risks

### Risk: Over-promising rebuild coverage

If the first slice claims it can repair all derived state, but only cards and graph are truly deterministic from frames today, the API becomes dishonest.

Mitigation:

- scope repair to cards, graph, and metadata only
- leave replay/index rebuild for later slices

### Risk: Silent drift from current extraction logic

If rebuild uses different logic than the runtime or existing Rust helpers, repaired state may be inconsistent with future writes.

Mitigation:

- reuse `enrich.rs`, `triplet.rs`, `CardStore::put(...)`, and `KnowledgeGraph` upsert semantics directly

### Risk: `doctor` weakens `open_path(...)`

If tolerant load paths leak into normal engine opens, corruption could become invisible.

Mitigation:

- keep `doctor` on a separate explicit path
- leave `open_path(...)` strict

---

## Recommended Next Step

Write a scoped implementation plan for the first doctor/rebuild slice covering:

- doctor/report types
- tolerant derived-file analysis for engine paths
- deterministic frames -> cards rebuild
- deterministic frames -> graph rebuild
- repair publication through a new committed generation
- Rust tests only

Python FFI should follow only after the Rust behavior is proven.
