# ACT-005 - Add local tools and permissions

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

Implement local Rust action tools and permission checks for delegated ANIMA tool calls.

## Deliverables

- Shell execution with timeout/cancel support.
- File read/write/edit/list/search tools.
- Local permission rules for read, write, and shell actions.
- Structured `tool_result` frames.

## Acceptance

- Tool dispatch can execute safe read/search actions.
- Dangerous shell/write actions route through permission decisions.
- Unit tests cover permission decisions and tool dispatch.

## Activity Log

- 2026-06-26 18:51 MYT - Ticket created.
- 2026-06-27 03:00 MYT - Revised from input/status work to Rust tools and permissions.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - backlog ticket only

