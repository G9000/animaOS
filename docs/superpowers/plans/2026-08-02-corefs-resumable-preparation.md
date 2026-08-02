# CoreFS Resumable Preparation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace PCF-004's aggregate in-memory validation-batch transport with an authenticated, encrypted, crash-resumable preparation protocol that handles writing corpora larger than 1 GiB while preserving one exact-CAS inactive-catalog publication.

**Architecture:** Rust owns all durable preparation state, prepared-object descriptors, validation, recovery, terminal receipts, and the single `VALIDATION_HEAD` transition. PyO3 exposes only session-guarded one-object and lifecycle operations. Python inventories SQLCipher writing sources, streams one canonical object at a time, and holds a `BEGIN IMMEDIATE` source fence from final reconciliation through native finalization. Public CoreFS mutation remains frozen and legacy SQLCipher remains authoritative until PCF-008.

**Tech Stack:** Rust 1.75 (`anima-corefs`, PyO3 `anima-core`), Python 3.12/FastAPI/SQLAlchemy/Alembic/SQLCipher, pytest, Bun workspace validation.

---

## Governing artifacts and non-negotiable invariants

- Approved design: `docs/superpowers/specs/2026-08-02-corefs-resumable-preparation-design.md`
- Product requirements: `docs/prds/portable-core-filesystem-v1.md`
- Child tracker: `tickets/portable-core-filesystem/PCF-004-diary-notes.md`
- Parent tracker: `tickets/portable-core-filesystem/PCF-000-portable-core-filesystem.md`
- Existing slice plan: `docs/superpowers/plans/2026-07-12-portable-core-filesystem.md#task-4-diary-folders-drafts-and-notes-vertical-slice`

Every task below must preserve these properties:

1. Collection and sealing never change `fs/VALIDATION_HEAD`; successful finalization changes it exactly once by exact-head CAS.
2. `fs/HEAD`, cutover receipts, and public mutation authority remain untouched.
3. Persistent preparation state is closed-schema, encrypted, authenticated, Core-bound, FRK-version-bound, and subject to explicit byte/count ceilings.
4. Peak migration memory is bounded by one source object, one encryption/copy buffer, and independently bounded metadata; no API accepts a corpus-wide `Vec<Vec<u8>>` or Python list of bodies.
5. Finalization validates the current SQLCipher source generation and inventory digest while a `BEGIN IMMEDIATE` fence prevents legacy writers from committing until publication finishes.
6. Recovery is deterministic across every durable boundary, including a crash after `VALIDATION_HEAD` publication but before terminal receipt publication.
7. Corrupt pointer quarantine never trusts unauthenticated pointer fields and cannot discard the only key needed to inspect retained encrypted state.

## Task 1: Add the preparation cryptographic domain and closed wire formats

**Files:**

- Modify: `packages/anima-corefs/src/crypto.rs`
- Create: `packages/anima-corefs/src/transaction/preparation.rs`
- Create: `packages/anima-corefs/src/transaction/preparation_tests.rs`
- Modify: `packages/anima-corefs/src/transaction.rs`

- [ ] **Step 1: Write failing domain-separation and closed-schema tests**

Add tests proving a new `preparation` subkey is deterministic for the same FRK, differs from object-wrap/catalog/search subkeys, and changes with FRK generation. Add decode tests that reject unknown fields, wrong schema versions, wrong Core IDs, wrong FRK versions, duplicate segment indexes, oversized fields, and trailing bytes for:

```rust
PreparationHeadRecord
PreparationSnapshot
PreparedObjectDescriptorSegment
FinalIntentSegment
PreparationReceipt
```

Run:

```powershell
cargo test -p anima-corefs crypto::tests::frk_subkeys_are_deterministic_and_domain_separated preparation_tests::formats -- --nocapture
```

Expected: new tests fail because the preparation key/domain and record codecs do not exist.

- [ ] **Step 2: Implement the preparation key and bounded record codecs**

