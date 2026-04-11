# anima-core Python Adoption Design

**Date:** 2026-04-11
**Status:** Draft for review
**Scope:** Define how the Python server should adopt more of `anima-core` without conflating that work with the longer-term Rust engine migration

---

## Goal

Make the Python server's relationship to `anima-core` explicit and deliberate:

- document what the server actually uses today
- separate "Rust crate roadmap" from "Python runtime integration status"
- define the next safe adoption slice
- avoid exporting a broad Python surface that implies capabilities the server does not yet depend on

This is a follow-up to the standalone-engine and single-writer storage docs, not a replacement for them.

---

## Why Now

The current repo state is mixed in a way that is technically valid but easy to misread:

- the Python server already uses `anima-core` in production paths for capsule I/O, adaptive cutoff, deterministic triplet extraction, and text normalization/cleanup
- the Rust crate also contains substantially more functionality than the Python server currently calls
- the server still uses Python and SQLAlchemy for its actual memory runtime, ranking glue, graph integration, and persistence model

That means the repo currently has two true statements at once:

1. `anima-core` is real production code, not dead weight
2. the Python server has not yet adopted the Rust engine as its runtime memory substrate

The gap is not that the Rust code exists. The gap is that the Python integration boundary is not explicit enough.

---

## Current State

### Production-used Python bindings

Today the server imports `anima_core` directly in four places:

- `apps/server/src/anima_server/services/vault.py`
- `apps/server/src/anima_server/services/agent/adaptive_retrieval.py`
- `apps/server/src/anima_server/services/agent/graph_triplets.py`
- `apps/server/src/anima_server/services/agent/text_processing.py`

The production-used FFI functions are:

- `read_capsule`
- `write_capsule`
- `find_adaptive_cutoff`
- `normalize_scores`
- `extract_triplets`
- `fix_pdf_spacing`
- `normalize_text`

### Python-native implementations still used by the server

The server still owns its own implementations for behavior that is conceptually available in Rust:

- `apps/server/src/anima_server/services/agent/embeddings.py`
  - `cosine_similarity`
  - reciprocal-rank fusion logic used by hybrid retrieval
- `apps/server/src/anima_server/services/agent/heat_scoring.py`
  - `compute_heat`

The server's stateful memory runtime also remains Python-owned:

- SQLAlchemy models and sessions
- `pgvector` / vector-store integration
- Python-side retrieval orchestration
- Python-side memory mutation and archival flows

### Rust crate status

This is consistent with the current `anima-core` roadmap.

The standalone-engine spec defines the long-term goal as turning `packages/anima-core` into the canonical memory substrate for ANIMA, but it also explicitly says the crate does not yet expose a fully coherent engine surface for host adoption.

So the right reading is:

- broad Rust functionality exists because the core is being built forward
- Python has only adopted a thin helper slice so far
- the missing piece is an explicit adoption strategy, not proof that the crate is unnecessary

---

## Problem Statement

The current layout creates three operational risks:

### 1. Integration ambiguity

The repo can be read as if the Python server already depends on the Rust memory engine. It does not.

### 2. Surface-area drift

The PyO3 module exports more functionality than the server uses, but there is no clear distinction between:

- production server bindings
- future engine-preview bindings
- stateless helpers that are candidates for next adoption

### 3. Premature stateful migration pressure

Because stateful engine types are exposed through FFI, there is pressure to view them as "ready for server integration" even though the server still has a different data model and persistence contract.

---

## Non-Goals

This slice does **not** include:

- replacing SQLAlchemy memory storage with `FrameStore`, `CardStore`, or `KnowledgeGraph`
- replacing `pgvector` with Rust `HnswIndex`
- making the path-backed Rust engine the live server database
- removing long-term engine work from `packages/anima-core`
- forcing all Rust exports to be immediately consumed by Python
- broad FFI expansion for stateful engine APIs

This is an integration-boundary cleanup and staged-adoption design, not a full runtime migration.

---

## Design Principles

### 1. Separate crate capability from server adoption

The Rust crate may legitimately own more capability than the Python server currently uses. That is acceptable as long as the Python boundary communicates what is:

- production-used now
- candidate for near-term adoption
- future engine-only or preview functionality

### 2. Prefer stateless deterministic helpers first

The next Rust adoption slices should be functions that:

- do not own persistent state
- have clear parity with existing Python behavior
- are easy to wrap with Python fallbacks
- are easy to test for exact or near-exact equivalence

Examples:

- `cosine_similarity`
- `compute_heat`
- `rrf_fuse`

### 3. Keep Python fallbacks until parity is proven

Every Rust-backed helper used by the server should continue to have a Python fallback until:

- parity tests are in place
- production behavior is stable
- local development remains usable in environments without the compiled module

### 4. Do not create dual ownership for stateful memory models

The server should not partially adopt Rust stateful engine classes while still treating Python/SQLAlchemy as the source of truth.

That would create unclear ownership over:

- persistence
- identity/version semantics
- locking and transactions
- graph and card rebuild rules

Stateful engine adoption should happen only as a dedicated later migration, not opportunistically.

### 5. Centralize the Python integration boundary

The server should stop importing `anima_core` ad hoc in multiple modules.

Instead, it should route all Rust-backed helpers through a single Python-owned adapter module that:

- performs optional import
- exposes a capability inventory
- owns fallback selection
- gives tests a single seam for monkeypatching and availability simulation

---

## Alternatives Considered

### Option A: Prune the Python FFI surface down to only currently used bindings

Pros:

- smallest public surface
- least ambiguity

Cons:

- fights the ongoing engine work
- creates churn in `ffi.rs`
- likely re-adds bindings as soon as the next adoption slice starts

