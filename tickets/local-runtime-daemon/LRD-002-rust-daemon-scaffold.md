# LRD-002 - Scaffold Rust daemon binary

- Status: backlog
- Priority: P1
- Scope: Rust
- Parent: `LRD-000`
- Depends on: `LRD-001`
- Owner: unassigned
- PRD: none
- Plan: docs/superpowers/plans/2026-06-26-local-runtime-daemon.md
- Created: 2026-06-26 17:06 MYT
- Updated: 2026-06-26 17:06 MYT
- Started:
- Completed:

## Goal

Add a Rust daemon/supervisor binary that can own local runtime lifecycle independently of the Tauri window.

## Deliverables

- New Rust binary or crate for daemon
- Basic process lifecycle shell
- Local control endpoint or IPC placeholder
- Config path and log path discovery

## Acceptance

- Daemon builds separately from the Tauri UI
- Daemon can run without opening the desktop window
- Daemon exposes a minimal health/status response

## Activity Log

- 2026-06-26 17:06 MYT - Ticket created.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - backlog ticket only
