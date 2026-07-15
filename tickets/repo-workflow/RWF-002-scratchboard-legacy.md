# RWF-002 - Mark scratchboard legacy and add migration checklist

- Status: backlog
- Priority: P2
- Scope: `scratchboard`, `docs`
- Parent: `RWF-000`
- Depends on: `RWF-001`
- Owner: unassigned
- PRD: none
- Plan: docs/superpowers/plans/2026-07-15-repository-organization-project-management.md
- Created: 2026-06-26 17:18 MYT
- Updated: 2026-07-15 17:27 MYT
- Started:
- Completed:

## Goal

Freeze `scratchboard/` for new work and define how old active workstreams migrate into tickets when touched.

## Deliverables

- Add `scratchboard/README.md` marking scratchboard as legacy
- Add a numbered migration checklist from scratchboard workstream to parent/child tickets
- Inventory `_system`, `v1-encrypted-core`, and `v2-memory-recall-reliability`
- Keep old links intact for historical PRDs

## Acceptance

- `scratchboard/README.md` links exactly to `docs/ops/prd-ticket-workflow.md`, `docs/prds/`, `docs/superpowers/plans/`, and `tickets/`
- The README identifies `_system` as legacy coordination metadata and inventories both `v1-encrypted-core` and `v2-memory-recall-reliability` with their migration-candidate context
- The README contains a numbered incremental migration checklist covering discovery, canonical artifacts, parent/child tickets, cross-links, state transfer, cutover, and validation
- `git diff --name-status -- scratchboard` shows only the new `scratchboard/README.md`; no existing legacy scratchboard file is modified or removed

## Activity Log

- 2026-06-26 17:18 MYT - Ticket created.
- 2026-07-15 17:11 MYT - Linked the ticket to the combined repository-organization implementation plan.
- 2026-07-15 17:27 MYT - Made legacy inventory, exact links, migration steps, and preservation acceptance measurable.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - backlog ticket only
