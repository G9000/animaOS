# LRD-004 - Add daemon health, logs, and restart policy

- Status: backlog
- Priority: P1
- Scope: daemon
- Parent: `LRD-000`
- Depends on: `LRD-002`, `LRD-003`, `LRD-009`
- Owner: unassigned
- PRD: none
- Plan: docs/superpowers/plans/2026-06-26-local-runtime-daemon.md
- Created: 2026-06-26 17:06 MYT
- Updated: 2026-06-26 17:18 MYT
- Started:
- Completed:

## Goal

Make the daemon reliable enough to supervise the runtime without hiding failures from the user.

## Deliverables

- Health check polling
- Restart with bounded backoff
- Log file paths and rotation policy
- PID/port tracking
- Failure state surfaced to desktop

## Acceptance

- Runtime crash is detected
- Restart attempts are bounded and visible
- Logs do not include secrets or memory payloads

## Activity Log

- 2026-06-26 17:06 MYT - Ticket created.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - backlog ticket only
