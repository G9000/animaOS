# LRD-001 - Define daemon lifecycle and control contract

- Status: backlog
- Priority: P1
- Scope: daemon + desktop
- Parent: `LRD-000`
- Depends on: none
- Owner: unassigned
- PRD: none
- Plan: docs/superpowers/plans/2026-06-26-local-runtime-daemon.md
- Created: 2026-06-26 17:06 MYT
- Updated: 2026-06-26 17:06 MYT
- Started:
- Completed:

## Goal

Define the daemon lifecycle states and the local control contract used by desktop and local tools.

## Deliverables

- Runtime state model: stopped, starting, ready, degraded, locked, stopping, failed
- Local control operations: status, start, stop, restart, open logs, lock, unlock handoff
- Local auth/IPC rule for official clients
- Error and retry model

## Acceptance

- Contract is documented before implementation starts
- Desktop and daemon can implement against the same state model
- Contract does not expose passphrases, raw DEKs, provider secrets, or memory payloads

## Activity Log

- 2026-06-26 17:06 MYT - Ticket created.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - backlog ticket only
