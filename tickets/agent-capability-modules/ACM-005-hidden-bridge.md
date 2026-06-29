# ACM-005 - Hidden desktop bridge channel for capability modules

- Status: backlog
- Priority: P1
- Scope: `apps/server`, `apps/desktop`
- Parent: `ACM-000`
- Depends on: `ACM-003`, `ACM-004`
- Owner: unassigned
- PRD: docs/prds/capability-modules/agent-capability-modules-v1.md
- Plan: docs/superpowers/plans/2026-06-29-agent-capability-modules.md
- Created: 2026-06-29 15:41 MYT
- Updated: 2026-06-29 15:41 MYT
- Started:
- Completed:

## Goal

Create the bridge pattern for modules that need desktop hardware or OS access.

## Deliverables

- Hidden bridge action registration.
- Server-side delegation for hidden actions.
- Desktop bridge lifecycle tied to unlock/auth state.

## Acceptance

- Hidden bridge actions are not model-visible.
- Bridge actions disconnect or become unavailable while logged out.
- Server module code can detect missing bridge availability.

## Activity Log

- 2026-06-29 15:41 MYT - Ticket created.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - not started
