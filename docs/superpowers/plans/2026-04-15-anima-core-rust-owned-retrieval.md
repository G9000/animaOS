# anima-core Rust-Owned Retrieval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first Rust-owned retrieval slice for ANIMA by adding a persisted, rebuildable memory index in `anima-core`, integrating it into the Python server, and laying the manifest/adapter foundation for transcript indexing next.

**Architecture:** Phase 1 is split into two implementation slices. Slice A adds shared retrieval metadata, a server-owned Python adapter, and a persisted memory index with rebuild/update/search APIs. Slice B extends the same contract to transcript indexing and transcript recall. Canonical data stays in SQLite and transcript artifacts; Rust indexes are derived and rebuildable.

**Tech Stack:** Rust, PyO3, Python 3.12, FastAPI, SQLAlchemy, pytest, cargo test

**Spec Reference:** `docs/superpowers/specs/2026-04-15-anima-core-rust-owned-retrieval-design.md`

**User Constraint:** Do not create commits until the user explicitly allows commits again.

---

## File Structure

### New Files

| File | Responsibility |
|------|---------------|
| `packages/anima-core/src/retrieval_index.rs` | Persisted manifest + memory-index storage, update, search, and rebuild primitives |
| `apps/server/src/anima_server/services/anima_core_retrieval.py` | Central Python adapter for Rust retrieval ownership, fallbacks, and health/status |
| `apps/server/tests/test_anima_core_retrieval.py` | Python adapter and integration tests for memory-index lifecycle |

### Modified Files

| File | Changes |
|------|---------|
| `packages/anima-core/src/lib.rs` | Export the retrieval index module |
| `packages/anima-core/src/ffi.rs` | Expose Python bindings for index health, rebuild, update, delete, and search |
| `packages/anima-core/Cargo.toml` | Add any small serialization/time dependencies needed by the retrieval index |
| `apps/server/src/anima_server/services/agent/memory_store.py` | Emit Rust index updates after canonical memory writes/supersedes |
| `apps/server/src/anima_server/api/routes/memory.py` | Route memory search through Rust index first, then fall back if unavailable/dirty |
| `apps/server/src/anima_server/services/agent/transcript_search.py` | Later task: route transcript recall through Rust transcript index |
| `apps/server/tests/test_optional_rust_imports.py` | Extend fallback coverage for the new adapter/import surface |
| `docs/architecture/system/cross-cutting.md` | Later doc sync for retrieval ownership and degraded mode |

---

## Task 1: Retrieval Adapter + Shared Metadata Foundation

**Files:**
- Create: `packages/anima-core/src/retrieval_index.rs`
- Modify: `packages/anima-core/src/lib.rs`
- Modify: `packages/anima-core/src/ffi.rs`
- Create: `apps/server/src/anima_server/services/anima_core_retrieval.py`
- Test: `apps/server/tests/test_anima_core_retrieval.py`

- [ ] **Step 1: Write the failing Python adapter tests**

Add tests that prove:
- the server exposes one retrieval adapter module
- the adapter reports Rust availability
- the adapter returns a normalized status object even when Rust retrieval bindings are missing

Run:

```powershell
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'
uv run pytest apps/server/tests/test_anima_core_retrieval.py -q
```

Expected: FAIL because the adapter module does not exist yet.

- [ ] **Step 2: Write the failing Rust metadata/manifest tests**

Add Rust tests in `packages/anima-core/src/retrieval_index.rs` for:
- empty manifest creation
- manifest dirty flag updates
- persisted manifest round-trip

Run:

```powershell
$env:CARGO_TARGET_DIR='C:\Users\leoca\OneDrive\Desktop\anima\animaOS\.worktrees\anima-core-retrieval\target'
cargo test -p anima-core retrieval_index --offline
```

Expected: FAIL because the module does not exist yet.

- [ ] **Step 3: Implement the minimal Rust retrieval metadata layer**

Add:
- retrieval manifest types
- disk load/save helpers
- dirty-state tracking for index families

Keep this minimal: no transcript logic yet, no graph logic yet.

- [ ] **Step 4: Implement the Python retrieval adapter**

Create `apps/server/src/anima_server/services/anima_core_retrieval.py` with:
- optional import of Rust bindings
- capability/status helpers
- one normalized API surface for future memory and transcript index calls
- explicit degraded-mode reporting when Rust retrieval is unavailable

- [ ] **Step 5: Run the targeted tests**

Run:

```powershell
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'
uv run pytest apps/server/tests/test_anima_core_retrieval.py -q
$env:CARGO_TARGET_DIR='C:\Users\leoca\OneDrive\Desktop\anima\animaOS\.worktrees\anima-core-retrieval\target'
cargo test -p anima-core retrieval_index --offline
```

Expected: PASS

- [ ] **Step 6: Checkpoint only**

Do **not** commit. Leave the worktree with passing tests and updated task state.

---

## Task 2: Persisted Rust Memory Index

**Files:**
- Modify: `packages/anima-core/src/retrieval_index.rs`
- Modify: `packages/anima-core/src/ffi.rs`
- Modify: `apps/server/src/anima_server/services/anima_core_retrieval.py`
- Test: `apps/server/tests/test_anima_core_retrieval.py`

- [ ] **Step 1: Write the failing Rust memory-index tests**

Add Rust tests for:
- upsert document
- delete document
- persisted round-trip
- rebuild from provided documents
- lexical search ranking

Run:

```powershell
$env:CARGO_TARGET_DIR='C:\Users\leoca\OneDrive\Desktop\anima\animaOS\.worktrees\anima-core-retrieval\target'
cargo test -p anima-core retrieval_index::tests --offline
```

