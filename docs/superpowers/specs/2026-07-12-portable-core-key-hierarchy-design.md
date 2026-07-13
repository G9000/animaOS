# Portable Core Key Hierarchy and Rotation Design

**Date:** 2026-07-12  
**Status:** Approved
**Scope:** Key derivation, wrapping, compromise boundaries, recovery, and resumable rotation for the SQLCipher Soul and encrypted Core Filesystem  
**Parent design:** [Portable Core Filesystem](2026-07-12-portable-core-filesystem-design.md)

---

## 1. Goal

Define the cryptographic lifecycle that the parent storage design depends on without inventing a second passphrase, placing raw keys in the manifest, or using one data-encryption key directly for every object.

This design builds on the repository's existing primitives:

- Argon2id for passphrase/recovery-secret key derivation
- AES-256-GCM for authenticated key wrapping and payload encryption
- HKDF-SHA256 with explicit domain separators
- random 32-byte data-encryption keys
- 12-byte unique AES-GCM nonces

Exact serialization is an implementation detail; key roles, separation, associated data, activation order, and recovery behavior are requirements.

## 2. Invariants

1. The passphrase and recovery phrase are never stored.
2. Passphrase change rewraps keyslots; it does not re-encrypt every Soul/CoreFS byte.
3. Soul and CoreFS never share a raw encryption key.
4. Catalogs and content objects never use the same derived encryption key.
5. Every content object has a random object DEK; large attachments do not share a global payload DEK.
6. AES-GCM nonce reuse under the same key is forbidden and tested.
7. Keyslot AAD binds Core ID, opaque owner ID, domain, purpose, and key version.
8. Object base AAD binds Core ID, object ID, revision, kind, envelope version, and object-key epoch; metadata/body frame AAD additionally binds the framing contract below.
9. A new key version is decryptable through both passphrase and recovery paths before it can become active.
10. Old key material remains decrypt-only until every retained catalog/object that needs it is either rewrapped, re-encrypted, or intentionally pruned.
11. Local ANIMA CORE artifacts derive an archive-layer key independently from Soul and Filesystem Root keys; compromising one archive key does not expose another artifact or change live key material.
12. CoreFS envelope, nonce, key-wrapping, and atomic key-publication primitives are implemented in Rust in `packages/anima-corefs`; Python orchestrates credential flows through the existing `anima-core` native extension and never becomes a second cryptographic implementation.

## 3. Root Unlock Hierarchy

Logical passphrase path:

```text
User passphrase
  -> Argon2id(passphrase, keyslot salt + recorded parameters)
  -> password KEK (memory only)
      -> unwrap SQLCipher Soul key
      -> unwrap Filesystem Root Key (FRK)
      -> unwrap any retained decrypt-only FRK versions
      -> open SQLCipher and unwrap Soul-internal domain DEKs from `soul_keyslots`
```

Logical recovery path:

```text
Recovery phrase
  -> Argon2id(recovery phrase, recovery-keyslot salt + recorded parameters)
  -> recovery KEK (memory only)
      -> unwrap SQLCipher Soul key
      -> unwrap Filesystem Root Key (FRK)
      -> unwrap any retained decrypt-only FRK versions
      -> open SQLCipher and unwrap recovery-wrapped Soul-internal domain DEKs from `soul_keyslots`
```

The current `wrap_dek` implementation derives one KEK per wrapped record using an independent salt. V1 may retain that physical representation. The diagram describes trust flow, not a requirement to reuse one KEK byte string across all slots.

## 4. Manifest Keyslots

The plaintext manifest contains no raw key or private profile data. It may contain:

- Core ID and opaque owner ID
- keyslot purpose/domain/version/status
- Argon2id salt and cost parameters
- AES-GCM wrapping nonce/tag/ciphertext
- cipher/KDF identifiers
- rotation state containing no private content

Required keyslot purposes:

| Purpose | Password-wrapped | Recovery-wrapped | Notes |
|---|---:|---:|---|
| SQLCipher Soul key | yes | yes | opens the Soul database |
| active Filesystem Root Key | yes | yes | derives catalog/object-wrap/search subkeys |
| decrypt-only Filesystem Root Key | yes while retained | yes while retained | opens retained old catalogs/key wraps during rotation |

