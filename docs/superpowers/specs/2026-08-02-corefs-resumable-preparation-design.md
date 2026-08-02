# CoreFS Resumable Preparation Design

**Date:** 2026-08-02
**Status:** Draft — approved direction, pending written-spec review
**Scope:** Add a bounded-memory, crash-resumable preparation protocol for large inactive CoreFS validation catalogs without weakening single-generation atomic publication
**Parent design:** [Portable Core Filesystem Design](2026-07-12-portable-core-filesystem-design.md)
**PRD:** [Portable Core Filesystem v1](../../prds/portable-core-filesystem-v1.md)
**Ticket:** [PCF-004](../../../tickets/portable-core-filesystem/PCF-004-diary-notes.md)

---

## 1. Decision

CoreFS will add one authenticated, encrypted, Core-scoped preparation protocol operated through an unlock session for large converter writes. Object bodies are encrypted and durably recorded one bounded object at a time without changing `fs/VALIDATION_HEAD`. After all source objects and the final logical catalog intent have been verified under a source mutation fence, one exact-head finalization publishes the complete inactive catalog in a single validation generation.

The protocol persists only encrypted preparation state and already encrypted immutable objects. It never writes plaintext content, never makes a partial graph visible, never changes authoritative `fs/HEAD`, and never unfreezes public CoreFS mutation before PCF-008.

The user approved this persistent protocol instead of either:

- an in-memory-only preparation handle, which would restart large migrations and strand untracked objects after every crash; or
- multiple visible validation generations, which would violate the all-or-nothing migration contract.

## 2. Goals

1. Migrate a valid CoreFS corpus larger than 1 GiB with peak memory bounded by one object input, one encryption chunk, and separately bounded catalog/preparation metadata.
2. Resume safely after process crash, application restart, or clean session shutdown.
3. Publish exactly one complete next `VALIDATION_HEAD` generation after all prepared objects and source state are verified.
4. Preserve exact validation-head CAS, object revision preconditions, stable IDs, authenticated metadata, and existing public-mutation freeze.
5. Define explicit abandonment, retained-object cleanup, FRK rotation, and session lifecycle behavior.

## 3. Non-Goals

- Change `fs/HEAD`, cut over diary/notes authority, or activate public CoreFS mutation. PCF-008 owns cutover.
- Add cloud sync, multi-device concurrency, or multiple simultaneous preparation owners.
- Delete unreachable encrypted objects immediately. Retention-safe physical pruning remains PCF-010 work.
- Make preparation state portable independently of its Core. It is encrypted CoreFS state bound to the same `core_id` and FRK generation.
- Generalize this into an arbitrary transaction log for every future mutation shape. V1 supports converter-owned inactive validation publication.

## 4. Invariants

1. `VALIDATION_HEAD` is unchanged throughout collection and sealing.
2. `VALIDATION_HEAD` changes at most once during successful finalization.
3. `fs/HEAD`, `CUTOVER_RECEIPT`, and `CUTOVER_COMPLETE` are never touched.
4. Every persisted preparation snapshot and prepared object is encrypted and authenticated before its pointer becomes durable.
5. A missing, corrupt, stale, wrong-Core, wrong-FRK, or replayed preparation pointer fails closed; it is never treated as an empty preparation.
6. A prepared object is unusable unless its authenticated descriptor, encrypted file, source fingerprint, and final catalog intent all agree.
7. A crash may leave unreachable encrypted files, but it cannot expose a partial logical graph or lose legacy authority.
8. FRK activation cannot proceed while an active preparation references the current FRK generation.
9. Session close waits only for the currently bounded native call. No required correctness state exists solely in process memory between calls.
10. Finalization may stream each encrypted envelope to recheck its ciphertext hash, but it never decrypts or materializes plaintext bodies.
11. The source inventory generation and digest are rechecked while a SQLCipher write fence excludes every legacy writing mutation through validation-pointer publication.

## 5. Physical Layout

```text
.anima/fs/
  PREPARATION_HEAD
  preparation-quarantine/
    <pointer-hash>.prep-pointer
  preparations/
    <opaque-preparation-id>/
      manifests/
        <sequence>-<segment>-<ciphertext-hash>.prep-manifest.acore
      snapshots/
        <sequence>-<ciphertext-hash>.prep.acore
      receipts/
        <opaque-receipt-id>.prep-receipt.acore
```

