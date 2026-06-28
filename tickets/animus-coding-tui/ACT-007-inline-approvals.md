# ACT-007 - Add inline approvals

- Status: done
- Priority: P1
- Scope: `apps/animus`, `apps/server`
- Parent: `ACT-000`
- Depends on: `ACT-001`, `ACT-004`, `ACT-005`
- Owner: codex
- PRD: docs/prds/animus/rust-coding-tui-v1.md
- Plan: docs/superpowers/plans/2026-06-27-animus-rust-coding-tui.md
- Created: 2026-06-26 18:51 MYT
- Updated: 2026-06-27 05:03 MYT
- Started: 2026-06-27 05:00 MYT
- Completed: 2026-06-27 05:03 MYT

## Goal

Add tool-aware inline approval prompts to the Rust TUI.

## Deliverables

- Pending approval state.
- Separate shell execution and file-change approval display models.
- Question/generic approval renderers.
- Accept, accept-for-session, policy-amendment accept where available, decline, and cancel decisions.
- `approval_response` send path with expected IDs.

## Acceptance

- Approval UI clearly shows what action is being requested.
- Decisions round-trip to server correctly.
- Approval reducer tests cover accept, accept-for-session, decline, cancel, and remembered/policy decision flows.

## Activity Log

- 2026-06-26 18:51 MYT - Ticket created.
- 2026-06-27 03:00 MYT - Revised for Rust inline approvals.
- 2026-06-27 05:00 MYT - Started approval state, app integration, and TUI approval rendering tests.
- 2026-06-27 05:03 MYT - Completed pending approval state, decision frames, session remembering, keyboard decisions, and TUI approval prompt rendering.

## Validation

- Commands:
  - `cargo test -p animus` - red first: missing approval state and app integration symbols
  - `cargo test -p animus approvals` - passed: 5 passed, 39 filtered out
  - `cargo test -p animus app` - passed: 13 passed, 31 filtered out
  - `cargo test -p animus permissions` - passed: 4 passed, 40 filtered out
  - `cargo test -p animus` - passed: 44 passed
  - `cargo check -p animus` - passed
  - `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test:server -- apps/server/tests/test_ws.py -q` - passed: 9 passed
- Changed paths:
  - apps/animus/src/approvals.rs
  - apps/animus/src/app.rs
  - apps/animus/src/tui.rs
  - apps/animus/src/main.rs
- Notes:
  - Approval decisions produce `approval_response` frames with `run_id`, `tool_call_id`, `approved`, and optional `reason`.
  - Shell and file-change approvals have separate display/session-remember models.
  - No server code changes were needed beyond ACT-001.
