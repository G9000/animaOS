# GWR-000 - Gateway Runtime Online Parent Tracker

- Status: in_progress
- Priority: P1
- Scope: `apps/server`, `apps/desktop`, `apps/anima-mod`, `apps/site`, `docs`
- Depends on: none
- Owner: codex-agent
- PRD: none
- Plan: docs/superpowers/plans/2026-06-26-gateway-runtime-online-delivery.md
- Created: 2026-06-26 16:38 MYT
- Updated: 2026-06-27 04:02 MYT
- Started: 2026-06-27 04:02 MYT
- Completed:

## Goal

Track the full gateway-runtime online initiative as one parent ticket while child tickets capture discrete implementation steps.

## Child Ticket Order

| Ticket | Title | Status | Depends on |
| --- | --- | --- | --- |
| `GWR-001` | Extract runtime auth primitives into dedicated package | `in_progress` | none |
| `GWR-002` | Add gateway-style request context contract | `backlog` | `GWR-001` |
| `GWR-010` | Add internal gateway-to-runtime contract | `backlog` | `GWR-002` |
| `GWR-003` | Add auth and rate middleware in API layer | `backlog` | `GWR-002`, `GWR-010` |
| `GWR-008` | Add end-to-end trace IDs and audit logs | `backlog` | `GWR-003` |
| `GWR-009` | Add compatibility auth bridge for desktop | `backlog` | `GWR-003` |
| `GWR-004` | Add device enrollment and revocation API | `backlog` | `GWR-003` |
| `GWR-005` | Centralize trust policy and nonce store | `backlog` | `GWR-003` |
| `GWR-006` | Standardize webhook and third-party ingress contract | `backlog` | `GWR-003`, `GWR-010` |
| `GWR-007` | Add outbound adapter abstraction | `backlog` | `GWR-006` |
| `GWR-011` | Add multi-device onboarding flow docs | `backlog` | `GWR-004`, `GWR-005` |
| `GWR-012` | Add web and websocket delivery baseline | `backlog` | `GWR-009`, `GWR-010` |
| `GWR-014` | Create gateway/runtime threat model | `backlog` | `GWR-002`, `GWR-010` |
| `GWR-013` | Security hardening pass and acceptance | `backlog` | `GWR-005`, `GWR-008`, `GWR-010`, `GWR-014` |

## Deliverables

- Stable gateway-runtime boundary with typed request context
- Ticket-level progress tracking for all boundary work
- Parent-level visibility into next work, completed work, and blockers

## Acceptance

- Every child ticket references this parent
- Parent status table reflects child progress
- Completed child tickets are listed below with timestamps

## Completed Tickets

- none

## Activity Log

- 2026-06-26 16:38 MYT - Parent tracker created for the gateway-runtime online initiative.
- 2026-06-26 16:57 MYT - Reordered child tickets so runtime contract work precedes gateway middleware, device trust, and external channels.
- 2026-06-26 17:18 MYT - Added threat model ticket before final security hardening.
- 2026-06-27 04:02 MYT - Started execution planning and opened `GWR-001` for implementation in this branch.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - tickets/gateway-runtime-online/GWR-000-parent.md
- Notes:
  - tracker only
