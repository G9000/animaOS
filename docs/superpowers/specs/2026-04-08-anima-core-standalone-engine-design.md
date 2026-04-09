# anima-core Standalone Engine Design

**Date:** 2026-04-08
**Status:** Approved for planning
**Scope:** Long-term `anima-core` evolution into a standalone storage and query engine for ANIMA

---

## Goal

Turn `packages/anima-core` from a collection of high-performance Rust helpers into the canonical memory substrate for ANIMA: verifiable, queryable, replayable, portable, and host-independent.

This is not a plan to copy Memvid's storage metaphor. It is a plan to adopt the parts Memvid does well at the engine layer:

- operational discipline
- integrated retrieval primitives
- temporal and replay ergonomics
- integrity and repair tooling
- coherent file-backed engine boundaries

ANIMA keeps its own identity model, cards, graph, and capsule format.

---

## Current State

`anima-core` already has meaningful building blocks:

- `frame.rs`: canonical storage atom
- `cards.rs`: versioned structured memory
- `graph.rs`: in-memory relational memory
- `search.rs` and `adaptive.rs`: hybrid retrieval and cutoff logic
- `temporal.rs`: temporal parsing and slice filtering
- `replay.rs`: action recording
- `capsule.rs`: portable encrypted `.anima` capsule
- `chunker.rs`, `enrich.rs`, `triplet.rs`: deterministic ingest helpers
- `ffi.rs`: Python bindings for selected primitives

What it does **not** have yet is a coherent engine surface. The crate exposes pieces, but not a stable object model for:

- open/create/use/verify a memory artifact
- report health and corruption
- replay or inspect sessions at the engine level
- query by time as a first-class indexed primitive
- ask for current entity state across cards + graph + frames
- define single-writer storage semantics

That is the real gap.

---

## Design Principles

### 1. Keep the `.anima` capsule, do not inherit the video container idea

Memvid's operational ideas are useful. Its container metaphor is not needed here.

For ANIMA, the canonical portable artifact remains the capsule implemented in `capsule.rs`. The engine should treat the capsule as the long-term interchange format and the live in-memory/on-disk engine state as a host-local working form.

### 2. Operations before product sugar

The first work should make the engine trustworthy:

- verify
- doctor
- stats
- dedup
- section inspection
- corruption diagnostics

This is higher leverage than adding more search features on top of a loose substrate.

### 3. Frames are canonical, everything else is a view or index

`Frame` remains the canonical storage unit.

- cards are structured projections over frames
- graph nodes/edges are relational projections over frames
- timeline index is an ordering/index over frames
- replay sessions reference frames, not separate memory objects

This keeps the engine internally coherent and makes rebuilds deterministic.

### 4. Derived structures must be rebuildable

Any lexical, temporal, replay, graph, or state index that can be reconstructed from canonical data should be marked derived and rebuildable.

The capsule may persist derived sections for speed, but verification must always distinguish:

- canonical state
- derived state
- corrupt derived state that can be rebuilt

### 5. One engine surface, many hosts

Long term, Python should be just one host over `anima-core`.

The crate should eventually expose a top-level engine facade that owns:

- frame store
- card store
- graph
- derived indexes
- replay sessions
- integrity report generation

This is the equivalent of Memvid's memory-file object, but aligned with ANIMA's model.

---

## Proposed Module Map

### New: `integrity.rs`

Responsibility:

- scan frames/cards/graph/capsule sections
- report checksum failures, duplicate records, orphaned version links, invalid supersession chains
- distinguish repairable vs non-repairable findings
- compute engine-level stats

Core outputs:

- `IntegrityReport`
- `IntegrityIssue`
- `RepairAction`
- `CoreStats`

### New: `engine.rs`

Responsibility:

- top-level engine facade for long-term standalone use
- own stores and indexes
- coordinate ingest, query, verify, replay, and export

This should not be built in Phase 1. It is the convergence point after the lower layers stabilize.

### New: `projection.rs`

Responsibility:

- build `EntityState` views from cards + graph + frames
- answer queries like:
  - current state for entity
  - version history for slot
  - connected entities
  - supporting frame IDs

This is the structured-memory equivalent of Memvid's `state(entity)` surface.

### Expanded: `temporal.rs`

Add:

- temporal index structure
- `as_of` queries
- `since/until` range search over indexed frames
- replay/timeline helpers that do not require full scans

### Expanded: `replay.rs`

Add:

- named session registry
- checkpoint labeling
- replay summary and diff APIs
- deterministic export/import of replay sessions

### Expanded: `frame.rs`

Add:

- exact dedup using checksum + content identity
- optional stable insertion semantics
- frame-level integrity helpers for orphaned supersession chains and invalid references

