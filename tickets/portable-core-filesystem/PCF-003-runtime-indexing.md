# PCF-003 - Machine-local Runtime and progressive indexing

- Status: in_progress
- Priority: P0
- Scope: `apps/server` runtime/indexing, desktop readiness/security UI
- Parent: `PCF-000`
- Depends on: `PCF-002`
- Owner: Codex
- PRD: `docs/prds/portable-core-filesystem-v1.md`
- Plan: `docs/superpowers/plans/2026-07-12-portable-core-filesystem.md#task-3-machine-local-runtime-and-progressive-indexing`
- Created: 2026-07-12 06:07 MYT
- Updated: 2026-07-29 13:49 MYT
- Started: 2026-07-29 01:57 MYT
- Completed:

## Goal

Move Runtime outside `.anima/`, add resumable migration/index state, progressively rebuild search after unlock, and expose safe readiness and FRK rotation operations.

## Deliverables

- Instance-scoped relocation for PostgreSQL, `.anima/indices`, health logs, blind tokens, checkpoints, and caches; machine-wide Tauri daemon path; durable migration journal.
- Machine-local Core-path/filesystem-identity registry and lease preventing divergent same-`core_id` copies from sharing mutable state, including explicit runtime-URL collision checks.
- Safe catalog/blind-token persistence with memory-only plaintext search and embeddings.
- Unlock-derived runtime sealing for crash-durable sensitive operational payloads; rebuildable chunks/OCR/source spans remain memory-only.
- Lock/logout/shutdown teardown for all decrypted state.
- Readiness and key-rotation API/desktop surfaces with executed tests.

## Acceptance

- `.anima/` contains no PostgreSQL directory.
- Static path-inventory tests reject Runtime indexes, logs, daemon files, or other unapproved writers under `.anima/`.
- Lock makes decrypted search/read unavailable and clears keys/vectors/query state.
- Deleted Runtime rebuilds without canonical data loss.
- Raw PostgreSQL/runtime-disk scans find none of the seeded message, chunk, OCR, source, candidate, pending-op, preview, or vector plaintext markers.
- Moved Core, same-machine transfer copy, stale lease, simultaneous clone, and runtime-URL collision tests never mix Runtime state.
- Rotation status/progress/recovery gates are operable through Security settings.

## Activity Log

- 2026-07-12 06:07 MYT - Ticket created.
- 2026-07-29 01:57 MYT - Claimed PCF-003 from merged `main` (`7a390eb3`) on branch `codex/pcf-003-runtime-indexing` in `.worktrees/pcf-003-runtime-indexing`. The isolated baseline passed `35` focused backend tests, the desktop production build, and `cargo check -p desktop`; implementation will follow the approved Task 3 plan test-first.
- 2026-07-29 13:49 MYT - Completed the machine-local relocation and unlock-scoped indexing foundation. The Runtime now has instance-bound opaque catalog/checkpoint/blind-token/migration/sealed-payload models at migration head `033_corefs_runtime_index`, HKDF-derived sealing and blind-index subkeys, atomic blind-token generations, progressive/degraded readiness state, and one teardown path attached to unlock sessions. Focused relocation/privacy/index/API/session/runtime validation passed `121` tests with `8` environment-dependent embedded-PostgreSQL skips.

## Validation

- Commands:
  - `not run yet`
- Changed paths:
  - none
- Notes:
  - Claim after PCF-002 is done.
