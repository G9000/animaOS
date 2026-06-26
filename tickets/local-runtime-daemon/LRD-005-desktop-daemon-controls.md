# LRD-005 - Integrate desktop with daemon controls

- Status: backlog
- Priority: P1
- Scope: desktop
- Parent: `LRD-000`
- Depends on: `LRD-004`, `LRD-006`
- Owner: unassigned
- PRD: none
- Plan: docs/superpowers/plans/2026-06-26-local-runtime-daemon.md
- Created: 2026-06-26 17:06 MYT
- Updated: 2026-06-26 17:06 MYT
- Started:
- Completed:

## Goal

Make the Tauri desktop app control and observe the daemon instead of owning the whole runtime process lifecycle.

## Deliverables

- Desktop status read path
- Start/stop/restart actions
- Tray behavior for open, hide, quit UI, and stop runtime
- Diagnostics/log access

## Acceptance

- Closing the desktop window does not kill runtime when background mode is enabled
- User can intentionally stop the runtime
- UI shows degraded/failed daemon state

## Activity Log

- 2026-06-26 17:06 MYT - Ticket created.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - backlog ticket only
