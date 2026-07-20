# CoreFS Catalog Commit Performance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make steady-state CoreFS full-catalog commits pass the existing PCF-002 latency gates without changing the V2 wire format, durability/recovery semantics, safe object-file checks, or benchmark contract.

**Architecture:** Keep disk state authoritative and add a process-local `Arc` snapshot selected only after exact pointer and FRK-derived key-identity checks. Remove redundant catalog decrypt/validation/hash passes at the trusted coordinator boundary, retain strict public/untrusted paths, then reduce allocation and unchanged-object key-unwrapping work while continuing to safely reopen every referenced immutable object.

**Tech Stack:** Rust 1.75, `cap-std`, `fs4`, `aes-gcm`, `hkdf`, `sha2`, Rust unit/integration tests, Python 3.12/pytest benchmark-contract validation, PowerShell, Git.

---

## Source of truth and stop rules

- Approved spec: `docs/superpowers/specs/2026-07-20-corefs-catalog-commit-performance-design.md`
- Active child: `tickets/portable-core-filesystem/PCF-002-corefs-catalog.md`
- Parent tracker: `tickets/portable-core-filesystem/PCF-000-portable-core-filesystem.md`
- Existing umbrella plan: `docs/superpowers/plans/2026-07-12-portable-core-filesystem.md`, Task 2 Steps 12-14
- Baseline source: merged `main` at `5a3a7a0feadad5734e297ce2e09835008660da15`
- Baseline evidence: medium/maximum-live/serialized-limit commit p95 values are 207.7262/1,060.9271/1,131.7692 ms; the unchanged gates are 100/250/250 ms.

Do not move serialization or encryption outside the measured public `commit` call. Do not remove the bounded serialization preflight, kernel lock, fsync/HEAD-last publication, recovery markers, strict decode, prepared-object verification, or per-commit `open_regular_file_in` validation for unchanged objects. If the final exact 30/200 run remains red after these approved changes, record the evidence, return PCF-002 and its parent to `blocked`, and stop for a separately approved architecture revision.

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
```

Use a private, test-only observer passed through the verification helper (not a process-global atomic) so the first test asserts exactly one decrypt/strict decode in one invocation and also asserts that the returned generation is that authenticated value. Retain tests proving tampered ciphertext, wrong Core ID, wrong FRK material, wrong generation, and non-canonical plaintext still fail through public APIs.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
cargo +1.75.0 test --locked -p anima-corefs head::tests::verified_catalog_open_decrypts_once_and_returns_that_generation -- --exact
cargo +1.75.0 test --locked -p anima-corefs head::tests::trusted_publication_head_matches_public_constructor_bytes -- --exact
cargo +1.75.0 test --locked -p anima-corefs catalog::v2::tests::publication_artifact_reuses_one_digest_for_name_and_head -- --exact
```

Expected: FAIL to compile because the verify-and-return helper and publication artifact do not exist.

- [ ] **Step 3: Add the minimal internal publication artifact**

In `catalog/v2.rs`, keep `encrypt_catalog_generation` public behavior unchanged and introduce a crate-private result for the coordinator:

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

Change `load_pointer_for_head` to consume the catalog returned by `verify_and_decrypt_catalog` instead of calling `decrypt_catalog_generation` again. Change `publish_catalog_pointer_with_hook` to use `CatalogPublication` for the physical name, plaintext size, encrypted bytes, and trusted HEAD.

- [ ] **Step 6: Run focused and surrounding tests**

Run:

```powershell
cargo +1.75.0 test --locked -p anima-corefs head::tests -- --nocapture
cargo +1.75.0 test --locked -p anima-corefs catalog::v2::tests -- --nocapture
cargo +1.75.0 test --locked -p anima-corefs --test catalog --test transaction
```

