# RWF-002 - Mark scratchboard legacy and add migration checklist

- Status: done
- Priority: P2
- Scope: `scratchboard`, `docs`
- Parent: `RWF-000`
- Depends on: `RWF-001`
- Owner: Codex
- PRD: none
- Plan: docs/superpowers/plans/2026-07-15-repository-organization-project-management.md
- Created: 2026-06-26 17:18 MYT
- Updated: 2026-07-15 20:35 MYT
- Started: 2026-07-15 20:23 MYT
- Completed: 2026-07-15 20:35 MYT

## Goal

Freeze `scratchboard/` as the entry point for new initiatives while allowing existing legacy workstreams to continue until a deliberately approved ticket cutover.

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
- 2026-07-15 20:23 MYT - Codex claimed `RWF-002` on branch `codex/repo-organization-project-management` in worktree `.worktrees/repo-organization-project-management`; set the child and parent row to `in_progress` with the dependency on completed `RWF-001` satisfied.
- 2026-07-15 20:25 MYT - Completed `RWF-002` after adding the legacy-only marker, current-state-safe inventory, canonical workflow links, and seven-step incremental migration checklist; preserved every existing scratchboard artifact unchanged and synchronized the parent row and completed history.
- 2026-07-15 20:34 MYT - Reopened `RWF-002` after review found an acceptance-breaking contradiction between the blanket frozen/unchanged wording and the permitted continuation of existing legacy workstreams until approved cutover; preserved the prior completion timestamp `2026-07-15 20:25 MYT`, cleared the current completion, and synchronized the parent row and completed history for repair.
- 2026-07-15 20:35 MYT - Re-completed `RWF-002` after clarifying that no new workstreams start in scratchboard, existing legacy work may continue until an approved cutover, this cleanup leaves current artifacts unchanged without making them forever immutable, and post-cutover progress belongs only in tickets; revalidated policy, links, checklist, preservation, and lifecycle state.

## Validation

- Commands:
  - `rg -n 'no new initiative or workstream starts here|Existing legacy workstreams may continue here until a deliberate, approved cutover|cleanup leaves every existing legacy artifact unchanged|preserves that history and its inbound links|does not make legacy files forever immutable|After a workstream.s cutover, record new progress only in tickets|After the approved cutover, record all new progress only in tickets' scratchboard/README.md`
  - `rg -n '^([1-7])\. ' scratchboard/README.md`
  - `rg -n '\[docs/ops/prd-ticket-workflow\.md\]\(\.\./docs/ops/prd-ticket-workflow\.md\)|\[docs/prds/\]\(\.\./docs/prds/\)|\[docs/superpowers/plans/\]\(\.\./docs/superpowers/plans/\)|\[tickets/\]\(\.\./tickets/\)' scratchboard/README.md`
  - PowerShell `Test-Path` check for all four relative Markdown targets resolved from `scratchboard/`
  - `git diff --name-status 62113b72d3725bea1571901d449a14f2c8cb42ed -- scratchboard`
  - `git diff --exit-code 62113b72d3725bea1571901d449a14f2c8cb42ed -- scratchboard/_system/active-tasks.md scratchboard/v1-encrypted-core scratchboard/v2-memory-recall-reliability`
  - `git diff --check`
- Changed paths:
  - scratchboard/README.md
  - tickets/repo-workflow/RWF-002-scratchboard-legacy.md
  - tickets/repo-workflow/RWF-000-parent.md
- Notes:
  - focused policy searches found the new-workstream prohibition, permitted legacy continuation until approved cutover, current-cleanup preservation boundary, later migration mutability boundary, and tickets-only post-cutover rule
  - all four canonical relative link targets resolved
  - all seven numbered migration steps remain present and ordered
  - scratchboard name-status relative to the initiative base contained only `A scratchboard/README.md`; the protected legacy paths had no diff
  - residual risks or follow-ups: current legacy workstream state still requires deliberate human and current-artifact confirmation before migration, as documented in the README
