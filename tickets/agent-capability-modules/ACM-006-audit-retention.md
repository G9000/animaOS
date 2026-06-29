# ACM-006 - Capability audit and retention policy

- Status: backlog
- Priority: P1
- Scope: `apps/server`
- Parent: `ACM-000`
- Depends on: `ACM-002`
- Owner: unassigned
- PRD: docs/prds/capability-modules/agent-capability-modules-v1.md
- Plan: docs/superpowers/plans/2026-06-29-agent-capability-modules.md
- Created: 2026-06-29 15:41 MYT
- Updated: 2026-06-29 15:41 MYT
- Started:
- Completed:

## Goal

Define how capability use is audited and how module outputs move through runtime, archive, and soul boundaries.

## Deliverables

- Capability audit service.
- Retention policy enum/contract.
- Tests for no raw sensitive payloads in audit records.

## Acceptance

- Sensitive payloads are not stored in audit logs.
- Retention policy is explicit per module.
- Soul writes still pass through existing memory promotion/write boundaries.

## Activity Log

- 2026-06-29 15:41 MYT - Ticket created.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - not started