`PREPARATION_HEAD` is a small pointer analogous to `HEAD`: it contains only an opaque preparation ID, snapshot sequence, encrypted snapshot hash, envelope version, and required FRK version. It reveals no logical names, counts, source identifiers, or content hashes. The pointer is accepted only after the referenced encrypted snapshot is reopened, authenticated, and matched to the pointer.

Only one active `PREPARATION_HEAD` exists per single-owner Core. Immutable descriptor/intent manifest segments let a small authoritative snapshot reference preparation metadata without combining the entire final catalog and all larger prepared descriptors into one plaintext envelope. Immutable historical manifests, snapshots, receipts, and quarantined pointers remain non-authoritative retention inventory until PCF-010 safely prunes them.

## 6. Cryptographic Domain

CoreFS derives a dedicated preparation subkey from the Filesystem Root Key with versioned HKDF label `anima-corefs-preparation-v1`. The preparation key is distinct from object-wrap, catalog, and search subkeys and is zeroized with the unlock session.

Preparation snapshot AEAD associated data binds:

- `core_id`;
- preparation schema and envelope versions;
- opaque preparation ID;
- snapshot sequence;
- required FRK version; and
- state (`collecting`, `ready`, `completed`, or `abandoned`).

Prepared object envelopes continue using the normal per-object DEK, object AAD, chunk authentication, and immutable physical layout. The preparation snapshot stores only the already wrapped Object DEK and authenticated descriptor needed to reconstruct a `PreparedObjectRevision`; it never stores a plaintext DEK.

## 7. Preparation Snapshot V1

The decrypted head snapshot and every encrypted manifest segment have independent plaintext ceilings. The total number and size of manifest segments are derived from the maximum valid catalog entry count plus the worst-case closed descriptor and final-intent encodings. CoreFS rejects a preparation before publication if those explicit ceilings are exceeded. A valid final catalog is never assumed to fit a same-sized preparation snapshot because prepared descriptors contain additional fields.

```json
{
  "schemaVersion": 1,
  "preparationId": "<opaque-id>",
  "sequence": 7,
  "state": "collecting",
  "scope": "pcf004-writing-v1",
  "requiredFrkVersion": 3,
  "createdAt": "...",
  "updatedAt": "...",
  "expectedValidationHead": {
    "generation": 4,
    "catalogHash": "..."
  },
  "source": {
    "ownerId": "<opaque-owner-id>",
    "inventoryVersion": 1,
    "mutationGeneration": 42,
    "inventoryDigest": "..."
  },
  "totals": {
    "objects": 120,
    "plaintextBytes": 2147483648
  },
  "manifestRoot": "...",
  "manifestSegments": [],
  "finalIntentRoot": null
}
```

Each prepared-descriptor record in an authenticated manifest segment contains:

- stable object ID, revision, kind, and object-key epoch;
- opaque physical object name;
- encoded size and encrypted-file SHA-256;
- content hash and object-key binding;
- wrapped Object DEK record;
- exact authenticated envelope metadata digest;
- source fingerprint and converter format version; and
- preparation ordinal.

The descriptor schema is closed and length-bounded. The head snapshot authenticates the ordered segment IDs, ciphertext hashes, per-segment counts, cumulative count/size, and a Merkle-style manifest root. Unknown fields, missing/reordered segments, duplicate stable IDs, duplicate physical names, non-monotonic ordinals, wrong revisions, or mismatched hashes fail closed. Final intent uses separately bounded authenticated segments and a distinct root so it can be reconstructed without duplicating the complete graph inside the head snapshot.

## 8. State Machine

### 8.1 Begin or resume

`begin_preparation_v1` accepts:

- an explicit expected validation head tuple, or explicit initial `None/None`;
- scope `pcf004-writing-v1`;
- owner/source identity, monotonic mutation generation, and inventory digest; and
- the current unlocked FRK version.

If no active pointer exists, CoreFS creates a `collecting` snapshot and atomically publishes `PREPARATION_HEAD`. If a pointer exists, CoreFS authenticates it and returns the current state only when scope, owner, Core, FRK version, expected validation tuple, and source identity match. A newer source mutation generation is reconciled through an exact-CAS snapshot transition rather than treated as a second owner. Other mismatches return a typed conflict; the protocol never replaces the active preparation implicitly.

### 8.2 Prepare one bounded object

`prepare_object_v1` requires exact preparation sequence/hash CAS and accepts exactly one closed metadata request plus one object body as a bounded Python bytes-like value or native streaming reader. The migration coordinator loops at Python level. PyO3 never constructs the existing whole-graph `ValidationBatch`, `list[bytes]`, `Vec<Vec<u8>>`, or an intermediate encrypted `Vec<u8>`.