Extend `FrkSubkeys` with a `preparation: SecretBytes` derived from a dedicated HKDF label, and add only an accessor that returns `&SecretBytes`. In `preparation.rs`, define explicit versioned records with `#[serde(deny_unknown_fields)]`, checked integer conversions, and separate limits for pointer, snapshot, descriptor segment, final-intent segment, and receipt envelopes. Bind AEAD AAD to record kind, schema version, `core_id`, preparation ID or pointer hash as appropriate, FRK version, and monotonic snapshot/segment number.

Do not reuse `MAX_CATALOG_ENVELOPE_SIZE` as a catch-all. Define small pointer/snapshot ceilings and segmented descriptor/intent ceilings so each durable unit is independently bounded.

- [ ] **Step 3: Make record publication immutable and durability-aware**

Reuse `publish_immutable_in_with_hook` for content-addressed encrypted records and `atomic_publish_in_with_hook` only for `PREPARATION_HEAD`. Use relative directory handles rooted below `.anima/fs`; never accept an absolute or caller-derived filesystem path.

- [ ] **Step 4: Run the focused and library tests**

```powershell
cargo test -p anima-corefs preparation_tests::formats -- --nocapture
cargo test -p anima-corefs crypto::tests -- --nocapture
cargo fmt --check -p anima-corefs
```

Expected: focused tests pass; formatting is clean.

- [ ] **Step 5: Commit the cryptographic/format slice**

```powershell
git add packages/anima-corefs/src/crypto.rs packages/anima-corefs/src/transaction.rs packages/anima-corefs/src/transaction/preparation.rs packages/anima-corefs/src/transaction/preparation_tests.rs
git -c commit.gpgsign=false commit -m "corefs: define encrypted preparation records"
```

## Task 2: Implement durable begin/resume and exact preparation-head CAS

**Files:**

- Modify: `packages/anima-corefs/src/transaction/preparation.rs`
- Modify: `packages/anima-corefs/src/transaction/preparation_tests.rs`
- Modify: `packages/anima-corefs/src/transaction.rs`

- [ ] **Step 1: Write failing begin/resume recovery tests**

Cover: no pointer; one active preparation; caller supplies the same source identity; competing begin; stale snapshot replay; missing snapshot; corrupt/torn pointer; wrong Core; wrong FRK generation; missing descriptor/intent segment; and restart after each immutable-record and pointer-publication boundary. Assert that missing/corrupt state fails closed and never silently begins an empty replacement.

Use a test-only publication hook to enumerate deterministic crash points. The authoritative state after restart must be either the prior complete snapshot or the complete next snapshot.

- [ ] **Step 2: Add the Core-scoped storage layout and state machine**

Implement fixed names:

```text
.anima/fs/PREPARATION_HEAD
.anima/fs/preparations/<authenticated-id>/snapshots/
.anima/fs/preparations/<authenticated-id>/descriptors/
.anima/fs/preparations/<authenticated-id>/intent/
.anima/fs/preparations/<authenticated-id>/receipts/
.anima/fs/preparation-quarantine/<pointer-sha256>.prep-pointer
```

The head contains only bounded authenticated routing facts and the current encrypted snapshot hash. Begin takes the expected validation-head identity, source-generation number, source-inventory digest, and source schema/version. Resume returns typed state plus reconciliation cursors; it never accepts caller-supplied prepared tokens.

- [ ] **Step 3: Serialize mutations through the existing kernel lock**

Place begin/resume/head transitions under the same rooted lock discipline used by validation commits. Re-read and authenticate `PREPARATION_HEAD` after acquiring the lock before every mutation. A state transition must require the exact prior pointer hash and exact prior snapshot number.

- [ ] **Step 4: Run focused crash/recovery coverage**

```powershell
cargo test -p anima-corefs preparation_tests::begin_resume -- --nocapture
cargo test -p anima-corefs preparation_tests::crash_boundaries -- --nocapture
cargo test -p anima-corefs --lib --no-fail-fast
```

Expected: all preparation and existing transaction tests pass.

- [ ] **Step 5: Commit begin/resume**

```powershell
git add packages/anima-corefs/src/transaction.rs packages/anima-corefs/src/transaction/preparation.rs packages/anima-corefs/src/transaction/preparation_tests.rs
git -c commit.gpgsign=false commit -m "corefs: persist resumable preparation state"
```

