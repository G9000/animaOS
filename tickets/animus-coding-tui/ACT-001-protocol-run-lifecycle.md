# ACT-001 - Fix server protocol and run lifecycle

- Status: done
- Priority: P1
- Scope: `apps/server`
- Parent: `ACT-000`
- Depends on: none
- Owner: codex
- PRD: docs/prds/animus/rust-coding-tui-v1.md
- Plan: docs/superpowers/plans/2026-06-27-animus-rust-coding-tui.md
- Created: 2026-06-26 18:51 MYT
- Updated: 2026-06-27 04:33 MYT
- Started: 2026-06-27 04:28 MYT
- Completed: 2026-06-27 04:33 MYT

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
- 2026-06-27 04:28 MYT - Started implementation; adding failing websocket lifecycle tests before route changes.
- 2026-06-27 04:33 MYT - Implemented websocket approval/cancel lifecycle fixes and verified focused server tests.

## Validation

- Commands:
  - `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test:server -- apps/server/tests/test_ws.py -q` - red first: 5 failed, 4 passed before route changes
  - `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test:server -- apps/server/tests/test_ws.py -q` - passed: 9 passed
  - `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test:server -- apps/server/tests/test_ws.py apps/server/tests/test_approval_reentry.py apps/server/tests/test_agent_service.py -q` - passed: 57 passed, 18 existing SQLAlchemy warnings
- Changed paths:
  - apps/server/src/anima_server/api/routes/ws.py
  - apps/server/tests/test_ws.py
- Notes:
  - `_translate_event()` now exposes `approval_required` and `tool_return.is_error`.
  - WebSocket approval responses stream translated resume events through the existing service-layer resume path.
  - Successful websocket cancels now emit a `cancelled` frame.

