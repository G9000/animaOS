# LTR-001 - Turso compatibility and risk spike

- Status: backlog
- Priority: P0
- Scope: `apps/server`
- Parent: `LTR-000`
- Depends on: none
- Owner: unassigned
- PRD: docs/prds/three-tier-architecture/local-turso-core-runtime-v1.md
- Plan: docs/superpowers/plans/2026-06-29-local-turso-core-runtime.md
- Created: 2026-06-29 15:50 MYT
- Updated: 2026-06-29 15:50 MYT
- Started:
- Completed:

## Goal

Prove or reject local Turso Database as a viable engine candidate for ANIMA's Soul and Runtime stores before production code depends on it.

## Deliverables

- Compatibility matrix for local Turso driver, SQLAlchemy integration, encryption, MVCC, transactions, vector functions, and unsupported SQL.
- Scratch tests or scripts for encrypted reopen, raw-byte plaintext inspection, concurrent writes with retry, and basic vector search.
- Written go/no-go recommendation for `LTR-002`, `LTR-003`, and `LTR-005`.

## Acceptance

- Local Turso package choice is identified and installability is verified.
- `PRAGMA journal_mode = mvcc` and `BEGIN CONCURRENT` behavior is tested.
- Encryption setup is tested with an ANIMA-derived raw key or documented as blocked.
- SQLAlchemy transaction behavior is understood well enough to design the adapter.
- Runtime PostgreSQL-specific features requiring rewrites are listed.
- Vector search feasibility is tested or explicitly blocked.

## Activity Log

- 2026-06-29 15:50 MYT - Ticket created.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - none

