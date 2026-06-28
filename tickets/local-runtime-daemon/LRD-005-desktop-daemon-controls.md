# LRD-005 - Integrate desktop with daemon controls

- Status: done
- Priority: P1
- Scope: desktop
- Parent: `LRD-000`
- Depends on: `LRD-004`, `LRD-006`
- Owner: unassigned
- PRD: none
- Plan: docs/superpowers/plans/2026-06-26-local-runtime-daemon.md
- Created: 2026-06-26 17:06 MYT
- Updated: 2026-06-27 10:02 MYT
- Started: 2026-06-27 09:45 MYT
- Completed: 2026-06-27 10:02 MYT

## Goal

Make the Tauri desktop app control and observe the daemon instead of owning the runtime process lifecycle.

## Deliverables

- Desktop status read path and polling
- Start/stop/restart controls
- Runtime lock/background toggles
- Diagnostics log viewer
- Tray close behavior to keep UI hidden without killing runtime

## Acceptance

- User can see degraded/failed daemon state in UI.
- User can intentionally stop and restart runtime from desktop controls.
- Desktop close behavior does not implicitly stop daemon process when background is enabled.

## Activity Log

- 2026-06-26 17:06 MYT - Ticket created.
- 2026-06-27 09:45 MYT - Implemented `daemon.ts` client and settings route with control actions, including logs and token input.
- 2026-06-27 10:02 MYT - Updated tray behavior and CORS allowlist for daemon API.
- 2026-06-27 10:02 MYT - Ticket marked done.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - apps/desktop/src/App.tsx
  - apps/desktop/src/pages/settings/Settings.tsx
  - apps/desktop/src/pages/settings/DaemonSettings.tsx
  - apps/desktop/src/lib/daemon.ts
  - apps/desktop/src-tauri/src/lib.rs
  - apps/desktop/src-tauri/tauri.conf.json
  - apps/desktop/package.json
  - apps/desktop/src-tauri/Cargo.toml
- Notes:
  - Desktop control page includes a single control channel with background, lock, and logs actions.
