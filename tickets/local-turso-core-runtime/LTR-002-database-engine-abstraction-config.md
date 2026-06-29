# LTR-002 - Database engine abstraction and config

- Status: backlog
- Priority: P0
- Scope: `apps/server/src/anima_server/db`, `apps/server/src/anima_server/config.py`
- Parent: `LTR-000`
- Depends on: `LTR-001`
- Owner: unassigned
- PRD: docs/prds/three-tier-architecture/local-turso-core-runtime-v1.md
- Plan: docs/superpowers/plans/2026-06-29-local-turso-core-runtime.md
- Created: 2026-06-29 15:50 MYT
- Updated: 2026-06-29 15:50 MYT
- Started:
- Completed:

## Goal

Introduce engine selection, manifest/config metadata, key derivation, and retryable Turso transaction helpers without changing the default SQLCipher/PostgreSQL behavior.

## Deliverables

- Soul and Runtime engine selection model.
- Manifest/config fields for active database engines and rollback metadata.
- Turso key derivation helper using ANIMA's existing Core passphrase flow.
- Retry helper for write-conflict-safe Turso transactions.
- Tests proving existing default database paths still behave unchanged.

## Acceptance

- SQLCipher remains the default Soul engine.
- PostgreSQL remains the default Runtime engine.
- Engine selection is explicit and not inferred from partial file presence.
- Turso key derivation uses a separate domain from SQLCipher.
- Retry helper handles known Turso conflict/busy errors identified in `LTR-001`.
- Focused database config tests pass.

## Activity Log

- 2026-06-29 15:50 MYT - Ticket created.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - none

