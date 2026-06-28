# ACT-003 - Build ANIMA WebSocket client

- Status: done
- Priority: P1
- Scope: `apps/animus`
- Parent: `ACT-000`
- Depends on: `ACT-001`, `ACT-002`
- Owner: codex
- PRD: docs/prds/animus/rust-coding-tui-v1.md
- Plan: docs/superpowers/plans/2026-06-27-animus-rust-coding-tui.md
- Created: 2026-06-26 18:51 MYT
- Updated: 2026-06-27 04:42 MYT
- Started: 2026-06-27 04:37 MYT
- Completed: 2026-06-27 04:42 MYT

## Goal

Implement the Rust client layer that connects Animus to ANIMA `/ws/agent`.

## Deliverables

- Typed Rust protocol models.
- Separate wire-frame and app-event models.
- Config loading for server URL, workspace, and unlock token/config.
- WebSocket connect/auth/tool schema registration.
- Current `run_id` tracking.
- Send helpers for user messages, tool results, approvals, and cancel.

## Acceptance

- Rust tests cover frame serialization/deserialization.
- App events are produced from typed protocol frames rather than loose JSON.
- Client can authenticate against `/ws/agent` in a focused smoke test or mocked transport.
- `run_id` is available for cancel and approval flows.

## Activity Log

- 2026-06-26 18:51 MYT - Ticket created.
- 2026-06-27 03:00 MYT - Revised for Rust WebSocket client.
- 2026-06-27 04:37 MYT - Started Rust protocol, config, and websocket client tests.
- 2026-06-27 04:42 MYT - Completed typed protocol, config resolution, websocket client wrapper, run tracking, and send helpers.

## Validation

- Commands:
  - `cargo test -p animus` - red first: missing protocol/config/client types and helpers
  - `cargo test -p animus protocol client config` - command syntax failed; Cargo accepts one test filter only
  - `cargo test -p animus protocol` - passed: 3 passed, 10 filtered out
  - `cargo test -p animus client` - passed: 6 passed, 7 filtered out
  - `cargo test -p animus config` - passed: 4 passed, 9 filtered out
  - `cargo test -p animus` - passed: 13 passed
  - `cargo check -p animus` - passed
  - `cargo run -p animus -- --headless` - passed; config resolver printed startup summary
- Changed paths:
  - apps/animus/src/config.rs
  - apps/animus/src/protocol.rs
  - apps/animus/src/client.rs
  - apps/animus/src/main.rs
- Notes:
  - Protocol models match the server frame names, including `approval_required`, `cancelled`, `spawn_event`, and `tool_return.is_error`.
  - Config precedence is CLI overrides, environment, legacy `.animus` config, then defaults.
  - `AnimaWsClient` exposes connect/auth/tool schema registration and send helpers; later TUI tickets will call it.
