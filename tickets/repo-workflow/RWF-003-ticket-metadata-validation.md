# RWF-003 - Add ticket metadata validation

- Status: backlog
- Priority: P2
- Scope: `tickets`, `apps`, `packages`, `docs`, `scratchboard`, `scripts`, `tests`, `package.json`
- Parent: `RWF-000`
- Depends on: `RWF-001`
- Owner: unassigned
- PRD: none
- Plan: docs/superpowers/plans/2026-07-15-repository-organization-project-management.md
- Created: 2026-06-26 17:18 MYT
- Updated: 2026-07-15 20:02 MYT
- Started:
- Completed:

## Goal

Add one read-only organization validator that reports repository metadata and hygiene drift without mutating the tree.

## Deliverables

- Add `scripts/check-repo-organization.ts` with deterministic, aggregated, read-only reporting
- Validate canonical ticket and parent-table statuses, parent-row-to-child uniqueness and status synchronization, every child `Parent:` reference to a conforming parent appearing in exactly one authoritative row, and the rule that a non-empty `Completed:` field implies `Status: done`
- Validate that `tickets/TEMPLATE.md` exposes top-level `PRD:`, `Spec:`, and `Plan:` fields without requiring legacy tickets to backfill missing `Spec:` metadata
- Validate recognized manifests for every direct child of `apps/` and `packages/`
- Validate singular `docs/audit/`, an untracked root `debug.log`, and a legacy marker at `scratchboard/README.md`
- Add focused Bun coverage in `tests/repo-organization.test.ts`
- Expose the validator through the root `check:repo` command without changing existing build, lint, or test commands

## Acceptance

- Canonical statuses are accepted and legacy or unknown authoritative statuses are reported; missing, ambiguous, duplicate, or mismatched parent rows are reported in both directions, including a child whose `Parent:` references a conforming parent but appears in zero or multiple authoritative rows
- A ticket with a non-empty `Completed:` field and any status other than `done` is reported
- A template missing `PRD:`, `Spec:`, or `Plan:` is reported, while a historical ticket is not rejected solely because it lacks `Spec:`
- Missing direct-child manifests, plural audit layout, tracked `debug.log`, and a missing scratchboard legacy marker are each reported actionably
- Multiple violations are grouped and reported in one read-only run; unexpected filesystem or Git failures use a distinct nonzero failure path
- `bun test tests/repo-organization.test.ts` covers LF/CRLF metadata, supported parent table forms, quoted/unquoted cells, both parent-row-to-child and child-reference-to-parent-row validation, organization checks, aggregation, and clean behavior
- `bun run check:repo` executes the approved validator from the repository root

## Activity Log

- 2026-06-26 17:18 MYT - Ticket created.
- 2026-07-15 17:11 MYT - Expanded acceptance to the approved read-only repository organization validator contract.
- 2026-07-15 19:13 MYT - Clarified the template-only PRD/spec/plan validator contract and one-way completion semantics without claiming or starting `RWF-003`; status remains `backlog` and owner remains `unassigned`.
- 2026-07-15 20:02 MYT - Expanded the planning contract to bidirectional parent-child validation after the missing `VMI-008` row was detected; kept `RWF-003` `backlog`/`unassigned`, updated only planning artifacts, and began no validator implementation.

## Validation

- Commands:
  - `rg -n '^- (PRD|Spec|Plan): none\r?$' tickets/TEMPLATE.md`
- Changed paths:
  - docs/ops/prd-ticket-workflow.md
  - tickets/TEMPLATE.md
  - docs/superpowers/specs/2026-07-15-repository-organization-cleanup-design.md
  - docs/superpowers/plans/2026-07-15-repository-organization-project-management.md
  - tickets/repo-workflow/RWF-003-ticket-metadata-validation.md
  - tickets/repo-workflow/RWF-000-parent.md
- Notes:
  - template metadata search returned exactly 3 matches
  - bidirectional child-reference and parent-row validation is now explicit in the approved design, implementation plan, and backlog ticket without claiming implementation
  - validator implementation and focused tests remain pending; ticket remains `backlog`/`unassigned`