Expected: FAIL on missing memory-index behavior.

- [ ] **Step 2: Implement the minimal persisted memory index**

Use:
- a simple persisted document store
- `SimpleBm25Index` for lexical ranking
- canonical ids and metadata needed by Python

Defer:
- embeddings
- transcript indexing
- graph integration

- [ ] **Step 3: Expose FFI for memory index lifecycle**

Add Python-callable bindings for:
- status
- rebuild
- upsert
- delete
- search

Return plain Python-friendly structures, not Rust-specific classes.

- [ ] **Step 4: Extend the Python adapter**

Wrap the new FFI calls in Python-owned helpers that:
- normalize parameters
- normalize returned payloads
- expose one seam for fallback behavior

- [ ] **Step 5: Run the targeted tests**

Run:

```powershell
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'
uv run pytest apps/server/tests/test_anima_core_retrieval.py -q
$env:CARGO_TARGET_DIR='C:\Users\leoca\OneDrive\Desktop\anima\animaOS\.worktrees\anima-core-retrieval\target'
cargo test -p anima-core retrieval_index --offline
```

Expected: PASS

- [ ] **Step 6: Checkpoint only**

Do **not** commit.

---

## Task 3: Python Memory Search Integration

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/memory_store.py`
- Modify: `apps/server/src/anima_server/api/routes/memory.py`
- Modify: `apps/server/src/anima_server/services/anima_core_retrieval.py`
- Test: `apps/server/tests/test_memory_api.py`
- Test: `apps/server/tests/test_anima_core_retrieval.py`

- [ ] **Step 1: Write the failing Python integration tests**

Add tests that prove:
- canonical memory writes trigger Rust index updates
- memory delete/supersede paths remove or replace index entries
- `/api/memory/{user_id}/search` uses Rust index results when available
- Python fallback still works when Rust retrieval is unavailable or dirty

Run:

```powershell
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'
uv run pytest apps/server/tests/test_anima_core_retrieval.py apps/server/tests/test_memory_api.py -q
```

Expected: FAIL because the current memory flow does not use the Rust index.

- [ ] **Step 2: Wire canonical write hooks to Rust index updates**

Update `memory_store.py` so:
- add/update/supersede/delete flows notify the retrieval adapter
- failures mark the memory index dirty instead of breaking canonical writes

- [ ] **Step 3: Route memory search through the retrieval adapter**

Update `api/routes/memory.py` so:
- Rust index search is the first path
- existing Python search remains the fallback path
- fallback is explicit and testable

- [ ] **Step 4: Run focused verification**

Run:

```powershell
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'
uv run pytest apps/server/tests/test_anima_core_retrieval.py apps/server/tests/test_memory_api.py -q
```

Expected: PASS

- [ ] **Step 5: Checkpoint only**

Do **not** commit.

---

## Task 4: Transcript Index + Recall Integration

**Files:**
- Modify: `packages/anima-core/src/retrieval_index.rs`
- Modify: `packages/anima-core/src/ffi.rs`
- Modify: `apps/server/src/anima_server/services/anima_core_retrieval.py`
- Modify: `apps/server/src/anima_server/services/agent/transcript_archive.py`
- Modify: `apps/server/src/anima_server/services/agent/transcript_search.py`
- Test: `apps/server/tests/test_p5_transcript_archive.py`
- Test: `apps/server/tests/test_anima_core_retrieval.py`

- [ ] **Step 1: Write the failing transcript-index tests**

Cover:
- transcript metadata ingestion into Rust
- transcript search ranking
- rebuild from transcript sidecars/artifacts
- degraded behavior when transcript index is missing/dirty

- [ ] **Step 2: Implement transcript indexing in Rust**

Reuse the same manifest and persistence model from the memory index.

- [ ] **Step 3: Integrate transcript archive and search**

Update Python so transcript export/update triggers Rust index writes and transcript search uses Rust first with explicit fallback.

- [ ] **Step 4: Run focused verification**

Run:

```powershell
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'
uv run pytest apps/server/tests/test_anima_core_retrieval.py apps/server/tests/test_p5_transcript_archive.py -q
$env:CARGO_TARGET_DIR='C:\Users\leoca\OneDrive\Desktop\anima\animaOS\.worktrees\anima-core-retrieval\target'
cargo test -p anima-core retrieval_index --offline
```

Expected: PASS

- [ ] **Step 5: Checkpoint only**

Do **not** commit.

---

## Task 5: Final Verification + Docs Sync

**Files:**
- Modify: `docs/architecture/system/cross-cutting.md`
- Modify: `docs/architecture/system/configuration.md`
- Test: existing targeted Rust/Python suites

- [ ] **Step 1: Update architecture docs**

Document:
- Rust-owned derived retrieval indexes
- canonical-vs-derived boundary
- degraded mode and rebuild behavior

- [ ] **Step 2: Run the final focused verification set**

Run:

```powershell
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'
uv run pytest apps/server/tests/test_anima_core_retrieval.py apps/server/tests/test_memory_api.py apps/server/tests/test_p5_transcript_archive.py apps/server/tests/test_optional_rust_imports.py -q
$env:CARGO_TARGET_DIR='C:\Users\leoca\OneDrive\Desktop\anima\animaOS\.worktrees\anima-core-retrieval\target'
cargo test -p anima-core --offline
```

Expected: PASS

- [ ] **Step 3: Stop for review**

Do **not** commit. Present the changed files, verification results, and remaining risks to the user for approval.