### Expanded: `cards.rs`

Add:

- exact dedup for repeated cards
- stronger version-chain inspection
- slot-state projection helpers
- canonical current-state view for an entity

### Expanded: `capsule.rs`

Add:

- section manifest inspection
- verification API that explains failures, not just pass/fail
- migration hooks and rebuild hints
- explicit handling of canonical vs derived sections

### Expanded: `ffi.rs`

Expose:

- verify / stats
- temporal query helpers
- session replay helpers
- state projection helpers

The current FFI surface exposes primitives but not an engine-like operational surface.

---

## Phased Roadmap

## Phase 1: Integrity and Operations Surface

Outcome:

- `anima-core` can explain what it contains and whether it is healthy
- dedup becomes a core ingest behavior
- capsule verification is explicit and inspectable
- Python can call verify/stats through FFI

Primary modules:

- `integrity.rs`
- `frame.rs`
- `cards.rs`
- `capsule.rs`
- `ffi.rs`

## Phase 2: Temporal Retrieval as a First-Class Primitive

Outcome:

- engine supports indexed timeline and `as_of` queries
- timeline operations stop being full-scan utilities

Primary modules:

- `temporal.rs`
- `frame.rs`
- later `engine.rs`

## Phase 3: Replay as a Queryable Engine Feature

Outcome:

- sessions can be recorded, summarized, replayed, and compared
- debugging and audit stop living only in host logic

Primary modules:

- `replay.rs`
- `ffi.rs`

## Phase 4: Structured State Projection

Outcome:

- cards + graph + frames produce a stable `EntityState`
- ANIMA can ask the core for current structured knowledge instead of rebuilding that logic in Python

Primary modules:

- `projection.rs`
- `cards.rs`
- `graph.rs`
- `ffi.rs`

## Phase 5: Standalone Engine Facade

Outcome:

- one coherent engine object
- explicit host lifecycle: open, query, replay, verify, export
- Python becomes a client, not the system of record for orchestration

Primary modules:

- `engine.rs`
- `capsule.rs`
- `ffi.rs`

---

## What To Borrow From Memvid

Borrow directly:

- verify / doctor / stats style operations
- temporal query ergonomics
- session replay ergonomics
- dedup as ingest discipline
- integrated engine surface that feels like one system

Borrow selectively:

- packaging of derived indexes inside the portable artifact
- repair tooling and diagnostic UX

Do **not** borrow:

- the video-file storage metaphor
- cloud/dashboard/tickets product layer
- flattening ANIMA's memory model into generic documents only

---

## Risks

### Risk 1: Premature facade

If `engine.rs` is introduced before integrity, temporal, replay, and state layers stabilize, it becomes a thin wrapper over unstable primitives and must be rewritten.

Mitigation:

- delay `engine.rs` until Phase 5

### Risk 2: Canonical vs derived confusion

If indexes and derived state are not clearly labeled, verification and portability become ambiguous.

Mitigation:

- every persisted section must declare whether it is canonical or rebuildable

### Risk 3: FFI lock-in

If Python integration dictates the Rust API too early, the engine will remain helper-shaped instead of host-independent.

Mitigation:

- design Rust types first, bind them second

### Risk 4: Over-scoping Phase 1

Trying to build verify, dedup, temporal indexing, replay, and engine facade at once will produce a broad but shallow substrate.

Mitigation:

- Phase 1 only covers integrity and ops

---

## Testing Strategy

### Rust unit coverage

Every new module should carry native Rust tests for:

- valid path
- corruption path
- duplicate path
- edge-case path

### Artifact-level tests

Capsule tests should cover:

- tampered section data
- tampered footer
- missing sections
- valid encrypted round-trip
- corrupted derived section with canonical section intact

### FFI smoke tests

The Python-facing bindings should be tested only after Rust behavior is already proven.

### Phase gates

Each phase should end with:

- `cargo test -p anima-core`
- targeted FFI smoke if bindings changed
- explicit note on whether the phase introduces new canonical data or only derived/index behavior

---

## Recommended Execution Order

1. Phase 1: integrity + dedup + stats
2. Phase 2: temporal index + `as_of`
3. Phase 3: replay engine
4. Phase 4: state projection
5. Phase 5: unified engine facade

This order preserves the correct dependency direction:

- trustworthy substrate first
- query primitives second
- semantic projections third
- host abstraction last

---

## Immediate Next Step

Write and execute a scoped implementation plan for Phase 1 only:

- new `integrity.rs`
- dedup hooks in `frame.rs` and `cards.rs`
- capsule verification/reporting improvements
- FFI exposure for verify/stats

Follow-on phases should each get their own execution plan after Phase 1 lands and stabilizes.
