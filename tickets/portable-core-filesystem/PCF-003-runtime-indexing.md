# PCF-003 - Machine-local Runtime and progressive indexing

- Status: backlog
- Priority: P0
- Scope: `apps/server` runtime/indexing, desktop readiness/security UI
- Parent: `PCF-000`
- Depends on: `PCF-002`
- Owner: unassigned
- PRD: `docs/prds/portable-core-filesystem-v1.md`
- Plan: `docs/superpowers/plans/2026-07-12-portable-core-filesystem.md#task-3-machine-local-runtime-and-progressive-indexing`
- Created: 2026-07-12 06:07 MYT
- Updated: 2026-07-12 06:07 MYT
- Started:
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

## Validation

- Commands:
  - `not run yet`
- Changed paths:
  - none
- Notes:
  - Claim after PCF-002 is done.
