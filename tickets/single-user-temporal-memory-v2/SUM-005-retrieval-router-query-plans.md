# SUM-005 - Retrieval router and query plans

- Status: backlog
- Priority: P1
- Scope: `apps/server/src/anima_server/services/agent`
- Parent: `SUM-000`
- Depends on: `SUM-003`, `SUM-004`
- Owner: unassigned
- PRD: docs/prds/memory/single-user-temporal-memory-v2.md
- Plan: docs/superpowers/plans/2026-06-27-single-user-temporal-memory-v2.md
- Created: 2026-06-27 12:40 MYT
- Updated: 2026-06-27 12:40 MYT
- Started:
- Completed:

## Goal

Route memory retrieval by user intent instead of using one generic scoring strategy for every turn.

## Deliverables

- `retrieval_router.py` with deterministic route labels and query plan objects.
- Source-specific retrieval composition for profile, graph, memory items, episodes, transcripts, foresight, experiences, and skills.
- Trace output showing chosen route, sources, and scores.
- Prompt/tool guidance updates for `search_long_memory`.
- Regression probes for route correctness.

## Acceptance

- Router fixture suite reaches agreed accuracy on representative user turns.
- Emotional support queries retrieve relationship and emotional context.
- Factual recall queries retrieve evidence-backed exact or episodic records.
- Project continuity queries retrieve active project/profile/episode context.
- Retrieval traces are serializable for UI/debug inspection.

## Activity Log

- 2026-06-27 12:40 MYT - Ticket created.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - none
