# LTR-006 - Runtime cutover and PostgreSQL bypass

- Status: backlog
- Priority: P1
- Scope: `apps/server/src/anima_server/main.py`, `apps/server/src/anima_server/db`, `apps/server/src/anima_server/services/health`
- Parent: `LTR-000`
- Depends on: `LTR-005`
- Owner: unassigned
- PRD: docs/prds/three-tier-architecture/local-turso-core-runtime-v1.md
- Plan: docs/superpowers/plans/2026-06-29-local-turso-core-runtime.md
- Created: 2026-06-29 15:50 MYT
- Updated: 2026-06-29 15:50 MYT
- Started:
- Completed:

## Goal

Allow the server to use Turso Runtime and skip embedded PostgreSQL startup when the active Runtime engine is Turso.

## Deliverables

- Runtime engine config or manifest selection for `postgres` versus `turso`.
- Startup lifecycle branch that does not start `pgserver` for Turso Runtime.
- Runtime rebuild-first initialization path from Soul and transcripts.
- Health checks for Turso Runtime readiness and migration state.
- Chat smoke tests with PostgreSQL bypassed.

## Acceptance

- Server boots without embedded PostgreSQL when Turso Runtime is selected.
- `/health` reports Turso Runtime status clearly.
- Basic auth, chat, memory block assembly, and Soul Writer queue behavior work.
- Runtime rebuild path does not require existing PostgreSQL state.
- PostgreSQL path still works when selected.

## Activity Log

- 2026-06-29 15:50 MYT - Ticket created.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - none

