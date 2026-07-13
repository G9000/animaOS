# SID-003 - Audience policy harness probes

- Status: backlog
- Priority: P1
- Scope: `apps/server/tests`, `docs/superpowers/plans`
- Parent: `SID-000`
- Depends on: `SID-001`
- Owner: unassigned
- PRD: docs/prds/memory/social-memory-identity-discovery-v1.md
- Plan: docs/superpowers/plans/2026-07-01-social-memory-identity-discovery.md
- Created: 2026-07-01 15:40 MYT
- Updated: 2026-07-01 15:40 MYT
- Started:
- Completed:

## Goal

Create deterministic harness probes that prevent future runtime work from leaking memories across people, duplicate names, groups, or shared-room contexts.

## Deliverables

- Probe definitions for duplicate-name ambiguity.
- Probe definitions for private-memory leakage between people.
- Probe definitions for group memory boundaries.
- Probe definitions for trace and dry-run prompt filtering.

## Acceptance

- Probes describe exact setup, action, and expected outcome.
- At least one probe covers two people named Alex.
- At least one probe covers a private memory about a person that is owned by someone else.
- Pending probes can exist before full social-memory storage lands.

## Activity Log

- 2026-07-01 15:40 MYT - Ticket created.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - backlog ticket only
