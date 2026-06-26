# ACT-005 - Add local tools and permissions

- Status: done
- Priority: P1
- Scope: `apps/animus`
- Parent: `ACT-000`
- Depends on: `ACT-003`
- Owner: codex
- PRD: docs/prds/animus/rust-coding-tui-v1.md
- Plan: docs/superpowers/plans/2026-06-27-animus-rust-coding-tui.md
- Created: 2026-06-26 18:51 MYT
- Updated: 2026-06-27 04:55 MYT
- Started: 2026-06-27 04:49 MYT
- Completed: 2026-06-27 04:55 MYT

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
- 2026-06-27 04:49 MYT - Started Rust local tools and permission policy with tests first.
- 2026-06-27 04:55 MYT - Completed local tool schema registry, file/shell/background tools, tool-result dispatch, and permission policy.

## Validation

- Commands:
  - `cargo test -p animus` - red first: missing permission/tool types and functions
  - `cargo test -p animus` - failed after implementation on Rust 2021-incompatible `if let` chains; fixed syntax
  - `cargo test -p animus` - failed once on background output timing; replaced fixed sleep with condition polling
  - `cargo test -p animus tools` - passed: 8 passed, 23 filtered out
  - `cargo test -p animus permissions` - passed: 4 passed, 27 filtered out
  - `cargo test -p animus` - passed: 31 passed
  - `cargo check -p animus` - passed
- Changed paths:
  - apps/animus/src/permissions.rs
  - apps/animus/src/tools/mod.rs
  - apps/animus/src/tools/files.rs
  - apps/animus/src/tools/shell.rs
  - apps/animus/src/tools/process.rs
  - apps/animus/src/main.rs
- Notes:
  - Tool schemas preserve the legacy action tool names.
  - File writes are constrained to the workspace; read-only mode denies writes.
  - Shell commands default to ask unless allowed; dangerous commands are denied even in shell-allow mode.

