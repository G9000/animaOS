# ACT-006 - Add input, slash commands, and status line

- Status: backlog
- Priority: P1
- Scope: `apps/animus`
- Parent: `ACT-000`
- Depends on: `ACT-004`
- Owner: unassigned
- PRD: docs/prds/animus/rust-coding-tui-v1.md
- Plan: docs/superpowers/plans/2026-06-27-animus-rust-coding-tui.md
- Created: 2026-06-26 18:51 MYT
- Updated: 2026-06-27 03:00 MYT
- Started:
- Completed:

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

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - backlog ticket only
