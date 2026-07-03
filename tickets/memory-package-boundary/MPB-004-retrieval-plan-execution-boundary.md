# MPB-004 - Retrieval plan execution boundary

- Status: backlog
- Priority: P1
- Scope: `apps/server/src/anima_server/services/memory/retrieval_router.py`, `apps/server/src/anima_server/services/memory/retrieval.py`, `apps/server/src/anima_server/services/agent/tools.py`
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

Turn the SUM-005 query plan into a clean execution boundary that records lane-level route reasons while still using the existing wide-evidence retrieval path.

## Deliverables

- Typed query-plan execution adapter.
- Route trace metadata that distinguishes route weights from retrieval scores.
- `search_long_memory` compatibility for explicit modes and auto mode.
- Focused tests for plan-to-mode mapping and trace serialization.

## Acceptance

- Existing `search_long_memory(query, mode)` behavior remains backward compatible.
- Auto mode records the selected intent, lanes, mode mapping, and evidence IDs when available.
- No per-lane retriever execution is claimed unless implemented and tested.

## Activity Log

- 2026-07-03 17:12 MYT - Ticket created.

## Validation

- Commands:
  - `not run yet`
- Changed paths:
  - none
- Notes:
  - none
