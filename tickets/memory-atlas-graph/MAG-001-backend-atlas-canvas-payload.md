# MAG-001 - Backend atlas canvas payload

- Status: backlog
- Priority: P1
- Scope: `apps/server`
- Parent: `MAG-000`
- Depends on: approved design spec
- Owner: unassigned
- PRD: none
- Spec: docs/superpowers/specs/2026-07-05-memory-atlas-graph-design.md
- Plan: docs/superpowers/plans/2026-07-05-memory-atlas-graph.md
- Created: 2026-07-05 13:07 MYT
- Updated: 2026-07-05 13:07 MYT
- Started:
- Completed:

## Goal

Add a read-only backend endpoint that returns bounded Memory Atlas canvas data for user-owned entities and active relations.

## Deliverables

- Atlas canvas route in `apps/server/src/anima_server/api/routes/graph.py`.
- Focused tests in `apps/server/tests/test_memory_atlas_graph_api.py`.
- Timestamp fallback for timeline rendering.
- Query options for cap, entity type, relation type, and search focus.

## Acceptance

- The route requires the existing unlocked-user dependency.
- The route returns only entities and active relations for the requested user.
- The payload includes nodes, edges, stats, available entity/relation type counts, and earliest/latest observed timestamps.
- Missing relation timestamps fall back deterministically.
- Focused backend tests pass.

## Activity Log

- 2026-07-05 13:07 MYT - Ticket created.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - none
