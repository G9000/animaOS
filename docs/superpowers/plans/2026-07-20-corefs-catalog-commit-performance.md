# CoreFS Catalog Commit Performance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make steady-state CoreFS full-catalog commits pass the existing PCF-002 latency gates without changing the V2 wire format, durability/recovery semantics, safe object-file checks, or benchmark contract.

**Architecture:** Keep disk state authoritative and add a process-local `Arc` snapshot selected only after exact pointer and FRK-derived key-identity checks plus SHA-256 reauthentication of the bounded catalog bytes named by HEAD. Remove redundant catalog decrypt/validation and publication-hash passes at the trusted coordinator boundary, retain strict public/untrusted paths, then reduce allocation and unchanged-object key-unwrapping work while continuing to safely reopen every referenced immutable object.

**PR #117 correction (2026-07-23):** The original exact-hit steps incorrectly treated a matching in-memory snapshot as sufficient after pointer rereads. Exact hits now reopen and hash the referenced catalog generation; missing or changed durable bytes clear the cache and fail closed while decryption, decoding, validation, and re-encoding remain skipped.

**Tech Stack:** Rust 1.75, `cap-std`, `fs4`, `aes-gcm`, `hkdf`, `sha2`, Rust unit/integration tests, Python 3.12/pytest benchmark-contract validation, PowerShell, Git.

---

## Source of truth and stop rules

- Approved spec: `docs/superpowers/specs/2026-07-20-corefs-catalog-commit-performance-design.md`
- Active child: `tickets/portable-core-filesystem/PCF-002-corefs-catalog.md`
- Parent tracker: `tickets/portable-core-filesystem/PCF-000-portable-core-filesystem.md`
- Existing umbrella plan: `docs/superpowers/plans/2026-07-12-portable-core-filesystem.md`, Task 2 Steps 12-14
- Baseline source: merged `main` at `5a3a7a0feadad5734e297ce2e09835008660da15`
- Baseline evidence: medium/maximum-live/serialized-limit commit p95 values are 207.7262/1,060.9271/1,131.7692 ms; the unchanged gates are 100/250/250 ms.

Do not move serialization or encryption outside the measured public `commit` call. Do not remove the bounded serialization preflight, kernel lock, fsync/HEAD-last publication, recovery markers, strict decode, prepared-object verification, or per-commit `open_regular_file_in` validation for unchanged objects. If the final exact 30/200 run remains red after these approved changes, record the evidence, return PCF-002 to `blocked`, refresh dependency eligibility for every remaining child, block the parent only when no other child can legally progress, and stop for a separately approved architecture revision.

## File responsibility map

| Path | Responsibility in this plan |
|---|---|
| `packages/anima-corefs/src/catalog/v2.rs` | Validated catalog encoding, one production publication artifact, digest/name reuse, strict public decode/encode preservation |
| `packages/anima-corefs/src/catalog/mod.rs` | Crate-visible re-export of new internal catalog publication types/functions if needed |
| `packages/anima-corefs/src/head.rs` | Verify-and-return authenticated catalog once; trusted HEAD construction from a coordinator-produced publication artifact |
| `packages/anima-corefs/src/transaction/cache.rs` | Pointer tuple, domain-separated key identities, immutable snapshot/object-validation state, poison-tolerant short-held cache mutex |
| `packages/anima-corefs/src/transaction/cache_tests.rs` | Internal counters/seams, poison, cache hit/miss, key identity, lock-order, and object-binding unit tests |
| `packages/anima-corefs/src/transaction.rs` | Coordinator integration, `Arc` committed catalogs, recovery/cache authority boundaries, ordered preconditions, prepared-revision validation, cache publication |
| `packages/anima-corefs/src/transaction/failure_tests.rs` | Cache behavior at pre-HEAD failures, post-HEAD recovery-pending outcomes, cutover recovery, and concurrent observation boundaries |
| `packages/anima-corefs/tests/transaction.rs` | Public same-process/cross-coordinator behavior and preserved missing/empty/link-layout rejection |
| `packages/anima-corefs/tests/rotation.rs` | Wrong same-version active/retained material and FRK rotation cache invalidation |
| `packages/anima-corefs/tests/catalog_benchmark.rs` | Production-path benchmark metadata and real-generation invariants only if a focused regression is required |
| `apps/server/tests/test_corefs_catalog_benchmark.py` | Unchanged strict reference artifact contract; no edit expected unless a regression proves metadata drift |
| `docs/benchmarks/portable-core-filesystem/catalog-reference-v1.json` | Final exact 30/200 evidence, updated only after final code is committed |
| `docs/superpowers/specs/2026-07-20-corefs-catalog-commit-performance-design.md` | Approval/implementation status and plan link |
| `tickets/portable-core-filesystem/PCF-000-portable-core-filesystem.md` | Parent progress/blocker synchronization |
| `tickets/portable-core-filesystem/PCF-002-corefs-catalog.md` | Child progress, validation, changed paths, and exact benchmark evidence |

Avoid unrelated refactors. `transaction.rs` is already large, so cache-specific types and tests belong in the two new `transaction/` files rather than expanding its unit-test module further.

### Task 1: Make catalog authentication and HEAD publication single-pass

**Files:**
- Modify: `packages/anima-corefs/src/catalog/v2.rs:1157-1320`
- Modify: `packages/anima-corefs/src/catalog/mod.rs:1-15` only if the internal items need an explicit crate-visible re-export
- Modify: `packages/anima-corefs/src/head.rs:17-101`
- Modify: `packages/anima-corefs/src/transaction.rs:1518-1536,2048-2080`
- Test: `packages/anima-corefs/src/head.rs` (new `#[cfg(test)] mod tests`)
- Test: `packages/anima-corefs/src/catalog/v2.rs:1916-end`

- [ ] **Step 1: Add failing tests for one authenticated open and equivalent trusted publication**

Add focused unit tests named:

```rust
#[test]
fn verified_catalog_open_decrypts_once_and_returns_that_generation() { /* scoped probe count == 1 */ }

#[test]
fn trusted_publication_head_matches_public_constructor_bytes() { /* compare encode_head outputs */ }

#[test]
fn publication_artifact_reuses_one_digest_for_name_and_head() { /* digest/name/hash agree */ }

#[test]
fn trusted_publication_path_hashes_once_and_decrypts_zero_times() { /* full coordinator publication */ }
```

Use private, invocation-scoped test observers passed through the verification and coordinator-publication helpers (not process-global atomics). The first test asserts exactly one decrypt/strict decode in one verification invocation and that the returned generation is that authenticated value. The coordinator test executes the same private publication path used by `commit`, asserting one ciphertext hash total and zero decrypt/strict-decode calls between validated catalog input and durable HEAD construction. Retain tests proving tampered ciphertext, wrong Core ID, wrong FRK material, wrong generation, and non-canonical plaintext still fail through public APIs.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
cargo +1.75.0 test --locked -p anima-corefs head::tests::verified_catalog_open_decrypts_once_and_returns_that_generation -- --exact
cargo +1.75.0 test --locked -p anima-corefs head::tests::trusted_publication_head_matches_public_constructor_bytes -- --exact
cargo +1.75.0 test --locked -p anima-corefs catalog::v2::tests::publication_artifact_reuses_one_digest_for_name_and_head -- --exact
cargo +1.75.0 test --locked -p anima-corefs transaction::tests::trusted_publication_path_hashes_once_and_decrypts_zero_times -- --exact
```

Expected: FAIL to compile because the verify-and-return helper and publication artifact do not exist.

- [ ] **Step 3: Add the minimal internal publication artifact**

In `catalog/v2.rs`, keep `encrypt_catalog_generation` public behavior unchanged and introduce a crate-private result for the coordinator. Thread an optional `#[cfg(test)]` invocation-scoped probe through a private inner helper so the production wrapper has no global state and the test can count its single hash:

