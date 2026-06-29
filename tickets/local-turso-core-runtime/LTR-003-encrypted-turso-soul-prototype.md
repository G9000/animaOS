# LTR-003 - Encrypted Turso Soul prototype

- Status: backlog
- Priority: P0
- Scope: `apps/server/src/anima_server/db`, `apps/server/tests`
- Parent: `LTR-000`
- Depends on: `LTR-002`
- Owner: unassigned
- PRD: docs/prds/three-tier-architecture/local-turso-core-runtime-v1.md
- Plan: docs/superpowers/plans/2026-06-29-local-turso-core-runtime.md
- Created: 2026-06-29 15:50 MYT
- Updated: 2026-06-29 15:50 MYT
- Started:
- Completed:

## Goal

Create an optional encrypted Turso Soul backend that can support a fresh Core's core auth and memory operations without replacing SQLCipher by default.

## Deliverables

- Turso Soul session factory behind explicit engine selection.
- Fresh Turso Soul schema bootstrap or Alembic-compatible migration path.
- Tests for unlock, close, reopen, auth/profile reads, and basic memory CRUD.
- Raw-byte inspection test using known fixture text.

## Acceptance

- A fresh Turso Soul DB can be created under a user Core path.
- The DB can be reopened only with the derived key.
- Basic user/auth/profile/memory operations work through existing service boundaries.
- Known plaintext fixture text is not visible in the raw database file.
- SQLCipher code paths remain unaffected.

## Activity Log

- 2026-06-29 15:50 MYT - Ticket created.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - none