Expected: PASS; public tamper/wrong-key tests remain green and trusted/public HEAD encodings are byte-identical.

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
```

- [ ] **Step 2: Run focused tests and verify RED**

```powershell
cargo +1.75.0 test --locked -p anima-corefs catalog::v2::tests::validated_marker_path_preserves_canonical_bytes -- --exact
cargo +1.75.0 test --locked -p anima-corefs catalog::v2::tests::trusted_encoder_keeps_the_bounded_preflight -- --exact
```

Expected: FAIL because no validated encoder/marker seam exists.

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

Because `CatalogGeneration` fields are private and `CatalogCutoverMarker` is validated at construction, make `with_cutover_marker` update only the marker in release builds. A debug-only assertion may run full validation, but production commit code must not rescan entries.

- [ ] **Step 5: Run focused and complete catalog tests**

```powershell
cargo +1.75.0 test --locked -p anima-corefs catalog::v2::tests -- --nocapture
cargo +1.75.0 test --locked -p anima-corefs --test catalog --test catalog_entries
```

Expected: PASS; canonical bytes, allocation bounds, reserved-state protection, graph validation, and non-canonical rejection are unchanged.

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
fn same_version_different_catalog_or_object_wrap_material_misses() { /* derive two identities */ }

#[test]
fn exact_pointer_and_key_identity_returns_the_arc_snapshot() { /* Arc::ptr_eq */ }

#[test]
fn poisoned_cache_is_cleared_and_treated_as_a_miss() { /* no storage error */ }

#[test]
fn cache_guard_is_released_before_observer_or_lock_callbacks() { /* probe try_lock */ }
```

- [ ] **Step 2: Run the new module tests and verify RED**

```powershell
cargo +1.75.0 test --locked -p anima-corefs transaction::cache_tests -- --nocapture
```

Expected: FAIL to compile because `transaction::cache` and the coordinator cache field do not exist.

- [ ] **Step 3: Implement domain-separated key identities outside the mutex**

In `cache.rs`, derive fixed 32-byte identifiers separately from `FrkSubkeys::catalog()` and `FrkSubkeys::object_wrap()` using HKDF-SHA256. The info bytes must include a stable domain, Core ID length/value, FRK version, and purpose:

```rust
const CACHE_ID_DOMAIN: &[u8] = b"anima-corefs-commit-cache-key-id-v1\0";

#[derive(Clone, Debug, Eq, PartialEq)]
struct CatalogKeyCacheId([u8; 32]);

#[derive(Clone, Debug, Eq, PartialEq)]
struct ObjectWrapKeyCacheId([u8; 32]);
```

Derive catalog IDs for every distinct version referenced by HEAD/receipt/completion through `FrkKeyring::require`. Derive the active object-wrap ID separately. Never store raw subkeys and never use these IDs as cryptographic authorization outside cache selection.

- [ ] **Step 4: Implement pointer and immutable snapshot types**

```rust
#[derive(Clone, Debug, Eq, PartialEq)]
struct PointerSet {
    head: Option<HeadRecord>,
    receipt: Option<HeadRecord>,
    complete: Option<HeadRecord>,
}

struct AuthenticatedCommitSnapshot {
    pointers: PointerSet,
    key_ids: RequiredCacheKeyIds,
    catalog: Arc<CatalogGeneration>,
    objects: Option<Arc<ValidatedObjectState>>,
}
```

The hit predicate compares the complete pointer set, Core binding, required catalog-key IDs, and the object-wrap ID before returning cached object bindings.

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

No method accepts an I/O, crypto, build, failure-hook, invalidation, or kernel-lock closure. Add test-only guard/counter probes in this module only.

- [ ] **Step 6: Run cache tests and strict lint**

```powershell
cargo +1.75.0 test --locked -p anima-corefs transaction::cache_tests -- --nocapture
cargo +1.75.0 clippy --locked -p anima-corefs --all-targets -- -D warnings
```

Expected: PASS; wrong same-version material misses, poisoned state is discarded, and only immutable `Arc` values escape the short lock.

- [ ] **Step 7: Commit Task 3**

```powershell
git add packages/anima-corefs/src/transaction.rs packages/anima-corefs/src/transaction/cache.rs packages/anima-corefs/src/transaction/cache_tests.rs
git -c commit.gpgsign=false commit -m "perf: add authenticated CoreFS commit cache"
```

### Task 4: Integrate cache authority with load, commit, recovery, and rotation

**Files:**
- Modify: `packages/anima-corefs/src/transaction.rs:670-683,790-850,1156-1536,1937-2045`
- Modify: `packages/anima-corefs/src/transaction/cache.rs`
- Modify: `packages/anima-corefs/src/transaction/cache_tests.rs`
- Modify: `packages/anima-corefs/src/transaction/failure_tests.rs`
- Test: `packages/anima-corefs/tests/transaction.rs`
- Test: `packages/anima-corefs/tests/rotation.rs`

