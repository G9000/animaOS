# PCF-008 - Cutover, transfer, and first-release validation

- Status: backlog
- Priority: P0
- Scope: migration cutover, local ANIMA CORE transfer/recovery, release validation
- Parent: `PCF-000`
- Depends on: `PCF-001`, `PCF-002`, `PCF-003`, `PCF-004`, `PCF-005`, `PCF-006`, `PCF-007`
- Owner: unassigned
- PRD: `docs/prds/portable-core-filesystem-v1.md`
- Spec: `docs/superpowers/specs/2026-08-02-corefs-resumable-preparation-design.md#111-packaged-desktop-writer-exclusion-for-plaintext-draft-cleanup`
- Plan: `docs/superpowers/plans/2026-07-12-portable-core-filesystem.md#task-8-cutover-transfer-and-first-release-validation`
- Created: 2026-07-12 06:07 MYT
- Updated: 2026-08-13 15:54 MYT
- Started:
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

## Validation

- Commands:
  - `not run yet`
- Changed paths:
  - none
- Notes:
  - Claim only after PCF-001 through PCF-007 are done.
