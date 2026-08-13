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
- Updated: 2026-08-13 19:34 MYT
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
- Notes:
  - PCF-001 through PCF-007 are done. The four-platform signed-package gate
    remains mandatory and cost-deferred; it cannot be dispatched or waived by
    local ticket execution.
  - The macOS host can compile-check the Python-enabled PyO3 binding, but its
    extension-module test target is not locally linkable against the venv
    interpreter. The binding regression remains in the Rust test target for a
    supported native runner and Step 4 will add the end-to-end Python first-
    mutation exercise.
