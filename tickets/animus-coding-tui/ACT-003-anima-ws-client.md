# ACT-003 - Build ANIMA WebSocket client

- Status: backlog
- Priority: P1
- Scope: `apps/animus`
- Parent: `ACT-000`
- Depends on: `ACT-001`, `ACT-002`
- Owner: unassigned
- PRD: docs/prds/animus/rust-coding-tui-v1.md
- Plan: docs/superpowers/plans/2026-06-27-animus-rust-coding-tui.md
- Created: 2026-06-26 18:51 MYT
- Updated: 2026-06-27 03:00 MYT
- Started:
- Completed:

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

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - backlog ticket only
