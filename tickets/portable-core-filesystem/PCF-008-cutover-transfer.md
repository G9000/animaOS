# PCF-008 - Cutover, transfer, and first-release validation

- Status: in_progress
- Priority: P0
- Scope: migration cutover, local ANIMA CORE transfer/recovery, release validation
- Parent: `PCF-000`
- Depends on: `PCF-001`, `PCF-002`, `PCF-003`, `PCF-004`, `PCF-005`, `PCF-006`, `PCF-007`
- Owner: Codex
- PRD: `docs/prds/portable-core-filesystem-v1.md`
- Spec: `docs/superpowers/specs/2026-08-02-corefs-resumable-preparation-design.md#111-packaged-desktop-writer-exclusion-for-plaintext-draft-cleanup`
- Plan: `docs/superpowers/plans/2026-07-12-portable-core-filesystem.md#task-8-cutover-transfer-and-first-release-validation`
- Created: 2026-07-12 06:07 MYT
- Updated: 2026-08-13 20:26 MYT
- Started: 2026-08-13 18:41 MYT
- Completed:

## Goal

Perform the verified reversible-to-forward-only cutover, provide safe cold/live transfer, and validate the first release without deleting legacy Soul rollback tables.

## Deliverables

- Resumable converter orchestration and acceptance states.
- Verified SQLCipher checkpoint and copy-verify-flip from `users/<legacy-id>/anima.db` to `.anima/soul/soul.db`.
- Authenticated first-write cutover marker.
- Legacy PostgreSQL relocation, encrypted recovery bundle, and plaintext retirement after marker.
- ANIMA CORE local transfer API/client/UI with full export/restore plus advanced Soul-only and CoreFS-only recovery.
- Rust-backed `anima_core_v2` streaming container with authenticated `full`/`soul`/`fs` kinds, <=8-MiB I/O chunks, reachable-object verification, no 16-MiB total section ceiling, and backward V1/JSON import.
- Hard-drive/removable-media destination preflight, `.partial` publication, single-file output, and authenticated <=2-GiB multipart fallback for FAT32-like limits.
- Bounded V2 KDF/header validation, one normative archive AAD tuple, pre-archive record hashing, globally unique archive nonce ordinals, and <=32-MiB aggregate streaming memory excluding the fixed Argon2 workspace.
- Same-volume import staging, authenticated active-Core registry-pointer activation, retained-old-Core rollback, and crash tests at every multipart/import publication boundary.
- Legacy app tables disabled as authority but retained read-only for PCF-009.
- Protected final signed Windows, macOS, DEB, and RPM replacement-install
  evidence for plaintext-draft cleanup, including exact artifact digests,
  recorded before irreversible cutover or first-release publication.

## Acceptance

- Rollback works before the marker and is rejected after it.
- Cold and live prepared transfer exclude Runtime and restore all canonical content.
- Full backend and Bun desktop tests execute and pass.
- Fresh Runtime/cache/log/index raw scans find no seeded portable plaintext; sealed operational payloads are unlock-only.
- Existing legacy sources remain recoverable in encrypted/read-only form for the observation window.
- After the first-write marker, no service recreates the legacy `users/<id>/anima.db` layout and transfers contain the single canonical Soul file.
- A >16-MiB binary-object round trip streams without whole-archive base64 buffering and excludes Runtime/device/credential state.
- Default artifacts are `anima-core-<timestamp>.anima`, `anima-core-soul-<timestamp>.anima`, and `anima-core-fs-<timestamp>.anima`; the authenticated payload kind, not the filename, controls import.
- Soul-only restore enters `filesystem_missing`; CoreFS-only restore enters recovery/export-only mode; neither starts as a complete ANIMA.
- Export/import memory remains bounded for an artifact larger than RAM, and insufficient capacity, unsupported destination, disconnect, tampered/missing/mixed volumes, or interrupted import cannot alter the live Core.
- Soul/FS scoped credential replacement cannot unlock undeclared compartments or promote a partial artifact to `full`; CoreFS-to-Soul attachment returns `corefs_reattachment_not_supported` in V1.
- Pre-authentication KDF/header limits, exact AAD fields, record-hash semantics, global nonce monotonicity, controller-last multipart commit, same-volume staging, registry swap, and old-Core rollback all have deterministic failure-injection coverage.
- The protected package workflow passes against the final signed MSI,
  notarized PKG, DEB, and RPM; all replacement-only launch-target, native
  process-census, post-WebView capability, and source-first cleanup checks pass;
  exact artifact digests are recorded. Missing or failed evidence blocks
  cutover and release publication.

## Activity Log

