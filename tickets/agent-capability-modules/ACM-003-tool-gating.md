# ACM-003 - Agent tool gating by capability policy

- Status: backlog
- Priority: P1
- Scope: `apps/server`
- Parent: `ACM-000`
- Depends on: `ACM-001`, `ACM-002`
- Owner: unassigned
- PRD: docs/prds/capability-modules/agent-capability-modules-v1.md
- Plan: docs/superpowers/plans/2026-06-29-agent-capability-modules.md
- Created: 2026-06-29 15:41 MYT
- Updated: 2026-06-29 15:41 MYT
- Started:
- Completed:

## Goal

Make agent-visible tools depend on enabled capability modules and hide lower-level bridge primitives.

## Deliverables

- Capability-aware tool assembly.
- Policy checks for unavailable/degraded modules.
- Hidden bridge primitive filtering.

## Acceptance

- Disabled optional modules expose no tools.
- Hidden bridge actions are callable only by server module code.
- The model sees semantic tools, not raw device primitives.

## Activity Log

- 2026-06-29 15:41 MYT - Ticket created.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - not started
