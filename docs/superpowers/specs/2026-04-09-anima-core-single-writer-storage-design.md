# anima-core Single-Writer Storage Design

**Date:** 2026-04-09  
**Status:** Draft for review  
**Scope:** First storage-backed engine slice after Phase 5 engine facade

---

## Goal

Add path-based single-writer semantics to `anima-core` so one writable engine instance can own a persisted engine path at a time, while in-memory engines and byte-based capsule import/export remain lock-free.

This slice is deliberately narrow. It is not a full storage engine, lease manager, or repair system. It is the minimum credible step from an in-memory `AnimaEngine` facade toward a host-local working store with explicit writer ownership.

---

## Why This First

`doctor` / rebuild tooling is less meaningful until persisted engine state has a clear ownership model. If multiple processes can mutate the same path without coordination, repair semantics become ambiguous and brittle.

So the next step is:

1. define the persisted working form
2. define who may write to it
3. fail fast when that contract is violated

Only after that should rebuild and recovery behavior expand.

---

## Non-Goals

This slice does **not** include:

- stale-lock recovery
- lock heartbeats or leases
- shared-write semantics
- OS-wide read locks
- persistent derived-index caches beyond the existing store files
- capsule-as-live-storage
- background sync
- repair / `doctor`

Those belong to later storage hardening.

---

## Working Model

The `.anima` capsule remains the interchange/export artifact.

The live working form becomes a **directory-backed engine path**. This directory is host-local and optimized for safe mutation, not for interchange.

Conceptual shape:

```text
<engine-dir>/
  metadata.json
  .lock
  frames.<generation>.bin
  cards.<generation>.bin
  graph.<generation>.bin
```

`metadata.json` is the committed snapshot pointer. It identifies the currently committed generation and which generation-specific files belong to that snapshot.

This keeps the existing design principle intact:

- capsule = portable exchange artifact
- engine directory = mutable working store

---

## Storage Semantics

### Canonical vs derived

The storage rules stay aligned with the standalone-engine spec:

- `frames.bin` is canonical
- `cards.bin` is derived
- `graph.bin` is derived
- `metadata.json` is engine-owned metadata and derived

For this slice:

- missing canonical frames data for the committed generation is fatal
- missing derived files are allowed and load as empty/rebuildable state
- malformed derived files are **not** treated as missing; they fail loudly

This is important because “missing” and “corrupt” mean different things operationally.

### Create-path initialization

`create_path(...)` must seed a valid empty canonical store immediately.

That means:

- if the path does not exist, the directory is created
- if the path already exists and already looks like an engine directory, `create_path(...)` fails and callers must use `open_path(...)`
- if the path exists but is non-empty and not a valid engine directory, `create_path(...)` fails
- an empty generation `0` canonical frames file is written as a valid serialized `FrameStore`
- generation `0` may omit cards and graph payloads
- `metadata.json` is written as the initial committed snapshot for generation `0`

So the rule becomes:

- missing canonical frames data for the committed generation is fatal for `open_path(...)`
- but `create_path(...)` is responsible for making sure a new engine path never starts in that invalid state

Because `create_path(...)` writes generation `0` before returning, a later read-only `open_path(...)` against a fresh path is valid without any prior `flush()`.

### Snapshot contract

The first storage slice needs one concrete commit model:

- `metadata.json` records the current committed generation
- read-only open reads `metadata.json` first, then loads only the generation-scoped files named by that committed snapshot
- `flush()` writes a fresh generation of files beside the current one
- `flush()` updates `metadata.json` last, making the new generation visible atomically from the reader's point of view
- old generation files are retained after commit; `flush()` does **not** delete prior generations in this first slice

This is the contract that makes read-only opens safe while a writer exists:

- before metadata replacement, readers still see the old generation
- after metadata replacement, readers see the new generation
- readers never assemble a snapshot from partially updated unversioned files
- readers that opened against an older committed generation still have the files for that generation available

---

## Lock Model

### Scope

Single-writer semantics apply only to **path-based writable engine handles**.

They do **not** apply to:

- `AnimaEngine::new()`
- `AnimaEngine::read_capsule(...)`
- `AnimaEngine::write_capsule(...)`
- other purely in-memory operations

### Behavior

- `create_path(...)` acquires an exclusive writer lock immediately
- writable `open_path(...)` acquires an exclusive writer lock immediately
- lock acquisition is non-blocking
- if another writer holds the lock, open fails fast with a lock-conflict error
- the lock is released on `close()` or drop
- read-only `open_path(...)` does not acquire the writer lock
- `flush()` is only allowed from a writable handle that owns the lock

### Lock primitive

The `.lock` path is a **lock target**, not a lockfile-presence protocol.

The intended primitive is:

- open the sidecar lock file
- acquire an OS-held exclusive file lock on that handle
- keep that handle alive for the lifetime of the writable engine handle

If the process crashes, the OS releases the held lock with the handle. A leftover `.lock` path on disk is inert by itself and does not count as an active writer.

This keeps stale-lock recovery out of scope for the first slice while still giving deterministic crash semantics. The `.lock` path may remain on disk after crashes, but without a held OS file lock it does not block a future writer.

### First-slice strictness

If a writer cannot acquire the lock, the API returns an explicit error rather than waiting, stealing, or retrying.

That keeps failure behavior easy to reason about and test.

### Read visibility during flush

Read-only opens are allowed while a writer exists, but they must only observe the **last committed snapshot**.

For the first slice, that means `flush()` must behave like a commit operation, not an in-place partial rewrite that readers can race halfway through.

The storage contract is therefore concrete for this slice:

