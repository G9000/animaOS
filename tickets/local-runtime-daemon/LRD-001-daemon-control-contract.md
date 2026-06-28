# LRD-001 - Define daemon lifecycle and control contract

- Status: done
- Priority: P1
- Scope: daemon + desktop
- Parent: `LRD-000`
- Depends on: none
- Owner: unassigned
- PRD: none
- Plan: docs/superpowers/plans/2026-06-26-local-runtime-daemon.md
- Created: 2026-06-26 17:06 MYT
- Updated: 2026-06-27 10:02 MYT
- Started: 2026-06-27 09:15 MYT
- Completed: 2026-06-27 10:02 MYT

## Goal

Define the daemon lifecycle states and local control contract used by desktop and local tools.

## Deliverables

- Runtime state model: stopped, starting, ready, degraded, locked, stopping, failed
- Local control operations: status, start, stop, restart, open logs, lock, unlock handoff, background policy
- Local auth/IPC rule for official clients
- Error and retry model

## Acceptance

- Contract is documented before implementation starts
- Desktop and daemon can implement against the same state model
- Control endpoint and responses include state, runtime identity, lock state, and restart policy
- Contract does not expose passphrases, raw DEKs, provider secrets, or memory payloads

## Activity Log

- 2026-06-26 17:06 MYT - Ticket created.
- 2026-06-27 09:15 MYT - Claimed ticket and implemented shared control contract in the contracts package.
- 2026-06-27 10:02 MYT - Completed contract coverage in daemon and desktop client plan.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - packages/anima-runtime-daemon-contracts/src/index.ts
- Notes:
  - Introduced daemon control states, routes, lock/background enums, and request/response contracts.