### Option B: Keep the broad FFI surface and do nothing

Pros:

- zero short-term code churn

Cons:

- preserves integration ambiguity
- makes it hard to reason about what is production-relevant
- encourages accidental drift

### Option C: Recommended

Keep the Rust engine roadmap intact, but make the Python adoption boundary explicit and narrow.

That means:

- centralize all Python access to `anima_core`
- classify bindings by usage tier
- adopt a small additional stateless helper slice
- explicitly defer stateful engine integration

This keeps momentum without pretending the migration is already done.

---

## Recommended Design

## 1. Introduce a server-owned adapter module

Create a single Python integration boundary:

- `apps/server/src/anima_server/services/anima_core_bindings.py`

Responsibility:

- import `anima_core` once
- expose feature/capability flags
- wrap production-used functions
- host Python fallbacks for candidate next-slice helpers
- provide a single test seam for "Rust present" vs "Rust absent" behavior

This module becomes the authoritative contract between the Python server and the Rust crate.

## 2. Define binding tiers explicitly

### Tier 1: Production server bindings

These are already used by the server and should be treated as supported:

- `read_capsule`
- `write_capsule`
- `find_adaptive_cutoff`
- `normalize_scores`
- `extract_triplets`
- `fix_pdf_spacing`
- `normalize_text`

### Tier 2: Near-term adoption candidates

These are stateless helpers with clear Python equivalents and low migration risk:

- `cosine_similarity`
- `compute_heat`
- `rrf_fuse`

They should be wrapped in Python and adopted only after parity tests are added.

### Tier 3: Deferred stateful engine bindings

These remain intentionally **not** integrated into the server runtime in this slice:

- `FrameStore`
- `CardStore`
- `KnowledgeGraph`
- `HnswIndex`
- engine/path-engine surfaces
- temporal/query/state-projection types

These belong to future dedicated migration work once the server is ready to move state ownership.

## 3. Migrate current ad hoc imports to the adapter module

The four current direct import sites should stop importing `anima_core` themselves.

They should instead import server-owned wrapper functions from `anima_core_bindings.py`.

This gives the server:

- one policy point for fallback behavior
- one place to document availability requirements
- one place to add audit tests

## 4. Adopt one narrow additional helper slice

The next real Rust adoption should be small and measurable:

- use Rust `cosine_similarity` behind the existing Python function surface
- use Rust `compute_heat` behind the existing Python function surface
- use Rust `rrf_fuse` behind the existing hybrid retrieval merge surface

This is the right next slice because it:

- improves the actual production integration story
- avoids storage/model migration risk
- preserves existing server APIs

## 5. Keep stateful engine adoption deferred

The design explicitly defers server runtime adoption of:

- Rust frame/card/graph stores
- path-backed engine lifecycle
- Rust-owned retrieval/storage indexes as the primary live store

Those remain governed by the existing standalone-engine, storage, and doctor/rebuild specs.

---

## File-Level Direction

### New Python boundary

- `apps/server/src/anima_server/services/anima_core_bindings.py`

### Existing server modules to rewire through the boundary

- `apps/server/src/anima_server/services/vault.py`
- `apps/server/src/anima_server/services/agent/adaptive_retrieval.py`
- `apps/server/src/anima_server/services/agent/graph_triplets.py`
- `apps/server/src/anima_server/services/agent/text_processing.py`
- `apps/server/src/anima_server/services/agent/embeddings.py`
- `apps/server/src/anima_server/services/agent/heat_scoring.py`

### Tests to extend

- `apps/server/tests/test_vault.py`
- `apps/server/tests/test_hybrid_retrieval.py`
- `apps/server/tests/test_memory_scored_retrieval.py`
- `apps/server/tests/test_heat_scoring.py`
- new: `apps/server/tests/test_anima_core_bindings.py`

### Rust file to document more clearly

- `packages/anima-core/src/ffi.rs`

The immediate change in `ffi.rs` should be documentation and grouping clarity, not stateful migration pressure.

---

## Success Criteria

This follow-up is successful when:

- the Python server has one explicit `anima_core` adapter module
- every Rust-backed helper used by the server flows through that adapter
- current production-used functions are clearly identified as Tier 1
- stateless math/search helpers are adopted behind Python-compatible wrappers
- parity tests exist for Rust-present and fallback behavior where practical
- stateful engine bindings remain explicitly deferred rather than ambiguously "available"

---

## Risks and Mitigations

### Risk: Wrapper churn without runtime value

Mitigation:

- the first code slice must include both centralization and real helper adoption, not just file moves

### Risk: Rust/Python parity drift

Mitigation:

- add direct parity tests for helper functions before switching production call sites

### Risk: Confusing "available via FFI" with "approved for server use"

Mitigation:

- document binding tiers in both this spec and `ffi.rs`
- keep stateful engine adoption out of this slice

---

## Open Questions

### 1. Should preview bindings stay exported to Python at all?

Recommended answer for now: yes, but documented as deferred/preview rather than implied runtime dependencies.

### 2. Should `chunk_text` be part of the near-term adoption slice?

Not yet. There is no current server chunking path that needs it, so adding it now would be speculative.

### 3. Should hybrid retrieval move to Rust wholesale in this slice?

No. The immediate goal is to adopt `rrf_fuse`, not to move the entire retrieval orchestration path into Rust.

---

## Recommendation

Proceed with a narrow Python adoption follow-up that does three things:

1. centralize all `anima_core` access behind one server-owned adapter
2. adopt `cosine_similarity`, `compute_heat`, and `rrf_fuse` behind existing Python APIs
3. explicitly defer stateful engine/runtime migration to later dedicated specs

That keeps the Rust core moving forward without overstating what the Python server already depends on.
