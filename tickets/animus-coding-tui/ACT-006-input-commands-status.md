# ACT-006 - Add input, slash commands, and status line

- Status: done
- Priority: P1
- Scope: `apps/animus`
- Parent: `ACT-000`
- Depends on: `ACT-004`
- Owner: codex
- PRD: docs/prds/animus/rust-coding-tui-v1.md
- Plan: docs/superpowers/plans/2026-06-27-animus-rust-coding-tui.md
- Created: 2026-06-26 18:51 MYT
- Updated: 2026-06-27 04:59 MYT
- Started: 2026-06-27 04:55 MYT
- Completed: 2026-06-27 04:59 MYT

## Goal

Make the Rust terminal interactive enough for real coding sessions.

## Deliverables

- Input buffer with cursor movement, history, and multiline handling.
- Enum/registry-driven slash commands with presentation order, descriptions, inline-arg support, availability rules, and autocomplete.
- Commands for help, clear, cancel, reconnect, permissions, status, diff, spawns, cancel-spawn, and quit.
- Composable status line with connection, cwd/project, permission mode, approval mode, current run/thread, spawn count, and task progress.

## Acceptance

- Common commands are discoverable and route correctly.
- Status line reflects session state without crowding transcript output.
- Command parsing/routing tests cover normal and busy states.

## Activity Log

- 2026-06-26 18:51 MYT - Ticket created.
- 2026-06-27 03:00 MYT - Revised for Rust input/commands/status.
- 2026-06-27 04:55 MYT - Started input buffer, slash command, command routing, and status-line tests.
- 2026-06-27 04:59 MYT - Completed input buffer, slash command registry/autocomplete, command routing, and status line.

## Validation

- Commands:
  - `cargo test -p animus` - red first: missing input/command/status types and functions
  - `cargo test -p animus` - failed once because status line truncated run id at 80 columns; reordered status fields
  - `cargo test -p animus input` - passed: 4 passed, 33 filtered out
  - `cargo test -p animus commands` - passed: 3 passed, 34 filtered out
  - `cargo test -p animus app` - passed: 6 passed, 31 filtered out
  - `cargo test -p animus` - passed: 37 passed
  - `cargo check -p animus` - passed
- Changed paths:
  - apps/animus/src/input.rs
  - apps/animus/src/commands.rs
  - apps/animus/src/app.rs
  - apps/animus/src/tui.rs
  - apps/animus/src/main.rs
- Notes:
  - Slash command names implemented: `/help`, `/clear`, `/cancel`, `/reconnect`, `/permissions`, `/status`, `/diff`, `/spawns`, `/cancel-spawn`, `/quit`.
  - `/cancel` routes to a command effect containing the active run id; the live websocket send remains for later integration.
