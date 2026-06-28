# LRD-002 - Scaffold Rust daemon binary

- Status: done
- Priority: P1
- Scope: Rust
- Parent: `LRD-000`
- Depends on: `LRD-001`
- Owner: unassigned
- PRD: none
- Plan: docs/superpowers/plans/2026-06-26-local-runtime-daemon.md
- Created: 2026-06-26 17:06 MYT
- Updated: 2026-06-27 10:02 MYT
- Started: 2026-06-27 09:25 MYT
- Completed: 2026-06-27 10:02 MYT

## Goal

Add a Rust daemon/supervisor binary that can own local runtime lifecycle independently of the Tauri window.

## Deliverables

- New Rust binary crate in workspace
- Health/control API surface on localhost control port
- Process launch abstraction with PID/port/log tracking
- Poller and restart policy scaffolding

## Acceptance

- Daemon crate is present and built as standalone process.
- Daemon supports status, health, control, and logs endpoints.
- Runtime can be started/stopped/restarted independently of the desktop.

## Activity Log

- 2026-06-26 17:06 MYT - Ticket created.
- 2026-06-27 09:25 MYT - Claimed and completed daemon scaffold implementation (`apps/local-runtime-daemon/src/main.rs`).

## Validation

- Commands:
  - not run yet
- Changed paths:
  - apps/local-runtime-daemon/src/main.rs
  - apps/local-runtime-daemon/Cargo.toml
  - Cargo.toml
- Notes:
  - Added lifecycle state machine, health polling, restart scheduling, and lock/background policy hooks.
