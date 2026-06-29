# LTR-000 - Local Turso Core and Runtime Parent Tracker

- Status: backlog
- Priority: P0
- Scope: `apps/server`, `docs/prds/three-tier-architecture`, `docs/superpowers/plans`, `tickets/local-turso-core-runtime`
- Depends on: none
- Owner: unassigned
- PRD: docs/prds/three-tier-architecture/local-turso-core-runtime-v1.md
- Plan: docs/superpowers/plans/2026-06-29-local-turso-core-runtime.md
- Created: 2026-06-29 15:50 MYT
- Updated: 2026-06-29 15:50 MYT
- Started:
- Completed:

## Goal

Track the gated migration from SQLCipher Soul plus embedded PostgreSQL Runtime to local Turso Soul plus local Turso Runtime while preserving Core portability, encryption, rollback, and the physical Soul/Runtime boundary.

## Child Ticket Order

| Ticket | Title | Status | Depends on |
| --- | --- | --- | --- |
| `LTR-001` | Turso compatibility and risk spike | `backlog` | none |
| `LTR-002` | Database engine abstraction and config | `backlog` | `LTR-001` |
| `LTR-003` | Encrypted Turso Soul prototype | `backlog` | `LTR-002` |
| `LTR-004` | Soul migration copy-verify-flip | `backlog` | `LTR-003` |
| `LTR-005` | Turso Runtime schema and transactions | `backlog` | `LTR-002` |
| `LTR-006` | Runtime cutover and PostgreSQL bypass | `backlog` | `LTR-005` |
| `LTR-007` | Vector retrieval parity without pgvector | `backlog` | `LTR-005` |
| `LTR-008` | Documentation, cleanup, and default decision | `backlog` | `LTR-004`, `LTR-006`, `LTR-007` |

## Deliverables

- Compatibility matrix for local Turso Database versus ANIMA's current SQLCipher and PostgreSQL usage.
- Database engine selection and manifest/config support.
- Optional encrypted Turso Soul backend.
- Copy-verify-flip migration from SQLCipher Soul to Turso Soul.
- Turso Runtime schema and transaction layer with conflict retry.
- Startup path that can bypass embedded PostgreSQL when Turso Runtime is active.
- Vector retrieval replacement or fallback that makes pgvector optional.
- Updated docs, health checks, rollout notes, and rollback instructions.

## Acceptance

- Child tickets reference this parent.
- Parent status table reflects child progress.
- Soul and Runtime remain physically separate database files.
- Existing SQLCipher Soul is never modified in place during migration.
- Failed migration leaves SQLCipher active.
- Turso Soul can unlock, reopen, and pass raw-byte plaintext inspection tests.
- Turso Runtime can handle concurrent runtime writes with retry and no lost rows.
- Server can boot without embedded PostgreSQL after Runtime and vector parity tickets pass.
- Architecture docs explain the new local engine choice and remaining tradeoffs.

## Completed Tickets

- none

## Activity Log

- 2026-06-29 15:50 MYT - Parent tracker created for local Turso Core and Runtime migration planning.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - tickets/local-turso-core-runtime/LTR-000-parent.md
- Notes:
  - tracker only

