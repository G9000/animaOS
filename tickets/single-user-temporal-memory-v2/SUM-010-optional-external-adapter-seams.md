# SUM-010 - Optional external adapter seams

- Status: backlog
- Priority: P3
- Scope: `apps/server/src/anima_server/services/agent`, `docs/architecture/memory`
- Parent: `SUM-000`
- Depends on: `SUM-003`, `SUM-005`
- Owner: unassigned
- PRD: docs/prds/memory/single-user-temporal-memory-v2.md
- Plan: docs/superpowers/plans/2026-06-27-single-user-temporal-memory-v2.md
- Created: 2026-06-27 12:40 MYT
- Updated: 2026-06-27 12:40 MYT
- Started:
- Completed:

## Goal

Create optional adapter seams for external retrieval or graph engines without making any external system canonical or mandatory.

## Deliverables

- Retrieval backend interface with native implementation as the reference.
- Rebuild contract from SQLCipher canonical memory into derived indexes.
- Documentation for optional Weaviate/Qdrant/LanceDB-style vector adapters.
- Deferred graph backend interface only after native temporal KG semantics are stable.

## Acceptance

- Native backend remains the default.
- External indexes can be dropped and rebuilt from canonical storage.
- No external service is required for normal local use.
- Adapter contract tests pass against the native backend.

## Activity Log

- 2026-06-27 12:40 MYT - Ticket created.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - none