- 2026-07-12 06:07 MYT - Ticket created.
- 2026-07-12 15:45 MYT - Added the approved animaOS/ANIMA CORE naming contract, local-only full/Soul/CoreFS streaming artifacts, removable-media preflight/multipart behavior, and independent recovery states.
- 2026-07-12 16:01 MYT - Closed independent-review gaps for scoped keys, normative archive crypto, controller-last multipart publication, atomic import activation, and deferred V1 reattachment.
- 2026-08-13 15:54 MYT - User approved moving PCF-004's cost-bearing final
  signed-package executions into this first-release ticket without waiving
  them. The protected workflow remains triggerless until PCF-008 is active and
  funded execution is separately authorized; irreversible cutover and release
  publication are forbidden until all four native results and exact artifact
  digests are recorded.
- 2026-08-13 18:41 MYT - Claimed by Codex on local branch
  `codex/pcf-008-cutover-transfer` from completed PCF-007 head `1067becf` after
  confirming PCF-001 through PCF-007 are done and no competing claim is
  visible. Local reversible implementation and validation may proceed; the
  triggerless paid package workflow, irreversible first-write marker, release
  publication, and merge remain unauthorized.
- 2026-08-13 18:58 MYT - Completed Step 1 locally. The manifest now records
  the exact reversible cutover states and a stable pending epoch, while only an
  authenticated committed `fs/HEAD` catalog marker can create forward-only
  session authority. Unlock repairs the crash seam after marked HEAD
  publication, rejects manifest-only authority, and blocks rollback after the
  marker. Logical reads and stable-role resolution follow the committed
  catalog after cutover but remain on `VALIDATION_HEAD` beforehand. The paid
  package workflow remains disabled and no irreversible first mutation or
  external action was performed.
- 2026-08-13 19:22 MYT - Added the first Step 2/Step 7 transfer milestone.
  Rust now provides the exact registered `anima_core_v2` fixed header and KDF,
  generation-bound encrypted inventory, closed full/Soul/CoreFS record
  allowlists, 8-MiB chunk streaming, global per-container nonce ordinals,
  authenticated footer/trailer, failed-import staging cleanup, and a binary
  round trip above the legacy 16-MiB ceiling. Python now preflights local
  capacity, writable atomic rename, and FAT-like file limits, then publishes
  verified single-file or controller-last multipart output with deterministic
  cancellation and every local publication seam covered. Step 2 and Step 7
  remain open for live snapshot pinning, native multipart-set cryptography,
  import activation, and API/UI integration. No paid workflow or irreversible
  cutover action was performed.
- 2026-08-13 19:34 MYT - Added authenticated same-volume import activation.
  Capacity preflight retains the existing Core and margin; a verified sibling
  staging Core is fsynced, renamed, and selected through a generation-monotonic
  HMAC-authenticated machine-local pointer while the prior Core remains a
  named rollback target. An authenticated activation journal recovers startup
  after the staging rename or pointer swap, terminal completion is replayable,
  pointer tampering and symbolic-link staging fail closed, and rollback swaps
  the two retained directories atomically without deleting either. All five
  activation crash seams plus rollback-after-pointer passed focused tests.
- 2026-08-13 19:39 MYT - Added the physical Soul-relocation portion of
  Step 3. Under the migration write barrier, the owner SQLCipher/SQLite
  database is WAL-checkpointed, page/cipher/FK/schema verified, hashed by a
  deterministic retained-table inventory, durably copied to
  `.anima/soul/soul.db`, independently reopened and reverified, and only then
  selected by an atomic manifest flip. The legacy encrypted database remains
  intact for pre-marker rollback; crash-after-copy resumes without overwrite,
  concurrent source mutation or target corruption cannot flip authority, and
  session/account routing follows the single canonical Soul path afterward.
  Step 3 remains open for converter-journal orchestration, parity acceptance,
  and the fresh outside-Core Runtime transition.
- 2026-08-13 19:45 MYT - Added the resumable converter coordinator portion of
  Step 3. One instance-scoped Runtime journal now drives preflight, write
  freeze, the combined PCF-004/005/006/007 portable-content converter,
  validation verification, and explicit accept or reject. Every checkpoint
  resumes idempotently; acceptance recovers a crash after pending-cutover
  publication, rejection recovers a crash after legacy rollback, failures
  require an explicit retry, and journal errors persist only a class name plus
  a class-only domain digest rather than private exception text. Step 3 remains
  open for full production API wiring, parity evidence across real fixtures,
  and the fresh outside-Core Runtime transition.