For each object, CoreFS:

1. validates IDs, revision, kind/content-type pairing, authenticated metadata, source fingerprint, and object-specific size limits;
2. streams envelope encryption directly into the staged immutable file through the existing chunked writer;
3. fsyncs and strictly reopens the immutable encrypted object;
4. creates the closed prepared descriptor; and
5. zeroizes transient plaintext and DEK material.

After the object succeeds, CoreFS writes or extends a bounded immutable descriptor manifest, writes the small next encrypted preparation snapshot, and atomically CAS-replaces `PREPARATION_HEAD`. A crash before pointer replacement leaves the prior snapshot authoritative and any newly written object/manifest unreachable. Retrying is safe and may create another unreachable ciphertext, but cannot duplicate a logical object in the active preparation. Hard per-object, per-manifest, segment-count, and cumulative descriptor ceilings are checked before the new pointer is published.

### 8.3 Reconcile changing legacy source

Legacy SQLCipher remains authoritative before PCF-008. The writing schema therefore maintains a monotonic source mutation generation that every diary, note, draft, and related attachment mutation increments in the same SQLCipher transaction. The converter records that generation, a source fingerprint per logical object, and a complete source inventory digest from one consistent read transaction.

Before sealing, Python recomputes the generation and inventory in a short consistent SQLCipher read transaction. If either changed, the converter prepares only added or changed objects, removes stale logical descriptors from the active manifest root, marks removed source objects absent from the final intent, and publishes another preparation snapshot. Physical ciphertext removed from the active root remains retention-managed garbage. Reconciliation repeats until both generation and digest match the snapshot; it never finalizes a mixture of two source generations.

### 8.4 Seal final intent

`seal_preparation_v1` requires exact preparation CAS plus the verified source mutation generation and inventory digest. It validates the complete folder/object graph, stable roles, policies, names, references, revision preconditions, and descriptor coverage.

The next encrypted `ready` snapshot contains only the authenticated final-intent root, ordered intent segment IDs/hashes/counts, canonical intent digest, exact expected validation head, and descriptor-manifest root. The complete bounded logical catalog intent remains in separately bounded encrypted segments and contains no body plaintext. Finalization reconstructs and validates the intent from those authenticated segments.

Once ready, object preparation is closed. A detected source change requires an explicit return to `collecting` through a new CAS snapshot, not an in-place mutation.

### 8.5 Finalize once

The Python coordinator first acquires its keyed migration mutex and a SQLCipher `BEGIN IMMEDIATE` transaction, which excludes every legacy writing mutation across processes. It recomputes the source mutation generation and inventory digest under that write fence. A mismatch returns to collection without calling native finalization. On a match, `finalize_preparation_v1` acquires the existing Core-wide commit lock and an active session operation guard while the SQLCipher fence remains held. Under that authority it:

1. reloads and authenticates `PREPARATION_HEAD` and the `ready` snapshot;
2. reloads the exact expected `VALIDATION_HEAD` tuple;
3. reconstructs prepared revision tokens from encrypted descriptors;
4. safe-opens and boundedly streams every referenced immutable encrypted envelope to verify size and ciphertext hash, then verifies object-key binding, wrapped-DEK record, revision, and authenticated envelope-metadata digest without decrypting its plaintext body;
5. builds and validates the complete next catalog from the sealed intent;
6. calls exactly one existing validation initialize/advance transaction; and
7. verifies the published validation catalog has the sealed canonical intent digest.

No plaintext object body is decrypted or materialized during finalization. Ciphertext is reread sequentially with one fixed-size buffer. The validation catalog, descriptor manifests, and preparation metadata remain within their explicit independent plaintext limits. V1 deliberately holds the SQLCipher write fence through this integrity pass and pointer publication; this favors a simple cross-store correctness boundary over writer latency. PCF-008 may optimize the fence with an anchored native validation lease only if it preserves the same no-write interval and crash semantics.

### 8.6 Complete the crash seam

`VALIDATION_HEAD` publication and `PREPARATION_HEAD` clearing cannot be one filesystem rename. Recovery therefore uses the sealed intent as a receipt. Completion and abandonment receipt IDs are deterministic keyed digests of preparation ID, terminal state, and final snapshot hash:

- crash before validation publication: `ready` remains active and finalization retries;
- crash after validation publication but before preparation completion: recovery loads `VALIDATION_HEAD`, authenticates its catalog, compares generation/predecessor and canonical intent digest, then creates or authenticates the deterministic encrypted `completed` receipt and clears `PREPARATION_HEAD` without republishing;
- a different validation head: typed conflict, no implicit overwrite or clear.