Existing field-level Soul domain DEKs migrate from application-coupled `user_keys` rows into a Soul-internal `soul_keyslots` table keyed by opaque owner ID, domain, wrapping path, and version. They remain inside SQLCipher because they protect Soul fields, but no longer depend on the application `users` table. Every active domain has both password- and recovery-wrapped rows. Unlock first opens SQLCipher through the manifest Soul keyslot, then unwraps these internal domain DEKs. Normal `full` recovery is incomplete unless every active Soul domain and required Filesystem Root Key can be opened; intentionally scoped `soul` and `fs` artifacts use the completeness rules in Section 7.5.

Keyslot wrapping AAD:

```text
anima-keyslot-v1:
  core=<core-id>:
  owner=<opaque-owner-id>:
  purpose=<soul|filesystem-root>:
  version=<n>:
  path=<password|recovery>
```

Soul-internal domain-keyslot AAD uses a separate versioned label and binds Core ID, opaque owner ID, Soul domain, key version, and password/recovery path. Reusing the manifest keyslot AAD label for Soul field-domain keys is forbidden.

## 5. Content Subkeys and Object Keys

After the FRK is unwrapped, HKDF-SHA256 derives independent subkeys with fixed versioned labels:

```text
FRK version N
  -> HKDF(info="anima-corefs-object-wrap-v1") -> Object Key-Wrapping Key (OKWK)
  -> HKDF(info="anima-corefs-catalog-v1")     -> Catalog Root Key
  -> HKDF(info="anima-corefs-search-v1")      -> Blind Search Key
```

The derived keys are held only in the unlock session and cleared on lock.

### 5.1 Object encryption

- Creating an object generates a random 32-byte Object DEK.
- The Object DEK encrypts that object's metadata and body-chunk frames using fresh random 96-bit nonces and revision/frame-bound AAD.
- The committed encrypted catalog stores the Object DEK wrapped by the active OKWK, bound to object ID, object-key epoch, and FRK version.
- Attachments/gallery binaries use the same per-object pattern; they never use the FRK or OKWK directly for payload encryption.
- A normal edit can reuse the current Object DEK with a new nonce. A targeted object-key rotation generates a new Object DEK and publishes a new encrypted object revision/key epoch.

#### Envelope V1 frame contract

- The encrypted metadata frame declares `chunkingVersion=1`, a 4-MiB plaintext chunk size, body length, chunk count, and whole-body SHA-256. Metadata plaintext is capped at 1 MiB.
- A body is capped at 2,048 chunks / 8 GiB; zero-length bodies have no body frames.
- Metadata AAD is the base object AAD plus `frame=metadata` and chunking version.
- Body-chunk AAD is the base object AAD plus `frame=body`, SHA-256 of the complete encrypted metadata frame, zero-based chunk index, declared chunk count, plaintext offset/length, total body length, and final flag.
- Every frame nonce is independently generated by the OS CSPRNG and must be unique within the revision. Deterministic prefix/counter or ID/timestamp-derived nonces are not allowed in V1.
- A writer authenticates/encrypts from a bounded input stream into an encrypted temporary sibling and publishes only the complete verified object. A reader authenticates metadata first and each body chunk before releasing its plaintext. Full reads additionally verify count, length, final flag, and whole-body hash; range reads authenticate every intersecting chunk and never claim whole-body verification unless all chunks were read.
- The framing parser rejects oversized declarations before allocation and rejects missing, duplicated, reordered, spliced, or trailing frames.

### 5.2 Catalog encryption

Each catalog generation uses a generation-specific key derived from the Catalog Root Key:

```text
catalog_key = HKDF(
  catalog_root_key,
  info="anima-catalog-generation-v1:<generation>"
)
```

The catalog generation and hash are authenticated. Catalog keys are not used for object payloads or keyslot wrapping.

### 5.3 Blind-search key

The Blind Search Key creates HMAC tokens for the optional PostgreSQL blind index. Plain normalized terms and the key are never persisted. Search computes query tokens only after unlock and decrypts/verifies candidate objects in memory.

## 6. Compromise Boundaries

