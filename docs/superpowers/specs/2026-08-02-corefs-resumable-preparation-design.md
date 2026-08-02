# CoreFS Resumable Preparation Design

**Date:** 2026-08-02
**Status:** Draft — approved direction, pending written-spec review
**Scope:** Add a bounded-memory, crash-resumable preparation protocol for large inactive CoreFS validation catalogs without weakening single-generation atomic publication
**Parent design:** [Portable Core Filesystem Design](2026-07-12-portable-core-filesystem-design.md)
**PRD:** [Portable Core Filesystem v1](../../prds/portable-core-filesystem-v1.md)
**Ticket:** [PCF-004](../../../tickets/portable-core-filesystem/PCF-004-diary-notes.md)

---

## 1. Decision

CoreFS will add one authenticated, encrypted, session-scoped preparation protocol for large converter writes. Object bodies are encrypted and durably recorded in bounded batches without changing `fs/VALIDATION_HEAD`. After all source objects and the final logical catalog intent have been verified, one exact-head finalization publishes the complete inactive catalog in a single validation generation.

The protocol persists only encrypted preparation state and already encrypted immutable objects. It never writes plaintext content, never makes a partial graph visible, never changes authoritative `fs/HEAD`, and never unfreezes public CoreFS mutation before PCF-008.

The user approved this persistent protocol instead of either:

- an in-memory-only preparation handle, which would restart large migrations and strand untracked objects after every crash; or
- multiple visible validation generations, which would violate the all-or-nothing migration contract.

## 2. Goals

1. Migrate a valid CoreFS corpus larger than 1 GiB with peak memory bounded by one object chunk, one bounded preparation batch, and bounded catalog metadata.
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
10. Finalization does not reread or materialize object bodies; it uses authenticated persisted descriptors and bounded catalog metadata.

## 5. Physical Layout

```text
.anima/fs/
  PREPARATION_HEAD
  preparations/
    <opaque-preparation-id>/
      snapshots/
        <sequence>-<ciphertext-hash>.prep.acore
      receipts/
        <opaque-receipt-id>.prep-receipt.acore
```

`PREPARATION_HEAD` is a small pointer analogous to `HEAD`: it contains only an opaque preparation ID, snapshot sequence, encrypted snapshot hash, envelope version, and required FRK version. It reveals no logical names, counts, source identifiers, or content hashes. The pointer is accepted only after the referenced encrypted snapshot is reopened, authenticated, and matched to the pointer.

Only one active `PREPARATION_HEAD` exists per single-owner Core. Immutable historical snapshots and receipts remain non-authoritative retention inventory until PCF-010 safely prunes them.

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