- [ ] **Step 1: Add failing same-process, cross-coordinator, wrong-key, and recovery tests**

Add public/integration regressions named:

```rust
#[test]
fn same_coordinator_reuses_only_the_exact_authenticated_head() { /* two normal commits */ }

#[test]
fn another_coordinator_advancing_head_forces_a_cache_miss() { /* shared root */ }

#[test]
fn cached_commit_rejects_wrong_same_version_active_material() { /* same version, different FRK */ }

#[test]
fn cached_load_rejects_wrong_same_version_retained_material() { /* receipt/complete keyring */ }
```

Add failure/concurrency tests named:

```rust
#[test]
fn pre_head_failure_keeps_only_the_prior_snapshot() { /* injected publication failure */ }

#[test]
fn post_head_recovery_pending_clears_the_cache() { /* receipt/completion failure */ }

#[test]
fn concurrent_unlocked_load_recovery_and_commit_do_not_invert_locks() { /* channels + timeout */ }
```

- [ ] **Step 2: Run the focused tests and verify RED**

```powershell
cargo +1.75.0 test --locked -p anima-corefs --test transaction same_coordinator_reuses_only_the_exact_authenticated_head -- --exact
cargo +1.75.0 test --locked -p anima-corefs --test transaction cached_commit_rejects_wrong_same_version_active_material -- --exact
cargo +1.75.0 test --locked -p anima-corefs transaction::failure_tests::post_head_recovery_pending_clears_the_cache -- --exact
```

Expected: FAIL because coordinator load/commit paths do not consult or publish cache state.

- [ ] **Step 3: Store catalog values by immutable `Arc` without changing the public API**

Change private `CommittedCatalog.catalog` storage to `Arc<CatalogGeneration>` and keep:

```rust
pub fn catalog(&self) -> &CatalogGeneration {
    self.catalog.as_ref()
}
```

Wrap validation/cold-load values once. Do not clone 5,500-27,500 entries on a cache hit.

- [ ] **Step 4: Add exact cache selection to unlocked and locked loads**

Refactor pointer reads into one `PointerSet`. Derive all required key identities before briefly reading the cache. On a hit, return the `Arc` catalog. On a miss, execute the existing complete recovery/authentication path, then cache only the authenticated catalog portion.

Public `load_committed` must still perform its second HEAD stability read. Release any cache guard before that read and before any recovery branch acquires `CoreCommitLock`.

- [ ] **Step 5: Add kernel-lock-first cache selection to normal commits**

Inside `commit_internal_with_keyring_and_hook`:

1. acquire `CoreCommitLock`;
2. validate pinned layout and read pointer set;
3. derive key identities without the cache mutex;
4. briefly select an `Arc` snapshot or run full recovery;
5. run preconditions/build/validation/publication without the cache mutex; and
6. replace/clear cache only after the durable result is known.

Keep the old snapshot on pre-HEAD failure. Clear on `recovery_pending`. A successful durable commit may replace the cache before the external invalidation callback; callback failure must not roll back disk or cache authority.

- [ ] **Step 6: Apply the same invalidation rules to initialization, recovery, and FRK rotation**

Validation-only initialization must not masquerade as an authoritative cached HEAD. Cutover recovery and rotation must use exact pointer/key identities and replace cache only after verified on-disk completion. Same-version different retained material must miss and fail normal authentication.

- [ ] **Step 7: Run all focused transaction/rotation/failure tests**

```powershell
cargo +1.75.0 test --locked -p anima-corefs --test transaction --test rotation
cargo +1.75.0 test --locked -p anima-corefs transaction::failure_tests -- --nocapture
cargo +1.75.0 test --locked -p anima-corefs transaction::cache_tests -- --nocapture
```

Expected: PASS without deadlock; recovery tests preserve the prior-HEAD-or-complete-next-generation invariant.

- [ ] **Step 8: Commit Task 4**

```powershell
git add packages/anima-corefs/src/transaction.rs packages/anima-corefs/src/transaction/cache.rs packages/anima-corefs/src/transaction/cache_tests.rs packages/anima-corefs/src/transaction/failure_tests.rs packages/anima-corefs/tests/transaction.rs packages/anima-corefs/tests/rotation.rs
git -c commit.gpgsign=false commit -m "perf: reuse exact authenticated CoreFS snapshots"
```

