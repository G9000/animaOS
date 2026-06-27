# SUM-001 - Baseline memory truth audit and eval probes

- Status: backlog
- Priority: P1
- Scope: `apps/server`, `docs/architecture/memory`, `docs/prds/memory`
- Parent: `SUM-000`
- Depends on: none
- Owner: unassigned
- PRD: docs/prds/memory/single-user-temporal-memory-v2.md
- Plan: docs/superpowers/plans/2026-06-27-single-user-temporal-memory-v2.md
- Created: 2026-06-27 12:40 MYT
- Updated: 2026-06-27 12:40 MYT
- Started:
- Completed:

## Goal

Establish the true live state of the memory system and add baseline recall probes before changing architecture.

## Deliverables

- Code/doc drift audit for predict-calibrate, retrieval routing, evidence, KG export/import, heat scoring, and sleep tasks.
- Focused memory eval probes for factual, emotional, profile, temporal, and pattern recall.
- Small fixes for high-impact known gaps when still present.
- Updated architecture notes where docs are stale.

## Acceptance

- Audit lists live code paths with file references.
- Baseline tests or eval probes can run deterministically without real provider calls.
- Any tiny fixes include focused tests.
- Follow-up tickets are updated if audit changes the plan.

## Activity Log

- 2026-06-27 12:40 MYT - Ticket created.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - none
