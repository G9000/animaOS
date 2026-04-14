# anima-core Rust-Owned Retrieval Design

**Date:** 2026-04-15
**Status:** Draft for review
**Scope:** Establish `anima-core` as the owner of persisted, rebuildable retrieval indexes for the Python server, starting with memory and transcript retrieval and documenting the later migration to a fuller Rust-owned recall plane

---

## Goal

Improve the two performance paths that matter most right now:

- chat-turn retrieval latency
- post-turn ingestion and index-update throughput

The immediate design goal is not to move all memory runtime logic into Rust at once. The immediate goal is to give `anima-core` ownership of the retrieval subsystem that most directly affects user-visible latency, while keeping canonical state safe and rebuildable.

Phase 1 therefore makes `anima-core` the owner of persisted, rebuildable retrieval indexes for:

- memory items
- memory episodes
- self-model and related recall blocks
- archived transcripts

Canonical truth remains in SQLite and transcript artifacts for this first slice.

This spec also records the later phases so the full Rust-core direction is explicit and does not need to be rediscovered in a future conversation.

---

## Why This Slice

The current Python server already uses `anima-core` for selected helper functions, but it does not treat Rust as the owner of retrieval or ingestion state. That means:

- Rust helps with hot algorithms, but not with the retrieval boundary itself
- Python still stitches together recall surfaces and pays most of the orchestration cost
- there is no persisted Rust-owned index layer that can warm quickly and serve repeat queries efficiently

If the target is materially better retrieval latency and ingestion throughput, the next high-leverage move is not "more helpers." It is a Rust-owned retrieval plane with explicit disk persistence, versioning, rebuild behavior, and query APIs.

---

## Problem Statement

The current repo state creates four practical constraints:

### 1. Retrieval remains Python-owned

The server still owns most recall orchestration, ranking glue, and state lookup even when Rust is available.

### 2. There is no persisted Rust retrieval layer

Without a durable Rust-owned index, the system cannot take full advantage of Rust for warm-start recall or for consistent incremental update behavior.

### 3. Full migration is attractive but too risky for one step

Moving memory, transcripts, graph, ingestion, and canonical storage into Rust in one phase would create too many consistency and invalidation problems at once.

### 4. Long-term intent is easy to forget

If Phase 1 is scoped narrowly without documenting the later phases, the repo will drift toward a local optimum and the larger Rust-core direction will need to be re-argued later.

---

## Non-Goals

Phase 1 does **not** include:

- replacing SQLite as canonical storage
- replacing SQLAlchemy models or transaction ownership
- moving the entire graph runtime into Rust
- rewriting the agent loop around a Rust host API
- changing the wire API exposed by the Python server
- removing Python fallbacks everywhere
- turning `anima-core` into the only executable host in this slice

This is a retrieval-plane ownership change, not a full runtime rewrite.

---

## User Intent Captured

This design is optimized for:

1. performance first
2. both chat retrieval and post-turn ingestion matter
3. Phase 1 can be bounded, but the full Rust-core migration path must be documented in the same spec

---

## Alternatives Considered

### Option A: Extend the current helper-only model

Keep Python as the owner of retrieval and move more hot functions into Rust.

Pros:

- easiest to ship
- low migration risk

Cons:

- does not create a real ownership boundary
- does not give Rust durable retrieval state
- improves hot loops without improving architecture

### Option B: Full Rust-owned recall plane immediately

Move memory, transcripts, graph retrieval, and more ingestion semantics into Rust in one phase.

Pros:

- highest ceiling
- strongest architectural move

Cons:

- too many consistency problems at once
- graph invalidation and claim correction semantics become the critical path
- easy to spend the entire phase on correctness plumbing instead of performance wins

### Option C: Recommended

Make `anima-core` the owner of a persisted, rebuildable retrieval subsystem for memory and transcripts first, while documenting the later migration to graph retrieval and deeper ingestion ownership.

Pros:

- establishes a real Rust ownership boundary
- targets the two most important latency paths
- keeps canonical truth safe in SQLite and transcript artifacts
- creates a clean foundation for later graph migration

Cons:

- graph retrieval remains Python-owned in Phase 1
- there is still a split between canonical writes and derived index ownership

---

## Recommendation

Proceed with Option C.

Phase 1 should create a Rust-owned retrieval plane for memory and transcripts. The system should treat Rust indexes as authoritative for retrieval execution but not as the only copy of data. SQLite and transcript artifacts remain canonical and are always sufficient to rebuild the Rust indexes.

This gives the system a meaningful architectural step forward without forcing a premature all-at-once rewrite.