The decrypted snapshot is bounded to the same maximum plaintext size as a final catalog. If its descriptors cannot fit, the final CoreFS catalog could not fit either and preparation fails before publication.

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
    "inventoryDigest": "..."
  },
  "totals": {
    "objects": 120,
    "plaintextBytes": 2147483648
  },
  "prepared": [],
  "finalIntent": null
}
```

Each `prepared` record contains:

- stable object ID, revision, kind, and object-key epoch;
- opaque physical object name;
- encoded size and encrypted-file SHA-256;
- content hash and object-key binding;
- wrapped Object DEK record;
- exact authenticated envelope metadata digest;
- source fingerprint and converter format version; and
- preparation ordinal.

The descriptor schema is closed and length-bounded. Unknown fields, duplicate stable IDs, duplicate physical names, non-monotonic ordinals, wrong revisions, or mismatched hashes fail closed.

## 8. State Machine

### 8.1 Begin or resume

`begin_preparation_v1` accepts:

- an explicit expected validation head tuple, or explicit initial `None/None`;
- scope `pcf004-writing-v1`;
- owner/source identity and inventory digest; and
- the current unlocked FRK version.

If no active pointer exists, CoreFS creates a `collecting` snapshot and atomically publishes `PREPARATION_HEAD`. If a pointer exists, CoreFS authenticates it and returns the current state only when scope, owner, Core, FRK version, expected validation tuple, and source identity match. Otherwise it returns a typed conflict; it never replaces the active preparation implicitly.

### 8.2 Prepare a bounded batch

`prepare_batch_v1` requires exact preparation sequence/hash CAS and accepts a bounded iterator of object requests. Python streams one legacy body or attachment at a time into the native call; PyO3 must not first collect the complete corpus into `list[bytes]` or `Vec<Vec<u8>>`.

For each object, CoreFS:

1. validates IDs, revision, kind/content-type pairing, authenticated metadata, source fingerprint, and object-specific size limits;
2. streams envelope encryption through the existing chunked writer;
3. fsyncs and strictly reopens the immutable encrypted object;
4. creates the closed prepared descriptor; and
5. zeroizes transient plaintext and DEK material.

After the bounded batch succeeds, CoreFS writes a complete next encrypted preparation snapshot and atomically CAS-replaces `PREPARATION_HEAD`. A crash before pointer replacement leaves the prior snapshot authoritative and any newly written object unreachable. Retrying is safe and may create another unreachable ciphertext, but cannot duplicate a logical object in the active preparation.

### 8.3 Reconcile changing legacy source

Legacy SQLCipher remains authoritative before PCF-008. The converter therefore records a source fingerprint per logical object and a complete source inventory digest.

Before sealing, Python recomputes the inventory in a short consistent SQLCipher read transaction. If it changed, the converter prepares only added or changed objects, marks removed source objects absent from the final intent, and publishes another preparation snapshot. It repeats until the recomputed inventory matches the snapshot. It never finalizes a mixture of two unverified source inventories.

### 8.4 Seal final intent

`seal_preparation_v1` requires exact preparation CAS and the verified source inventory digest. It validates the complete folder/object graph, stable roles, policies, names, references, revision preconditions, and descriptor coverage.

The next encrypted `ready` snapshot contains the complete bounded logical catalog intent, its canonical intent digest, the exact expected validation head, and the descriptors it references. It contains no body plaintext.

Once ready, object preparation is closed. Any source change requires an explicit return to `collecting` through a new CAS snapshot, not an in-place mutation.

### 8.5 Finalize once

`finalize_preparation_v1` acquires the existing Core-wide commit lock and an active session operation guard. Under that authority it:

1. reloads and authenticates `PREPARATION_HEAD` and the `ready` snapshot;
2. reloads the exact expected `VALIDATION_HEAD` tuple;
3. reconstructs prepared revision tokens from encrypted descriptors;
4. safe-opens and verifies every referenced immutable encrypted object by size, ciphertext hash, object-key binding, wrapped-DEK record, revision, and envelope metadata digest;
5. builds and validates the complete next catalog from the sealed intent;
6. calls exactly one existing validation initialize/advance transaction; and
7. verifies the published validation catalog has the sealed canonical intent digest.

No object body is loaded during finalization. The validation catalog and preparation metadata remain within their existing bounded plaintext limits.

### 8.6 Complete the crash seam

`VALIDATION_HEAD` publication and `PREPARATION_HEAD` clearing cannot be one filesystem rename. Recovery therefore uses the sealed intent as a receipt:

- crash before validation publication: `ready` remains active and finalization retries;
- crash after validation publication but before preparation completion: recovery loads `VALIDATION_HEAD`, authenticates its catalog, compares generation/predecessor and canonical intent digest, then writes an encrypted `completed` receipt and clears `PREPARATION_HEAD` without republishing;
- a different validation head: typed conflict, no implicit overwrite or clear.

Clearing means an atomic remove/replace protocol with parent-directory durability matching existing pointer publication rules. A completed receipt is immutable and contains only encrypted audit/recovery facts.

## 9. Abandonment and Garbage

Abandonment is explicit and exact-CAS. `abandon_preparation_v1` writes an encrypted `abandoned` receipt naming the final authenticated preparation snapshot, then clears `PREPARATION_HEAD`. It never deletes prepared objects synchronously.

Prepared and crash-orphaned objects are eligible for PCF-010 pruning only when authenticated inventory proves they are referenced by none of:

- authoritative `HEAD` or retained committed catalogs;
- `VALIDATION_HEAD` or retained validation catalogs;
- active `PREPARATION_HEAD` snapshots; or
- retained completed/abandoned preparation receipts required by policy.

This intentionally prefers bounded encrypted garbage over accidental content loss.

## 10. FRK and Object-Key Rotation

FRK activation returns a typed `PreparationActive` error while `PREPARATION_HEAD` exists. The user or migration coordinator must resume/finalize or explicitly abandon the preparation first. V1 does not rewrap an in-flight preparation journal.

This rule prevents a preparation snapshot from straddling FRK generations and avoids persisting multiple decrypt-capable keyrings solely for migration. After rotation, a new preparation uses the active FRK version. Old FRKs remain governed by existing retained-catalog and backup gates; preparation snapshots and receipts join that authenticated retention inventory before PCF-010 retirement.

Targeted object-key rotation ignores unreachable prepared objects. Once an object becomes referenced by `VALIDATION_HEAD`, normal catalog-bound rotation rules apply.

## 11. Session and Concurrency Semantics

- Every begin, batch, seal, finalize, complete, and abandon call acquires the existing `CorefsSession` operation guard.
- `begin_close` rejects new calls; `close` waits only for the current bounded call and then releases leases.
- No native preparation handle or raw key must survive between calls.
- The Core-wide writer lock serializes pointer CAS and final publication, but body streaming does not hold the lock. Only the short preparation-snapshot publication is locked after a batch.
- One active preparation per Core avoids ambiguous ownership. Exact pointer CAS rejects concurrent draft import, unlock migration, or retry races; the caller reloads, merges source state, and retries.
- A callback failure after durable snapshot publication reports committed progress rather than rolling back the pointer in memory.

## 12. Python and PyO3 Boundary

PyO3 exposes the protocol only on the long-lived `CorefsSession`; top-level public mutations stay frozen.

The boundary uses streaming readers or one bounded Python bytes-like object per object. It must not accept the whole corpus as one JSON/base64 payload or materialize all bodies before checking a total. Metadata is canonical bounded JSON; binary content is passed separately from metadata.

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
- session closing/closed.

Errors are safe to retry only when explicitly classified retryable. Corrupt or mismatched authenticated state fails closed and requires operator-visible recovery; it is never reclassified as a missing head.

## 14. Validation

Required tests include:

1. a logical corpus above 1 GiB prepared with test-sized bounded batches while peak retained body memory stays below the configured batch ceiling;
2. eleven individually valid maximum-size attachments accepted without aggregate rejection or whole-corpus materialization;
3. crash injection before/after object write, preparation snapshot write, preparation pointer replace, validation catalog write, validation pointer replace, completion receipt, and preparation pointer clear;
4. restart from every crash seam with either safe resume or idempotent completion;
5. exact preparation and validation CAS conflicts;
6. source changes during collection and immediately before seal;
7. corrupt, missing, replayed, wrong-Core, wrong-FRK, and stale preparation snapshots;
8. descriptor tampering, missing object, ciphertext replacement, wrong wrapped DEK, and envelope metadata mismatch;
9. no body reread during finalization and no complete-corpus `Vec<Vec<u8>>`/Python-list materialization;
10. bounded memory and prompt `begin_close`/`close` behavior at batch boundaries;
11. finalization publishes exactly one validation generation and never changes authoritative `HEAD`;
12. crash-after-validation-publication completes without a second generation;
13. explicit abandonment leaves encrypted objects unreachable and records retention inventory;
14. FRK rotation rejects active preparation and succeeds after completion/abandonment;
15. retained preparation snapshots prevent unsafe FRK retirement and PCF-010 GC;
16. unchanged reruns reuse prepared descriptors and exact source fingerprints without rewriting bodies;
17. draft import and unlock migration conflict/retry without losing either source; and
18. existing PCF-004, CoreFS library, rotation, recovery, session lifecycle, desktop, and build suites remain green.

## 15. Rollout

1. Add preparation cryptographic format, pointer, and failure-injection tests behind private converter APIs.
2. Add bounded object preparation and restart recovery.
3. Add sealed intent and exact-head finalization.
4. Add abandonment, retention inventory, rotation exclusion, and session-close coverage.
5. Replace the current whole-corpus PCF-004 transport with the preparation protocol.
6. Re-run PCF-004 conversion, API, desktop, native, recovery, and large-corpus validation.
7. Keep `VALIDATION_HEAD` inactive and public mutation frozen until PCF-008.

## 16. Acceptance

The preparation protocol is accepted only when:

- legitimate writing corpora above 1 GiB prepare with bounded peak memory;
- every crash seam resumes or completes without partial visibility;
- one final exact-CAS operation publishes the complete inactive catalog;
- no plaintext, raw DEK, logical path, or private content leaks to disk, Runtime, or logs;
- active preparation safely gates FRK rotation and retained-state cleanup;
- session shutdown is bounded at per-call boundaries;
- corrupted or conflicting state fails closed with typed recovery guidance; and
- the complete PCF-004 acceptance suite and required broader CoreFS/build gates pass.
