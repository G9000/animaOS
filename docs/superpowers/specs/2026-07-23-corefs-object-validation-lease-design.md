# CoreFS Object Validation Lease Design

**Status:** Approved for specification on 2026-07-23

**Ticket:** PCF-002 catalog-performance architecture revision

**Parent:** PCF-000 Portable Core Filesystem

**Prior design:** `docs/superpowers/specs/2026-07-20-corefs-catalog-commit-performance-design.md`

## Context

PR #117 merged the first catalog-commit optimization pass. Its source-current exact
30-warm-up/200-sample reference run passes every unchanged PCF-002 catalog gate except
maximum-live commit latency:

| Fixture | Object files | Commit p50/p95/p99 | Gate | Result |
|---|---:|---:|---:|---|
| medium | 500 | `67.9843/78.9163/84.2530` ms | p95 <= 100 ms | Pass |
| maximum-live | 2,500 | `251.3036/361.1692/442.4276` ms | p95 <= 250 ms | Fail |
| serialized-limit | 0 | `126.0642/157.8677/169.2512` ms | p95 <= 250 ms | Pass |

The similarly sized catalog-only fixture is green. The remaining cost is concentrated
in `validate_prepared_revisions`: every unchanged object still performs a
capability-relative open, opened-versus-linked identity validation, regular-file and
symlink validation, link-count validation, a metadata length query, and close.

The user approved a security-equivalent revision on 2026-07-23. Full catalog snapshots,
catalog/object wire formats, public APIs, durability, recovery, rotation, benchmark
fixtures, timer boundaries, and acceptance thresholds remain unchanged. The revision
may replace repeated unchanged-object opens only when it proves behavior equivalent to
the current filesystem validation boundary.

## Decision

Add a process-local **object validation lease** for the Windows reference path. A valid
lease combines:

1. exact existing authenticated pointer, key-identity, catalog, and object-binding cache
   authority;
2. one retained file handle and captured file identity for every validated referenced
   object;
3. fresh handle metadata checks on every commit; and
4. an ordered, fail-closed object-directory change monitor used to decide which paths
   require the existing full safe-open validation.

An exact clean lease replaces 2,500 repeated open/path-resolution/close cycles with
2,500 retained-handle metadata queries plus a bounded monitor fence. A dirty object
name follows the current safe-open path. Any uncertainty disables the optimization and
performs the current complete validation.

The lease is an optimization, never disk or cryptographic authority. It is not
serialized, transferred, or accepted after process restart.

## Goals

- Pass the unchanged maximum-live p95 <= 250 ms reference gate without changing its
  2,500 real immutable object files.
- Preserve full immutable V2 catalog generations and the current HEAD-last durable
  publication sequence.
- Preserve the current behavior for missing, zero-length, symlinked, non-regular,
  replaced-by-non-file, and unexpectedly hard-linked referenced objects.
- Preserve exact pointer, FRK/key identity, catalog-byte, object-record, recovery, and
  rotation cache binding.
- Preserve cold-path and unsupported-platform correctness through the existing
  capability-relative safe-open validator.
- Keep the complete public `commit` call inside the benchmark timer.

## Non-goals

- No delta, journal, Merkle, pack, or sharded catalog/object format.
- No persistent validation sidecar and no timestamp-only or directory-mtime authority.
- No weakening of catalog-byte reauthentication introduced by PR #117.
- No new assumption that an unchanged object is cryptographically rehashed on every
  commit. The current unchanged-object path does not do that.
- No privileged NTFS USN-journal dependency.
- No requirement that non-Windows platforms use the fast path in this revision.
- No change to CoreFS logical operations, public APIs, object encryption, or prepared
  revision semantics.

## Existing validation contract

For each unchanged catalog object, `validate_existing_object_file` currently:

1. opens the catalog physical name relative to the pinned `objects/` capability;
2. compares metadata from the opened file with non-following metadata from the linked
   directory entry;
3. requires both observations to be the same regular, non-symlink file;
4. requires one link, except for the one recognized crash-stale immutable staging
   alias; and
5. requires nonzero length.

The validator does not rehash unchanged encrypted bytes, compare the file identity to
the identity seen on the prior commit, or create an atomic snapshot against a hostile
writer after each file is closed. The lease must preserve the five listed checks and
their timing behavior at least as strongly; it must not claim a stronger existing
content-integrity guarantee.

## Why a directory watcher alone is insufficient

