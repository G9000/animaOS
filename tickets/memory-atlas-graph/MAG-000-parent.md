# MAG-000 - Memory Atlas Graph Parent Tracker

- Status: backlog
- Priority: P1
- Scope: `apps/server`, `packages/api-client`, `apps/desktop`, `docs/superpowers/plans`, `tickets/memory-atlas-graph`
- Depends on: approved Memory Atlas design spec
- Owner: unassigned
- PRD: none
- Spec: docs/superpowers/specs/2026-07-05-memory-atlas-graph-design.md
- Plan: docs/superpowers/plans/2026-07-05-memory-atlas-graph.md
- Created: 2026-07-05 13:07 MYT
- Updated: 2026-07-05 13:07 MYT
- Started:
- Completed:

## Goal

Track the Memory Atlas Graph initiative that adds a read-only `/memory/graph` desktop surface for exploring ANIMA's entity graph as a temporal Timeline Bloom with on-demand detail and evidence inspection.

## Child Ticket Order

| Ticket | Title | Status | Depends on |
| --- | --- | --- | --- |
| `MAG-001` | Backend atlas canvas payload | `backlog` | approved design spec |
| `MAG-002` | Backend detail and evidence reads | `backlog` | `MAG-001` |
| `MAG-003` | API client atlas contract | `backlog` | `MAG-001`, `MAG-002` |
| `MAG-004` | Desktop Memory Atlas route and shell | `backlog` | `MAG-003` |
| `MAG-005` | Timeline Bloom canvas and inspector | `backlog` | `MAG-004` |
| `MAG-006` | Visual smoke, docs, and final validation | `backlog` | `MAG-005` |

## Deliverables

- User-scoped backend atlas payload for active `KGEntity` and `KGRelation` graph data.
- Focused relation/detail/evidence read paths that do not overload the canvas payload.
- Typed `@anima/api-client` contract for atlas and detail reads.
- `/memory/graph` desktop route linked from the Memory page.
- Timeline Bloom canvas with search, filters, timeline scrubber, and clean-story-first inspector.
- Focused backend, desktop, and visual validation records.

## Acceptance

- Every child ticket references this parent.
- The parent status table reflects child progress.
- `/graph` remains available as the technical graph inspector.
- `/memory/graph` renders a read-only living memory map.
- Initial canvas payload stays bounded and does not decrypt every evidence row.
- Node and edge selection can inspect details and evidence on demand.
- Final validation includes visual smoke, not only typecheck/build.

## Completed Tickets

- none

## Activity Log

- 2026-07-05 13:07 MYT - Parent tracker created for Memory Atlas Graph planning.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - tickets/memory-atlas-graph/MAG-000-parent.md
- Notes:
  - planning tracker only