- 2026-08-13 19:56 MYT - Added the native V2 archive bridge and the first
  authenticated transfer-source boundary. Python can now invoke bounded Rust
  file export/import without base64 buffering; the live native session emits
  only the committed catalog, its authenticated pointer/cutover records, and
  objects reachable from that catalog under the session object lease. The
  server wrapper constructs manifest/Soul/recovery inputs itself, materializes
  only wrapped keyslot metadata in a short-lived private file, rejects any
  native source outside the active Core, and verifies extraction in disposable
  same-volume staging. Rust archive tests remain `6 passed`, the Python binding
  compile-check passes, and the new wrapper tests pass `3`. Step 2 and Step 7
  remain open for coherent Soul generation/checkpoint capture and one
  authenticated nonce sequence across a multipart volume set; no paid workflow
  or irreversible cutover action occurred.
- 2026-08-13 20:07 MYT - Added the first Step 6 product-facing transfer slice.
  Authenticated users can obtain an authoritative full/Soul/CoreFS estimate,
  probe an exact local destination for capacity and atomic publication, start a
  bounded background single-file export, poll progress/completion, and request
  safe cancellation without persisting the passphrase or exposing physical
  Core source paths. The desktop now presents **Export ANIMA CORE** as the
  primary flow, labels advanced recovery modes and degraded states, displays
  checkpoint/capacity/file-limit/publication/progress/verification state, and
  redirects the legacy Vault page. Backend transfer coverage passes `48`, API
  client and desktop contracts pass `31`, the desktop production build passes,
  and PyO3/Ruff/diff checks pass. Step 6 remains open for authenticated import
  activation/rollback in the UI; multipart remains visibly gated until Step 7
  provides one globally authenticated volume set. No paid workflow or
  irreversible cutover action occurred.
- 2026-08-13 20:26 MYT - Added the non-activating first-mutation milestone for
  Step 4. The existing logical planner now commits the approved first mutation
  through the same Core-wide transaction as the authenticated cutover marker,
  then advances later mutations only from committed `fs/HEAD`. The PyO3
  session accepts exact selected-snapshot identity, body encoding, principal,
  and manifest-derived cutover mode; Python reconciles a post-HEAD crash before
  choosing the mode and retains the original cutover receipt identity across
  later heads. A closed-schema HTTP dispatcher, canonical bounded base64 body
  decoding, optimistic errors, index invalidation, and client multi-target
  fail-closed behavior are implemented and tested, but the compile-time
  `CORE_FS_PUBLIC_MUTATION_ADAPTERS_READY` gate remains false until every
  content-family adapter and the funded signed-package evidence are complete.
  CoreFS mutation tests pass `7`, the focused server band passes `66`, strict
  CoreFS Clippy and the Python-enabled binding compile-check pass, and scoped
  anima-core Clippy passes with only the previously recorded unrelated crate
  lints allowed. No public mutation, paid workflow, or irreversible cutover
  action occurred.

## Validation

- Commands:
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run pytest apps/server/tests/test_corefs_cutover.py apps/server/tests/test_corefs_indexer.py apps/server/tests/test_dev_session_continuity.py -q` (`82 passed`)
  - `cargo test -p anima-corefs` (complete native suite passed)
  - `cargo clippy -p anima-corefs --all-targets -- -D warnings` (passed)
  - `PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1 cargo check -p anima-core --features python` (passed)
  - scoped Ruff check/format, Rust format, and `git diff --check` (passed)
  - `cargo test -p anima-core core_archive` (`6 passed`)
  - scoped `cargo clippy -p anima-core --lib` with only unrelated pre-existing
    crate lints allowed (`passed`; the unchanged strict crate-wide invocation
    remains blocked by existing `cards.rs`, `frame.rs`, and `path_engine.rs`
    warnings)
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run pytest apps/server/tests/test_corefs_transfer.py -q`
    (`31 passed`)
  - scoped Ruff check/format and `git diff --check` (passed)
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run pytest apps/server/tests/test_corefs_transfer.py -q`
    after import activation (`42 passed`)
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run pytest apps/server/tests/test_corefs_soul_relocation.py apps/server/tests/test_corefs_cutover.py apps/server/tests/test_auth.py -q`
    (`35 passed`)
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run pytest apps/server/tests/test_security_hardening.py apps/server/tests/test_runtime_db.py -q`
    (`70 passed`)
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run pytest apps/server/tests/test_corefs_orchestration.py apps/server/tests/test_corefs_cutover.py apps/server/tests/test_corefs_soul_relocation.py apps/server/tests/test_corefs_transfer.py -q`
    (`76 passed`)
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run pytest apps/server/tests/test_corefs_runtime_privacy.py -q`
    (`52 passed`)
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run pytest apps/server/tests/test_corefs_archive_transfer.py -q`
    (`3 passed`)
  - `PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1 cargo check -p anima-core --features python`
    after adding the archive bindings (passed)
  - `cargo test -p anima-core core_archive --lib` after binding the committed
    inventory (`6 passed`)
  - scoped Ruff and `git diff --check` for the archive bridge (passed)
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run pytest apps/server/tests/test_corefs_transfer.py apps/server/tests/test_corefs_archive_transfer.py apps/server/tests/test_corefs_transfer_api.py -q`
    (`48 passed`)
  - `bun test packages/api-client/tests/client.test.ts apps/desktop/tests/corefs-transfer.test.ts`
    (`31 passed`)
  - `bun run --cwd apps/desktop build` (passed)
  - `cargo test -p anima-corefs logical::mutation --lib` (`7 passed`)
  - `cargo clippy -p anima-corefs --all-targets -- -D warnings` (passed)
  - `PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1 cargo check -p anima-core --features python`
    after adding logical mutation binding (passed)
  - scoped strict anima-core Clippy with only the recorded unrelated
    `cards.rs`, `frame.rs`, and `path_engine.rs` lints allowed (passed)
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run pytest apps/server/tests/test_corefs_logical.py apps/server/tests/test_corefs_cutover.py apps/server/tests/test_corefs_api.py -q`
    (`66 passed`)
  - `bun test packages/api-client/tests/client.test.ts` (`28 passed`)
  - direct Python-enabled anima-core unit-test linking remains unavailable on
    this macOS extension-module host because Python symbols are not linked;
    the same binding compiles, while its transaction behavior is covered in
    anima-corefs and its Python authority/request behavior is covered by the
    server band above
