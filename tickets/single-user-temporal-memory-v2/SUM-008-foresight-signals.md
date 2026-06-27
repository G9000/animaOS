# SUM-008 - Foresight signals

- Status: backlog
- Priority: P2
- Scope: `apps/server/src/anima_server/models`, `apps/server/src/anima_server/services/agent`
- Parent: `SUM-000`
- Depends on: `SUM-002`
- Owner: unassigned
- PRD: docs/prds/memory/single-user-temporal-memory-v2.md
- Plan: docs/superpowers/plans/2026-06-27-single-user-temporal-memory-v2.md
- Created: 2026-06-27 12:40 MYT
- Updated: 2026-06-27 12:40 MYT
- Started:
- Completed:

## Goal

Implement future-oriented memory so Anima can remember commitments, expected events, and follow-up opportunities.

## Deliverables

- F8 `ForesightSignal` model and migration.
- Consolidation extraction for future events and expected outcomes.
- Relative date resolution against conversation timestamp.
- Lifecycle sweep for active, due, occurred, stale, and cancelled signals.
- Retrieval and proactive prompt integration.

## Acceptance

- Future events are extracted without requiring explicit task creation.
- Relative dates resolve deterministically in tests.
- Stale/due lifecycle transitions are covered.
- Foresight signals remain evidence-backed.

## Activity Log

- 2026-06-27 12:40 MYT - Ticket created.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - none