### Task 5: Remove allocation and unchanged-object key-work duplication

**Files:**
- Modify: `packages/anima-corefs/src/transaction.rs:2307-2476`
- Modify: `packages/anima-corefs/src/transaction/cache.rs`
- Modify: `packages/anima-corefs/src/transaction/cache_tests.rs`
- Test: `packages/anima-corefs/tests/transaction.rs`

- [ ] **Step 1: Add failing precondition-equivalence and object-cache tests**

Add tests named:

```rust
#[test]
fn ordered_coverage_matches_changed_created_moved_and_deleted_cases() { /* table-driven */ }

#[test]
fn exact_cached_object_tuple_skips_repeated_dek_unwrap() { /* test counter */ }

#[test]
fn changed_wrapper_or_object_wrap_identity_never_reuses_binding() { /* typed mismatch */ }

#[test]
fn cache_hit_still_rejects_missing_empty_and_unexpected_link_objects() { /* safe open */ }
```

Use the existing safe-open/link test helpers. Do not replace file opens with directory timestamps or existence-only metadata.

- [ ] **Step 2: Run focused tests and verify RED**

```powershell
cargo +1.75.0 test --locked -p anima-corefs transaction::cache_tests::exact_cached_object_tuple_skips_repeated_dek_unwrap -- --exact
cargo +1.75.0 test --locked -p anima-corefs --test transaction cache_hit_still_rejects_missing_empty_and_unexpected_link_objects -- --exact
```

Expected: FAIL because validation neither returns reusable object state nor consumes cached bindings.

- [ ] **Step 3: Replace full hash maps in precondition coverage with an ordered merge**

Exploit canonical stable-ID ordering:

```rust
match current_id.cmp(next_id) {
    Ordering::Less => validate_changed_or_deleted_source(current_entry, preconditions)?,
    Ordering::Equal => validate_changed_source_and_destination(current_entry, next_entry, preconditions)?,
    Ordering::Greater => validate_new_destination(next_entry, current, preconditions)?,
}
```

Index only precondition-referenced existing parents when destination checks need lookup. Preserve every `MissingSourcePrecondition` and `MissingDestinationPrecondition` result from existing integration tests.

- [ ] **Step 4: Return validated object state from prepared-revision validation**

Make `validate_prepared_revisions` return an immutable `ValidatedObjectState` for the next authoritative catalog. Each record contains the exact stable ID and immutable object tuple plus the non-secret binding digest.

On cache miss, an unchanged exact tuple needs at most one unwrap, not two identical unwraps. On an exact snapshot plus object-wrap-key-identity hit, reuse the cached binding without unwrap. New/changed objects retain exact prepared token, wrapped-key, length, and encrypted-hash checks.

- [ ] **Step 5: Preserve per-commit safe object opens**

For every unchanged object, continue to call `validate_existing_object_file`, which uses `open_regular_file_in`, opened/linked identity equality, non-symlink regular-file checks, allowed link-count rules, and the nonzero-length check. Cache only cryptographic validation, never filesystem presence/layout.

- [ ] **Step 6: Publish object state only with the durable next snapshot**

Attach the returned `ValidatedObjectState` to the new cache snapshot only after the corresponding HEAD/cutover completion is durable. Authenticated cold loads have `objects: None` until a commit validates them. Failures follow Task 4's keep/clear rules.

- [ ] **Step 7: Run focused and surrounding tests**

```powershell
cargo +1.75.0 test --locked -p anima-corefs transaction::cache_tests -- --nocapture
cargo +1.75.0 test --locked -p anima-corefs --test transaction
cargo +1.75.0 test --locked -p anima-corefs --test rotation
```

Expected: PASS; diagnostics show zero repeated unchanged-object unwraps on an exact cache hit, while every safe-open regression still rejects invalid layout.

- [ ] **Step 8: Commit Task 5**

```powershell
git add packages/anima-corefs/src/transaction.rs packages/anima-corefs/src/transaction/cache.rs packages/anima-corefs/src/transaction/cache_tests.rs packages/anima-corefs/tests/transaction.rs
git -c commit.gpgsign=false commit -m "perf: reuse validated CoreFS object bindings"
```

### Task 6: Run complete correctness gates and a disposable diagnostic profile

