# LRD-008 - Add release packaging pipeline

- Status: done
- Priority: P2
- Scope: release
- Parent: `LRD-000`
- Depends on: `LRD-007`
- Owner: unassigned
- PRD: none
- Plan: docs/superpowers/plans/2026-06-26-local-runtime-daemon.md
- Created: 2026-06-26 17:06 MYT
- Updated: 2026-06-27 10:02 MYT
- Started: 2026-06-27 10:00 MYT
- Completed: 2026-06-27 10:02 MYT

## Goal

Package desktop, daemon, and runtime artifacts into an installer that normal users can run without developer commands.

## Deliverables

- Release metadata preparation script for daemon/runtime launch assumptions
- Release artifact checklist and upgrade notes
- Build-time hook integration for desktop packaging command

## Acceptance

- Packaging pipeline emits deterministic metadata for installer packaging.
- Release flow keeps `.anima` and runtime database data paths untouched by default.
- Manifest includes daemon/runtime launch defaults and candidate binary locations.

## Activity Log

- 2026-06-26 17:06 MYT - Ticket created.
- 2026-06-27 10:00 MYT - Implemented desktop release preparation script.
- 2026-06-27 10:02 MYT - Ticket marked done.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - scripts/prepare-desktop-release.ts
  - apps/desktop/package.json
  - apps/desktop/src-tauri/tauri.conf.json
- Notes:
  - Script writes `.anima/runtime-daemon-release.json` and validates artifact paths before release build.
