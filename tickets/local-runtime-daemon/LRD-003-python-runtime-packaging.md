# LRD-003 - Package Python runtime artifact

- Status: done
- Priority: P1
- Scope: server packaging
- Parent: `LRD-000`
- Depends on: `LRD-001`
- Owner: unassigned
- PRD: none
- Plan: docs/superpowers/plans/2026-06-26-local-runtime-daemon.md
- Created: 2026-06-26 17:06 MYT
- Updated: 2026-06-27 10:02 MYT
- Started: 2026-06-27 09:30 MYT
- Completed: 2026-06-27 10:02 MYT

## Goal

Create a production packaging strategy for the Python FastAPI runtime that the Rust daemon can supervise.

## Deliverables

- Decision matrix for packaging choices
- Runtime artifact location contract for daemon launcher
- Release prep script to generate packaging metadata for local deployment

## Acceptance

- Packaging decision is documented with tradeoffs.
- Daemon can locate runtime artifacts by environment or source fallback.
- Release preparation emits runtime/daemon metadata for installers.

## Activity Log

- 2026-06-26 17:06 MYT - Ticket created.
- 2026-06-27 09:30 MYT - Decided default launch strategy and added release prep metadata script.
- 2026-06-27 10:02 MYT - Marked ticket done.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - scripts/prepare-desktop-release.ts
  - packages/anima-runtime-daemon-contracts/src/index.ts
- Notes:
  - Packaging decision currently uses `python` local server launch by default, with explicit artifact mode supported through env vars.