## Task 3: Prepare and reconcile one encrypted object at a time

**Files:**

- Modify: `packages/anima-corefs/src/transaction/preparation.rs`
- Modify: `packages/anima-corefs/src/transaction/preparation_tests.rs`
- Modify: `packages/anima-corefs/src/transaction.rs`
- Modify: `packages/anima-corefs/src/transaction/converter.rs`

- [ ] **Step 1: Write failing bounded-object tests**

Exercise a new `PrepareObjectRequest` with one `Read` body. Prove deterministic resume for an already prepared `(object_id, revision, content_hash)`, rejection of different content at the same logical revision, descriptor-segment rollover, exact prepared-count/byte accounting, stable-role and graph metadata retention, and no mutation of `VALIDATION_HEAD`.

Add a test-only small metadata limit that prepares enough objects to represent a logical corpus above 1 GiB without allocating 1 GiB. Track the maximum body simultaneously owned by the harness and assert it never exceeds one object plus fixed buffers.

- [ ] **Step 2: Extract reusable converter validation from whole-body ownership**

Refactor `converter.rs` so ID, name, kind/format, policy, reference, role, revision, and graph validation can operate on metadata plus prepared descriptors. Keep `ValidationBatch` only until Task 6 removes the production caller; do not make the new preparation API wrap or rebuild a `ValidationBatch`.

- [ ] **Step 3: Implement immutable prepared-object descriptors**

Stream the one input through the existing envelope/object preparation path, validate the durable ciphertext with bounded reads, then persist the wrapped DEK, physical name, hashes, sizes, kind, revision, policy, and reference metadata in an encrypted descriptor segment. Publish a new snapshot and pointer only after both the immutable object and descriptor segment are durable.

On resume, re-open and authenticate the descriptor chain rather than accepting an in-memory `PreparedObjectRevision` from Python.

- [ ] **Step 4: Add reconciliation and bounded status APIs**

Return counts, total plaintext/ciphertext bytes, next cursor, descriptor roots, and missing/conflicting logical identities. Page results under explicit response limits; never return the complete descriptor set for a maximum-size preparation.

- [ ] **Step 5: Run focused tests and commit**

```powershell
cargo test -p anima-corefs preparation_tests::prepare_object -- --nocapture
cargo test -p anima-corefs preparation_tests::bounded_large_corpus -- --nocapture
cargo test -p anima-corefs transaction::converter::tests -- --nocapture
git add packages/anima-corefs/src/transaction.rs packages/anima-corefs/src/transaction/converter.rs packages/anima-corefs/src/transaction/preparation.rs packages/anima-corefs/src/transaction/preparation_tests.rs
git -c commit.gpgsign=false commit -m "corefs: prepare converter objects incrementally"
```

## Task 4: Seal intent and finalize exactly one validation generation

**Files:**

- Modify: `packages/anima-corefs/src/transaction/preparation.rs`
- Modify: `packages/anima-corefs/src/transaction/preparation_tests.rs`
- Modify: `packages/anima-corefs/src/transaction.rs`
- Modify: `packages/anima-corefs/src/transaction/converter.rs`
- Test: `packages/anima-corefs/tests/validation_batch.rs`

- [ ] **Step 1: Write failing seal/finalize tests**

Cover incomplete descriptors, duplicate IDs/roles, missing references, folder cycles, descriptor/intent root mismatch, changed source generation/digest, changed expected validation head, object tampering, and retry after a crash immediately before and after `VALIDATION_HEAD` publication. Assert unsuccessful seal/finalize leaves the head unchanged; successful finalization increments it once; a post-head retry returns the same committed outcome instead of publishing again.

- [ ] **Step 2: Persist a separately segmented final intent**

Seal folder/object ordering and final catalog metadata into bounded encrypted intent segments. The ready snapshot authenticates only the ordered segment roots/indexes, expected source generation/digest, exact expected validation head, and aggregate counts. It must not embed the entire intent.

- [ ] **Step 3: Reconstruct finalization entirely from durable state**

