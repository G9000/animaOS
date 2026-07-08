# MAG-002 - Backend detail and evidence reads

- Status: backlog
- Priority: P1
- Scope: `apps/server`
- Parent: `MAG-000`
- Depends on: `MAG-001`
- Owner: unassigned
- PRD: none
- Spec: docs/superpowers/specs/2026-07-05-memory-atlas-graph-design.md
- Plan: docs/superpowers/plans/2026-07-05-memory-atlas-graph.md
- Created: 2026-07-05 13:07 MYT
- Updated: 2026-07-05 13:07 MYT
- Started:
- Completed:

## Goal

Add focused read paths for selected relation details and bounded source evidence snippets without bloating the initial atlas canvas payload.

## Deliverables

- Relation detail route in `apps/server/src/anima_server/api/routes/graph.py`.
- Bounded evidence snippet loading for selected relations.
- Tests for relation metadata, source/target entities, evidence bounds, and user scoping.

## Acceptance

- Relation detail reads require unlock and user ownership checks.
- Relation detail includes source/target entities, relation type, mentions, confidence, status, and temporal metadata.
- Evidence snippets load only for the selected item and are bounded.
- Canvas payload remains evidence-light.
- Focused backend tests pass.

## Activity Log

- 2026-07-05 13:07 MYT - Ticket created.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - none
