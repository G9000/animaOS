# MAG-005 - Timeline Bloom canvas and inspector

- Status: backlog
- Priority: P1
- Scope: `apps/desktop`
- Parent: `MAG-000`
- Depends on: `MAG-004`
- Owner: unassigned
- PRD: none
- Spec: docs/superpowers/specs/2026-07-05-memory-atlas-graph-design.md
- Plan: docs/superpowers/plans/2026-07-05-memory-atlas-graph.md
- Created: 2026-07-05 13:07 MYT
- Updated: 2026-07-05 13:07 MYT
- Started:
- Completed:

## Goal

Render the living Timeline Bloom graph with deterministic layout, search, filters, timeline scrubber, and clean-story-first inspector.

## Deliverables

- React Flow atlas canvas component.
- Deterministic layout helpers.
- Timeline filtering/dimming behavior.
- Entity type and relation type filters.
- Search focus or highlight.
- Node and edge selection inspector.
- On-demand relation detail/evidence fetch.

## Acceptance

- Graph nodes and edges are visible with non-empty atlas data.
- Reloading the same payload produces stable positions.
- Timeline scrubber changes node/edge visibility or dimming.
- Search and filters affect the visible graph.
- Node selection opens entity details.
- Edge selection opens relation details and evidence snippets.
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