Under the kernel lock, re-read the exact preparation pointer, source fence token supplied by the server, validation head, descriptor chain, intent chain, and every referenced encrypted object. Revalidate ciphertext in bounded chunks, reconstruct internal `PreparedObjectRevision` values, reuse converter graph checks, build the bounded catalog, and call the existing single validation-generation publication primitive.

Finalization may read ciphertext multiple times within bounds; it must never materialize decrypted corpus bodies or a corpus-wide body vector.

- [ ] **Step 4: Add committed-outcome recovery**

Record the intended validation generation/catalog hash before publication. If restart finds that exact generation authoritative, publish/return the deterministic completion receipt. If a different head won, report a typed conflict and preserve the preparation for disposition.

- [ ] **Step 5: Run transaction and integration tests**

```powershell
cargo test -p anima-corefs preparation_tests::seal_finalize -- --nocapture
cargo test -p anima-corefs preparation_tests::post_head_recovery -- --nocapture
cargo test -p anima-corefs --test validation_batch --no-fail-fast
cargo test -p anima-corefs --lib --no-fail-fast
```

- [ ] **Step 6: Commit exact finalization**

```powershell
git add packages/anima-corefs/src/transaction.rs packages/anima-corefs/src/transaction/converter.rs packages/anima-corefs/src/transaction/preparation.rs packages/anima-corefs/src/transaction/preparation_tests.rs packages/anima-corefs/tests/validation_batch.rs
git -c commit.gpgsign=false commit -m "corefs: finalize prepared catalogs atomically"
```

## Task 5: Complete abandonment, quarantine, FRK rotation, and session semantics

**Files:**

- Modify: `packages/anima-corefs/src/transaction/preparation.rs`
- Modify: `packages/anima-corefs/src/transaction/preparation_tests.rs`
- Modify: `packages/anima-corefs/src/transaction.rs`
- Modify: `packages/anima-corefs/tests/rotation.rs`

- [ ] **Step 1: Write failing terminal-state and rotation tests**

Cover idempotent abandon, crash before/after receipt/head removal, completed receipt replay, active-preparation rotation rejection, corrupt-pointer rotation rejection, Core-global hash-addressed quarantine, quarantine name independence from pointer content, old-FRK retention enforcement, and later safe GC eligibility without physical deletion.

- [ ] **Step 2: Implement deterministic completion and abandonment receipts**

Derive receipt identity from authenticated preparation identity plus terminal outcome. Publish the receipt before clearing `PREPARATION_HEAD` when needed for retry proof; on restart, reconcile receipt, pointer, and validation head into exactly one terminal outcome.

- [ ] **Step 3: Implement operator-only corrupt-pointer quarantine**

Hash the raw pointer bytes first and move/copy them only to the fixed Core-global quarantine directory under that hash. Do not parse a preparation ID to choose the destination. Require explicit operator action, durable quarantine publication, and retained availability of the pointer's possible old FRK before allowing new preparation activation.

- [ ] **Step 4: Gate FRK rotation and cleanup**

Reject rotation while a preparation is active or its pointer cannot be authenticated. After approved quarantine, allow rotation only when the keyring retains all versions needed for quarantined encrypted state. Record unreachable prepared objects for PCF-010 retention-aware GC; do not delete them in this task.

- [ ] **Step 5: Run and commit lifecycle coverage**

```powershell
cargo test -p anima-corefs preparation_tests::terminal -- --nocapture
cargo test -p anima-corefs preparation_tests::quarantine -- --nocapture
cargo test -p anima-corefs --test rotation --no-fail-fast
git add packages/anima-corefs/src/transaction.rs packages/anima-corefs/src/transaction/preparation.rs packages/anima-corefs/src/transaction/preparation_tests.rs packages/anima-corefs/tests/rotation.rs
git -c commit.gpgsign=false commit -m "corefs: close preparation lifecycle safely"
```

## Task 6: Expose session-guarded one-object PyO3 operations

**Files:**

- Modify: `packages/anima-core/src/ffi.rs`
- Modify: `packages/anima-core/Cargo.toml`
- Modify: `packages/anima-corefs/src/transaction.rs`

- [ ] **Step 1: Write failing PyO3 boundary tests**

Add tests for these versioned methods:

