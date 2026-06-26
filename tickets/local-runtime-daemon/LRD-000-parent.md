# LRD-000 - Local Runtime Daemon Parent Tracker

- Status: backlog
- Priority: P1
- Scope: `apps/desktop`, `apps/server`, `packages`, `docs`
- Depends on: none
- Owner: unassigned
- PRD: none
- Plan: docs/superpowers/plans/2026-06-26-local-runtime-daemon.md
- Created: 2026-06-26 17:06 MYT
- Updated: 2026-06-26 17:18 MYT
- Started:
- Completed:

## Goal

Track the local runtime daemon initiative as a separate project from gateway/runtime online delivery.

## Child Ticket Order

| Ticket | Title | Status | Depends on |
| --- | --- | --- | --- |
| `LRD-001` | Define daemon lifecycle and control contract | `backlog` | none |
| `LRD-006` | Define lock/unlock and background job policy | `backlog` | `LRD-001` |
| `LRD-009` | Create local daemon threat model | `backlog` | `LRD-001`, `LRD-006` |
| `LRD-002` | Scaffold Rust daemon binary | `backlog` | `LRD-001` |
| `LRD-003` | Package Python runtime artifact | `backlog` | `LRD-001` |
| `LRD-004` | Add daemon health, logs, and restart policy | `backlog` | `LRD-002`, `LRD-003`, `LRD-009` |
| `LRD-005` | Integrate desktop with daemon controls | `backlog` | `LRD-004`, `LRD-006` |
| `LRD-007` | Add OS autostart/service installation | `backlog` | `LRD-004`, `LRD-005` |
| `LRD-008` | Add release packaging pipeline | `backlog` | `LRD-007` |

## Deliverables

- Rust daemon/supervisor design and implementation path
- Packaged Python runtime lifecycle under daemon control
- Desktop integration for runtime status and user controls
- Installer/release path that avoids terminal commands and Docker for normal users

## Acceptance

- Every child ticket references this parent
- Parent status table reflects child progress
- Completed child tickets are listed below with timestamps

## Completed Tickets

- none

## Activity Log

- 2026-06-26 17:06 MYT - Parent tracker created for local runtime daemon initiative.
- 2026-06-26 17:18 MYT - Added daemon threat model ticket before lifecycle implementation hardening.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - tickets/local-runtime-daemon/LRD-000-parent.md
- Notes:
  - tracker only