| Compromised material | Exposure |
|---|---|
| passphrase without Core manifest/keyslots | no direct content; offline guessing still depends on obtaining keyslots |
| one password/recovery KEK | keyslots derived with that exact salt/parameters; KEKs are not reused across independently salted slots in the current representation |
| SQLCipher Soul key | SQLCipher Soul contents and, when the attacker also has the matching instance-local Runtime rows/ID, operational payloads sealed under the derived Runtime Sealing Key; CoreFS object payloads remain separate |
| active FRK | all content whose Object DEKs/catalogs are wrapped or derived under that FRK version, including retained revisions reachable through retained catalogs |
| one Object DEK | all revisions encrypted in that object's current key epoch; other objects remain separate |
| one catalog-generation key | that encrypted catalog generation; object payloads still require their Object DEKs |
| Blind Search Key | blind-token dictionary/equality analysis for that Core; not object decryption by itself |
| one PostgreSQL blind-token table without key | equality/frequency leakage and opaque object associations, not plaintext terms/content |
| instance-local PostgreSQL sealed payloads without SQLCipher Soul key | routing/status metadata and ciphertext only; no operational payload plaintext |

An FRK is intentionally the Core Filesystem root of trust. Per-object DEKs limit isolated object-key compromise and make targeted rekey/deletion possible, but FRK compromise exposes every Object DEK wrapped by that root version.

## 7. Passphrase Change and Recovery

### 7.1 Full-Core passphrase change

For a normal complete ANIMA CORE, passphrase credential generations span both manifest root keyslots and SQLCipher `soul_keyslots`; changing only one store is invalid. Partial recovery modes use Section 7.5 and may never silently promote themselves to `full`.

1. Unlock all active/decrypt-only manifest root keys and every active Soul-domain DEK with the current passphrase.
2. Allocate password credential generation `N+1` and derive independent new password KEKs/salts for every manifest and Soul-domain slot.
3. In one SQLCipher transaction, write `pending` password wrappers for every active Soul-domain DEK under generation `N+1`; retain generation `N` rows.
4. Write `pending` generation `N+1` manifest wrappers for the SQLCipher Soul key and all required FRK versions; retain generation `N` slots.
5. Reload both stores from disk and run an isolated close/reopen verifier using the new passphrase. It must unwrap the pending SQLCipher key, FRKs, and every pending Soul-domain DEK and confirm they match the in-memory source keys.
6. Atomically set manifest `active_password_credential_generation=N+1` and mark generation `N` decrypt-only.
7. In SQLCipher, promote generation `N+1` Soul-domain rows to active and generation `N` rows to decrypt-only.
8. Verify another normal close/reopen with the new passphrase before a later cleanup transaction removes generation `N` password wrappers.

If a crash occurs after step 6 but before step 7, unlock follows the manifest active credential generation and accepts matching verified `pending` Soul rows, then finalizes their status. Before step 6, generation `N` remains authoritative. This is the cross-store commit rule; no implementation may delete generation `N` while `N+1` is only partially represented or unverified.

No Soul database, catalog, object, or attachment payload is re-encrypted merely because the passphrase changed.

### 7.2 Full-Core recovery

1. Derive recovery KEKs from the supplied recovery phrase and stored recovery-keyslot parameters.
2. Unwrap the SQLCipher Soul key and every required FRK version.
3. Verify the active Soul and committed catalog before changing credentials.
4. Create and verify a complete new password credential generation across manifest root keyslots and every SQLCipher Soul-domain keyslot using Section 7.1.
5. Preserve or explicitly rotate recovery wrapping according to the recovery-product flow.

Full-Core recovery is incomplete if it can open SQLCipher but not the committed content catalog, or vice versa. That rule does not reject an intentionally scoped artifact whose authenticated payload kind and manifest explicitly declare `soul` or `fs`.

### 7.3 Local ANIMA CORE archive keys

The `anima_core_v2` transfer layer protects record framing, artifact metadata, and multipart integrity in addition to the existing SQLCipher/CoreFS encryption. Every implementation uses this exact derivation:

```text
argon = Argon2id(passphrase, salt=kdfSalt, time=4, memory=131072 KiB, parallelism=4, outputLength=32)
archiveKey = HKDF-SHA256(ikm=argon, salt=None, info="anima-core-archive-v2", outputLength=32)
```