```rust
pub(crate) struct CatalogPublication {
    encrypted: Vec<u8>,
    plaintext_size: usize,
    info: CatalogGenerationEnvelopeInfo,
    digest: [u8; 32],
    physical_name: String,
}

pub(crate) fn encrypt_catalog_generation_for_publication(
    keys: &FrkSubkeys,
    core_id: &str,
    payload: &CatalogGeneration,
) -> Result<CatalogPublication, CatalogError> {
    // Produce the bounded canonical plaintext and ciphertext once.
    // Parse generation/version once, hash ciphertext once, and derive the physical name from it.
}
```

Expose read-only crate-private accessors rather than public fields. Keep `catalog_generation_physical_name` for arbitrary public bytes and route it through the same formatting helper.

- [ ] **Step 4: Return the catalog from HEAD verification and construct trusted HEAD without reopening bytes**

In `head.rs` add:

```rust
pub(crate) fn verify_and_decrypt_catalog(
    &self,
    keys: &FrkSubkeys,
    core_id: &str,
    encrypted_catalog: &[u8],
) -> Result<CatalogGeneration, HeadError> {
    // Run the existing version/generation/hash checks and return the one decrypted catalog.
}

pub(crate) fn new_for_publication(
    keys: &FrkSubkeys,
    core_id: &str,
    catalog: &CatalogGeneration,
    publication: &CatalogPublication,
    required_frk_version: u32,
) -> Result<Self, HeadError> {
    // Check Core/key version, catalog generation, envelope info, and artifact digest.
    // Do not decrypt or hash publication.encrypted() again.
}
```

Make public `verify_catalog` call `verify_and_decrypt_catalog(...).map(drop)`. Keep public `new_for_catalog` strict for arbitrary encrypted input.

- [ ] **Step 5: Use the single-pass helpers in the coordinator**

Change `load_pointer_for_head` to consume the catalog returned by `verify_and_decrypt_catalog` instead of calling `decrypt_catalog_generation` again. Change `publish_catalog_pointer_with_hook` to use `CatalogPublication` for the physical name, plaintext size, encrypted bytes, and trusted HEAD. Its private test-only inner form accepts the scoped publication probe and records the actual production stages; the ordinary production wrapper passes no probe.

- [ ] **Step 6: Run focused and surrounding tests**

Run:

```powershell
cargo +1.75.0 test --locked -p anima-corefs head::tests -- --nocapture
cargo +1.75.0 test --locked -p anima-corefs catalog::v2::tests -- --nocapture
cargo +1.75.0 test --locked -p anima-corefs transaction::tests::trusted_publication_path_hashes_once_and_decrypts_zero_times -- --exact
cargo +1.75.0 test --locked -p anima-corefs --test catalog --test transaction
```

Expected: PASS; the trusted production publication path reports one hash and zero decrypts, public tamper/wrong-key tests remain green, and trusted/public HEAD encodings are byte-identical.

- [ ] **Step 7: Commit Task 1**

```powershell
git add packages/anima-corefs/src/catalog/v2.rs packages/anima-corefs/src/catalog/mod.rs packages/anima-corefs/src/head.rs packages/anima-corefs/src/transaction.rs
git -c commit.gpgsign=false commit -m "perf: open and publish CoreFS catalogs once"
```

### Task 2: Add validated catalog transformations without weakening untrusted paths

**Files:**
- Modify: `packages/anima-corefs/src/catalog/v2.rs:593-711,1157-1187,1247-1279`
- Test: `packages/anima-corefs/src/catalog/v2.rs:1916-end`
- Test: `packages/anima-corefs/tests/catalog.rs`

- [ ] **Step 1: Add failing validated-value equivalence tests**

Add:

```rust
#[test]
fn validated_marker_path_preserves_canonical_bytes() { /* trusted and public encoders match */ }

#[test]
fn trusted_encoder_keeps_the_bounded_preflight() { /* oversized value fails before output */ }

#[test]
fn untrusted_decode_still_rejects_noncanonical_bytes() { /* existing strictness remains */ }

#[test]
fn zero_epoch_cutover_marker_is_rejected_before_the_fast_path() { /* constructor returns error */ }

#[test]
fn caller_created_invalid_graph_never_reaches_the_marker_fast_path() { /* orphan/duplicate/cycle cases */ }
```

- [ ] **Step 2: Run the new fast-path tests as RED plus fail-closed characterizations as GREEN**

```powershell
cargo +1.75.0 test --locked -p anima-corefs catalog::v2::tests::validated_marker_path_preserves_canonical_bytes -- --exact
cargo +1.75.0 test --locked -p anima-corefs catalog::v2::tests::trusted_encoder_keeps_the_bounded_preflight -- --exact
cargo +1.75.0 test --locked -p anima-corefs catalog::v2::tests::zero_epoch_cutover_marker_is_rejected_before_the_fast_path -- --exact
cargo +1.75.0 test --locked -p anima-corefs catalog::v2::tests::caller_created_invalid_graph_never_reaches_the_marker_fast_path -- --exact
```

Expected: the validated encoder/marker tests FAIL because the new seam does not exist; the zero-epoch and invalid caller-created graph characterizations PASS through existing constructors. For the graph test, cover at least orphan parent, duplicate stable ID, and folder cycle and assert the exact existing `CatalogError` before `with_cutover_marker` can be called.

- [ ] **Step 3: Separate validated internal encoding from public validation**

Implement one private serializer that always performs both bounded passes but assumes entry invariants are already established:

```rust
fn encode_validated_catalog_generation_with_shape(
    payload: &CatalogGeneration,
    shape: PhysicalNameWireShape,
) -> Result<Vec<u8>, CatalogError> {
    let wire = WireCatalogGenerationRef { catalog: payload, physical_name_shape: shape };
    bounded_json_preflight(&wire, MAX_CATALOG_PLAINTEXT_SIZE).map_err(map_bounded_error)?;
    bounded_json_to_vec(&wire, MAX_CATALOG_PLAINTEXT_SIZE).map_err(map_bounded_error)
}
```

Public `encode_catalog_generation` validates before calling it. Coordinator publication calls the crate-private validated variant. `decode_catalog_generation` still reconstructs, validates, and compares canonical bytes.

- [ ] **Step 4: Make the existing crate-private marker transition O(1)**

Because `CatalogGeneration` fields are private and `CatalogCutoverMarker::new` rejects epoch zero, make `with_cutover_marker` update only the marker in release builds. A debug-only assertion may run full validation, but production commit code must not rescan entries. Do not add an unchecked marker constructor, expose catalog fields, or accept entries outside `CatalogGeneration::new`; the two GREEN characterizations are the proof boundary for caller-created values.

- [ ] **Step 5: Run focused and complete catalog tests**

```powershell
cargo +1.75.0 test --locked -p anima-corefs catalog::v2::tests -- --nocapture
cargo +1.75.0 test --locked -p anima-corefs --test catalog --test catalog_entries
```

Expected: PASS, including the exact zero-epoch and orphan/duplicate/cycle characterizations; canonical bytes, allocation bounds, reserved-state protection, graph validation, and non-canonical rejection are unchanged.

- [ ] **Step 6: Commit Task 2**

```powershell
git add packages/anima-corefs/src/catalog/v2.rs packages/anima-corefs/tests/catalog.rs
git -c commit.gpgsign=false commit -m "perf: trust validated CoreFS catalog values"
```

### Task 3: Build the key-bound, poison-tolerant cache primitive

**Files:**
- Create: `packages/anima-corefs/src/transaction/cache.rs`
- Create: `packages/anima-corefs/src/transaction/cache_tests.rs`
- Modify: `packages/anima-corefs/src/transaction.rs:1-55,790-850,3257`

- [ ] **Step 1: Add failing cache/key-identity tests**

Create `cache_tests.rs` with tests named:

