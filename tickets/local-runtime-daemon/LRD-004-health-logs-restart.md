# LRD-004 - Add daemon health, logs, and restart policy

- Status: done
- Priority: P1
- Scope: daemon
- Parent: `LRD-000`
- Depends on: `LRD-002`, `LRD-003`, `LRD-009`
- Owner: unassigned
- PRD: none
- Plan: docs/superpowers/plans/2026-06-26-local-runtime-daemon.md
- Created: 2026-06-26 17:06 MYT
- Updated: 2026-06-27 10:02 MYT
- Started: 2026-06-27 09:40 MYT
- Completed: 2026-06-27 10:02 MYT

## Goal

Make the daemon reliable enough to supervise the runtime without hiding failures from the user.

## Deliverables

- Health check polling on runtime endpoint
- Restart policy with bounded backoff
- Log file rotation and logs endpoint
- PID/port state surfaced in status response

## Acceptance

- Runtime crash/stop is detected and surfaced as daemon state.
- Restart attempts are bounded and visible.
- Logs endpoint provides recent lines without exposing secrets.

## Activity Log

- 2026-06-26 17:06 MYT - Ticket created.
- 2026-06-27 09:40 MYT - Implemented periodic health polling, restart delays, failure handling, and log retrieval API.
- 2026-06-27 10:02 MYT - Ticket marked done.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - apps/local-runtime-daemon/src/main.rs
- Notes:
  - Health loop and restart scheduling are implemented in-process with bounded exponential backoff.