The exact fixed-header field order is `magic`, `formatVersion`, `headerLength`, `cipherId`, `kdfId`, `kdfProfileId`, `kdfTimeCost`, `kdfMemoryKiB`, `kdfParallelism`, `kdfSalt[32]`, `archiveId`, `volumeSetId`, `payloadKind`, `volumeMode`, `declaredVolumeCount`, `chunkLimitBytes`, and `noncePrefix[4]`. Before invoking Argon2, the reader validates magic/version/header length, requires the registered V2 cipher/KDF/profile and exact costs above, requires the 32-byte salt and 8-MiB chunk limit, validates enum/count/ID encodings, and rejects unknown or out-of-range values. A future profile requires a new registered format/profile version; untrusted header values never directly allocate arbitrary KDF resources. The encrypted manifest repeats `archiveId`, `volumeSetId`, `payloadKind`, `volumeMode`, and `declaredVolumeCount`; every repeated value must exactly equal the authenticated fixed header or import fails. The archive layer never uses the SQLCipher Soul key, FRK, OKWK, Catalog Root Key, Blind Search Key, or Object DEKs as archive payload keys.

The authenticated artifact manifest declares payload kind `full`, `soul`, or `fs`. A `soul` artifact carries only password/recovery wrappers needed for the Soul subset. An `fs` artifact carries only password/recovery wrappers needed for retained FRK versions and has no SQLCipher Soul keyslot. A `full` artifact carries both subsets and binds the coherent Soul/CoreFS generation pair. Import may unwrap only the keys declared for that artifact kind and must reject undeclared cross-compartment key material.

The fixed-header hash is `SHA-256` over the exact serialized fixed header, including version, cipher/KDF profile, salt, chunk limit, archive ID, volume-set ID, and declared payload kind. The encrypted artifact manifest and every record chunk bind that header hash, so tampering with pre-authentication parameters is detected after the bounded KDF step.

Before encryption, the exporter makes a bounded pre-hash pass over each pinned immutable source record (including the stable Soul checkpoint) and defines `recordHash = SHA-256(exact pre-archive record bytes)`. It then makes the streaming encryption pass. The normative archive-chunk AAD tuple is:

```text
anima-core-archive-chunk-v2:
  headerHash, archiveId, volumeSetId, payloadKind,
  recordType, recordPath, recordOrdinal, recordHash,
  chunkIndex, chunkCount, plaintextOffset,
  plaintextLength, ciphertextLength, finalFlag,
  volumeOrdinal
```

All three documents and implementations must use this tuple without abbreviation. Archive nonces use a fresh CSPRNG-generated 32-bit per-archive prefix plus one monotonically increasing 64-bit global chunk ordinal across every record and volume. The ordinal never resets at a record or volume boundary. Restarting an interrupted export discards the partial set and creates a new archive ID, salt, key, nonce prefix, and ordinal sequence. Writers and readers reject ordinal repetition, regression, overflow, or any tuple/length/count mismatch. Completion authenticates a whole-artifact inventory/footer, so truncation, splicing, appending, volume mixing, and record omission fail closed.

For a single-file artifact, `volumeSetId` equals `archiveId` and `volumeOrdinal=0`; multipart ordinals start at 1. `chunkCount`, both lengths, and `finalFlag` are derived from the preflighted record length and fixed chunk limit before encryption, then verified again during decryption.

Each source or ciphertext buffer is at most 8 MiB. Aggregate export/import streaming working memory is at most 32 MiB, excluding the fixed 128-MiB Argon2 workspace and fixed runtime/library overhead. Inventory/footer entries are incrementally hashed and, if disk-spooled, remain encrypted/authenticated under `archiveKey` and are deleted with the partial artifact on failure.

The archive passphrase may be the same text the user normally enters to unlock ANIMA, but its random salt and archive-specific KDF/domain produce unrelated key bytes. Opening the transfer container does not replace normal restored-Core keyslot verification.

### 7.4 Recovery-credential replacement

Replacing the recovery phrase uses the same two-store generation protocol as passphrase change:

1. While all root keys and Soul-domain DEKs are unlocked, require the new recovery phrase and allocate recovery credential generation `R+1`.
2. Write pending recovery wrappers for every Soul-domain DEK in one SQLCipher transaction.
3. Write pending recovery wrappers for the SQLCipher Soul key and all required FRK versions in the manifest.
4. Reload both stores and independently verify that the new recovery phrase unwraps every pending root and Soul-domain slot to the expected raw keys.
5. Atomically activate manifest recovery generation `R+1`, then promote matching SQLCipher rows. A crash between these actions follows the same pending-row finalization rule as password credential generation.
6. Retain recovery generation `R` decrypt-only until a second full recovery reopen passes and the user confirms the replacement phrase is safely recorded.

For `full` mode, no recovery-generation activation may occur if any Soul domain, active/decrypt-only FRK, or SQLCipher root wrapper is absent or fails verification. Intentionally scoped recovery follows Section 7.5 and activates credentials only for the declared compartment.

### 7.5 Scoped compartment recovery

Artifact kind defines the required key-completeness set:

| Mode | Required keys | Allowed state |
|---|---|---|
| `full` | SQLCipher Soul key, every active Soul-domain DEK, and every FRK required by retained committed catalogs | normal ANIMA startup after coherent generation-pair verification |
| `soul` | SQLCipher Soul key and every active Soul-domain DEK declared by the authenticated Soul snapshot; FRKs must be absent | unlock and run identity/cognition in explicit `filesystem_missing` degraded mode; CoreFS-dependent features remain unavailable |
| `fs` | every FRK required by the exported retained catalogs; SQLCipher Soul key and Soul-domain DEKs must be absent | authenticated CoreFS browse/export through animaOS recovery UI only; no agent startup |

Passphrase or recovery-credential replacement in `soul` mode writes and verifies only the Soul root/domain wrappers, records the credential generation as `soul` scoped, and preserves `filesystem_missing`. Replacement in `fs` mode writes and verifies only the retained FRK wrappers, records the generation as `fs` scoped, and preserves recovery/export-only mode. Neither scoped generation can satisfy a `full` unlock or be merged by matching generation numbers alone.

Initializing a new empty CoreFS after Soul-only recovery is an explicit destructive acknowledgement that the old filesystem is absent. It generates a new FRK, CoreFS lineage ID, empty catalog generation, and both password/recovery wrappers; therefore it requires the current passphrase plus verified recovery phrase or completion of the separate recovery-credential replacement flow. It never claims to restore missing objects.

V1 does not attach a CoreFS-only artifact to a Soul or synthesize a coherent generation pair. The recovery UI may authenticate, browse, and export those files only. Reattachment requires a later design for Core/owner lineage proof, conflict handling, and atomic pair publication.

## 8. Filesystem Root Key Rotation

FRK rotation rewraps live Object DEKs and changes catalog/search subkeys without rewriting all object ciphertext.

Because the recovery KEK is derived from the recovery phrase and is not stored, FRK rotation requires the user to supply and verify the recovery phrase (or explicitly replace it through a separate recovery-reset flow) before a new FRK can be activated.

Crash-resumable activation protocol:

1. Generate FRK version `N+1` in memory.
2. Write both password- and recovery-wrapped `pending` keyslots for `N+1` to the manifest and fsync it.
3. Reload the serialized manifest from disk and independently unwrap both pending keyslots using the supplied passphrase and recovery phrase. Verify both results equal FRK `N+1`; any failure aborts before catalog publication.
4. Derive the new OKWK/Catalog/Blind Search subkeys.
5. Create a complete next catalog generation whose live Object DEKs are rewrapped under the new OKWK and whose catalog is encrypted under the new generation key. Object ciphertext remains unchanged.
6. Atomically publish that catalog and update `fs/HEAD`, including required FRK version `N+1`, through the normal Core-wide commit protocol.
7. Atomically mark `N+1` active and `N` decrypt-only in the manifest.
8. Rebuild blind-search tokens under the new Blind Search Key; do not accept old/new mixed tokens as complete.
9. Perform a second full close/reopen verification through both passphrase and recovery paths against the active Soul/catalog/content sample.
10. Retain FRK `N` until every older catalog generation requiring it has expired or been pruned and a verified backup using `N+1` exists.
11. Remove old password/recovery keyslots only through the separately gated `PCF-010` authenticated prune/retirement transaction.

If interrupted:

