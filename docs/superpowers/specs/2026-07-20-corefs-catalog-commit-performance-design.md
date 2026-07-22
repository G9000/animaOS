# CoreFS Catalog Commit Performance Design

**Date:** 2026-07-20

**Status:** Implemented and correctness-validated; exact reference gates failed; architecture revision required

**Task 10 local evidence (2026-07-22):** The unchanged 30-warm-up/200-sample reference run passed the medium, serialized-limit, durable-write, and maximum-live serialized-size gates, but maximum-live commit p95 was 299.6261 ms against the unchanged 250 ms gate. The strict artifact is preserved with `allPassed: false`; clearance requires a separately approved architecture revision.

**Task 10 validation note:** The literal plan Step 3 direct-inspection snippet used the stale path `<fixture>/fs/objects`. Production `run_fixture_benchmark(&fixture_root)` passes the fixture root to `CoreCommitCoordinator::new(root)`, and retained integration coverage reads `<fixture>/objects`. The literal assertion failure was preserved; the same read-only provenance, schema, generation, catalog, object-count, and temporary-file assertions passed with the production-canonical sibling object root. The plan was not edited because doing so would have changed the source commit bound into the generated artifact.

**Ticket:** PCF-002 Step 12

**Implementation plan:** `docs/superpowers/plans/2026-07-20-corefs-catalog-commit-performance.md`

**Branch:** `codex/pcf-002-catalog-performance`

## Decision

Retain the complete immutable catalog-generation format and the existing durable publication sequence. Reduce repeated work in normal commits with:

1. a process-local authenticated catalog snapshot cache keyed by the exact authoritative pointer set and domain-separated identities of the FRK-derived key material that authenticated it;
2. a validated-object state cache that avoids repeating cryptographic key-binding work for byte-for-byte unchanged object records after a successful validating commit; and
3. crate-private fast paths for catalog values whose private constructors or authenticated decoder have already established the complete graph and policy invariants.

The cache is an optimization, never an authority. Every commit still acquires the Core-wide kernel lock, revalidates the pinned layout, rereads the pointer records, enforces mutation preconditions, checks referenced object files through the existing safe-open path, emits a complete bounded canonical catalog, encrypts it, publishes it immutably, and durably advances `fs/HEAD`.

## Context and blocker

PCF-002 cannot complete until the fixed reference profile passes all catalog gates. The latest source/binary/Cargo.lock/target-bound 30-warm-up/200-sample artifact passed provenance, schema, direct fixture inspection, durable-write latency, and maximum-live size. These commit and lock-hold p95 gates remain red:

| Fixture | Entries | Commit p95 | Gate |
|---|---:|---:|---:|
| medium | 5,000 live + 500 tombstones | 207.7262 ms | 100 ms |
| maximum-live | 25,000 live + 2,500 tombstones | 1,060.9271 ms | 250 ms |
| serialized-limit | exact 16 MiB catalog | 1,131.7692 ms | 250 ms |

The near-linear growth from 5,500 to 27,500 entries, and the similar maximum-live and exact-16-MiB timings despite their different byte sizes, point to repeated per-entry work as the primary constraint rather than durable I/O or ciphertext size alone.

## Goals

- Pass the existing 100 ms medium and 250 ms large-fixture p95 gates on the approved Windows 11/NTFS/NVMe profile.
- Preserve the V2 full-generation wire format and every current recovery and durability boundary.
- Preserve the current public API and fail-closed behavior across process races, FRK rotation, cutover recovery, missing objects, zero-length objects, symlinks, replacements, and unexpected hard links.
- Keep cold starts and cache misses correct even when they are slower than steady-state commits.
- Keep benchmark provenance and timing honest: the measured interval remains the complete public `commit` call.

## Non-goals

- No delta, journal, checkpoint, or Merkle catalog format.
- No relaxation of fsync, immutable publication, HEAD-last ordering, kernel exclusion, or recovery markers.
- No movement of catalog serialization or encryption outside the measured `commit` call solely to improve the benchmark.
- No removal of the allocation-free bounded-size preflight.
- No weakening of untrusted catalog decode, canonical-byte rejection, prepared-revision verification, or existing object-file layout checks.
- No change to the benchmark fixtures, sample counts, thresholds, or artifact contract.

## Current hot path

The normal commit currently performs several complete or object-proportional passes while holding the lock:

1. `load_committed_recovering_with_keyring` reads pointer records and the referenced catalog envelope. `HeadRecord::verify_catalog` decrypts, reconstructs, validates, and canonical-reencodes the catalog, then `load_pointer_for_head` immediately decrypts, reconstructs, validates, and canonical-reencodes the same bytes a second time to return the catalog.
2. `build_next` returns a `CatalogGeneration` that was already validated by its private-field constructor; applying the existing cutover marker validates the complete catalog again.
3. `validate_precondition_coverage` allocates current and next entry maps and scans both catalogs.
4. `validate_prepared_revisions` scans the complete next catalog. For every unchanged object it unwraps the current and next Object DEK records, compares derived key bindings, and safely opens the immutable object file.
5. catalog encryption validates the complete next catalog again, performs the required allocation-free bounded serialization preflight, materializes canonical plaintext, and encrypts it. Catalog naming hashes the full encrypted envelope, then `HeadRecord::new_for_catalog` decrypts and fully validates the just-created envelope and hashes it again before durable publication.

The optimization targets duplicate decrypt/authentication, invariant validation, full-envelope hashing, map allocation, and unchanged-object key unwraps. It does not remove the one bounded canonical plaintext emission or durable publication.

## Design

### 1. Authenticated snapshot cache

`CoreCommitCoordinator` gains a poison-tolerant mutex containing at most one `AuthenticatedCommitSnapshot`. The snapshot contains:

- the canonical decoded `fs/HEAD`, cutover receipt, and cutover completion records observed together;
- the authenticated and decoded `CatalogGeneration` referenced by HEAD;
- the required FRK version and Core identity binding;
- a domain-separated catalog-key cache identity for every FRK version used to authenticate HEAD/receipt/completion state, plus the active object-wrap-key cache identity when object validation is cached; and
- optional validated-object state described below.

Each cache identity is a fixed-length HKDF-derived identifier using an explicit CoreFS cache-binding domain, the Core ID, FRK version, and the applicable high-entropy catalog or object-wrap subkey. It is safe to compare and store but is never accepted as cryptographic authority outside cache selection. The cache contains decrypted catalog metadata already returned by the coordinator today, but it stores no FRK, catalog key, Object DEK, plaintext object content, or new secret material.

For a normal commit, after acquiring the kernel lock and revalidating the pinned layout, the coordinator rereads all authoritative pointer records. A hit requires exact equality with the cached HEAD/receipt/complete tuple, exact Core identity, exact required FRK version, equality of every required catalog-key cache identity, equality of the active object-wrap-key identity before object-binding reuse, and a non-recovery state. The cached catalog was previously authenticated against that exact HEAD and key material and may then replace catalog-file read, hash verification, decryption, decode, and canonical-reencode.

Any mismatch, missing pointer, malformed pointer, recovery state, FRK-version mismatch, same-version key-identity mismatch, or absent cache becomes a cache miss and uses the existing full load/recovery path. Wrong key material therefore reaches normal catalog authentication and fails closed. A successful full path may refresh the authenticated catalog portion of the cache. Public unlocked `load_committed` retains its existing reread/race handling; a cache match does not eliminate its pointer stability check.

Mutex poisoning must not make CoreFS unavailable. A poisoned cache is cleared and treated as a miss; storage authority remains on disk.

### 2. Cache publication and invalidation

The cache changes only at authority boundaries:

- A fully verified cold or mismatch load may cache the authenticated catalog, but not validated-object bindings it did not verify.
- A successful commit may cache the new authoritative snapshot and its validated-object state only after catalog publication and durable HEAD/cutover completion succeed.
- A post-HEAD outcome marked `recovery_pending` clears the cache and forces the existing recovery path on the next operation.
- A failure before authoritative HEAD publication leaves the prior cache entry unchanged.
- FRK rotation, validation-only initialization, cutover recovery, or any pointer divergence clears or replaces the cache only after the corresponding on-disk state has been verified.
- External invalidation callback delivery does not determine cache authority; the durable pointer state does.

A restart or another process begins with an empty cache. Cross-process commits are detected by the mandatory pointer reread under the kernel lock.

### 3. Concurrency and lock ordering

The cache mutex protects only `Option<Arc<AuthenticatedCommitSnapshot>>`. Callers briefly lock it to clone the immutable `Arc`, compare an already-collected key, replace the `Arc`, or clear it. They never hold the cache mutex while performing filesystem I/O, cryptography, catalog traversal, user build closures, failure hooks, invalidation callbacks, or kernel-lock acquisition.

Operations that require `CoreCommitLock` always acquire the kernel lock first, collect and verify on-disk state without the cache mutex, and only then perform a short cache lookup or replacement. Public unlocked loads read their pointer tuple first, release any cache guard before the existing second pointer read, and release it before entering a recovery path that acquires `CoreCommitLock`. No path may acquire the kernel lock while holding the cache mutex.

