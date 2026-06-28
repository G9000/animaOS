# LRD-006 - Define lock/unlock and background job policy

- Status: done
- Priority: P1
- Scope: daemon + server
- Parent: `LRD-000`
- Depends on: `LRD-001`
- Owner: unassigned
- PRD: none
- Plan: docs/superpowers/plans/2026-06-26-local-runtime-daemon.md
- Created: 2026-06-26 17:06 MYT
- Updated: 2026-06-27 10:02 MYT
- Started: 2026-06-27 09:50 MYT
- Completed: 2026-06-27 10:02 MYT

## Goal

Define what the daemon may keep running after the UI closes and how unlocked core state is handled.

## Deliverables

- Lock policy fields (`lock`, `lockOnClose`, `lockOnIdle`) in status responses
- Runtime lock/unlock control operation
- Background mode control operation for crash-retry behavior
- Tray close behavior that hides UI and keeps runtime policy untouched by default

## Acceptance

- Closing UI does not silently change unlock state.
- Lock and background mode are surfaced and controllable from UI.
- Policy is explicit in control/status contract and implementation.

## Activity Log

- 2026-06-26 17:06 MYT - Ticket created.
- 2026-06-27 09:50 MYT - Added lock fields/policies in daemon runtime state and desktop lock/background controls.
- 2026-06-27 10:02 MYT - Ticket marked done.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - apps/local-runtime-daemon/src/main.rs
  - apps/desktop/src/pages/settings/DaemonSettings.tsx
  - apps/desktop/src/pages/settings/Settings.tsx
  - apps/desktop/src-tauri/src/lib.rs
- Notes:
  - Lock/background controls are persisted in daemon process state and exposed through status responses.