```rust
#[test]
fn same_version_different_catalog_or_object_wrap_material_misses() { /* two keysets */ }

#[test]
fn exact_pointer_and_key_identity_returns_the_arc_snapshot() { /* Arc::ptr_eq */ }

#[test]
fn poisoned_cache_is_cleared_and_treated_as_a_miss() { /* no storage error */ }

#[test]
fn cache_guard_is_released_before_external_probe() { /* try_lock after get/replace */ }

#[test]
fn empty_validated_object_state_is_buildable_and_searchable() { /* complete API */ }
```

- [ ] **Step 2: Run the new module tests and verify RED**

```powershell
cargo +1.75.0 test --locked -p anima-corefs transaction::cache_tests -- --nocapture
```

Expected: FAIL to compile because `transaction::cache`, the complete snapshot/object-state types, and the coordinator cache field do not exist.

- [ ] **Step 3: Implement domain-separated key identities outside the mutex**

In `cache.rs`, derive fixed 32-byte identifiers separately from `FrkSubkeys::catalog()` and `FrkSubkeys::object_wrap()` using HKDF-SHA256. The info bytes include a stable domain, Core ID length/value, FRK version, and purpose:

```rust
const CACHE_ID_DOMAIN: &[u8] = b"anima-corefs-commit-cache-key-id-v1\0";

#[derive(Clone, Debug, Eq, PartialEq)]
struct CatalogKeyCacheId([u8; 32]);

#[derive(Clone, Debug, Eq, PartialEq)]
struct ObjectWrapKeyCacheId([u8; 32]);
```

Derive catalog IDs for every distinct version referenced by HEAD/receipt/completion through `FrkKeyring::require`. Derive the active object-wrap ID separately. Never store raw subkeys and never use these IDs as cryptographic authorization outside cache selection.

- [ ] **Step 4: Implement every snapshot field type now so Task 3 compiles**

```rust
#[derive(Clone, Debug, Eq, PartialEq)]
struct PointerSet {
    head: Option<HeadRecord>,
    receipt: Option<HeadRecord>,
    complete: Option<HeadRecord>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct ValidatedObjectBinding {
    object_id: OpaqueId,
    revision: u64,
    object_key_epoch: u32,
    physical_name: ObjectPhysicalName,
    content_hash: ContentHash,
    kind: ObjectKind,
    wrapped_dek: WrappedObjectDekRecord,
    binding_digest: [u8; 32],
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
struct ValidatedObjectState {
    by_object_id: Box<[ValidatedObjectBinding]>, // canonical object-ID order
}

struct AuthenticatedCommitSnapshot {
    pointers: PointerSet,
    key_ids: RequiredCacheKeyIds,
    catalog: Arc<CatalogGeneration>,
    objects: Option<Arc<ValidatedObjectState>>,
}
```

Implement `ValidatedObjectState::empty`, sorted construction with duplicate rejection, and binary-search lookup in this task. Task 8 will populate and consume non-empty bindings. The hit predicate compares the complete pointer set, Core binding, every required catalog-key ID, and the active object-wrap ID before returning cached object bindings.

- [ ] **Step 5: Implement short-held poison-tolerant synchronization**

```rust
#[derive(Default)]
struct CommitCache {
    inner: Mutex<Option<Arc<AuthenticatedCommitSnapshot>>>,
}

impl CommitCache {
    fn get(&self, key: &CacheLookupKey) -> Option<Arc<AuthenticatedCommitSnapshot>> { /* clone Arc */ }
    fn replace(&self, value: Arc<AuthenticatedCommitSnapshot>) { /* short swap */ }
    fn clear(&self) { /* recover poisoned guard with into_inner, then take */ }
}
```

No cache method accepts an I/O, crypto, build, failure-hook, invalidation, observer, or kernel-lock closure. Only an immutable `Arc` may leave the guard scope.

- [ ] **Step 6: Run cache tests and strict lint**

```powershell
cargo +1.75.0 test --locked -p anima-corefs transaction::cache_tests -- --nocapture
cargo +1.75.0 clippy --locked -p anima-corefs --all-targets -- -D warnings
```

Expected: PASS; the complete snapshot type compiles, wrong same-version material misses, poison becomes a miss, object-state lookup is deterministic, and the mutex is free immediately after cache methods return.

- [ ] **Step 7: Commit Task 3**

```powershell
git add packages/anima-corefs/src/transaction.rs packages/anima-corefs/src/transaction/cache.rs packages/anima-corefs/src/transaction/cache_tests.rs
git -c commit.gpgsign=false commit -m "perf: add authenticated CoreFS commit cache"
```

### Task 4: Integrate exact cache selection into unlocked and locked catalog loads

**Files:**
- Modify: `packages/anima-corefs/src/transaction.rs:670-683,1156-1536`
- Modify: `packages/anima-corefs/src/transaction/cache.rs`
- Modify: `packages/anima-corefs/src/transaction/cache_tests.rs`
- Test: `packages/anima-corefs/tests/transaction.rs`

- [ ] **Step 1: Add failing unlocked-load authority and work-count tests**

Add invocation-scoped private `CatalogLoadProbe` counters for pointer reads, catalog-file reads, catalog decrypts, catalog encodes, and `cache.try_lock()` at external stages. The normal production wrapper passes no probe. Counter/guard tests live in crate-private `transaction::cache_tests`; public race/behavior tests live in `tests/transaction.rs`. Add tests:

```rust
#[test]
fn unlocked_exact_hit_reauthenticates_catalog_bytes_without_crypto() { /* pointers > 0; file == 1; decrypt/encode == 0 */ }

#[test]
fn unlocked_cache_hit_rejects_missing_catalog_bytes() { /* exact hit fails closed */ }

#[test]
fn unlocked_cache_hit_rejects_changed_catalog_bytes() { /* HEAD hash mismatch */ }

#[test]
fn unlocked_second_head_change_discards_the_candidate_hit() { /* existing stability read */ }

#[test]
fn another_coordinator_advancing_head_forces_unlocked_load_miss() { /* shared root */ }

#[test]
fn unlocked_load_holds_no_cache_guard_during_pointer_io_or_crypto() { /* try_lock succeeds */ }
```

- [ ] **Step 2: Run RED cache-counter tests and pre-change GREEN authority characterizations**

```powershell
cargo +1.75.0 test --locked -p anima-corefs transaction::cache_tests::unlocked_exact_hit_reauthenticates_catalog_bytes_without_crypto -- --exact
cargo +1.75.0 test --locked -p anima-corefs --test transaction unlocked_second_head_change_discards_the_candidate_hit -- --exact
cargo +1.75.0 test --locked -p anima-corefs --test transaction another_coordinator_advancing_head_forces_unlocked_load_miss -- --exact
cargo +1.75.0 test --locked -p anima-corefs transaction::cache_tests::unlocked_load_holds_no_cache_guard_during_pointer_io_or_crypto -- --exact
```

Expected: `unlocked_exact_hit...` and `unlocked_load_holds_no_cache_guard...` FAIL because pointer-set cache selection/counters do not exist. `unlocked_second_head_change...` and `another_coordinator...` PASS before caching, characterizing the existing authority behavior that the optimized path must preserve.

- [ ] **Step 3: Implement the unlocked load path**

Refactor HEAD/receipt/completion reads into one `PointerSet`. Derive all required key identities before `CommitCache::get`. On an exact hit, reopen the bounded catalog generation named by HEAD, verify its SHA-256, and return the cached `Arc<CatalogGeneration>` without decrypting, decoding, invariant-validating, or re-encoding. Missing or changed bytes clear the cache and fail closed. On a miss, execute the existing full authentication/recovery path and cache only the authenticated catalog with `objects: None`.

Change private `CommittedCatalog.catalog` storage to `Arc<CatalogGeneration>` while keeping `pub fn catalog(&self) -> &CatalogGeneration`. Public `load_committed` still performs its second HEAD stability read after the cache guard is gone; a changed second read discards the candidate and follows the existing retry/failure contract.

- [ ] **Step 4: Run unlocked-load tests and commit**

