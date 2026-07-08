# MAG-004 - Desktop Memory Atlas route and shell

- Status: backlog
- Priority: P1
- Scope: `apps/desktop`
- Parent: `MAG-000`
- Depends on: `MAG-003`
- Owner: unassigned
- PRD: none
- Spec: docs/superpowers/specs/2026-07-05-memory-atlas-graph-design.md
- Plan: docs/superpowers/plans/2026-07-05-memory-atlas-graph.md
- Created: 2026-07-05 13:07 MYT
- Updated: 2026-07-05 13:07 MYT
- Started:
- Completed:

## Goal

Create the `/memory/graph` desktop route, Memory page entry point, and shell layout for the Memory Atlas.

## Deliverables

- New route in `apps/desktop/src/App.tsx`.
- Link from `apps/desktop/src/pages/memory/Memory.tsx`.
- New `apps/desktop/src/pages/memory/MemoryGraph.tsx`.
- Initial `apps/desktop/src/components/memory-atlas/` component set.
- Loading, empty, and error states.

## Acceptance

- `/memory/graph` loads behind the protected app layout.
- Memory page links to the atlas near Images.
- The shell has left controls, center canvas area, and right inspector area.
- The page handles empty graph data without a broken canvas.
- Desktop build/typecheck passes.

## Activity Log

- 2026-07-05 13:07 MYT - Ticket created.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - none