```text
preparation_begin_or_resume_v1
preparation_status_v1
preparation_prepare_object_v1
preparation_seal_v1
preparation_finalize_v1
preparation_abandon_v1
preparation_quarantine_corrupt_pointer_v1
```

Assert every method acquires `CorefsOperationGuard`; close waits for an in-flight operation; new calls fail after close begins; Python receives typed conflict/corruption/source-fence errors; and `prepare_object` accepts exactly one bytes-like body per call. Prove no new method accepts `Vec<Vec<u8>>`, `Vec<PyBytes>`, or a corpus-wide JSON body list.

- [ ] **Step 2: Implement minimal wire mappings**

Use bounded JSON only for metadata/status/intent pages and one Python buffer for one object. Convert native outcomes to the existing wire-dictionary style without exposing keys, wrapped DEKs, physical paths, or preparation secrets.

- [ ] **Step 3: Retire the aggregate production path**

After Task 8 moves the sole production caller, remove `CorefsSession.validation_batch_parts_v1` and `CORE_FS_VALIDATION_BODY_AGGREGATE_LIMIT`. Keep crate-private converter helpers and any explicitly test-only compatibility fixture only if existing validation-batch tests still require them; no server code may call the aggregate API.

- [ ] **Step 4: Run and commit PyO3 coverage**

```powershell
cargo test -p anima-core --lib corefs_preparation -- --nocapture
cargo test -p anima-core --lib corefs_session -- --nocapture
cargo check -p anima-core --features python
git add packages/anima-core/src/ffi.rs packages/anima-core/Cargo.toml packages/anima-corefs/src/transaction.rs
git -c commit.gpgsign=false commit -m "core: expose bounded preparation sessions"
```

## Task 7: Add a transactional SQLCipher writing-source generation

**Files:**

- Create: `apps/server/alembic_core/versions/20260802_0001_add_corefs_writing_source_generation.py`
- Modify: `apps/server/src/anima_server/models/agent_runtime.py`
- Modify: `apps/server/src/anima_server/models/__init__.py`
- Create: `apps/server/tests/test_corefs_writing_generation.py`
- Modify: `apps/server/tests/test_corefs_migration.py`
- Modify: `apps/server/tests/test_diary_api.py`

- [ ] **Step 1: Write failing migration and mutation-generation tests**

Test a fresh upgrade, upgrade from `20260721_0001`, downgrade/upgrade, and one head only. Exercise INSERT/UPDATE/DELETE for `diary_folders`, `diary_entries`, and `diary_attachments`; assert the per-user generation increments in the same transaction, rollback does not increment, cascades do not lose monotonicity, users remain isolated, and every existing diary service writer advances the value.

- [ ] **Step 2: Add the source-state table and SQLite triggers**

Create a small per-user table such as `corefs_writing_source_state(user_id PRIMARY KEY, generation NOT NULL)` and SQLite triggers on all three legacy writing tables. Each trigger must atomically insert generation `1` or increment the existing row using `NEW.user_id` or `OLD.user_id` as appropriate. The SQLAlchemy model is for typed reads; triggers are the authority so alternate writers cannot bypass the fence accidentally.

- [ ] **Step 3: Prove `BEGIN IMMEDIATE` writer exclusion**

Using two independent SQLCipher connections, begin the finalization fence on one connection and assert a legacy write on the second cannot commit until the first commits/rolls back. Keep the test bounded with SQLite busy timeouts and deterministic synchronization, not sleeps.

- [ ] **Step 4: Validate and commit the migration**

```powershell
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; uv run pytest apps/server/tests/test_corefs_writing_generation.py apps/server/tests/test_corefs_migration.py apps/server/tests/test_diary_api.py -q
uv run alembic -c apps/server/alembic_core.ini heads
```

Expected Alembic output: exactly `20260802_0001 (head)`.

```powershell
git add apps/server/alembic_core/versions/20260802_0001_add_corefs_writing_source_generation.py apps/server/src/anima_server/models/agent_runtime.py apps/server/src/anima_server/models/__init__.py apps/server/tests/test_corefs_writing_generation.py apps/server/tests/test_corefs_migration.py apps/server/tests/test_diary_api.py
git -c commit.gpgsign=false commit -m "server: fence legacy writing mutations"
```

