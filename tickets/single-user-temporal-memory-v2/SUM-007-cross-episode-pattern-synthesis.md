# SUM-007 - Cross-episode pattern synthesis

- Status: backlog
- Priority: P2
- Scope: `apps/server/src/anima_server/services/agent`
- Parent: `SUM-000`
- Depends on: `SUM-005`, `SUM-006`
- Owner: unassigned
- PRD: docs/prds/memory/single-user-temporal-memory-v2.md
- Plan: docs/superpowers/plans/2026-06-27-single-user-temporal-memory-v2.md
- Created: 2026-06-27 12:40 MYT
- Updated: 2026-06-27 12:40 MYT
- Started:
- Completed:

## Goal

Add a sleep-time synthesis pass that discovers recurring patterns across episodes and turns repeated observations into compact, evidence-backed memory.

## Deliverables

- `pattern_synthesis.py` sleep-time task.
- Episode sampling by time window, topic, and salience.
- Pattern extraction prompt and strict JSON parser.
- Storage strategy for patterns as profile fields, graph relations, or memory items.
- Prompt block rendering for high-confidence active patterns only.

## Acceptance

- Single mentions do not create durable patterns.
- Repeated evidence across episodes creates a pattern.
- Patterns cite source episode/evidence IDs.
- Prompt rendering stays compact.

## Activity Log

- 2026-06-27 12:40 MYT - Ticket created.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - none
