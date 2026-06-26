# ACT-008 - Add ANIMA spawn and thread visibility

- Status: done
- Priority: P1
- Scope: `apps/animus`, `apps/server`
- Parent: `ACT-000`
- Depends on: `ACT-004`
- Owner: codex
- PRD: docs/prds/animus/rust-coding-tui-v1.md
- Plan: docs/superpowers/plans/2026-06-27-animus-rust-coding-tui.md
- Created: 2026-06-26 18:51 MYT
- Updated: 2026-06-27 06:20 MYT
- Started: 2026-06-27 05:04 MYT
- Completed: 2026-06-27 06:20 MYT

## Goal

Expose ANIMA's single-identity background spawning model in the Rust TUI.

## Deliverables

- Spawn event types for queued/running/completed/failed/cancelled.
- Spawn count in status line.
- `/spawns` list view with task preview, running/closed state, and status marker.
- `/cancel-spawn <id>` command when server support exists.

## Acceptance

- Running, completed, failed, and cancelled spawn states can be represented.
- Spawn/thread list supports navigation-ready state even if fast switching is deferred.
- Spawned workers are labeled as ANIMA background processes, not independent personas.
- Dangerous delegated action tools are not exposed to spawns by default.

## Activity Log

- 2026-06-26 18:51 MYT - Ticket created.
- 2026-06-27 03:00 MYT - Revised for Rust spawn/thread visibility.
- 2026-06-27 05:04 MYT - Started spawn visibility after reading the P8 N-Agent Spawning PRD.
- 2026-06-27 06:20 MYT - Completed typed spawn statuses, background process list rendering, status count, and unsupported cancel-spawn state.

## Validation

- Commands:
  - `cargo test -p animus` - red first: missing spawn status/state and command effect types
  - `cargo test -p animus spawns` - passed: 2 passed, 46 filtered out
  - `cargo test -p animus commands` - passed: 4 passed, 44 filtered out
  - `cargo test -p animus protocol` - passed: 4 passed, 44 filtered out
  - `cargo test -p animus` - passed: 48 passed
  - `cargo check -p animus` - passed
- Changed paths:
  - apps/animus/src/spawns.rs
  - apps/animus/src/protocol.rs
  - apps/animus/src/app.rs
  - apps/animus/src/commands.rs
  - apps/animus/src/main.rs
- Notes:
  - UI labels use "background process" and avoid separate-persona language.
  - `/cancel-spawn <id>` reports a clear unsupported state because this branch does not add server cancel-spawn websocket support.
