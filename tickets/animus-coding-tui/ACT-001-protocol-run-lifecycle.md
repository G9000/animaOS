# ACT-001 - Fix server protocol and run lifecycle

- Status: backlog
- Priority: P1
- Scope: `apps/server`
- Parent: `ACT-000`
- Depends on: none
- Owner: unassigned
- PRD: docs/prds/animus/rust-coding-tui-v1.md
- Plan: docs/superpowers/plans/2026-06-27-animus-rust-coding-tui.md
- Created: 2026-06-26 18:51 MYT
- Updated: 2026-06-27 03:00 MYT
- Started:
- Completed:

## Goal

Make `/ws/agent` expose the complete run lifecycle needed by the Rust Animus TUI.

## Deliverables

- Server frames for run start, cancellation, approval required, turn complete, structured errors, and spawn lifecycle.
- Working `approval_response` handling.
- `cancel` handling keyed by current `run_id`.
- Focused server tests for approval and cancel behavior.

## Acceptance

- Cancel is idempotent and emits a terminal frame.
- Approval responses are not ignored.
- Rust client work can rely on stable frame shapes.
- Focused tests cover the fixed paths.

## Activity Log

- 2026-06-26 18:51 MYT - Ticket created.
- 2026-06-27 03:00 MYT - Revised for Rust rewrite scope.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - backlog ticket only