Clearing means an atomic remove/replace protocol with parent-directory durability matching existing pointer publication rules. If the deterministic receipt already exists, retry must safe-open it and require an exact authenticated match; a mismatch fails closed. A completed receipt is immutable and contains only encrypted audit/recovery facts.

## 9. Abandonment and Garbage

Abandonment is explicit and exact-CAS. `abandon_preparation_v1` creates or authenticates the deterministic encrypted `abandoned` receipt naming the final authenticated preparation snapshot, then clears `PREPARATION_HEAD`. A crash or retry at either write seam follows the same existing-receipt rules as completion. It never deletes prepared objects synchronously.

A corrupt, replayed, wrong-Core, or otherwise unauthenticatable `PREPARATION_HEAD` cannot use normal abandonment. An explicit operator-only `quarantine_preparation_v1` path acquires the Core commit lock, requires the byte-exact expected pointer hash, durably preserves the original pointer bytes at the Core-global hash-addressed `preparation-quarantine/<pointer-hash>.prep-pointer`, writes an authenticated quarantine receipt under the active preparation subkey, and only then clears the live pointer by byte-exact CAS. No filesystem component or destination is ever derived from unauthenticated pointer fields. Quarantine does not assert that unknown prepared objects are abandoned and makes all preparation inventory not proven unrelated ineligible for GC. All possibly required FRK generations remain retained until PCF-010 or a later recovery proves the inventory safe.

Prepared and crash-orphaned objects are eligible for PCF-010 pruning only when authenticated inventory proves they are referenced by none of:

- authoritative `HEAD` or retained committed catalogs;
- `VALIDATION_HEAD` or retained validation catalogs;
- active `PREPARATION_HEAD` snapshots; or
- retained completed/abandoned preparation receipts required by policy.

This intentionally prefers bounded encrypted garbage over accidental content loss.

## 10. FRK and Object-Key Rotation

FRK activation returns a typed `PreparationActive` error whenever `PREPARATION_HEAD` exists, including when it is corrupt or unauthenticatable. The user or migration coordinator must resume/finalize or explicitly abandon an authenticated preparation first. A corrupt pointer requires the operator-only quarantine transition. V1 does not rewrap an in-flight preparation journal.

This rule prevents a preparation snapshot from straddling FRK generations and avoids persisting multiple decrypt-capable keyrings solely for migration. After normal completion or abandonment, a new preparation uses the active FRK version. Quarantine permits later activation only while conservatively retaining all possibly required old FRKs and forbids their retirement. Preparation manifests, snapshots, receipts, and quarantine inventory join the authenticated retention gates before PCF-010 retirement.

Targeted object-key rotation ignores unreachable prepared objects. Once an object becomes referenced by `VALIDATION_HEAD`, normal catalog-bound rotation rules apply.

## 11. Session and Concurrency Semantics

- Every begin, object-prepare, seal, finalize, complete, abandon, and quarantine call acquires the existing `CorefsSession` operation guard.
- `begin_close` rejects new calls; `close` waits only for the current bounded call and then releases leases.
- No native preparation handle or raw key must survive between calls.
- The Core-wide writer lock serializes pointer CAS and final publication, but one-object body encryption does not hold the lock. Only the short manifest/snapshot pointer publication is locked after each object.
- One active preparation per Core avoids ambiguous ownership. Exact pointer CAS rejects concurrent draft import, unlock migration, or retry races; the caller reloads, merges source state, and retries.
- A callback failure after durable snapshot publication reports committed progress rather than rolling back the pointer in memory.

## 12. Python and PyO3 Boundary

PyO3 exposes the protocol only on the long-lived `CorefsSession`; top-level public mutations stay frozen.

The boundary adds a new one-object preparation input rather than adapting `ValidationBatchObject` or `ValidationBatch`. It uses a native streaming reader or one bounded Python bytes-like object per call, with the size ceiling checked before allocation/copy wherever the Python buffer protocol permits. It must not accept the whole corpus as one JSON/base64 payload, materialize all bodies before checking a total, or build an intermediate encrypted byte vector. Metadata is canonical bounded JSON; binary content is passed separately and streamed directly to the staged file.

Python owns:

- SQLCipher source inventory and per-record fingerprints;
- diary/note/draft codecs and sanitizer/media extraction;
- source-change reconciliation;
- private migration checkpoint/status projection; and
- scheduling retries after unlock.

Rust owns:

