# LTR-004 - Soul migration copy-verify-flip

- Status: backlog
- Priority: P0
- Scope: `apps/server/src/anima_server/db`, `apps/server/src/anima_server/services/core.py`, `apps/server/tests`
- Parent: `LTR-000`
- Depends on: `LTR-003`
- Owner: unassigned
- PRD: docs/prds/three-tier-architecture/local-turso-core-runtime-v1.md
- Plan: docs/superpowers/plans/2026-06-29-local-turso-core-runtime.md
- Created: 2026-06-29 15:50 MYT
- Updated: 2026-06-29 15:50 MYT
- Started:
- Completed:

## Goal

Implement the safe migration from existing SQLCipher Soul DBs to encrypted Turso Soul DBs using copy-verify-flip semantics and rollback metadata.

## Deliverables

- Migration service or command that opens SQLCipher and writes a fresh Turso Soul DB.
- Table copy ordering with primary keys preserved.
- Verifier for row counts, FK integrity, schema version, selected content hashes, and reopen checks.
- Manifest flip only after verification passes.
- Failure-injection tests proving SQLCipher remains active after a failed migration.

## Acceptance

- Existing SQLCipher Soul DB is never modified in place.
- A successful migration preserves durable identity and memory data.
- A failed migration leaves the manifest pointing at SQLCipher.
- Migration logs report changed paths, row counts, and verification status.
- Rollback metadata is recorded until cleanup is explicitly requested.

## Activity Log

- 2026-06-29 15:50 MYT - Ticket created.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - none