This order prevents commit/load-recovery inversion. Immutable `Arc` snapshots also prevent a cache replacement from invalidating state already selected by an in-flight operation without requiring a long-held mutex or a full catalog clone.

### 4. Validated catalog fast paths

`CatalogGeneration` already has private fields and can only arise from validating constructors, controlled crate-private transformations, or authenticated untrusted decode. The implementation may therefore add narrowly named crate-private operations for validated values:

- applying an already-validated cutover marker without rerunning the unchanged entry graph;
- encoding a validated catalog without calling the complete graph validator again; and
- reusing cached invariant results only when the exact catalog snapshot matches.

Public untrusted validation and decoding remain unchanged. The encoder still performs the allocation-free bounded-size preflight before the one materializing serialization, so oversized input cannot cause an unbounded proportional allocation.

### 5. Single-pass authenticated open and publication artifact

The head/catalog boundary gains crate-private helpers while retaining the current public fail-closed APIs:

- a verify-and-decrypt operation returns the authenticated `CatalogGeneration`; `HeadRecord::verify_catalog` may continue to expose `Result<(), _>` by discarding that result, while `load_pointer_for_head` consumes it directly instead of decrypting the envelope again; and
- the production encoder returns an internal publication artifact containing encrypted bytes, plaintext size, parsed generation/envelope version, the one SHA-256 digest, and the derived physical name. A crate-private `HeadRecord` constructor accepts this artifact plus the already-validated source catalog and required FRK version, checks generation/version/key consistency, and uses the artifact digest without decrypting or hashing the newly created envelope again.

These helpers are not general trust shortcuts. They are callable only where the same coordinator has just produced or authenticated the exact bytes. Public construction from arbitrary encrypted bytes continues to decrypt, validate, and bind the catalog before returning.

### 6. Allocation-light precondition coverage

Catalog entries are canonical and sorted by stable ID. `validate_precondition_coverage` should compare current and next entries with an ordered merge rather than constructing two full hash maps. Destination-vacancy lookup may use a bounded index only for the referenced parents or another measured allocation-light structure.

This preserves the rule that every changed source and every newly occupied existing-parent destination has a matching caller precondition. It changes representation cost, not authority semantics.

### 7. Unchanged-object validation

After a commit has successfully validated an object record, the cache may retain a non-secret validation record keyed by stable ID and the complete immutable object-body tuple: revision, physical name, content hash, kind, object-key epoch, and wrapped-DEK record. It may also retain the derived non-secret key-binding digest already used by `PreparedObjectRevision` validation.

On an exact authenticated snapshot hit:

- a byte-for-byte unchanged object tuple may reuse its cached key-binding result instead of unwrapping both current and next DEKs;
- a new or changed object must follow the existing prepared-revision token, key-binding, file-length, and encrypted-hash validation path; and
- every unchanged referenced object must still pass `open_regular_file_in` and the nonzero-length check on every commit.

Keeping the safe open preserves current detection of missing, zero-length, symlinked, replaced, or unexpectedly hard-linked object files. This first design does not introduce directory timestamp heuristics, retained file handles, or a weaker batch existence check. If the exact benchmark remains red, further object-file optimization requires a separate reviewed design proving equivalent file-identity and link-count semantics.

### 8. Commit flow

The steady-state normal commit becomes:

1. acquire `CoreCommitLock` and retain the existing acquisition timestamp;
2. revalidate pinned root and directory handles;
3. read and decode HEAD, receipt, and completion records;
4. derive and compare the required catalog/object-wrap cache identities, then select an exact cache hit or authenticate/decrypt the referenced catalog once on the full recovery path;
5. validate active FRK version and caller preconditions;
6. build the next already-validated immutable generation;
7. apply marker state through the validated-value path;
8. compare current/next with allocation-light precondition coverage;
9. validate prepared changes, reuse only exact cached unchanged-object bindings, and safely reopen every unchanged object;
10. run bounded canonical serialization and encryption, compute one publication digest, construct HEAD from the trusted publication artifact, and perform immutable catalog publication, HEAD-last publication, cleanup, fsync, and unlock exactly as today;
11. publish or clear the cache according to the durable result; and
12. deliver invalidation after unlock as today.

## Failure and recovery behavior

