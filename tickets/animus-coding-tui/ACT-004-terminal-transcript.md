# ACT-004 - Add terminal event loop and transcript

- Status: backlog
- Priority: P1
- Scope: `apps/animus`
- Parent: `ACT-000`
- Depends on: `ACT-003`
- Owner: unassigned
- PRD: docs/prds/animus/rust-coding-tui-v1.md
- Plan: docs/superpowers/plans/2026-06-27-animus-rust-coding-tui.md
- Created: 2026-06-26 18:51 MYT
- Updated: 2026-06-27 03:00 MYT
- Started:
- Completed:

## Goal

Build the Rust terminal event loop and basic transcript rendering.

## Deliverables

- Terminal raw mode/alternate screen lifecycle.
- App state and reducer for connection, input, transcript, current run, and errors.
- App event enum for widget-to-app coordination.
- History-cell renderers for user, assistant, reasoning, shell execution, file changes, plans/todos, approvals, notices, search, session events, and errors.
- Live streaming assistant output.

## Acceptance

- Terminal state is restored on normal exit and error exit.
- Mocked server frames render through typed app events into readable history cells.
- App reducer tests cover representative stream frames.

## Activity Log

- 2026-06-26 18:51 MYT - Ticket created.
- 2026-06-27 03:00 MYT - Revised for Rust terminal transcript.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - backlog ticket only