A directory monitor can observe deletion, creation, rename, reparse, size, and
last-write events within `objects/`, but it is not sufficient for the current
link-count contract. A local NTFS characterization created a hard link to a referenced
object in a sibling directory while monitoring `objects/`; the source directory emitted
no event. The file's handle metadata still exposes the increased link count.

Therefore:

- the monitor owns path-name invalidation;
- retained-handle metadata owns per-commit nonzero-length and link-count checks; and
- any non-unit link count enters the existing slow validator so the recognized
  crash-stale staging exception remains exact.

## Architecture

### 1. Lease state

Extend the authenticated commit snapshot with optional object lease state:

```text
ObjectValidationLease
  directory_identity
  monitor_generation
  monitor_state: Clean | Dirty(names) | Unknown
  objects: stable-ID ordered LeasedObjectBinding[]

LeasedObjectBinding
  existing ValidatedObjectBinding
  retained capability-open File
  opened_file_identity
```

The lease inherits the existing snapshot's exact `PointerSet`,
`RequiredCacheKeyIds`, authenticated catalog, and object-wrap-key identity. Reuse
requires exact equality of all existing cache fields and the full catalog object tuple.
The retained handle contains no plaintext, Object DEK, FRK, or new secret material.

`Unknown` is terminal for that lease instance. It is dropped and rebuilt only through
a complete validating path.

### 2. Monitor contract

Introduce a crate-private `ObjectDirectoryMonitor` abstraction with three outcomes:

```text
fence() -> CleanThrough(sequence)
         | DirtyThrough(sequence, names)
         | Unknown(reason)
```

The Windows backend uses the already pinned `objects/` directory handle and native
directory-change notifications. It must:

- arm before the initial full object scan;
- request file-name, directory-name, attributes, size, last-write, security, and
  reparse-relevant changes;
- report buffer overflow, cancellation, handle loss, parse errors, or incomplete
  rename pairing as `Unknown`;
- preserve events that arrive between fences;
- return only after an implementation-specific directory-entry fence proves that
  earlier path events have been delivered; and
- ignore a fence event only when its unpredictable name matches that monitor
  instance's active fence operation.

The proposed Windows fence is an exclusive create/delete lifecycle for a reserved
random probe entry inside `objects/`. Seeing its terminal notification establishes the
queue boundary. Probe cleanup is mandatory on the healthy path. A stale probe, failed
cleanup, unprovable notification ordering, or benchmark temp-file residue makes the
monitor `Unknown` and disables the fast path.

The implementation plan must begin with a focused platform characterization of this
fence. If native behavior cannot establish the stated boundary, this design does not
permit treating the monitor as clean; implementation must stop and return for a new
architecture decision.

### 3. Lease construction

Lease construction follows this order:

1. acquire the existing Core-wide kernel lock;
2. revalidate the pinned root, `fs/`, `catalogs/`, and `objects/` identities;
3. read and authenticate the exact pointer/catalog state through the existing path;
4. arm the object-directory monitor;
5. validate every referenced object through the existing safe-open rules;
6. retain the validated opened handles rather than closing and reopening them;
7. validate new/prepared objects through the current length and encrypted-hash path,
   returning the already validated handle;
8. fence the monitor; and
9. publish `Clean` lease state only when no relevant unaccounted event occurred.

If a relevant event occurs during the scan, the implementation retries once from a
fresh pointer/layout observation under the same lock. A second event, any ambiguity, or
resource failure returns to a successful commit/load only through the uncached current
validator; it does not publish a lease.

### 4. Exact-hit commit flow

After the existing lock, layout, pointer, key-identity, and catalog-byte checks succeed:

1. clone the exact authenticated snapshot without holding the cache mutex over I/O;
2. verify the current `objects/` directory identity equals the lease identity;
3. fence and drain the monitor;
4. merge dirty names with changed/new/deleted catalog object tuples;
5. for every exact unchanged clean object, query metadata from its retained handle;
6. require a regular file, the captured file identity, nonzero length, and exactly one
   link;
7. route a dirty name, metadata error, identity mismatch, or non-unit link count through
   `open_regular_file_in` and the existing slow checks;
8. validate new and changed objects through the complete prepared-revision path;
9. perform a final monitor fence before publication; retry or fall back if a referenced
   path changed during validation;
10. continue unchanged canonical serialization, encryption, immutable publication, and
    durable HEAD-last advancement; and
11. replace lease state only after durable authority is established.

Unreferenced ordinary object-directory entries do not invalidate the catalog. A new
unreferenced hard link is still detected because the referenced retained handle's link
count changes. Rename pairs, directory events, reparse/security changes, or events that
cannot be attributed to one physical object dirty the entire lease.