```powershell
cargo +1.75.0 test --locked -p anima-corefs transaction::cache_tests::unlocked_exact_hit_reauthenticates_catalog_bytes_without_crypto -- --exact
cargo +1.75.0 test --locked -p anima-corefs --test transaction unlocked_second_head_change_discards_the_candidate_hit -- --exact
cargo +1.75.0 test --locked -p anima-corefs --test transaction another_coordinator_advancing_head_forces_unlocked_load_miss -- --exact
cargo +1.75.0 test --locked -p anima-corefs transaction::cache_tests::unlocked_load_holds_no_cache_guard_during_pointer_io_or_crypto -- --exact
git add packages/anima-corefs/src/transaction.rs packages/anima-corefs/src/transaction/cache.rs packages/anima-corefs/src/transaction/cache_tests.rs packages/anima-corefs/tests/transaction.rs
git -c commit.gpgsign=false commit -m "perf: reuse exact CoreFS snapshot on unlocked load"
```

Expected: PASS; pointer records and the second HEAD are reread, while an exact hit performs one bounded catalog-file read and hash verification with zero decrypts or encodes. Missing or changed generation bytes fail before returning cached state.

- [ ] **Step 5: Add failing locked-load and lock-order tests**

```rust
#[test]
fn locked_exact_hit_reauthenticates_catalog_bytes_without_crypto() { /* internal locked loader */ }

#[test]
fn locked_load_acquires_kernel_lock_before_cache_and_releases_cache_before_io() { /* ordered stages + try_lock */ }
```

```powershell
cargo +1.75.0 test --locked -p anima-corefs transaction::cache_tests::locked_exact_hit_reauthenticates_catalog_bytes_without_crypto -- --exact
cargo +1.75.0 test --locked -p anima-corefs transaction::cache_tests::locked_load_acquires_kernel_lock_before_cache_and_releases_cache_before_io -- --exact
```

Expected: FAIL because the locked loader has no exact-hit or stage-observer seam.

- [ ] **Step 6: Implement and commit locked-load integration**

The locked helper must require an already-held `CoreCommitLock`, reread/validate the pinned layout and complete pointer set, derive key IDs without the cache mutex, clone at most one snapshot `Arc`, and perform all I/O/crypto after the guard is gone. It must never acquire the cache mutex before the kernel lock.

```powershell
cargo +1.75.0 test --locked -p anima-corefs transaction::cache_tests::locked_exact_hit_reauthenticates_catalog_bytes_without_crypto -- --exact
cargo +1.75.0 test --locked -p anima-corefs transaction::cache_tests::locked_load_acquires_kernel_lock_before_cache_and_releases_cache_before_io -- --exact
cargo +1.75.0 test --locked -p anima-corefs --test transaction
git add packages/anima-corefs/src/transaction.rs packages/anima-corefs/src/transaction/cache.rs packages/anima-corefs/src/transaction/cache_tests.rs packages/anima-corefs/tests/transaction.rs
git -c commit.gpgsign=false commit -m "perf: reuse exact CoreFS snapshot under commit lock"
```

Expected: PASS; the observed order is kernel lock, pointer I/O/key derivation, short cache access, then any miss-path I/O/crypto with the cache mutex free.

### Task 5: Integrate cache authority into normal commits

**Files:**
- Modify: `packages/anima-corefs/src/transaction.rs:1937-2160`
- Modify: `packages/anima-corefs/src/transaction/cache.rs`
- Modify: `packages/anima-corefs/src/transaction/cache_tests.rs`
- Modify: `packages/anima-corefs/src/transaction/failure_tests.rs`
- Test: `packages/anima-corefs/tests/transaction.rs`

- [ ] **Step 1: Add failing normal-commit authority, failure, and guard tests**

```rust
#[test]
fn same_coordinator_commit_reuses_only_the_exact_authenticated_head() { /* second commit hit */ }

#[test]
fn another_coordinator_advance_is_observed_by_commit() { /* shared root; existing authority */ }

#[test]
fn commit_rejects_wrong_same_version_active_material_before_cache() { /* existing authentication */ }

#[test]
fn pre_head_failure_keeps_only_the_prior_snapshot() { /* publication hook */ }

#[test]
fn post_head_recovery_pending_clears_the_cache() { /* receipt/completion failure */ }

#[test]
fn commit_holds_no_cache_guard_during_kernel_lock_io_crypto_build_hooks_or_invalidation() { /* stage try_lock */ }
```

The same-coordinator hit counter and stage observer live in crate-private `transaction::cache_tests`; public cross-coordinator/wrong-key behavior remains in `tests/transaction.rs`. The stage observer is invocation-scoped and is called immediately after kernel-lock acquisition, pointer I/O, key derivation, precondition/build, encryption/publication, failure hook, and invalidation callback boundaries. Each callback asserts `cache.inner.try_lock().is_ok()`; it does not alter production order.

- [ ] **Step 2: Run RED cache-state/guard tests and pre-change GREEN authority characterizations**

```powershell
cargo +1.75.0 test --locked -p anima-corefs transaction::cache_tests::same_coordinator_commit_reuses_only_the_exact_authenticated_head -- --exact
cargo +1.75.0 test --locked -p anima-corefs --test transaction another_coordinator_advance_is_observed_by_commit -- --exact
cargo +1.75.0 test --locked -p anima-corefs --test transaction commit_rejects_wrong_same_version_active_material_before_cache -- --exact
cargo +1.75.0 test --locked -p anima-corefs transaction::failure_tests::pre_head_failure_keeps_only_the_prior_snapshot -- --exact
cargo +1.75.0 test --locked -p anima-corefs transaction::failure_tests::post_head_recovery_pending_clears_the_cache -- --exact
cargo +1.75.0 test --locked -p anima-corefs transaction::cache_tests::commit_holds_no_cache_guard_during_kernel_lock_io_crypto_build_hooks_or_invalidation -- --exact
```

Expected: the same-coordinator cache counter, pre/post-HEAD cache-state tests, and guard-stage proof FAIL because cache integration is absent. The cross-coordinator and wrong-key public characterizations PASS before caching and must remain green after implementation.

- [ ] **Step 3: Implement kernel-lock-first normal commit selection**

Inside `commit_internal_with_keyring_and_hook`: acquire `CoreCommitLock`; validate pinned layout and reread the pointer set; derive identities without the cache mutex; briefly clone an exact snapshot or run full recovery; then run preconditions, catalog build, validation, encryption, publication, hooks, and invalidation with no cache guard. Wrong same-version active material must miss and fail normal authentication.

- [ ] **Step 4: Publish cache state only at durable outcomes**

Keep the old snapshot on every pre-HEAD error. Clear on any `recovery_pending` outcome. After durable HEAD/cutover completion, replace with the new exact pointer/key/catalog snapshot; external invalidation callback failure cannot roll back disk or cache authority. Validation-only initialization never publishes an authoritative HEAD snapshot.

- [ ] **Step 5: Run surrounding tests and commit**

```powershell
cargo +1.75.0 test --locked -p anima-corefs --test transaction
cargo +1.75.0 test --locked -p anima-corefs transaction::failure_tests -- --nocapture
cargo +1.75.0 test --locked -p anima-corefs transaction::cache_tests -- --nocapture
git add packages/anima-corefs/src/transaction.rs packages/anima-corefs/src/transaction/cache.rs packages/anima-corefs/src/transaction/cache_tests.rs packages/anima-corefs/src/transaction/failure_tests.rs packages/anima-corefs/tests/transaction.rs
git -c commit.gpgsign=false commit -m "perf: reuse exact authenticated CoreFS commit snapshots"
```

Expected: PASS; cache authority tracks only durable disk authority and every observed external stage sees the cache mutex unlocked.

### Task 6: Integrate receipt/completion recovery and FRK rotation separately

