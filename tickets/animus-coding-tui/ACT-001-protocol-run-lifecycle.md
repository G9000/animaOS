# ACT-001 - Fix protocol and run lifecycle

- Status: backlog
- Priority: P1
- Scope: `apps/animus`, `apps/server`
- Parent: `ACT-000`
- Depends on: none
- Owner: unassigned
- PRD: docs/prds/animus/coding-tui-v1.md
- Plan: docs/superpowers/plans/2026-06-26-animus-coding-tui.md
- Created: 2026-06-26 18:51 MYT
- Updated: 2026-06-26 18:51 MYT
- Started:
- Completed:

## Goal

Make Animus and `/ws/agent` agree on run lifecycle, cancel, approval response, and server frame types.

## Deliverables

- Animus protocol types for all server frames needed by the TUI.
- Current `run_id` tracking in Animus.
- Cancel messages include `run_id`.
- Server approval response handling is implemented or wired to the existing runtime path.
- Focused tests for protocol/lifecycle behavior.

## Acceptance

- `run_started` and `cancelled` frames are typed and handled by Animus.
- `/cancel` can target the current run without malformed payloads.
- Approval responses round-trip to the server without being ignored.
- Tests cover the fixed frame shapes and at least one cancel/approval path.

## Activity Log

- 2026-06-26 18:51 MYT - Ticket created.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - backlog ticket only

