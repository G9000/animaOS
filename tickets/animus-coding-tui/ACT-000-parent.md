# ACT-000 - Animus Rust Coding TUI Parent Tracker

- Status: backlog
- Priority: P1
- Scope: `apps/animus`, `apps/server`, `docs/prds/animus`
- Depends on: none
- Owner: unassigned
- PRD: docs/prds/animus/rust-coding-tui-v1.md
- Plan: docs/superpowers/plans/2026-06-27-animus-rust-coding-tui.md
- Created: 2026-06-26 18:51 MYT
- Updated: 2026-06-27 03:00 MYT
- Started:
- Completed:

## Goal

Track the rewrite that replaces the current Bun/Ink Animus CLI with a Rust-native ANIMA-first coding terminal.

## Child Ticket Order

| Ticket | Title | Status | Depends on |
| --- | --- | --- | --- |
| `ACT-001` | Fix server protocol and run lifecycle | `backlog` | none |
| `ACT-002` | Scaffold Rust package and license notes | `backlog` | none |
| `ACT-003` | Build ANIMA WebSocket client | `backlog` | `ACT-001`, `ACT-002` |
| `ACT-004` | Add terminal event loop and transcript | `backlog` | `ACT-003` |
| `ACT-005` | Add local tools and permissions | `backlog` | `ACT-003` |
| `ACT-006` | Add input, slash commands, and status line | `backlog` | `ACT-004` |
| `ACT-007` | Add inline approvals | `backlog` | `ACT-001`, `ACT-004`, `ACT-005` |
| `ACT-008` | Add ANIMA spawn/thread visibility | `backlog` | `ACT-004` |
| `ACT-009` | Replace Bun wiring, smoke tests, and docs | `backlog` | `ACT-005`, `ACT-006`, `ACT-007`, `ACT-008` |

## Deliverables

- Rust-native Animus TUI replacing the Bun/Ink implementation.
- Explicit protocol-first, event-driven, history-cell-based Rust TUI architecture.
- Fixed ANIMA WebSocket protocol lifecycle for runs, cancel, and approvals.
- Rich transcript, tool rendering, input, command autocomplete, and status display.
- Local Rust action tools and permission checks.
- ANIMA-native background spawn/thread visibility and commands.
- License/source hygiene for adapted upstream UI/protocol ideas.

## Acceptance

- Every child ticket references this parent.
- Parent status table reflects child progress.
- Completed child tickets are listed below with timestamps.
- Initiative can be picked up from the PRD, plan, and ticket folder without prior chat context.
- The v1 replacement does not keep the Bun/Ink CLI as a supported fallback.

## Completed Tickets

- none

## Activity Log

- 2026-06-26 18:51 MYT - Parent tracker created for Animus coding TUI work.
- 2026-06-27 03:00 MYT - Revised initiative scope to a full Rust-native rewrite replacing Bun/Ink.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - tickets/animus-coding-tui/ACT-000-parent.md
- Notes:
  - tracker only