**Files:**
- Modify: `packages/anima-corefs/src/transaction.rs:1156-2045`
- Modify: `packages/anima-corefs/src/transaction/cache.rs`
- Modify: `packages/anima-corefs/src/transaction/cache_tests.rs`
- Modify: `packages/anima-corefs/src/transaction/failure_tests.rs`
- Test: `packages/anima-corefs/tests/rotation.rs`

- [ ] **Step 1: Add failing recovery-shape, mixed-key, and concurrency tests**

```rust
#[test]
fn receipt_without_head_bypasses_cache_and_runs_recovery() { /* no authoritative HEAD */ }

#[test]
fn missing_head_with_completion_bypasses_cache_and_runs_recovery() { /* completion only */ }

#[test]
fn divergent_receipt_and_completion_bypass_cache() { /* non-identical pointers */ }

#[test]
fn mixed_frk_recovery_derives_every_required_catalog_key_identity() { /* HEAD/receipt/complete versions */ }

#[test]
fn cutover_recovery_replaces_cache_only_after_verified_completion() { /* no early replace */ }

#[test]
fn concurrent_unlocked_load_recovery_and_commit_do_not_invert_locks() { /* channels + bounded timeout */ }

#[test]
fn recovery_holds_no_cache_guard_during_lock_io_crypto_or_hooks() { /* stage try_lock */ }
```

- [ ] **Step 2: Run GREEN recovery-authority characterizations and RED cache-publication/guard tests**

```powershell
cargo +1.75.0 test --locked -p anima-corefs transaction::failure_tests::receipt_without_head_bypasses_cache_and_runs_recovery -- --exact
cargo +1.75.0 test --locked -p anima-corefs transaction::failure_tests::missing_head_with_completion_bypasses_cache_and_runs_recovery -- --exact
cargo +1.75.0 test --locked -p anima-corefs transaction::failure_tests::divergent_receipt_and_completion_bypass_cache -- --exact
cargo +1.75.0 test --locked -p anima-corefs transaction::failure_tests::mixed_frk_recovery_derives_every_required_catalog_key_identity -- --exact
cargo +1.75.0 test --locked -p anima-corefs transaction::failure_tests::cutover_recovery_replaces_cache_only_after_verified_completion -- --exact
cargo +1.75.0 test --locked -p anima-corefs transaction::failure_tests::concurrent_unlocked_load_recovery_and_commit_do_not_invert_locks -- --exact
cargo +1.75.0 test --locked -p anima-corefs transaction::cache_tests::recovery_holds_no_cache_guard_during_lock_io_crypto_or_hooks -- --exact
```

Expected per command:

- `receipt_without_head...`, `missing_head_with_completion...`, and `divergent_receipt_and_completion...` PASS before caching, characterizing existing fail-closed recovery rather than a cache hit.
- `mixed_frk_recovery...` FAIL because required catalog cache identities do not exist.
- `cutover_recovery_replaces_cache...` FAIL because recovery does not publish cache state.
- `concurrent_unlocked_load...` PASS before caching, characterizing current recovery/commit liveness.
- `recovery_holds_no_cache_guard...` FAIL because the cache/stage seam does not exist.

Rerun all seven after implementation; every command must then PASS.

- [ ] **Step 3: Implement recovery integration and commit it alone**

Treat receipt-only, completion-only, missing HEAD, divergent receipt/completion, and any pointer/key-ID mismatch as cache misses. Derive a catalog identity for every distinct FRK version referenced by the complete pointer set before cache access. Acquire `CoreCommitLock` before any recovery cache access; hold no cache guard across pointer/catalog I/O, decrypt/verify, cutover completion, hooks, or callbacks. Replace only after verified on-disk completion; clear on recovery-pending or ambiguous results.

```powershell
cargo +1.75.0 test --locked -p anima-corefs transaction::failure_tests -- --nocapture
cargo +1.75.0 test --locked -p anima-corefs transaction::cache_tests::recovery_holds_no_cache_guard_during_lock_io_crypto_or_hooks -- --exact
git add packages/anima-corefs/src/transaction.rs packages/anima-corefs/src/transaction/cache.rs packages/anima-corefs/src/transaction/cache_tests.rs packages/anima-corefs/src/transaction/failure_tests.rs
git -c commit.gpgsign=false commit -m "perf: bind CoreFS recovery cache to every pointer and key"
```

Expected: PASS without timeout; recovery preserves the prior-HEAD-or-complete-next-generation invariant.

- [ ] **Step 4: Add failing rotation-key and guard tests**

```rust
#[test]
fn cached_load_rejects_wrong_same_version_retained_material() { /* retained keyring */ }

#[test]
fn successful_rotation_replaces_cache_only_after_cutover_completion() { /* exact next pointer/key IDs */ }

#[test]
fn rotation_holds_no_cache_guard_during_lock_io_crypto_or_hooks() { /* stage try_lock */ }
```

```powershell
cargo +1.75.0 test --locked -p anima-corefs --test rotation cached_load_rejects_wrong_same_version_retained_material -- --exact
cargo +1.75.0 test --locked -p anima-corefs --test rotation successful_rotation_replaces_cache_only_after_cutover_completion -- --exact
cargo +1.75.0 test --locked -p anima-corefs transaction::cache_tests::rotation_holds_no_cache_guard_during_lock_io_crypto_or_hooks -- --exact
```

Expected per command: `cached_load_rejects_wrong_same_version_retained_material` PASS before rotation caching as an existing authentication characterization. `successful_rotation_replaces_cache_only_after_cutover_completion` and `rotation_holds_no_cache_guard_during_lock_io_crypto_or_hooks` FAIL because rotation cache publication and its scoped guard seam do not exist. Rerun all three after implementation; every command must then PASS.

- [ ] **Step 5: Implement rotation integration, validate, and commit**

Rotation derives old/new catalog and object-wrap identities outside the cache mutex, uses exact pointer sets, keeps the cache mutex free during kernel lock, I/O, unwrap/rewrap, catalog crypto, publication, and callbacks, and replaces only after durable cutover completion. Same-version different retained material must miss and fail authentication.

```powershell
cargo +1.75.0 test --locked -p anima-corefs --test rotation
cargo +1.75.0 test --locked -p anima-corefs transaction::cache_tests::rotation_holds_no_cache_guard_during_lock_io_crypto_or_hooks -- --exact
git add packages/anima-corefs/src/transaction.rs packages/anima-corefs/src/transaction/cache.rs packages/anima-corefs/src/transaction/cache_tests.rs packages/anima-corefs/tests/rotation.rs
git -c commit.gpgsign=false commit -m "perf: bind CoreFS rotation cache to cutover authority"
```

Expected: PASS; wrong retained material cannot hit and completed rotation publishes only the exact new authenticated state.

### Task 7: Replace precondition allocation with an ordered merge

**Files:**
- Modify: `packages/anima-corefs/src/transaction.rs:2307-2391`
- Test: `packages/anima-corefs/src/transaction/cache_tests.rs`
- Test: `packages/anima-corefs/tests/transaction.rs`

- [ ] **Step 1: Add failing table-driven equivalence tests**

Add `ordered_coverage_matches_changed_created_moved_deleted_and_parent_cases`, covering unchanged, content change, rename/move, create, delete/tombstone, source-parent change, destination-parent requirement, duplicate/missing preconditions, and stable-ID boundary ordering. For each row, compare the proposed ordered helper's exact success/error with the current implementation.

- [ ] **Step 2: Run RED**

```powershell
cargo +1.75.0 test --locked -p anima-corefs transaction::cache_tests::ordered_coverage_matches_changed_created_moved_deleted_and_parent_cases -- --exact
```

Expected: FAIL to compile because the ordered helper does not exist.

- [ ] **Step 3: Implement the ordered merge without changing errors**

Walk canonical stable-ID order once. Use `Ordering::Less/Equal/Greater` to validate deleted/changed sources, changed source plus destination, and newly created destinations. Index only precondition-referenced existing parents needed by destination checks. Preserve every `MissingSourcePrecondition`, `MissingDestinationPrecondition`, duplicate, and stale-revision result byte-for-byte at the public error layer.

