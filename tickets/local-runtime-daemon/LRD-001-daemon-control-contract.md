# LRD-001 - Define daemon lifecycle and control contract

- Status: in_progress
- Priority: P1
- Scope: daemon + desktop
- Parent: `LRD-000`
- Depends on: none
- Owner: unassigned
- PRD: none
- Plan: docs/superpowers/plans/2026-06-26-local-runtime-daemon.md
- Created: 2026-06-26 17:06 MYT
- Updated: 2026-06-27 04:26 MYT
- Completed:
- Started: 2026-06-27 04:26 MYT

## Goal

Define the daemon lifecycle states and the local control contract used by desktop and local tools.

## Deliverables

- Runtime state model: stopped, starting, ready, degraded, locked, stopping, failed.
- Local control operations: status, start, stop, restart, open logs, lock, unlock handoff.
- Local auth/IPC policy for official clients.
- Shared endpoint shape:
  - `GET /api/v1/runtime-daemon/status`
  - `POST /api/v1/runtime-daemon/start`
  - `POST /api/v1/runtime-daemon/stop`
  - `POST /api/v1/runtime-daemon/restart`
  - `POST /api/v1/runtime-daemon/lock`
  - `POST /api/v1/runtime-daemon/unlock`
  - `POST /api/v1/runtime-daemon/logs`
- Error and retry model with deterministic categories and client guidance.

## Acceptance

- Contract is documented before implementation starts
- Desktop and daemon can implement against the same state model
- Contract does not expose passphrases, raw DEKs, provider secrets, or memory payloads

## Activity Log

- 2026-06-26 17:06 MYT - Ticket created.
- 2026-06-27 04:26 MYT - Started implementation. Added canonical TypeScript control-contract package `@anima/daemon-contracts`, desktop control helper scaffold, and architecture contract documentation.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - packages/anima-runtime-daemon-contracts/package.json
  - packages/anima-runtime-daemon-contracts/src/index.ts
  - apps/desktop/package.json
  - apps/desktop/src/lib/daemon-control.ts
  - docs/architecture/system/local-runtime-daemon-control-contract.md
  - docs/architecture/README.md
- Notes:
  - Contract defines a source-of-truth lifecycle/control schema for daemon and desktop integration.
