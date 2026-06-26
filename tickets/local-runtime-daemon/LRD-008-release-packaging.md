# LRD-008 - Add release packaging pipeline

- Status: backlog
- Priority: P2
- Scope: release
- Parent: `LRD-000`
- Depends on: `LRD-007`
- Owner: unassigned
- PRD: none
- Plan: docs/superpowers/plans/2026-06-26-local-runtime-daemon.md
- Created: 2026-06-26 17:06 MYT
- Updated: 2026-06-26 17:06 MYT
- Started:
- Completed:

## Goal

Package desktop, daemon, and runtime artifacts into an installer that normal users can install and run without developer commands.

## Deliverables

- Release artifact layout
- Installer integration
- Config and migration notes
- Upgrade/rollback behavior

## Acceptance

- Packaged app starts without `bun dev`
- Daemon and runtime artifacts are installed together
- Update flow preserves `.anima` data and runtime DB state

## Activity Log

- 2026-06-26 17:06 MYT - Ticket created.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - backlog ticket only