- [ ] **Step 4: Validate and commit only preconditions**

```powershell
cargo +1.75.0 test --locked -p anima-corefs transaction::cache_tests::ordered_coverage_matches_changed_created_moved_deleted_and_parent_cases -- --exact
cargo +1.75.0 test --locked -p anima-corefs --test transaction
git add packages/anima-corefs/src/transaction.rs packages/anima-corefs/src/transaction/cache_tests.rs packages/anima-corefs/tests/transaction.rs
git -c commit.gpgsign=false commit -m "perf: merge CoreFS preconditions in catalog order"
```

Expected: PASS; only allocation/iteration changes.

### Task 8: Reuse object-key bindings while preserving every safe object open

**Files:**
- Modify: `packages/anima-corefs/src/transaction.rs:2392-2558`
- Modify: `packages/anima-corefs/src/transaction/cache.rs`
- Modify: `packages/anima-corefs/src/transaction/cache_tests.rs`
- Test: `packages/anima-corefs/tests/transaction.rs`
- Test: `packages/anima-corefs/tests/rotation.rs`

- [ ] **Step 1: Add failing binding-hit, key-binding, and filesystem-layout tests**

```rust
#[test]
fn exact_cached_object_tuple_skips_repeated_dek_unwrap() { /* scoped unwrap counter == 0 on hit */ }
#[test]
fn changed_wrapped_dek_never_reuses_binding() { /* unwrap occurs/fails */ }
#[test]
fn wrong_object_wrap_key_identity_never_reuses_binding() { /* same version, other material */ }
#[test]
fn changed_object_key_epoch_never_reuses_binding() { /* epoch-only mismatch */ }
#[test]
fn cache_hit_rejects_missing_object() { /* existing safe-open helper */ }
#[test]
fn cache_hit_rejects_empty_object() { /* zero length */ }
#[test]
fn cache_hit_rejects_symlinked_object() { /* platform helper */ }
#[test]
fn cache_hit_rejects_replaced_object() { /* opened/linked identity */ }
#[test]
fn cache_hit_rejects_unexpected_hard_link() { /* link-count policy */ }
```

- [ ] **Step 2: Run RED binding-cache tests and pre-change GREEN safe-open characterizations**

```powershell
cargo +1.75.0 test --locked -p anima-corefs transaction::cache_tests::exact_cached_object_tuple_skips_repeated_dek_unwrap -- --exact
cargo +1.75.0 test --locked -p anima-corefs transaction::cache_tests::changed_wrapped_dek_never_reuses_binding -- --exact
cargo +1.75.0 test --locked -p anima-corefs transaction::cache_tests::wrong_object_wrap_key_identity_never_reuses_binding -- --exact
cargo +1.75.0 test --locked -p anima-corefs transaction::cache_tests::changed_object_key_epoch_never_reuses_binding -- --exact
cargo +1.75.0 test --locked -p anima-corefs --test transaction cache_hit_rejects_missing_object -- --exact
cargo +1.75.0 test --locked -p anima-corefs --test transaction cache_hit_rejects_empty_object -- --exact
cargo +1.75.0 test --locked -p anima-corefs --test transaction cache_hit_rejects_symlinked_object -- --exact
cargo +1.75.0 test --locked -p anima-corefs --test transaction cache_hit_rejects_replaced_object -- --exact
cargo +1.75.0 test --locked -p anima-corefs --test transaction cache_hit_rejects_unexpected_hard_link -- --exact
```

Expected: the four binding-cache tests FAIL because prepared-revision validation neither returns nor consumes non-empty `ValidatedObjectState`. The missing, empty, symlink, replacement, and unexpected-hard-link tests PASS through the uncached safe-open path as pre-change characterizations; rerun all nine after caching.

- [ ] **Step 3: Populate and consume the Task 3 object-state API**

Make `validate_prepared_revisions` return immutable state for the next authoritative catalog. Each binding covers exact object ID, revision, object-key epoch, physical name, content hash, kind, complete wrapped-DEK record, and a domain-separated non-secret binding digest. A cache hit requires exact snapshot pointers plus exact object-wrap key identity and exact tuple equality. An epoch-only difference, changed wrapper, or wrong material never reuses a binding. On a miss, an unchanged exact tuple unwraps at most once; new/changed objects retain exact prepared token, wrapped-key, length, encrypted-hash, epoch, and binding checks.

- [ ] **Step 4: Preserve safe opens on all cache hits**

For every unchanged object, still call `validate_existing_object_file`/`open_regular_file_in` and enforce opened/linked identity equality, non-symlink regular file, allowed link count, and nonzero length. Cache only cryptographic binding work; never cache filesystem existence/layout.

- [ ] **Step 5: Publish object state only with durable authority**

Attach returned object state only to the cache snapshot for the corresponding durable HEAD/cutover completion. Authenticated cold loads retain `objects: None` until a successful validating commit. Pre-HEAD failures keep prior state; recovery-pending/ambiguous results clear it.

- [ ] **Step 6: Run focused/surrounding tests and commit**

```powershell
cargo +1.75.0 test --locked -p anima-corefs transaction::cache_tests -- --nocapture
cargo +1.75.0 test --locked -p anima-corefs --test transaction --test rotation
git add packages/anima-corefs/src/transaction.rs packages/anima-corefs/src/transaction/cache.rs packages/anima-corefs/src/transaction/cache_tests.rs packages/anima-corefs/tests/transaction.rs packages/anima-corefs/tests/rotation.rs
git -c commit.gpgsign=false commit -m "perf: reuse validated CoreFS object bindings"
```

Expected: PASS; exact hits perform zero repeated unwraps while all five invalid-layout classes and both key-binding mismatch classes still fail closed.

### Task 9: Run complete correctness gates and an asserted disposable diagnostic

**Files:**
- Modify only when a failing regression exposes a defect in Task 1-8 files
- Modify: `packages/anima-corefs/src/benchmark.rs` for one private timing wrapper
- Test: `packages/anima-corefs/src/benchmark.rs` (new focused unit test)
- Test: `packages/anima-corefs/tests/catalog_benchmark.rs` (existing publication/full-byte characterizations)
- No reference artifact update in this task

- [ ] **Step 1: Add a failing proof that timing wraps the complete public commit call**

Add a private `measure_public_commit` helper used by the measured loop and a unit test:

```rust
#[test]
fn measured_interval_wraps_the_complete_public_commit_callback() {
    // The closure records entry/exit and sleeps briefly; elapsed includes the sleep
    // and is returned only after the full closure result.
}
```

Retain the existing integration characterizations `percentile_uses_deterministic_nearest_rank_and_report_schema_has_required_metrics` (publication path begins `commit-lock`, ends `fs-head-write-flush`, one production serialization) and `measured_runner_publishes_real_exact_catalogs_for_all_reference_generations` (real full-size catalog bytes, final HEAD and catalog count).

- [ ] **Step 2: Run the new RED test and existing GREEN benchmark characterizations**

```powershell
cargo +1.75.0 test --locked -p anima-corefs benchmark::tests::measured_interval_wraps_the_complete_public_commit_callback -- --exact
cargo +1.75.0 test --locked -p anima-corefs --test catalog_benchmark percentile_uses_deterministic_nearest_rank_and_report_schema_has_required_metrics -- --exact
cargo +1.75.0 test --locked -p anima-corefs --test catalog_benchmark measured_runner_publishes_real_exact_catalogs_for_all_reference_generations -- --exact
```

Expected: the new helper test FAILS to compile; both existing characterization tests PASS.

- [ ] **Step 3: Implement the timing wrapper and rerun all four benchmark-contract properties**

The helper takes a closure, captures `Instant::now()` immediately before invoking it, and computes elapsed immediately after it returns. The measured loop calls it with the exact public `coordinator.commit(...)` call; catalog construction outside `commit` remains outside the timer, while builder callback execution, validation, complete serialization/encryption, kernel lock, durable HEAD-last publication, and invalidation inside public `commit` remain inside.

