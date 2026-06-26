# LRD-006 - Define lock/unlock and background job policy

- Status: backlog
- Priority: P1
- Scope: daemon + server
- Parent: `LRD-000`
- Depends on: `LRD-001`
- Owner: unassigned
- PRD: none
- Plan: docs/superpowers/plans/2026-06-26-local-runtime-daemon.md
- Created: 2026-06-26 17:06 MYT
- Updated: 2026-06-26 17:06 MYT
- Started:
- Completed:

## Goal

Define what the daemon may keep running after the UI closes and how unlocked core state is handled.

## Deliverables

- Background mode policy
- Lock-on-idle and lock-on-quit rules
- Background job behavior when core is locked
- User-facing settings for runtime persistence

## Acceptance

- Closing UI does not silently change unlock state
- Memory/sleep jobs pause or degrade safely when locked
- Policy is explicit before background daemon behavior ships

## Activity Log

- 2026-06-26 17:06 MYT - Ticket created.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - backlog ticket only