**Files:**
- Modify only if a test exposes a defect: Task 1-5 implementation/test files
- No reference artifact update in this task

- [ ] **Step 1: Run format and diff hygiene**

```powershell
cargo +1.75.0 fmt -p anima-corefs -- --check
git diff --check
git status --short
```

Expected: PASS; the worktree is clean because every implementation task was committed.

- [ ] **Step 2: Run the full Rust 1.75 and strict Clippy gates**

```powershell
cargo +1.75.0 test --locked -p anima-corefs
cargo +1.75.0 clippy --locked -p anima-corefs --all-targets -- -D warnings
```

Expected: PASS; only the existing subprocess crash helpers are ignored by their parent harness.

- [ ] **Step 3: Run the complete Python benchmark contract**

```powershell
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'
uv run --locked --project apps/server pytest apps/server/tests/test_corefs_catalog_benchmark.py -q
```

Expected: 121 tests pass unless new focused contract cases intentionally increase the count; no existing case regresses.

- [ ] **Step 4: Run a disposable 1/5 release diagnostic**

```powershell
$diag = Join-Path $env:TEMP ("anima-corefs-catalog-diagnostic-" + (Get-Date -Format 'yyyyMMdd-HHmmss'))
$diagJson = "$diag.json"
cargo +1.75.0 run --release --locked -p anima-corefs --bin catalog_benchmark -- --target $diag --warmups 1 --samples 5 | Tee-Object -FilePath $diagJson
$report = Get-Content -Raw -LiteralPath $diagJson | ConvertFrom-Json
$report.fixtures | Select-Object name, commitMs, lockHoldMs, serializedSizeBytes
```

Expected: exit 0, three real fixture reports, generation/catalog count 8 for each fixture, and no fixture slower than its pre-optimization p95 baseline. This diagnostic is directional only and is not acceptance evidence. If it shows no improvement or a gross regression, stop before the expensive reference run and return to the approved design rather than changing thresholds or fixtures.

- [ ] **Step 5: Confirm source cleanliness for provenance**

```powershell
git status --short
git log -5 --oneline
```

Expected: no output from status; the five scoped implementation commits are present. Do not amend or create code commits after this checkpoint without rerunning Tasks 6-7.

### Task 7: Generate exact reference evidence and synchronize project state

**Files:**
- Modify: `docs/benchmarks/portable-core-filesystem/catalog-reference-v1.json`
- Modify: `docs/superpowers/specs/2026-07-20-corefs-catalog-commit-performance-design.md`
- Modify: `tickets/portable-core-filesystem/PCF-002-corefs-catalog.md`
- Modify: `tickets/portable-core-filesystem/PCF-000-portable-core-filesystem.md`

- [ ] **Step 1: Verify and archive only the approved create-only target**

```powershell
$benchmarkRoot = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'animaOS\benchmarks'))
$target = [IO.Path]::GetFullPath((Join-Path $benchmarkRoot 'corefs-catalog-reference-v1'))
if (-not $target.StartsWith($benchmarkRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw "unsafe benchmark target" }
if (Test-Path -LiteralPath $target) {
  $archive = "$target.pre-cache-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
  Move-Item -LiteralPath $target -Destination $archive
}
```

Expected: the exact prior target is moved intact to one sibling archive; no recursive delete is used.

- [ ] **Step 2: Run the unchanged exact 30/200 reference command**

```powershell
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'
uv run --locked --project apps/server python apps/server/scripts/benchmark_corefs_catalog.py --reference --target $target
```

Expected pass path: exit 0 and `gates.allPassed = true`. Exit 2 means the strict artifact was produced but one or more unchanged performance gates remain red; follow the failure branch in Step 5 and stop.

- [ ] **Step 3: Independently validate source, binary, lockfile, gates, and fixture tree**

