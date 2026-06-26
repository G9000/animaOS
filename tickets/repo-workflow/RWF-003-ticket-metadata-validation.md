# RWF-003 - Add ticket metadata validation

- Status: backlog
- Priority: P2
- Scope: `tickets`, `scripts`
- Parent: `RWF-000`
- Depends on: `RWF-001`
- Owner: unassigned
- PRD: none
- Plan: docs/ops/prd-ticket-workflow.md
- Created: 2026-06-26 17:18 MYT
- Updated: 2026-06-26 17:18 MYT
- Started:
- Completed:

## Goal

Add a lightweight validation command that checks ticket metadata and parent-child consistency.

## Deliverables

- Script or documented command to validate `tickets/`
- Check required fields: Status, Priority, Scope, Parent, Depends on, Owner, Created, Updated
- Check child tickets reference existing parent trackers
- Check parent trackers list existing child files
- Check allowed status values

## Acceptance

- Validation fails on missing required metadata
- Validation reports stale parent references and missing child files
- Command is documented in `tickets/README.md`

## Activity Log

- 2026-06-26 17:18 MYT - Ticket created.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - backlog ticket only