- preparation cryptography and pointer durability;
- immutable object streaming and descriptor validation;
- exact-CAS state transitions;
- final graph/catalog validation;
- final validation publication and crash recovery; and
- lifecycle/rotation exclusion.

The private migration checkpoint may expose state, progress, and typed errors, but never logical paths, titles, filenames, body hashes, wrapped keys, or plaintext content in Runtime columns or logs.

## 13. Error Model

The native boundary returns typed errors for:

- preparation missing, active, corrupt, stale, wrong scope, wrong owner, or wrong FRK;
- preparation CAS conflict;
- validation-head CAS conflict;
- source inventory changed;
- descriptor/object mismatch;
- object or metadata size limit;
- graph, role, policy, or reference invalidity;
- final intent mismatch;
- finalization already completed with a different intent;
- active preparation blocking FRK rotation; and
- preparation quarantined or quarantine CAS/receipt mismatch; and
- session closing/closed.

Errors are safe to retry only when explicitly classified retryable. Corrupt or mismatched authenticated state fails closed and requires operator-visible recovery; it is never reclassified as a missing head.

## 14. Validation

Required tests include:

1. a logical corpus above 1 GiB prepared one bounded object at a time while peak retained body memory stays below the configured per-object ceiling;
2. eleven individually valid maximum-size attachments accepted without aggregate rejection or whole-corpus materialization;
3. crash injection before/after object write, descriptor/intent manifest write, preparation snapshot write, preparation pointer replace, validation catalog write, validation pointer replace, completion/abandon receipt, and preparation pointer clear;
4. restart from every crash seam with either safe resume or idempotent completion;
5. exact preparation and validation CAS conflicts;
6. source changes during collection, after seal, and immediately before validation-pointer publication, proving the SQLCipher mutation fence rejects stale finalization;
7. corrupt, missing, replayed, wrong-Core, wrong-FRK, and stale preparation snapshots;
8. descriptor tampering, missing object, ciphertext replacement, wrong wrapped DEK, and envelope metadata mismatch;
9. bounded ciphertext streaming during finalization with no plaintext decryption/materialization and no complete-corpus `Vec<Vec<u8>>`/Python-list materialization;
10. bounded memory, immediate rejection of new work after `begin_close`, and `close` draining only the active per-object or finalization call;
11. finalization publishes exactly one validation generation and never changes authoritative `HEAD`;
12. crash-after-validation-publication completes without a second generation;
13. explicit abandonment is idempotent across both receipt/clear crash seams, leaves encrypted objects unreachable, and records retention inventory;
14. FRK rotation rejects active or corrupt preparation state, succeeds after completion/abandonment, and permits activation after quarantine only while old-FRK retirement remains blocked;
15. retained preparation snapshots prevent unsafe FRK retirement and PCF-010 GC;
16. unchanged reruns reuse prepared descriptors and exact source fingerprints without rewriting bodies;
17. draft import and unlock migration conflict/retry without losing either source; and
18. existing PCF-004, CoreFS library, rotation, recovery, session lifecycle, desktop, and build suites remain green.

## 15. Rollout

1. Add separate preparation snapshot/manifest cryptographic formats, pointer, and failure-injection tests behind private converter APIs.
2. Add the SQLCipher writing mutation generation/fence and prove every legacy writer participates.
3. Add one-object bounded preparation and restart recovery without the current whole-graph containers.
4. Add sealed intent and exact-head finalization under the source write fence.
5. Add deterministic completion/abandonment, corrupt-head quarantine, retention inventory, rotation exclusion, and session-close coverage.
6. Replace the current whole-corpus PCF-004 transport with the preparation protocol.
7. Re-run PCF-004 conversion, API, desktop, native, recovery, and large-corpus validation.
8. Keep `VALIDATION_HEAD` inactive and public mutation frozen until PCF-008.

## 16. Acceptance

The preparation protocol is accepted only when:

- legitimate writing corpora above 1 GiB prepare with bounded peak memory;
- every crash seam resumes or completes without partial visibility;
- one final exact-CAS operation publishes the complete inactive catalog;
- no legacy writing mutation can cross the verified source generation-to-validation-publication fence;
- no plaintext, raw DEK, logical path, or private content leaks to disk, Runtime, or logs;
- active preparation safely gates FRK rotation and retained-state cleanup;
- session shutdown is bounded at per-call boundaries;
- corrupted or conflicting state fails closed with typed recovery guidance; and
- the complete PCF-004 acceptance suite and required broader CoreFS/build gates pass.
