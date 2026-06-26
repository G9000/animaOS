# ACT-005 - Add rich input, slash commands, and status line

- Status: backlog
- Priority: P1
- Scope: `apps/animus/src/ui`
- Parent: `ACT-000`
- Depends on: `ACT-003`
- Owner: unassigned
- PRD: docs/prds/animus/coding-tui-v1.md
- Plan: docs/superpowers/plans/2026-06-26-animus-coding-tui.md
- Created: 2026-06-26 18:51 MYT
- Updated: 2026-06-26 18:51 MYT
- Started:
- Completed:

## Goal

Make Animus input and commands feel like a serious coding TUI, with discoverable slash commands, history, busy-state behavior, and useful status.

## Deliverables

- Rich input component with history and command autocomplete.
- Command registry and routing rules.
- Built-in commands for help, clear, cancel, reconnect, plan, spawns, cancel-spawn, and quit.
- Status line showing connection, mode, cwd, current run, and spawn count.

## Acceptance

- Slash commands are discoverable from the input.
- Commands that should work while busy can bypass the normal prompt queue.
- Status line communicates enough session state without crowding the transcript.
- Command routing tests cover normal and busy states.

## Activity Log

- 2026-06-26 18:51 MYT - Ticket created.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - backlog ticket only

