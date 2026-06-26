# ACT-008 - Add ANIMA spawn and thread visibility

- Status: backlog
- Priority: P1
- Scope: `apps/animus`, `apps/server`
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

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - backlog ticket only