```powershell
cargo +1.75.0 test --locked -p anima-corefs benchmark::tests::measured_interval_wraps_the_complete_public_commit_callback -- --exact
cargo +1.75.0 test --locked -p anima-corefs --test catalog_benchmark percentile_uses_deterministic_nearest_rank_and_report_schema_has_required_metrics -- --exact
cargo +1.75.0 test --locked -p anima-corefs --test catalog_benchmark measured_runner_publishes_real_exact_catalogs_for_all_reference_generations -- --exact
git add packages/anima-corefs/src/benchmark.rs packages/anima-corefs/tests/catalog_benchmark.rs
git -c commit.gpgsign=false commit -m "test: prove complete CoreFS benchmark commit timing"
```

Expected: PASS. Together these exact tests prove benchmark contract item 16: lock acquisition first, HEAD publication last, complete public `commit` wall time, and real full catalog bytes.

- [ ] **Step 4: Run format and diff hygiene**

```powershell
cargo +1.75.0 fmt -p anima-corefs -- --check
git diff --check
git status --short
```

Expected: PASS and clean worktree.

- [ ] **Step 5: Run full Rust 1.75, strict Clippy, and Python gates**

```powershell
cargo +1.75.0 test --locked -p anima-corefs
cargo +1.75.0 clippy --locked -p anima-corefs --all-targets -- -D warnings
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'
uv run --locked --project apps/server pytest apps/server/tests/test_corefs_catalog_benchmark.py -q
```

Expected: all CoreFS tests pass (only existing crash helpers ignored), strict Clippy passes, and at least the existing 121 benchmark-contract tests pass.

- [ ] **Step 6: Run and assert a disposable 1/5 release diagnostic**

```powershell
$diag = Join-Path $env:TEMP ("anima-corefs-catalog-diagnostic-" + (Get-Date -Format 'yyyyMMdd-HHmmss'))
$diagJson = "$diag.json"
cargo +1.75.0 run --release --locked -p anima-corefs --bin catalog_benchmark -- --target $diag --warmups 1 --samples 5 | Tee-Object -FilePath $diagJson
$diagnosticExit = $LASTEXITCODE
if ($diagnosticExit -ne 0) { throw "diagnostic benchmark exited $diagnosticExit" }
$report = Get-Content -Raw -LiteralPath $diagJson | ConvertFrom-Json
$expected = @{
  'medium' = @{ live = 5000; tombstone = 500; total = 5500; baseline = 207.7262 }
  'maximum-live' = @{ live = 25000; tombstone = 2500; total = 27500; baseline = 1060.9271 }
  'serialized-limit' = @{ live = 25000; tombstone = 0; total = 25000; baseline = 1131.7692 }
}
if (@($report.fixtures).Count -ne 3) { throw 'diagnostic fixture count mismatch' }
foreach ($fixture in $report.fixtures) {
  if (-not $expected.ContainsKey([string]$fixture.name)) { throw "unexpected fixture $($fixture.name)" }
  $want = $expected[[string]$fixture.name]
  if ($fixture.liveCount -ne $want.live -or $fixture.tombstoneCount -ne $want.tombstone -or $fixture.totalCount -ne $want.total) { throw "$($fixture.name) logical counts mismatch" }
  if ($fixture.warmupCommits -ne 1 -or $fixture.sampleCount -ne 5 -or $fixture.finalHeadGeneration -ne 8 -or $fixture.finalCatalogCount -ne 8) { throw "$($fixture.name) generation/sample counts mismatch" }
  if ($fixture.productionSerializationsPerCommit -ne 1) { throw "$($fixture.name) left production serialization path" }
  if ([double]$fixture.commitMs.p95 -gt [double]$want.baseline) { throw "$($fixture.name) shows no directional improvement" }
}
```

Expected: exit 0 and every assertion passes. This diagnostic is directional, not acceptance evidence.

- [ ] **Step 7: Handle a correctness or diagnostic failure explicitly**

If correctness, provenance-shape, or safe-open assertions fail, add a focused RED regression in the originating task's test file, make the narrow approved fix, commit it separately as `fix: preserve CoreFS catalog commit invariants`, and rerun all of Task 9. If correctness passes but a directional timing assertion fails, use only the approved scoped counters to locate unfinished redundant work; add a focused RED/GREEN fix and commit `perf: complete approved CoreFS catalog fast path`, then rerun all of Task 9. If resolving it requires a new format, weaker open/durability/recovery rule, fixture/timer/threshold change, or other unapproved architecture, stop and return for design approval.

- [ ] **Step 8: Confirm source cleanliness for provenance**

```powershell
git status --short
git log -12 --oneline
```

Expected: clean worktree and the scoped implementation commits are visible. Do not amend or create code commits after this checkpoint without rerunning Tasks 9-10.

### Task 10: Generate exact reference evidence and synchronize project state

**Files:**
- Modify: `docs/benchmarks/portable-core-filesystem/catalog-reference-v1.json`
- Modify: `docs/superpowers/specs/2026-07-20-corefs-catalog-commit-performance-design.md`
- Modify: `tickets/portable-core-filesystem/PCF-002-corefs-catalog.md`
- Modify: `tickets/portable-core-filesystem/PCF-000-portable-core-filesystem.md`

- [ ] **Step 1: Verify and archive only the approved create-only target**

```powershell
$benchmarkRoot = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'animaOS\benchmarks'))
$target = [IO.Path]::GetFullPath((Join-Path $benchmarkRoot 'corefs-catalog-reference-v1'))
if (-not $target.StartsWith($benchmarkRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'unsafe benchmark target' }
if (Test-Path -LiteralPath $target) {
  $archive = "$target.pre-cache-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
  Move-Item -LiteralPath $target -Destination $archive
}
```

Expected: the exact prior target moves intact to one sibling archive; no recursive delete is used.

- [ ] **Step 2: Recompute the target and run the unchanged exact 30/200 command**

```powershell
$benchmarkRoot = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'animaOS\benchmarks'))
$target = [IO.Path]::GetFullPath((Join-Path $benchmarkRoot 'corefs-catalog-reference-v1'))
if (-not $target.StartsWith($benchmarkRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'unsafe benchmark target' }
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'
uv run --locked --project apps/server python apps/server/scripts/benchmark_corefs_catalog.py --reference --target $target
$referenceExit = $LASTEXITCODE
if ($referenceExit -ne 0 -and $referenceExit -ne 2) { throw "reference benchmark exited unexpected code $referenceExit" }
if ($referenceExit -eq 0) { Write-Output 'reference command passed; validate green artifact' }
if ($referenceExit -eq 2) { Write-Output 'reference command produced a red artifact; validate it, then take blocker branch' }
```

Expected pass path: exit 0. Exit 2 is allowed only because the strict artifact was produced with one or more unchanged red gates; continue provenance validation, then take the blocker branch.

- [ ] **Step 3: Recompute paths and independently validate the actual artifact**

