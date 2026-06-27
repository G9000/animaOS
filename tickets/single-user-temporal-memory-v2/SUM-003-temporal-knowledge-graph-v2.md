# SUM-003 - Temporal knowledge graph v2

- Status: backlog
- Priority: P1
- Scope: `apps/server/src/anima_server/models`, `apps/server/src/anima_server/services/agent`
- Parent: `SUM-000`
- Depends on: `SUM-002`
- Owner: unassigned
- PRD: docs/prds/memory/single-user-temporal-memory-v2.md
- Plan: docs/superpowers/plans/2026-06-27-single-user-temporal-memory-v2.md
- Created: 2026-06-27 12:40 MYT
- Updated: 2026-06-27 12:40 MYT
- Started:
- Completed:

## Goal

Upgrade the existing knowledge graph into a temporal, evidence-backed graph suitable for evolving relationships and preferences.

## Deliverables

- Temporal relation fields and migration.
- Evidence linkage for graph relations.
- Alias and embedding-based entity deduplication.
- Evolution chain semantics for soft changes.
- Graph retrieval helpers for relationship history and latest belief resolution.
- Export/import coverage for KG state.

## Acceptance

- Relation lifecycle tests cover observed time, valid time, supersession, and evolution.
- Graph retrieval can return both current belief and supporting history.
- KG export/import preserves entities, relations, temporal fields, and evidence references.
- Existing graph behavior remains compatible.

## Activity Log

- 2026-06-27 12:40 MYT - Ticket created.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - none
