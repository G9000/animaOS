# LRD-009 - Create local daemon threat model

- Status: backlog
- Priority: P1
- Scope: daemon + desktop
- Parent: `LRD-000`
- Depends on: `LRD-001`, `LRD-006`
- Owner: unassigned
- PRD: none
- Plan: docs/superpowers/plans/2026-06-26-local-runtime-daemon.md
- Created: 2026-06-26 17:18 MYT
- Updated: 2026-06-26 17:18 MYT
- Started:
- Completed:

## Goal

Create a threat model for the local daemon before implementing restart policy, desktop controls, and OS autostart.

## Deliverables

- Trust boundary map for desktop UI, daemon, Python runtime, local tools, OS autostart, `.anima`, logs, and runtime DB
- Asset list for passphrases, unlock state, raw DEKs, sidecar nonce, local control credentials, provider secrets, logs, and memory payloads
- Attacker capability list for malicious localhost process, local malware, stolen machine, unauthorized local user, tampered daemon binary, and log scraping
- Policy decisions for lock-on-close, lock-on-idle, background jobs, local IPC, restart-after-crash, and autostart
- Accepted-risk list with owner and revisit date

## Acceptance

- Threat model references the daemon lifecycle and lock/background policy
- Local control channel has an explicit authentication or OS-permission decision
- Logging rules explicitly forbid passphrases, raw DEKs, provider secrets, and memory payloads

## Activity Log

- 2026-06-26 17:18 MYT - Ticket created.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - backlog ticket only