- Changed paths:
  - `apps/server/src/anima_server/services/corefs/cutover.py`
  - `apps/server/src/anima_server/services/sessions.py`
  - `apps/server/tests/test_corefs_cutover.py`
  - `packages/anima-core/src/ffi.rs`
  - `packages/anima-corefs/src/logical/backend.rs`
  - `packages/anima-corefs/src/transaction/converter.rs`
  - `docs/superpowers/plans/2026-07-12-portable-core-filesystem.md`
  - `tickets/portable-core-filesystem/PCF-000-portable-core-filesystem.md`
  - `tickets/portable-core-filesystem/PCF-008-cutover-transfer.md`
  - `packages/anima-core/src/core_archive.rs`
  - `packages/anima-core/{Cargo.toml,src/lib.rs}` and `Cargo.lock`
  - `apps/server/src/anima_server/services/corefs/transfer.py`
  - `apps/server/tests/test_corefs_transfer.py`
  - `apps/server/src/anima_server/services/corefs/soul_relocation.py`
  - `apps/server/src/anima_server/db/{session.py,user_store.py}`
  - `apps/server/tests/test_corefs_soul_relocation.py`
  - `apps/server/src/anima_server/services/corefs/orchestration.py`
  - `apps/server/tests/test_corefs_orchestration.py`
  - `apps/server/src/anima_server/services/corefs/archive_transfer.py`
  - `apps/server/tests/test_corefs_archive_transfer.py`
  - `apps/server/src/anima_server/{main.py,schemas/corefs_transfer.py}`
  - `apps/server/src/anima_server/api/routes/corefs_transfer.py`
  - `apps/server/src/anima_server/services/corefs/transfer_jobs.py`
  - `apps/server/tests/test_corefs_transfer_api.py`
  - `packages/api-client/src/{client.ts,types.ts}`
  - `packages/api-client/tests/client.test.ts`
  - `apps/desktop/src/{App.tsx,pages/settings/Settings.tsx,pages/settings/CoreTransferSettings.tsx}`
  - `apps/desktop/tests/corefs-transfer.test.ts`
  - `packages/anima-corefs/src/logical/{mod.rs,mutation.rs,mutation/executor.rs,mutation/tests.rs}`
  - `packages/anima-core/src/ffi.rs`
  - `apps/server/src/anima_server/{schemas/corefs.py,api/routes/corefs.py}`
  - `apps/server/src/anima_server/services/corefs/{logical.py,cutover.py}`
  - `apps/server/tests/{test_corefs_api.py,test_corefs_logical.py,test_corefs_cutover.py}`
  - `packages/api-client/src/types.ts`
- Notes:
  - PCF-001 through PCF-007 are done. The four-platform signed-package gate
    remains mandatory and cost-deferred; it cannot be dispatched or waived by
    local ticket execution.
  - The macOS host can compile-check the Python-enabled PyO3 binding, but its
    extension-module test target is not locally linkable against the venv
    interpreter. The binding regression remains in the Rust test target for a
    supported native runner and Step 4 will add the end-to-end Python first-
    mutation exercise.