### 5. Dirty-object slow path

The slow path remains the existing authority:

- missing path: error;
- zero length: `ReferencedObjectMissing`;
- symlink/reparse or non-regular replacement: invalid layout;
- opened-versus-linked identity mismatch: invalid layout;
- link count other than one: existing crash-stale alias proof or invalid layout; and
- new/changed prepared revision: exact size plus complete encrypted-byte SHA-256 and
  prepared-token/key-binding checks.

A successful dirty-object validation refreshes that handle only in the candidate next
lease. It does not mutate the currently authoritative lease in place.

### 6. Publication, failures, and recovery

Lease publication follows the existing disk-authority boundary:

- failure before HEAD leaves prior disk authority intact but retains any observed dirty
  monitor state;
- durable HEAD success may publish the exact next lease;
- post-HEAD marker, callback, or invalidation failure that returns recovery-pending
  clears lease authority;
- receipt-only, completion-only, missing-HEAD, divergent-pointer, and ambiguous recovery
  states perform no lease hit;
- successful recovery publishes a lease only after a complete object validation and
  exact pointer reauthentication; and
- cache poisoning or monitor-thread panic clears the lease and uses the normal path.

The lease never makes a missing catalog acceptable. Every distinct catalog named by
HEAD, receipt, and completion is still reopened and SHA-256-verified before cached
decoded state is consumed.

### 7. Rotation

FRK rotation preserves object files but changes catalog/key authority. Rotation may
carry retained handles into a candidate next lease only when:

- old and new pointer sets are both authenticated;
- the exact object physical tuple is unchanged;
- directory identity and monitor continuity are unchanged;
- every handle passes fresh metadata validation; and
- cutover completion is durably verified.

Any mixed-key, recovery-pending, monitor-unknown, or callback-failure state drops the
lease. A cold full validation remains valid behavior.

### 8. Concurrency and lock ordering

The fixed order is:

```text
CoreCommitLock
  -> clone cache snapshot under short cache mutex
  -> release cache mutex
  -> monitor fence/drain
  -> retained-handle metadata and safe-open I/O
  -> catalog validation/publication
  -> release kernel lock
  -> callbacks with no cache or monitor guard held
```

No cache or monitor mutex may be held during kernel-lock acquisition, catalog/object
I/O, crypto, failure hooks, invalidation callbacks, or user build callbacks.

Each coordinator owns its own monitor and handles. A second coordinator's commit
changes pointers and produces directory events; pointer mismatch already prevents an
exact cache hit. External changes that race after one object's metadata check retain the
same non-atomic boundary as the current sequential safe-open loop and remain dirty for
the next operation. The final monitor fence strengthens path-change detection during
the loop without claiming atomic protection against arbitrary hostile open handles.

### 9. Resource and lifecycle policy

The Windows fast path must reserve its handle and monitor resources before advertising
a clean lease. Allocation or handle-open failure disables the lease without failing an
otherwise valid CoreFS operation.

Lease handles are closed on:

- Core lock/logout;
- coordinator shutdown;
- suspend;
- transfer/export preparation;
- removable-drive eject preparation;
- monitor failure; and
- cache replacement or clear.

The implementation must bound retained handles to the current catalog object count and
record a diagnostic reason when it falls back. Non-Windows builds use the current
safe-open path until a platform backend can prove the same monitor fence and resource
lifecycle.

## Error behavior

No new public error is required for an optimization miss. Monitor and lease failures
fall back internally.

Existing public errors remain exact for invalid disk state. Only failure to complete a
required full validation may fail the operation. Diagnostic probes may expose
crate-private counters/reasons for tests and benchmark attribution.

## Performance decision gate

Implementation begins with a disposable Windows release-mode spike that measures the
2,500-object current safe-open loop against:

1. retained-handle metadata validation;
2. the two monitor fences; and
3. dirty-set merge overhead.

The spike uses real fixture object files and the same capability-relative metadata
helpers. It changes no benchmark fixture, public timer, threshold, or reference
artifact.

If the combined lease validation does not provide credible margin below the unchanged
250 ms full-commit gate, implementation stops before building recovery/rotation
integration and returns for an object-pack or broader storage-layout decision.

After complete correctness gates pass, run the existing disposable diagnostic and then
the exact unchanged 30/200 reference command. Only the exact reference artifact may
clear PCF-002.

## Required tests

### Monitor and platform contract

- monitor arms before scanning;
- fence observes prior create, delete, rename, symlink/reparse, truncate, and replacement
  events;