## Task 8: Stream Python writing preparation and finalize under the source fence

**Files:**

- Create: `apps/server/src/anima_server/services/corefs/writing_source.py`
- Modify: `apps/server/src/anima_server/services/corefs/diary_migration.py`
- Modify: `apps/server/src/anima_server/services/sessions.py`
- Modify: `apps/server/src/anima_server/api/routes/diary.py`
- Modify: `apps/server/tests/test_corefs_diary_migration.py`
- Modify: `apps/server/tests/test_corefs_notes.py`
- Modify: `apps/server/tests/conftest.py`

- [ ] **Step 1: Write failing bounded-orchestration tests**

Replace fake-session expectations for `validation_batch_parts_v1` with the preparation lifecycle. Test new migration, exact rerun, crash/restart after every object, reconcile-and-skip of durable matches, conflict on changed same-revision content, source mutation before seal, source mutation after seal, mutation blocked during the final fence, native finalization failure, post-head recovery, unlock retry, and draft-import retry.

Use spies/counters and generated small bodies to model an inventory whose logical aggregate exceeds 1 GiB; assert the orchestrator holds at most one canonical body and one decrypted attachment at a time. Do not allocate a 1 GiB test buffer.

- [ ] **Step 2: Separate inventory from body production**

In `writing_source.py`, add:

```python
WritingSourceInventory
WritingSourceObjectDescriptor
iter_writing_source_objects(...)
read_writing_source_generation(...)
begin_writing_source_fence(...)
```

Inventory computes deterministic folder/object ordering, IDs, revisions, metadata, attachment storage identities, counts, and a source digest without retaining bodies. The iterator re-reads one source row/blob, canonicalizes/sanitizes it, yields one body to native preparation, then releases it before advancing.

- [ ] **Step 3: Remove corpus-wide Python ownership**

Delete or reshape `PreparedWritingObject.content`, `PreparedWritingSnapshot.objects`, and `InactiveWritingCatalog.publish_native` so no path constructs `[item.content for item in self.objects]`. Attachment decryption must be scoped to the current yielded object. Post-publication verification must compare bounded metadata/hash inventories or stream one object at a time; it must not read the complete prepared corpus back into a tuple.

- [ ] **Step 4: Implement resume/reconcile/seal**

Begin/resume with the initial source generation and inventory digest. Page native status, skip exact durable descriptors, prepare missing objects one at a time, then re-read source generation/inventory. Any mismatch restarts reconciliation without sealing stale intent. Seal only the exact complete inventory.

- [ ] **Step 5: Hold the SQLCipher source fence through publication**

Start `BEGIN IMMEDIATE` on a dedicated SQLCipher connection, recompute generation and inventory digest inside that transaction, and pass the exact fence values to native finalize. Keep the transaction open until native finalization and bounded result verification finish. On mismatch/failure, roll back and leave preparation resumable; on success, commit the read/fence transaction and journal the existing non-secret checkpoint.

- [ ] **Step 6: Preserve unlock and API behavior**

Keep legacy SQLCipher authoritative and routes read-compatible before PCF-008. Unlock may resume a preparation but must not expose partial CoreFS state. Draft import follows the same single-active-preparation CAS and deterministic retry behavior.

- [ ] **Step 7: Run focused Python validation and commit**

```powershell
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; uv run pytest apps/server/tests/test_corefs_writing_generation.py apps/server/tests/test_corefs_diary_migration.py apps/server/tests/test_corefs_notes.py apps/server/tests/test_diary_api.py -q
uv run ruff check apps/server/src/anima_server/services/corefs/writing_source.py apps/server/src/anima_server/services/corefs/diary_migration.py apps/server/src/anima_server/services/sessions.py apps/server/src/anima_server/api/routes/diary.py apps/server/tests/test_corefs_writing_generation.py apps/server/tests/test_corefs_diary_migration.py
git add apps/server/src/anima_server/services/corefs/writing_source.py apps/server/src/anima_server/services/corefs/diary_migration.py apps/server/src/anima_server/services/sessions.py apps/server/src/anima_server/api/routes/diary.py apps/server/tests/test_corefs_writing_generation.py apps/server/tests/test_corefs_diary_migration.py apps/server/tests/test_corefs_notes.py apps/server/tests/conftest.py
git -c commit.gpgsign=false commit -m "server: stream resumable writing preparation"
```

