# LRD-009 - Create local daemon threat model

- Status: done
- Priority: P1
- Scope: daemon + desktop
- Parent: `LRD-000`
- Depends on: `LRD-001`, `LRD-006`
- Owner: unassigned
- PRD: none
- Plan: docs/superpowers/plans/2026-06-26-local-runtime-daemon.md
- Created: 2026-06-26 17:18 MYT
- Updated: 2026-06-27 10:02 MYT
- Started: 2026-06-27 09:55 MYT
- Completed: 2026-06-27 10:02 MYT

## Goal

Create a threat model for the local daemon before implementing restart policy, desktop controls, and OS autostart.

## Deliverables

- Trust boundary map for desktop UI, daemon, Python runtime, local tools, OS autostart, `.anima`, logs, and runtime DB
- Asset list and allowed exposures
- Authentication and policy decisions for local control
- Accepted risk register for background and local IPC behavior

## Acceptance

- Threat model references the daemon lifecycle and lock/background policy
- Local control channel has explicit authentication or OS-permission decision
- Logging rules explicitly forbid passphrases, raw DEKs, provider secrets, and memory payloads

## Activity Log

- 2026-06-26 17:18 MYT - Ticket created.
- 2026-06-27 09:55 MYT - Captured local threat model and controls for daemon control/auth/log handling in ticket and contract.
- 2026-06-27 10:02 MYT - Ticket marked done.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - packages/anima-runtime-daemon-contracts/src/index.ts
  - apps/local-runtime-daemon/src/main.rs
  - apps/desktop/src/pages/settings/DaemonSettings.tsx
  - tickets/local-runtime-daemon/LRD-001-daemon-control-contract.md
  - tickets/local-runtime-daemon/LRD-006-lock-background-policy.md
- Notes:
  - Control token is optional and only sent in headers when configured; secrets are not logged.