- events between validation and the final fence force retry/fallback;
- buffer overflow, cancellation, handle loss, malformed rename pairs, probe cleanup
  failure, and monitor panic become `Unknown`;
- unexpected ordinary files remain irrelevant;
- an inside-directory hard link dirties the path;
- an outside-directory hard link produces no required watcher event but is rejected by
  retained-handle link count; and
- clean shutdown leaves no fence probe or other temporary file.

### Lease equivalence

- exact clean objects perform zero repeated opens;
- clean objects still perform one fresh handle-metadata validation per commit;
- missing, zero-length, symlinked, replaced-by-directory, and unexpectedly hard-linked
  objects fail on a warm hit;
- the recognized crash-stale immutable staging alias retains existing behavior;
- dirty regular objects use the current opened-versus-linked identity check;
- changed wrapped DEKs, object-key epochs, physical names, kinds, hashes, revisions, or
  key identities never reuse a leased binding; and
- wrong same-version key material still reaches authentication and fails closed.

### Authority, recovery, and concurrency

- all PR #117 HEAD/receipt/completion catalog-byte reauthentication tests remain green;
- all-missing pointers and first mutation cannot consume a stale lease;
- pre-HEAD failure cannot publish candidate handles;
- post-HEAD recovery-pending outcomes clear the lease;
- rotation publishes only after verified cutover completion;
- two coordinators and injected external changes cannot bypass pointer or monitor
  invalidation;
- cache/monitor poison recovery has no lock inversion; and
- callbacks observe no cache or monitor guard held.

### Performance and provenance

- counters prove zero unchanged-object opens and exactly one handle metadata query per
  clean object;
- fixture object counts remain `500/2,500/0`;
- the complete public commit remains the measured interval;
- all catalog bytes remain full canonical generations;
- final HEAD/catalog counts and zero-temporary-file assertions remain unchanged; and
- the strict source/binary/Cargo.lock/target/argv report contract remains unchanged.

## Expected implementation surface

- `packages/anima-corefs/Cargo.toml`
- `Cargo.lock`
- `packages/anima-corefs/src/transaction.rs`
- `packages/anima-corefs/src/transaction/cache.rs`
- `packages/anima-corefs/src/transaction/object_lease.rs`
- `packages/anima-corefs/src/transaction/object_lease/windows.rs`
- `packages/anima-corefs/src/transaction/cache_tests.rs`
- `packages/anima-corefs/src/transaction/failure_tests.rs`
- `packages/anima-corefs/tests/transaction.rs`
- `packages/anima-corefs/tests/rotation.rs`
- `packages/anima-corefs/src/benchmark.rs`
- `packages/anima-corefs/tests/catalog_benchmark.rs`
- `apps/server/tests/test_corefs_catalog_benchmark.py`
- `docs/superpowers/plans/2026-07-20-corefs-catalog-commit-performance.md`
- `docs/benchmarks/portable-core-filesystem/catalog-reference-v1.json` only after a valid
  exact reference run
- PCF-002 and PCF-000 ticket metadata/evidence

## Alternatives rejected

- **Watcher-only invalidation:** misses at least outside-directory hard-link creation and
  cannot preserve the current link-count contract.
- **NTFS USN journal:** can report link changes and survive process restarts, but it is
  NTFS-specific and requires privileged volume-journal access.
- **Retained handles without monitoring:** preserves handle identity, length, and link
  count but cannot prove that the catalog physical name still names that handle.
- **Skip unchanged-object validation:** changes fail-closed behavior.
- **Authenticated persistent inventory:** an attacker or external tool can change an
  object without updating the inventory; it is not filesystem authority.
- **Object packs/shards:** viable if this lease cannot meet the gate, but changes object
  layout, reads, recovery, transfer, GC, and rotation and therefore requires a broader
  approved design.
- **Weaken the benchmark or move work outside `commit`:** violates PCF-002 acceptance.

## Completion criteria

This architecture revision is complete only when:

1. the platform fence characterization proves the required ordered boundary;
2. every correctness, recovery, rotation, concurrency, and fail-closed regression
   passes;
3. full Rust 1.75 tests, strict Clippy, formatting, Python benchmark-contract tests, and
   diff hygiene pass;
4. the unchanged exact 30/200 reference artifact passes all gates, including
   maximum-live p95 <= 250 ms;
5. independent specification and quality reviews have no unresolved substantive
   finding; and
6. PCF-002 and PCF-000 record the artifact, validation, and legal state transition.