- the writer prepares a fresh generation of files
- `metadata.json` is replaced last
- read-only open treats `metadata.json` as the committed snapshot pointer
- old generation files remain on disk after commit

Readers must not see a mixed old/new state from a partially completed flush.

### Cleanup policy

Generation cleanup is intentionally out of scope for this first slice.

That means:

- `flush()` never removes old generation files
- `close()` never compacts generations
- later storage-hardening work may add explicit compaction / garbage-collection rules

This keeps the first implementation simple and preserves the last committed snapshot for any reader that opened before the newest commit became visible.

---

## Proposed API Shape

This design adds a path-backed lifecycle layer on top of the current `AnimaEngine` facade instead of changing the in-memory constructor.

Possible Rust shape:

```rust
pub enum EngineOpenMode {
    ReadOnly,
    ReadWrite,
}

pub struct ReadOnlyPathEngineHandle {
    engine: AnimaEngine,
    root: PathBuf,
}

pub struct ReadWritePathEngineHandle {
    engine: AnimaEngine,
    root: PathBuf,
    lock: Option<WriterLockGuard>,
}

impl ReadOnlyPathEngineHandle {
    pub fn engine(&self) -> &AnimaEngine;
    pub fn close(self) -> crate::Result<()>;
}

impl ReadWritePathEngineHandle {
    pub fn engine(&self) -> &AnimaEngine;
    pub fn engine_mut(&mut self) -> &mut AnimaEngine;
    pub fn flush(&mut self) -> crate::Result<()>;
    pub fn close(self) -> crate::Result<()>;
}

impl AnimaEngine {
    pub fn create_path(path: impl AsRef<Path>) -> crate::Result<ReadWritePathEngineHandle>;
    pub fn open_path(path: impl AsRef<Path>, mode: EngineOpenMode) -> crate::Result<EnginePathHandle>;
}
```

Where `EnginePathHandle` can be an enum wrapper if one return type is needed:

```rust
pub enum EnginePathHandle {
    ReadOnly(ReadOnlyPathEngineHandle),
    ReadWrite(ReadWritePathEngineHandle),
}
```

Design constraints:

- the path-backed type owns the lock, not bare `AnimaEngine`
- a read-only handle cannot expose write/flush behavior or mutable engine access
- the in-memory engine remains usable on its own
- path lifecycle and lock lifecycle stay coupled

The exact names may change during implementation, but the ownership model should not.

---

## Error Model

This slice needs explicit storage errors instead of collapsing everything into generic I/O.

At minimum:

- path missing / invalid
- frames file missing
- lock conflict
- write attempted through read-only handle
- malformed derived file
- malformed canonical file

The most important behavior is that lock conflict is distinguishable from general file failure.

---

## Alternatives Considered

### 1. Metadata lease only

Rejected for first slice.

Why:

- does not actually prevent concurrent writes
- can only detect contention after mutation has already started
- weaker than the problem requires

### 2. Full lease manager with heartbeat

Rejected for first slice.

Why:

- adds timeout/recovery complexity too early
- harder to test and reason about
- unnecessary before basic path-backed lifecycle exists

### 3. Lock the `.anima` file directly

Rejected for first slice.

Why:

- mixes interchange and working-store concerns
- encourages full-file rewrite semantics for routine mutation
- gives no clean place for mutable working metadata or future rebuild markers

### 4. Recommended approach: directory-backed engine with sidecar lock

Accepted.

Why:

- clean separation between live store and capsule artifact
- minimal and explicit writer contract
- easy to extend later with rebuild metadata and repair state

---

## Testing Strategy

The first implementation should prove the storage contract directly.

Required tests:

1. create-path acquires a writer lock
2. second writable open on the same path fails with lock conflict
3. read-only open succeeds while a writer exists
4. flush persists state and reload round-trips it
5. missing derived files load as empty state
6. missing canonical frames file fails
7. malformed derived file fails and is not downgraded to absence
8. closing/dropping a writer releases the lock so a later writer can open

The tests should prefer temporary directories and process-local contention checks first. Cross-process tests can come later if needed.

---

## Incremental Rollout

### Step 1

Add the path-backed handle and directory layout.

### Step 2

Add exclusive writer lock acquisition for writable opens.

### Step 3

Add flush/load round-trip with canonical vs derived file rules.

### Step 4

Expose the path-backed lifecycle to Python only after Rust behavior is proven.

---

## Risks

### Risk: Lock tied to wrong abstraction

If the lock is stored on bare `AnimaEngine`, in-memory uses become polluted with file-system concerns.

Mitigation:

- keep lock ownership on the path-backed handle

### Risk: Missing vs corrupt derived state gets conflated

If malformed derived files are silently ignored, real corruption becomes invisible.

Mitigation:

- only true absence loads as empty
- parse/read failures stay failures

This is intentionally stricter than the longer-term rebuild story. The standalone-engine direction allows corrupt derived state to be rebuildable in principle, but this first storage slice does not yet include repair or automatic rebuild. So the first open-path contract is:

- absent derived state: acceptable, load empty
- corrupt derived state: explicit failure

Later `doctor` / rebuild work can add a degraded-open or rebuild path without weakening the initial safety contract.

### Risk: Read-only behavior is underspecified

If read-only and writable opens share the same mutation surface, writes can leak through accidentally.

Mitigation:

- enforce writable-only `flush()`
- make mutable engine access impossible or explicit on read-only handles

---

## Recommended Next Step

Write a scoped implementation plan for the first single-writer storage slice with:

- path-backed handle
- directory layout
- lock acquisition/release
- flush/load rules
- Rust tests only

Python exposure should be a follow-up step after the Rust path contract is green.
