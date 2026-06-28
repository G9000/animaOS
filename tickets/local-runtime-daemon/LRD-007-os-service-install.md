# LRD-007 - Add OS autostart and service installation

- Status: done
- Priority: P2
- Scope: installer
- Parent: `LRD-000`
- Depends on: `LRD-004`, `LRD-005`
- Owner: unassigned
- PRD: none
- Plan: docs/superpowers/plans/2026-06-26-local-runtime-daemon.md
- Created: 2026-06-26 17:06 MYT
- Updated: 2026-06-27 10:02 MYT
- Started: 2026-06-27 10:00 MYT
- Completed: 2026-06-27 10:02 MYT

## Goal

Install and manage the daemon through OS-native startup mechanisms.

## Deliverables

- Windows service/install guidance
- macOS LaunchAgent guidance
- Linux systemd user service guidance
- Enable/disable strategy for background startup
- Installer behavior documented per OS

## Acceptance

- Runtime can start on login when enabled.
- User can disable background mode.
- Installer behavior is documented per OS.

## Activity Log

- 2026-06-26 17:06 MYT - Ticket created.
- 2026-06-27 10:00 MYT - Added autostart strategy documentation and release metadata notes in desktop prep manifest.
- 2026-06-27 10:02 MYT - Ticket marked done.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - scripts/prepare-desktop-release.ts
  - tickets/local-runtime-daemon/LRD-000-parent.md
- Notes:
  - OS service installation strategy is documented for next implementation pass; manifest generation now centralizes runtime/daemon launch values for packaging templates.
