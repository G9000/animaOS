# MPB-003 - Temporal claims and graph facades

- Status: backlog
- Priority: P1
- Scope: `apps/server/src/anima_server/services/memory`, `apps/server/src/anima_server/services/agent/claims.py`, `apps/server/src/anima_server/services/agent/knowledge_graph.py`
- Parent: `MPB-000`
- Depends on: `MPB-002`
- Owner: unassigned
- PRD: docs/prds/memory/single-user-temporal-memory-v2.md
- Plan: docs/superpowers/plans/2026-07-03-memory-package-boundary-hardening.md
- Created: 2026-07-03 17:12 MYT
- Updated: 2026-07-03 17:12 MYT
- Started:
- Completed:

## Goal

Expose temporal claim and temporal graph helpers from `services.memory` while preserving existing `services.agent` implementation modules.

## Deliverables

- `services.memory.temporal_claims` facade.
- `services.memory.temporal_graph` facade.
- Tests for current/history/valid-at helper behavior.
- Compatibility notes for existing agent imports.

## Acceptance

- Callers can use `services.memory` for current fact, fact history, current relationship, relationship history, and valid-at lookups.
- Existing claims and KG tests still pass.
- Facades delegate rather than duplicate persistence logic.

## Activity Log

- 2026-07-03 17:12 MYT - Ticket created.

## Validation

- Commands:
  - `not run yet`
- Changed paths:
  - none
- Notes:
  - none