- Cache corruption or inconsistency can only cause a miss: exact pointer and identity checks gate every reuse.
- Wrong same-version active or retained key material cannot hit because the derived cache identity differs; the fallback authentication path then rejects it.
- A process crash loses the cache and returns to the existing disk-only recovery behavior.
- A concurrent coordinator cannot reuse stale state because it must win the kernel lock and then observe the current pointer tuple.
- Receipt-only, missing-HEAD-after-cutover, divergent receipt/completion, and mixed-FRK states bypass the cache.
- The cache is never persisted, serialized, transferred, or included in backup/restore.
- All existing failure-injection points and their HEAD-before/after visibility guarantees remain in force.

## Test strategy

Implementation is test-first. Focused regressions must prove:

1. a second same-head operation rereads pointer records but does not reread/decrypt/canonical-reencode the current catalog;
2. a cache-miss load decrypts and canonical-validates the referenced catalog exactly once while preserving every HEAD binding check;
3. publication constructs byte-identical HEAD and physical-name values from one digest without decrypting the coordinator's just-encrypted catalog;
4. wrong same-version active keys and wrong same-version retained receipt/completion keys miss the cache and fail normal authentication;
5. an external coordinator advancing HEAD invalidates the cache and loads the new authenticated catalog;
6. cutover recovery states, pointer disagreement, and FRK-version changes never use a stale snapshot;
7. a pre-HEAD failure retains only the prior valid cache, while a recovery-pending post-HEAD outcome clears it;
8. poisoned cache synchronization becomes a miss rather than a storage failure;
9. concurrent unlocked load/recovery and commit complete without cache/kernel-lock inversion or deadlock;
10. no test seam observes the cache mutex held during I/O, cryptography, build/hook/invalidation callbacks, or kernel-lock acquisition;
11. trusted encoding is byte-identical to existing canonical encoding, while untrusted non-canonical bytes still fail;
12. the validated marker path cannot construct an invalid marker or bypass graph validation for caller-created entries;
13. ordered precondition coverage rejects the same missing-source and missing-destination cases as the current implementation;
14. unchanged cached object bindings avoid repeated unwraps, while changed wrappers and wrong FRK bindings still fail;
15. missing, empty, symlinked, replaced, and unexpected-hard-link object files still fail on a cache hit; and
16. benchmark measurement still records lock acquisition first, HEAD publication last, complete `commit` wall time, full catalog bytes, and all strict provenance fields.

Required broader validation remains:

- `cargo +1.75.0 test --locked -p anima-corefs`
- `cargo +1.75.0 clippy --locked -p anima-corefs --all-targets -- -D warnings`
- `cargo fmt -p anima-corefs -- --check`
- `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; uv run --locked --project apps/server pytest apps/server/tests/test_corefs_catalog_benchmark.py -q`
- existing transaction, failure-injection, rotation, and benchmark integration targets
- `git diff --check`

## Benchmark sequence and acceptance

After correctness gates pass:

1. Run a non-reference 1-warm-up/5-sample diagnostic on a disposable target to catch gross regressions. This run is not acceptance evidence.
2. Commit the final code before creating reference evidence.
3. Archive the prior create-only target intact.
4. Produce a fresh private release binary and run the unchanged exact 30-warm-up/200-sample reference command on the approved profile.
5. Independently validate source commit, binary hash and file identity, committed Cargo.lock hash, target identity, exact argv, closed schema, fixture counts, catalog counts, object counts, and absence of temporary files.

PCF-002 clears Step 12 only when all existing p95, durable-write, and serialized-size gates pass. If this conservative implementation remains red after one measured implementation pass, stop and return for a separate architectural decision rather than stacking ad hoc benchmark-specific changes.

## Alternatives rejected for this revision

- **Prepare or encrypt outside the measured commit:** reduces lock time but does not honestly improve the complete public commit latency currently gated.
- **Delta or journal catalogs:** can provide better asymptotic scaling but changes recovery, retention, transfer, GC, and key-retirement design; it is too broad for this blocker-clearance pass.
- **Skip unchanged object-file opens:** faster but weakens current fail-closed layout and missing-object detection.
- **Weaken durability or benchmark evidence:** violates PCF-002 acceptance and is not an optimization.

## Expected implementation surface

- `packages/anima-corefs/src/transaction.rs`
- `packages/anima-corefs/src/catalog/v2.rs`
- `packages/anima-corefs/src/head.rs`
- focused CoreFS transaction/catalog tests
- `packages/anima-corefs/tests/catalog_benchmark.rs` only if measurement regressions need new assertions
- `apps/server/tests/test_corefs_catalog_benchmark.py` only if strict artifact validation must describe new non-timing metadata
- PCF-002/PCF-000 validation and activity evidence

The V2 catalog schema, benchmark fixture definitions, public API contracts, and durable artifact layout should remain unchanged.
