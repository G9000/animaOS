# LTR-005 - Turso Runtime schema and transactions

- Status: backlog
- Priority: P1
- Scope: `apps/server/src/anima_server/models`, `apps/server/src/anima_server/db`, `apps/server/alembic_runtime`, `apps/server/tests`
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

Create a Turso-compatible Runtime schema and transaction layer for operational tables currently backed by embedded PostgreSQL.

## Deliverables

- Runtime model compatibility pass for SQLite/Turso types.
- Turso runtime bootstrap or migration path.
- Transaction helper integration for runtime writes that need `BEGIN CONCURRENT` and retry.
- Concurrent write tests for messages, runs, pending ops, memory candidates, session notes, workflow checkpoints, and document rows.

## Acceptance

- Turso Runtime tables can be created from a clean data dir.
- PostgreSQL-only `ARRAY`, `TIMESTAMPTZ`, JSON dialect assumptions, and dialect SQL are handled.
- Runtime write hot paths use retryable transactions where needed.
- Concurrent write tests pass without lost rows.
- PostgreSQL Runtime path remains available.

## Activity Log

- 2026-06-29 15:50 MYT - Ticket created.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - none

