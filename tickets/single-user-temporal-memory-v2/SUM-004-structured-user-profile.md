# SUM-004 - Structured user profile

- Status: backlog
- Priority: P1
- Scope: `apps/server/src/anima_server/models`, `apps/server/src/anima_server/services/agent`, `apps/server/src/anima_server/api`
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

Create an evidence-backed structured user profile that can be rendered compactly into the prompt and inspected or corrected through Open Mind surfaces.

## Deliverables

- Profile storage decision: new `UserProfileField` table or adapted `MemoryClaim` model.
- Typed categories for identity, relationships, work, preferences, goals, values, constraints, emotional patterns, and active projects.
- Consolidation extraction for profile updates.
- Sleep-time profile reconciliation.
- Compact profile prompt block.
- Inspection/correction API.

## Acceptance

- Profile fields are typed, confidence-scored, and evidence-linked.
- Profile prompt rendering is compact and deterministic.
- Correcting a profile field preserves audit history.
- Tests cover extraction parsing, reconciliation, rendering, and API behavior.

## Activity Log

- 2026-06-27 12:40 MYT - Ticket created.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - none
