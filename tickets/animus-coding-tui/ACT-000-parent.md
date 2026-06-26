# ACT-000 - Animus Coding TUI Parent Tracker

- Status: backlog
- Priority: P1
- Scope: `apps/animus`, `apps/server`, `docs/prds/animus`
- Depends on: none
- Owner: unassigned
- PRD: docs/prds/animus/coding-tui-v1.md
- Plan: docs/superpowers/plans/2026-06-26-animus-coding-tui.md
- Created: 2026-06-26 18:51 MYT
- Updated: 2026-06-26 18:51 MYT
- Started:
- Completed:

## Goal

Track the short-port initiative that upgrades Animus into a production-grade ANIMA-first coding terminal while preserving ANIMA's server/runtime/memory model.

## Child Ticket Order

| Ticket | Title | Status | Depends on |
| --- | --- | --- | --- |
| `ACT-001` | Fix protocol and run lifecycle | `backlog` | none |
| `ACT-002` | Define source adaptation boundary and license notes | `backlog` | none |
| `ACT-003` | Split Animus app coordinator and render view | `backlog` | `ACT-001`, `ACT-002` |
| `ACT-004` | Add rich transcript and tool rendering | `backlog` | `ACT-003` |
| `ACT-005` | Add rich input, slash commands, and status line | `backlog` | `ACT-003` |
| `ACT-006` | Add inline approval flow | `backlog` | `ACT-001`, `ACT-003` |
| `ACT-007` | Add ANIMA-native spawn visibility and commands | `backlog` | `ACT-001`, `ACT-003` |
| `ACT-008` | Run smoke tests, docs, and tracker cleanup | `backlog` | `ACT-004`, `ACT-005`, `ACT-006`, `ACT-007` |

## Deliverables

- Animus TUI structure and terminal UX.
- Fixed ANIMA WebSocket protocol lifecycle for runs, cancel, and approvals.
- Rich transcript, tool rendering, input, command autocomplete, and status display.
- ANIMA-native background spawn visibility and commands.
- License/source hygiene for adapted upstream UI pieces.

## Acceptance

- Every child ticket references this parent.
- Parent status table reflects child progress.
- Completed child tickets are listed below with timestamps.
- Initiative can be picked up from the PRD, plan, and ticket folder without prior chat context.

## Completed Tickets

- none

## Activity Log

- 2026-06-26 18:51 MYT - Parent tracker created for Animus production coding TUI short port.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - tickets/animus-coding-tui/ACT-000-parent.md
- Notes:
  - tracker only
