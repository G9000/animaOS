# MAG-003 - API client atlas contract

- Status: backlog
- Priority: P1
- Scope: `packages/api-client`
- Parent: `MAG-000`
- Depends on: `MAG-001`, `MAG-002`
- Owner: unassigned
- PRD: none
- Spec: docs/superpowers/specs/2026-07-05-memory-atlas-graph-design.md
- Plan: docs/superpowers/plans/2026-07-05-memory-atlas-graph.md
- Created: 2026-07-05 13:07 MYT
- Updated: 2026-07-05 13:07 MYT
- Started:
- Completed:

## Goal

Expose the Memory Atlas backend contract through the shared TypeScript API client.

## Deliverables

- Atlas graph and relation detail interfaces in `packages/api-client/src/types.ts`.
- `api.graph.atlas(...)` and `api.graph.relation(...)` methods in `packages/api-client/src/client.ts`.
- Client tests or typecheck validation for the new methods.

## Acceptance

- Desktop callers can fetch atlas payloads and relation details through `@anima/api-client`.
- Optional query params serialize through `URLSearchParams`.
- Existing graph client methods continue to work.
- Client tests or desktop build/typecheck pass.

## Activity Log

- 2026-07-05 13:07 MYT - Ticket created.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - none