```powershell
$artifactPath = 'docs/benchmarks/portable-core-filesystem/catalog-reference-v1.json'
$artifact = Get-Content -Raw -LiteralPath $artifactPath | ConvertFrom-Json
$head = (git rev-parse HEAD).Trim()
if ($artifact.sourceCommit -ne $head) { throw "artifact source mismatch" }
if (-not $artifact.gates.allPassed) { throw "reference gates failed" }
$binaryPath = [string]$artifact.benchmarkBinary.path
$binaryHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $binaryPath).Hash.ToLowerInvariant()
if ($binaryHash -ne [string]$artifact.benchmarkBinary.sha256) { throw "binary hash mismatch" }
@'
import hashlib, json, pathlib, subprocess
artifact = json.loads(pathlib.Path("docs/benchmarks/portable-core-filesystem/catalog-reference-v1.json").read_text())
committed = subprocess.check_output(["git", "show", "HEAD:Cargo.lock"])
assert hashlib.sha256(committed).hexdigest() == artifact["benchmarkBuild"]["cargoLockSha256"]
assert artifact["warmupCommits"] == 30 and artifact["measuredCommits"] == 200
assert {row["name"] for row in artifact["fixtures"]} == {"medium", "maximum-live", "serialized-limit"}
'@ | uv run --locked --project apps/server python -
foreach ($fixture in @('medium','maximum-live','serialized-limit')) {
  $root = Join-Path $target $fixture
  if (-not (Test-Path -LiteralPath (Join-Path $root 'fs\HEAD') -PathType Leaf)) { throw "$fixture HEAD missing" }
  if ((Get-ChildItem -LiteralPath (Join-Path $root 'fs\catalogs') -File).Count -ne 232) { throw "$fixture catalog count mismatch" }
  $expectedObjects = @{ 'medium' = 500; 'maximum-live' = 2500; 'serialized-limit' = 0 }
  if ((Get-ChildItem -LiteralPath (Join-Path $root 'fs\objects') -File).Count -ne $expectedObjects[$fixture]) { throw "$fixture object count mismatch" }
  if ((Get-ChildItem -LiteralPath $root -Recurse -File | Where-Object { $_.Name -like '*.tmp' }).Count -ne 0) { throw "$fixture temporary files remain" }
}
```

Expected: PASS. Compare the artifact's recorded final counts to the same independently observed fixture counts before recording acceptance.

- [ ] **Step 4: Re-run final contract and diff gates against the generated artifact**

```powershell
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'
uv run --locked --project apps/server pytest apps/server/tests/test_corefs_catalog_benchmark.py -q
git diff --check
```

Expected: PASS.

- [ ] **Step 5: Record either the pass path or the blocker path atomically**

Pass path:

- update the spec status to implemented and locally validated;
- append exact p50/p95/p99, artifact/binary/Cargo.lock hashes, target identity, fixture counts, and validation commands to PCF-002;
- update PCF-002 and PCF-000 timestamps/activity together;
- keep PCF-002 and the parent `in_progress` pending separately authorized publication/current-head review, and keep PCF-003 ineligible; and
- record changed paths and residual risk (`none` for local correctness; publication/review remains a separate action).

Failure path:

- preserve the generated red artifact;
- set PCF-002 to `blocked`, preserve its original `Started:`, and name the exact failing gates plus the required clearance (a separately approved architecture revision);
- set the parent PCF-002 row and top-level parent to `blocked` because PCF-003 remains dependency-ineligible; and
- do not implement delta catalogs, skip object opens, change fixtures, or loosen thresholds.

- [ ] **Step 6: Commit the evidence and synchronized metadata**

Pass:

```powershell
git add docs/benchmarks/portable-core-filesystem/catalog-reference-v1.json docs/superpowers/specs/2026-07-20-corefs-catalog-commit-performance-design.md tickets/portable-core-filesystem/PCF-002-corefs-catalog.md tickets/portable-core-filesystem/PCF-000-portable-core-filesystem.md
git -c commit.gpgsign=false commit -m "bench: pass CoreFS catalog reference gates"
```

Failure:

```powershell
git add docs/benchmarks/portable-core-filesystem/catalog-reference-v1.json docs/superpowers/specs/2026-07-20-corefs-catalog-commit-performance-design.md tickets/portable-core-filesystem/PCF-002-corefs-catalog.md tickets/portable-core-filesystem/PCF-000-portable-core-filesystem.md
git -c commit.gpgsign=false commit -m "bench: record CoreFS catalog performance blocker"
```

- [ ] **Step 7: Stop before external actions**

```powershell
git status --short
git log -7 --oneline
```

Expected: clean worktree. Report the result and request separate authority for any push, PR creation/update, `@codex review`, feedback handling, monitoring, or merge. Do not perform any of those actions under plan-execution authority alone.