- before step 2, nothing durable changed
- between steps 2 and 6, `N` remains active and `N+1` is verified pending material that has not replaced `fs/HEAD`
- after step 6 but before step 7, open reads FRK version `N+1` from `fs/HEAD`, unwraps the verified matching pending keyslot, and resumes manifest finalization
- after step 7, `N+1` is active and `N` remains decrypt-only until retirement criteria pass

The manifest rotation state records active/pending/decrypt-only versions and the published catalog generation, but no raw keys.

## 9. Object-Key Rotation

Object-key rotation is used when one object key may be compromised or policy requires cryptographic replacement:

1. Decrypt the current live object revision.
2. Generate a new random Object DEK and increment object-key epoch.
3. Re-encrypt a complete new revision with a fresh nonce and new Object DEK.
4. Wrap the new Object DEK under the active OKWK.
5. Commit the object/catalog generation atomically.
6. Retain the prior Object DEK only while a retained catalog/revision requires it.

Deleting an object removes it from the committed catalog but does not claim immediate physical erasure from SSD wear-leveling or backups. Cryptographic deletion requires the `PCF-010` authenticated retention-waiver/prune flow to remove every catalog/object revision that contains the wrapped Object DEK and retire any separately retained key material while reporting backup limitations.

## 10. Nonce and AAD Requirements

- Metadata and body-frame nonces are generated independently with the operating-system CSPRNG and checked for within-revision reuse.
- Tests must prove no code path derives a nonce solely from timestamp, revision, or object ID.
- Revision reuse under one Object DEK is forbidden even if content differs.
- Decryption rejects mismatched Core ID, object ID, kind, revision, key epoch, envelope version, frame type, metadata-frame hash, chunk index/count, offset/length, total length, or final flag.
- Keyslot decryption rejects mismatched owner, purpose, version, or password/recovery path.
- Catalog decryption rejects mismatched Core ID, generation, FRK version, or expected hash.

## 11. Rotation Readiness and UI

Key rotation reports:

- active, pending, and decrypt-only key versions
- current phase and committed catalog generation
- live objects rewrapped/verified
- blind-index rebuild progress
- whether passphrase and recovery reopen checks passed
- whether old-key retirement is currently safe

ANIMA remains readable during preparation using the old active key. Canonical writes pause only for the short catalog/`fs/HEAD` activation transaction. Old-key retirement is never automatic merely because activation succeeded.

## 12. Testing

- password and recovery unlock of Soul plus CoreFS
- wrong passphrase/recovery phrase and tampered keyslot rejection
- SQLCipher key and FRK separation
- per-object compromise-boundary fixtures
- unique nonce, injected nonce collision, frame-AAD mismatch, truncation/reordering, maximum-size, full-stream, and authenticated range-read tests
- passphrase rewrap without payload rewrite
- interrupted cross-store password credential-generation change at every manifest/SQLCipher boundary
- recovery-credential replacement covering every manifest root and Soul-domain keyslot
- interrupted FRK rotation at every durable boundary
- serialized password/recovery pending-keyslot verification before any `fs/HEAD` publication
- `fs/HEAD` referencing a pending FRK version recovery/finalization
- retained old catalog decryption and safe old-key retirement
- targeted object-key rotation
- blind-search key rotation and no mixed-generation completeness
- raw disk scan for seeded Soul/CoreFS plaintext markers
- zero key/plaintext leakage in logs, progress events, PostgreSQL, and crash reports
- `anima_core_v2` full/Soul/CoreFS-only key allowlists, state-preserving scoped credential replacement, independent archive-key derivation, and cross-artifact/keyslot confusion rejection
- fixed-header length/profile/salt bounds before Argon2, header-hash authentication, exact normative chunk-AAD fields, pre-archive record-hash verification, global nonce-ordinal uniqueness/overflow/restart behavior, and aggregate-memory assertions
- multipart missing/reordered/mixed-volume, truncation, append, controller-last commit, footer, wrong-passphrase, and interrupted-import/registry-activation failure tests

## 13. Deferred

- hardware-backed or TPM/Secure Enclave keyslots
- multi-owner/shared-Core key distribution
- per-family FRKs
- external-editor plaintext mounts
- persisted encrypted full-text or semantic indexes
- remote recovery escrow
