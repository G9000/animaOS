# RWF-001 - Rebuild the canonical ticket initiative index

- Status: backlog
- Priority: P2
- Scope: `tickets`
- Parent: `RWF-000`
- Depends on: none
- Owner: unassigned
- PRD: none
- Plan: docs/superpowers/plans/2026-07-15-repository-organization-project-management.md
- Created: 2026-06-26 17:18 MYT
- Updated: 2026-07-15 17:27 MYT
- Started:
- Completed:

## Goal

Rebuild `tickets/README.md` as the concise canonical index of active, completed, and legacy or unclassified initiatives.

## Deliverables

- Rebuild the canonical `tickets/README.md` initiative index
- Classify conforming parent trackers as active or completed from normalized parent metadata
- Classify folders without a conforming parent as legacy or unclassified
- Link classified initiatives to parent trackers and retain conventions, template/workflow links, and `bun run check:repo`

## Acceptance

- `tickets/README.md` contains `Active Initiatives`, `Completed Initiatives`, and `Legacy or Unclassified` sections
- Every conforming parent tracker appears exactly once under active or completed according to normalized parent `Status:` metadata
- Parent completion is never inferred from child state, historical prose, blockers, or progress counts
- Every folder without a conforming parent is listed under legacy or unclassified, and every listed parent link resolves
- The index retains ticket conventions, template and workflow links, and the `bun run check:repo` command without duplicating child acceptance criteria

## Activity Log

- 2026-06-26 17:18 MYT - Ticket created.
- 2026-07-15 17:11 MYT - Aligned the ticket with the combined repository-organization plan and canonical `tickets/README.md` dashboard.
- 2026-07-15 17:27 MYT - Narrowed the outcome to the approved concise initiative classification index.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - backlog ticket only
