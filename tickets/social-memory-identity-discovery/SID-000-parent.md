# SID-000 - Social Memory Identity Discovery Parent Tracker

- Status: backlog
- Priority: P1
- Scope: `docs/prds/memory`, `docs/superpowers/plans`, `apps/server/src/anima_server/services/agent`, `apps/server/tests`
- Depends on: agent runtime and harness foundations
- Owner: unassigned
- PRD: docs/prds/memory/social-memory-identity-discovery-v1.md
- Plan: docs/superpowers/plans/2026-07-01-social-memory-identity-discovery.md
- Created: 2026-07-01 15:40 MYT
- Updated: 2026-07-01 15:40 MYT
- Started:
- Completed:

## Goal

Track the deferred social-memory identity discovery initiative so future agent runtime, harness, and F14 work preserve stable person identity, duplicate-name resolution, and audience-safe memory boundaries.

## Child Ticket Order

| Ticket | Title | Status | Depends on |
| --- | --- | --- | --- |
| `SID-001` | Runtime audience context contract | `backlog` | agent runtime foundation |
| `SID-002` | Identity discovery and duplicate-name model | `backlog` | `SID-001` |
| `SID-003` | Audience policy harness probes | `backlog` | `SID-001` |
| `SID-004` | Memory scope metadata design | `backlog` | `SID-002`, `SID-003` |

## Deliverables

- Runtime context contract for speaker, audience, conversation scope, and policy.
- Person identity discovery model with aliases, linked accounts, relationship labels, confidence, and evidence.
- Harness probes for duplicate-name ambiguity and private-memory leakage.
- Memory metadata design for created-by person, subject person, source scope, and allowed audience.

## Acceptance

- The PRD and plan remain linked from this parent tracker.
- Child tickets stay backlog until explicitly scheduled.
- Future runtime and harness work can reference this tracker instead of rediscovering the social-memory boundary.
- Full F14 multi-user/group memory remains deferred unless explicitly reauthorized.

## Completed Tickets

- none

## Activity Log

- 2026-07-01 15:40 MYT - Parent tracker created for social memory identity discovery planning.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - tickets/social-memory-identity-discovery/SID-000-parent.md
- Notes:
  - planning tracker only
