# RWF-001 - Add top-level tickets dashboard

- Status: backlog
- Priority: P2
- Scope: `tickets`
- Parent: `RWF-000`
- Depends on: none
- Owner: unassigned
- PRD: none
- Plan: docs/superpowers/plans/2026-07-15-repository-organization-project-management.md
- Created: 2026-06-26 17:18 MYT
- Updated: 2026-07-15 17:11 MYT
- Started:
- Completed:

## Goal

Create a top-level dashboard that shows active initiatives, parent trackers, next ticket, blockers, and completion counts.

## Deliverables

- Rebuild the canonical `tickets/README.md` initiative index
- Separate active and completed initiatives from legacy or unclassified folders
- Derive parent initiative state from canonical parent metadata
- Link each classified initiative to its parent tracker without duplicating child acceptance

## Acceptance

- A future agent can answer "what is next" from one file
- `tickets/README.md` links to every conforming parent tracker and classifies nonconforming initiative folders as legacy or unclassified
- Active versus completed placement follows normalized parent metadata rather than inferred child or prose state
- Dashboard does not duplicate detailed child acceptance criteria

## Activity Log

- 2026-06-26 17:18 MYT - Ticket created.
- 2026-07-15 17:11 MYT - Aligned the ticket with the combined repository-organization plan and canonical `tickets/README.md` dashboard.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - backlog ticket only
