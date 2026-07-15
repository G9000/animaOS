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
- Updated: 2026-07-15 20:25 MYT
- Started: 2026-07-15 20:23 MYT
- Completed: 2026-07-15 20:25 MYT

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
- 2026-07-15 20:23 MYT - Codex claimed `RWF-002` on branch `codex/repo-organization-project-management` in worktree `.worktrees/repo-organization-project-management`; set the child and parent row to `in_progress` with the dependency on completed `RWF-001` satisfied.
- 2026-07-15 20:25 MYT - Completed `RWF-002` after adding the legacy-only marker, current-state-safe inventory, canonical workflow links, and seven-step incremental migration checklist; preserved every existing scratchboard artifact unchanged and synchronized the parent row and completed history.

## Validation

- Commands:
  - `rg -n 'legacy|frozen|PRD|tickets|v1-encrypted-core|v2-memory-recall-reliability|Migration checklist|bulk-move' scratchboard/README.md`
  - `rg -n '^([1-7])\. ' scratchboard/README.md`
  - `rg -n '\[docs/ops/prd-ticket-workflow\.md\]\(\.\./docs/ops/prd-ticket-workflow\.md\)|\[docs/prds/\]\(\.\./docs/prds/\)|\[docs/superpowers/plans/\]\(\.\./docs/superpowers/plans/\)|\[tickets/\]\(\.\./tickets/\)' scratchboard/README.md`
  - PowerShell `Test-Path` check for all four relative Markdown targets resolved from `scratchboard/`
  - `git diff --name-status -- scratchboard`
  - `git diff --exit-code HEAD -- scratchboard/_system/active-tasks.md scratchboard/v1-encrypted-core scratchboard/v2-memory-recall-reliability`
  - `git diff --check`
- Changed paths:
  - scratchboard/README.md
  - tickets/repo-workflow/RWF-002-scratchboard-legacy.md
  - tickets/repo-workflow/RWF-000-parent.md
- Notes:
  - focused content searches found the legacy/frozen marker, canonical route, both migration candidates, no-bulk-move policy, and all seven numbered steps
  - all four canonical relative link targets resolved
  - scratchboard name-status output contained only `A scratchboard/README.md`; the protected legacy paths had no diff
  - residual risks or follow-ups: current legacy workstream state still requires deliberate human and current-artifact confirmation before migration, as documented in the README