---

## Phase 1 Boundary

Phase 1 makes `anima-core` responsible for:

- persisted retrieval indexes on disk
- index versioning and compatibility checks
- incremental index updates after canonical writes
- rebuilds from canonical stores
- query-time retrieval and ranking for covered surfaces

Phase 1 leaves Python responsible for:

- API routes
- session and auth handling
- canonical writes to SQLite
- transcript archival lifecycle
- prompt assembly and final selection of what enters the model context

The clean boundary is:

- Python = canonical write path and orchestration
- Rust = retrieval/index ownership for covered surfaces

---

## Why Memory + Transcripts First, Not Graph

Memory and transcript retrieval are the best first targets because they are:

- directly tied to chat-turn latency
- strongly tied to post-turn ingestion throughput
- easier to index incrementally
- easier to rebuild deterministically

Graph retrieval is explicitly deferred to Phase 2 because it is more entangled with:

- claim correction and supersession
- edge invalidation
- neighborhood traversal semantics
- reconciliation between extracted facts and current entity state

That work should build on a proven Rust-owned retrieval/update protocol rather than being the first place that protocol is invented.

---

## Storage Model

### Canonical vs derived

For this design:

- SQLite remains canonical for memory/runtime records
- transcript archive artifacts remain canonical for archived conversation content
- Rust retrieval indexes are derived, persisted, versioned, and rebuildable

This distinction must remain explicit in both code and docs.

### Proposed on-disk layout

Conceptual layout under `.anima/`:

```text
.anima/
  indices/
    manifest.json
    staging/
    memory/
      ...
    transcripts/
      ...
```

The exact file names may change during implementation, but the system needs:

- a top-level manifest
- per-index-family storage
- a staging area for rebuild/swap

### Manifest responsibilities

The manifest should record at least:

- index schema version
- `anima-core` builder version
- per-index generation ids
- dirty/rebuild-required flags
- last rebuild timestamps
- compatibility metadata

The manifest is not canonical user data. It is engine-owned metadata for the derived retrieval plane.

---

## Index Scope

### Memory index

The Phase 1 memory index should cover:

- memory items
- memory episodes
- self-model blocks
- other prompt-relevant recall blocks that are already persisted canonically and queried frequently

The index should support:

- text retrieval
- filterable metadata
- score/rank outputs suitable for prompt assembly

Representative metadata fields:

- user id
- source type
- category
- timestamps
- importance or heat-like ranking signals
- canonical record ids

### Transcript index

The Phase 1 transcript index should cover:

- archived transcript metadata
- searchable text units or chunks
- pointers back to transcript artifacts and sidecars

The transcript index should support:

- lexical search
- recency-aware ranking
- metadata filtering
- snippet/preview generation

Phase 1 does not need to change transcript canonical storage. It only needs to make transcript recall materially faster and more consistent.

---

## Query Flow

The query-time contract should be:

1. Python asks `anima-core` for retrieval candidates for a given user and query context.
2. Rust executes retrieval against its persisted indexes.
3. Rust returns compact candidate records with:
   - canonical ids
   - source type
   - scores
   - rank/debug metadata
   - optional snippets or previews
4. Python decides how many candidates to inject into the prompt and how to combine them with other runtime context.

This keeps prompt policy in Python while moving retrieval execution into Rust.

---

## Write and Update Flow

The write-time contract should be:

1. Python commits canonical changes first.
2. Python emits deterministic update calls to `anima-core`.
3. Rust updates the relevant persisted indexes.
4. If Rust update succeeds, the retrieval plane remains clean.
5. If Rust update fails after canonical commit, Rust or Python marks the index family dirty and schedules repair or rebuild.

This order is important. Canonical data must never depend on successful index mutation to exist safely.

---

## Rebuild Model

Rebuild behavior is a core part of this design, not an optional cleanup tool.

### Rebuild triggers

Rebuild should happen when:

- indexes are missing
- manifest/index version is incompatible
- an incremental update failed
- health checks detect corruption or mismatch

### Rebuild sources

Rebuilds must use only canonical sources:

- SQLite canonical rows
- transcript archive artifacts and metadata

Rebuilds must not depend on a previous derived index being present or healthy.

### Rebuild execution

Rebuilds should:

- write into staging
- validate the staged generation
- atomically swap the new generation into service
- update the manifest last

This keeps readers from seeing half-built index state.

---

## Failure Model

The design should explicitly support these degraded states:

### 1. Missing index

Expected behavior:

- retrieval for that index family is unavailable or degraded
- rebuild can restore service from canonical stores