```powershell
$benchmarkRoot = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'animaOS\benchmarks'))
$target = [IO.Path]::GetFullPath((Join-Path $benchmarkRoot 'corefs-catalog-reference-v1'))
if (-not $target.StartsWith($benchmarkRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'unsafe benchmark target' }
$env:ANIMA_EXPECTED_REFERENCE_TARGET = $target
@'
import hashlib, json, os, pathlib, subprocess, sys
sys.path.insert(0, str(pathlib.Path("apps/server/scripts").resolve()))
import benchmark_corefs_catalog as bench

artifact_path = pathlib.Path("docs/benchmarks/portable-core-filesystem/catalog-reference-v1.json")
artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
target = pathlib.Path(os.environ["ANIMA_EXPECTED_REFERENCE_TARGET"]).resolve(strict=True)
binary = pathlib.Path(artifact["benchmarkBinary"]["path"]).resolve(strict=True)
binary_evidence = bench.probe_reference_path(binary)
target_evidence = bench.probe_reference_path(target)
assert artifact["sourceCommit"] == head
assert artifact["benchmarkBuild"]["sourceCommit"] == head
assert binary_evidence.volume_serial == artifact["benchmarkBinary"]["volumeSerial"]
assert binary_evidence.file_id == artifact["benchmarkBinary"]["fileId"]
assert not (binary_evidence.attributes & bench.CLOUD_OR_REPARSE_ATTRIBUTES)
assert not (target_evidence.attributes & bench.CLOUD_OR_REPARSE_ATTRIBUTES)
assert os.path.normcase(str(binary_evidence.canonical_path)) == os.path.normcase(str(binary))
assert os.path.normcase(str(target_evidence.canonical_path)) == os.path.normcase(str(target))
assert hashlib.sha256(binary.read_bytes()).hexdigest() == artifact["benchmarkBinary"]["sha256"]
committed_lock = subprocess.check_output(["git", "show", "HEAD:Cargo.lock"])
assert hashlib.sha256(committed_lock).hexdigest() == artifact["benchmarkBuild"]["cargoLockSha256"]
build = artifact["benchmarkBuild"]
expected_build_command = ["cargo", "+1.75.0", "build", "--release", "--locked", "-p", "anima-corefs", "--bin", "catalog_benchmark", "--target-dir", build["targetDirectory"]]
assert build["command"] == expected_build_command and build["preservedForAudit"] is True
assert build["forcedEnvironment"] == {"CARGO_INCREMENTAL": "0"}
expected_binary = pathlib.Path(build["targetDirectory"]) / "release" / "catalog_benchmark.exe"
assert os.path.normcase(str(expected_binary.resolve())) == os.path.normcase(str(binary))
expected_argv = [str(binary), "--target", str(target), "--warmups", "30", "--samples", "200"]
assert [os.path.normcase(v) if i in (0, 2) else v for i, v in enumerate(artifact["benchmarkCommand"])] == [os.path.normcase(v) if i in (0, 2) else v for i, v in enumerate(expected_argv)]
assert os.path.normcase(artifact["profile"]["target"]) == os.path.normcase(str(target))
expected_counts = {"medium": (5000, 500, 5500, 500), "maximum-live": (25000, 2500, 27500, 2500), "serialized-limit": (25000, 0, 25000, 0)}
assert artifact["warmupCommits"] == 30 and artifact["measuredCommits"] == 200
assert {row["name"] for row in artifact["fixtures"]} == set(expected_counts)
for row in artifact["fixtures"]:
    live, tombstone, total, object_count = expected_counts[row["name"]]
    assert (row["liveCount"], row["tombstoneCount"], row["totalCount"]) == (live, tombstone, total)
    assert row["warmupCommits"] == 30 and row["sampleCount"] == 200
    assert row["finalHeadGeneration"] == 232 and row["finalCatalogCount"] == 232
    catalog_root = target / row["name"] / "fs"
    object_root = target / row["name"] / "objects"
    assert (catalog_root / "HEAD").is_file()
    assert len(list((catalog_root / "catalogs").iterdir())) == row["finalCatalogCount"]
    assert len(list(object_root.iterdir())) == object_count
    assert not any(path.name.endswith(".tmp") for path in (target / row["name"]).rglob("*"))
bench.validate_and_finalize_report(
    artifact,
    expected_source_commit=head,
    expected_binary_path=binary,
    expected_binary_sha256=artifact["benchmarkBinary"]["sha256"],
    expected_binary_volume_serial=binary_evidence.volume_serial,
    expected_binary_file_id=binary_evidence.file_id,
    expected_reference_target=target,
    expected_benchmark_build=build,
)
print({"allPassed": artifact["gates"]["allPassed"], "binaryIdentity": (binary_evidence.volume_serial, binary_evidence.file_id), "targetIdentity": (target_evidence.volume_serial, target_evidence.file_id)})
'@ | uv run --locked --project apps/server python -
```

The snippet above is the generation-time validator: before the evidence commit, current `HEAD` is the source commit measured into the artifact. A post-handoff audit must instead use the artifact's recorded `sourceCommit`, verify the benchmark source surface and committed `Cargo.lock` at that commit, and confirm that later commits are limited to the generated evidence and documentation/state synchronization. It must not rewrite the artifact to the later metadata-only `HEAD`.

Expected: PASS whether gates are green or red. The validator enforces the closed schema and recalculated gates; the explicit assertions bind current source, binary file identity/hash/path, private build/Cargo.lock, exact target/argv, fixture logical counts, on-disk object/catalog counts, and final generations.

- [ ] **Step 4: Re-run the full contract plus named provenance/identity regressions**

```powershell
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'
uv run --locked --project apps/server pytest apps/server/tests/test_corefs_catalog_benchmark.py -q
uv run --locked --project apps/server pytest apps/server/tests/test_corefs_catalog_benchmark.py -q -k "complete_reference_report_schema_is_accepted or strict_report_schema_rejects_every_missing_required_family or strict_report_schema_rejects_extra_or_contradictory_records or report_is_bound_to_exact_fixtures_command_source_and_binary or post_run_binary_provenance_rejects_identity_or_hash_changes or reference_target_chain_revalidation_rejects_identity_changes or held_reference_target_chain_blocks_target_rename"
git diff --check
```

Expected: PASS. The named tests explicitly prove closed-schema, exact command/source/binary/fixture binding, post-run binary identity, and target identity/rename protection.

- [ ] **Step 5: Record either the pass path or blocker path atomically**

Pass path:

- set the spec status to `Implemented and locally validated; exact reference gates passed`;
- append exact p50/p95/p99, artifact/binary/Cargo.lock hashes, binary and target identities, fixture counts, and validation commands to PCF-002;
- update PCF-002 and PCF-000 timestamps/activity together;
- keep PCF-002 and the parent `in_progress` pending separately authorized publication/current-head review, and keep dependency-ineligible children unchanged; and
- record changed paths and residual risk (`none` for local correctness; publication/review remains separate).

Red-artifact path:

- preserve the generated red artifact and set the spec status to `Implemented and correctness-validated; exact reference gates failed; architecture revision required`;
- set PCF-002 to `blocked`, preserve its original `Started:`, and record exact failing gates plus the clearance (`separately approved architecture revision`);
- reread PCF-000 and every remaining child ticket, recompute dependency eligibility from current statuses, update the PCF-002 row, and set PCF-000 to `blocked` only if no other child is legally eligible to progress; otherwise keep the parent `in_progress` and name the eligible child;
- keep every unrelated child's ownership/status unchanged; and
- do not implement delta catalogs, skip object opens, change fixtures, or loosen thresholds.

- [ ] **Step 6: Commit evidence and synchronized metadata**

Pass:

```powershell
git add docs/benchmarks/portable-core-filesystem/catalog-reference-v1.json docs/superpowers/specs/2026-07-20-corefs-catalog-commit-performance-design.md tickets/portable-core-filesystem/PCF-002-corefs-catalog.md tickets/portable-core-filesystem/PCF-000-portable-core-filesystem.md
git -c commit.gpgsign=false commit -m "bench: pass CoreFS catalog reference gates"
```

Red artifact:

```powershell
git add docs/benchmarks/portable-core-filesystem/catalog-reference-v1.json docs/superpowers/specs/2026-07-20-corefs-catalog-commit-performance-design.md tickets/portable-core-filesystem/PCF-002-corefs-catalog.md tickets/portable-core-filesystem/PCF-000-portable-core-filesystem.md
git -c commit.gpgsign=false commit -m "bench: record CoreFS catalog performance blocker"
```

- [ ] **Step 7: Stop before external actions**

```powershell
git status --short
git log -15 --oneline
```

Expected: clean worktree. Report the result and request separate authority for any push, PR creation/update, `@codex review`, feedback handling, monitoring, or merge. Do not perform any external action under plan-execution authority alone.