## Task 9: Prove end-to-end bounds, recovery, and unchanged product behavior

**Files:**

- Modify: `packages/anima-corefs/src/transaction/preparation_tests.rs`
- Modify: `packages/anima-core/src/ffi.rs`
- Modify: `apps/server/tests/test_corefs_diary_migration.py`
- Modify: `apps/desktop/tests/journal-corefs.test.ts`
- Modify: `tickets/portable-core-filesystem/PCF-004-diary-notes.md`
- Modify: `tickets/portable-core-filesystem/PCF-000-portable-core-filesystem.md`

- [ ] **Step 1: Run the full affected Rust gates**

```powershell
cargo test -p anima-corefs --lib --no-fail-fast
cargo test -p anima-corefs --tests --no-fail-fast
cargo test -p anima-core --lib
cargo check -p anima-core --features python
cargo clippy -p anima-corefs --all-targets -- -D warnings
cargo fmt --check
```

Expected: all tests/checks pass. If strict Clippy exposes pre-existing untouched warnings, record exact files/lines and run a diff-scoped no-new-warning check; do not weaken lints.

- [ ] **Step 2: Run the full affected Python/Desktop gates**

```powershell
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; uv run pytest apps/server/tests/test_diary_api.py apps/server/tests/test_corefs_diary_migration.py apps/server/tests/test_corefs_notes.py apps/server/tests/test_corefs_writing_generation.py apps/server/tests/test_corefs_migration.py -q
bun test apps/desktop/tests/journal-corefs.test.ts apps/desktop/tests/journal-draft-migration.test.ts apps/desktop/tests/journal-html.test.ts
bun run lint:server
bun run build
```

Smoke-check unlock/session resume, diary list/get, attachment retrieval, draft import, stable `core.journal`/`core.notes` role resolution, and `GET /health` against an isolated `ANIMA_DATA_DIR`.

- [ ] **Step 3: Attempt repository-wide validation**

```powershell
bun run test
bun run check:repo
git diff --check
```

Record exact outcomes. A timeout without a summary is not a pass; diagnose any failure before editing and distinguish unrelated baseline failures from regressions.

- [ ] **Step 4: Obtain independent implementation review**

Dispatch an independent reviewer with the approved spec, this plan, and the final diff. Require findings only for consequential correctness, security, privacy, data-loss, bounded-memory, crash-recovery, source-fence, and compatibility regressions. Resolve substantive findings test-first; disposition style/speculative/non-blocking churn with evidence.

- [ ] **Step 5: Synchronize PCF-004 and PCF-000**

Record changed paths, commands/results, independent review evidence, exact commit, and whether the protocol blocker is cleared. Do not mark PCF-004 done until every original diary/notes acceptance item plus the large-corpus protocol is green. Do not change parent ownership.

- [ ] **Step 6: Commit final evidence**

```powershell
git add packages/anima-corefs/src/transaction/preparation_tests.rs packages/anima-core/src/ffi.rs apps/server/tests/test_corefs_diary_migration.py apps/desktop/tests/journal-corefs.test.ts tickets/portable-core-filesystem/PCF-004-diary-notes.md tickets/portable-core-filesystem/PCF-000-portable-core-filesystem.md
git -c commit.gpgsign=false commit -m "tickets: record CoreFS preparation evidence"
```

## Stop conditions

Stop and return to the design gate instead of improvising if implementation would require any of the following:

- more than one visible validation generation for a single migration;
- plaintext preparation state or caller-provided filesystem paths;
- a corpus-wide body container at the Rust, PyO3, or Python boundary;
- finalization without both exact validation-head CAS and a live SQLCipher source fence;
- trusting unauthenticated pointer fields for quarantine or recovery;
- deleting prepared objects before PCF-010 retention approval;
- changing `fs/HEAD`, public mutation authority, or legacy read/write authority before PCF-008.