### 2. Dirty index after failed update

Expected behavior:

- canonical data remains valid
- Rust reports dirty state
- later rebuild clears it

### 3. Incompatible index version

Expected behavior:

- Rust refuses to use incompatible data
- rebuild produces a compatible generation

### 4. Missing transcript artifact

Expected behavior:

- transcript recall may return partial or unavailable results
- health diagnostics should surface the inconsistency

### 5. Rust module unavailable

Expected behavior:

- Phase 1 may preserve a degraded Python fallback path for continuity
- degraded mode should be explicit in logs and health status

The system must never silently assume "all good" when the Rust retrieval plane is unavailable.

---

## Verification Strategy

The implementation should be considered correct only if it proves both parity and recovery behavior.

### Functional parity

Add fixture-driven comparisons between current Python retrieval behavior and the new Rust retrieval behavior for covered surfaces.

### Recovery and rebuild

Add tests for:

- missing index
- incompatible version
- dirty-state rebuild
- transcript index rebuild from artifacts

### Ingestion integration

Add tests for:

- canonical write -> incremental Rust update -> successful retrieval
- canonical write -> forced Rust update failure -> dirty state -> successful rebuild

### Performance validation

Measure at least:

- chat-turn retrieval latency before and after
- post-turn ingestion/index update latency before and after

The design is successful only if the system gets faster in those two paths without weakening correctness guarantees.

---

## File-Level Direction

This design intentionally does not lock every filename, but the expected work spans:

- `packages/anima-core/`
  - new or expanded persisted-index modules
  - manifest/versioning support
  - query/update/rebuild FFI surface
- `apps/server/`
  - retrieval integration boundary
  - canonical write hooks that emit Rust index updates
  - rebuild and health wiring

The implementation should avoid scattering direct FFI calls across many server modules. A centralized Python boundary for Rust retrieval ownership remains the preferred integration pattern.

---

## Full Rust-Core Migration Plan

This section exists so the later work is documented now rather than re-litigated later.

### Phase 1: Rust-owned memory + transcript retrieval

Outcome:

- Rust owns persisted, rebuildable indexes for memory and transcripts
- Python remains canonical writer and orchestrator
- user-visible recall latency improves

### Phase 2: Rust-owned graph retrieval

Outcome:

- Rust adds graph index ownership and graph-aware query APIs
- graph retrieval joins the same recall plane as memory and transcripts
- Python stops stitching graph results as a separate retrieval subsystem

This phase should include:

- persisted graph index structures
- graph neighborhood expansion
- graph-aware ranking merged with the Rust recall result surface

### Phase 3: Rust-owned ingestion primitives

Outcome:

- Rust owns more of the ingest and index-build pipeline
- canonical normalization/chunking and related deterministic prep move into Rust
- update protocols become more transactional and less ad hoc

This phase should include:

- batch update protocols
- generation markers and dirty-state recovery
- stronger durability rules for incremental indexing

### Phase 4: Evaluate deeper canonical ownership

Outcome:

- decide whether selected canonical storage responsibilities should move into Rust

This phase is intentionally not pre-committed. The bar is pragmatic:

- move canonical ownership only where it reduces complexity and improves trustworthiness
- keep SQLite canonical if that remains the best system boundary

The long-term direction is a stronger Rust core, but not a dogmatic rewrite for its own sake.

---

## Success Criteria

Phase 1 is successful when:

- `anima-core` owns persisted retrieval indexes for memory and transcripts
- those indexes are explicitly rebuildable from canonical stores
- retrieval latency improves on covered surfaces
- post-turn ingestion/index update throughput improves
- the Python server remains correct when indexes are dirty, missing, or incompatible
- the full later migration path is documented and unambiguous

---

## Open Questions

These questions should be settled during planning, not left implicit:

1. What exact candidate schema should Rust return to Python for prompt assembly?
2. Which current recall blocks belong in the Phase 1 memory index beyond memory items and episodes?
3. How much degraded Python fallback should remain once the Rust retrieval plane lands?
4. What health surface should expose dirty/incompatible/missing index state?
5. Should transcript snippets be generated at index time, query time, or both?

---

## Recommendation

Start with a real ownership boundary, not another helper expansion.

Implement a Rust-owned, persisted, rebuildable retrieval plane for memory and transcripts first. Use that to improve chat-turn retrieval latency and post-turn ingestion throughput. Keep SQLite and transcript artifacts canonical. Document graph retrieval, ingestion ownership, and deeper storage decisions as later phases in the same roadmap.

That is the highest-leverage move that is still likely to ship cleanly.
