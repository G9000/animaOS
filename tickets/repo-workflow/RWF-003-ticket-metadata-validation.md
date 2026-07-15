# RWF-003 - Add ticket metadata validation

- Status: in_progress
- Priority: P2
- Scope: `tickets`, `apps`, `packages`, `docs`, `scratchboard`, `scripts`, `tests`, `package.json`
- Parent: `RWF-000`
- Depends on: `RWF-001`
- Owner: Codex
- PRD: none
- Plan: docs/superpowers/plans/2026-07-15-repository-organization-project-management.md
- Created: 2026-06-26 17:18 MYT
- Updated: 2026-07-15 21:06 MYT
- Started: 2026-07-15 20:43 MYT
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
- 2026-07-15 20:43 MYT - Codex claimed `RWF-003` on branch `codex/repo-organization-project-management` in worktree `.worktrees/repo-organization-project-management`; dependency `RWF-001` is `done`, no competing claim was visible, and the child and parent row were set to `in_progress`.
- 2026-07-15 20:53 MYT - Added the read-only validator, injected CLI result boundary, root `check:repo` command, and 24 focused tests through strict RED/GREEN cycles; live validation has zero ticket violations and intentionally leaves `RWF-003` `in_progress` until Task 8 removes the two scheduled hygiene findings.
- 2026-07-15 21:06 MYT - Fixed the review-discovered Markdown table boundary defect through regression-first TDD: escaped pipes and pipes inside inline code no longer shift authoritative Ticket or Status columns; kept the child and parent row `in_progress` pending Task 8 hygiene.

## Validation

- Commands:
  - `bun test tests/repo-organization.test.ts` (initial RED before implementation)
  - `bun test tests/repo-organization.test.ts` (live-fixture regression RED)
  - `bun test tests/repo-organization.test.ts` (final GREEN)
  - `bun test tests/repo-organization.test.ts` (escaped-pipe review RED)
  - `bun test tests/repo-organization.test.ts` (escaped-pipe review GREEN)
  - `bun run check:repo`
  - `rg -n '^- (PRD|Spec|Plan): none\r?$' tickets/TEMPLATE.md`
  - read-only Bun assertion using `parseTicketDocument`, `loadRepositorySnapshot`, and `collectOrganizationViolations`
  - `git diff --check`
- Changed paths:
  - package.json
  - scripts/check-repo-organization.ts
  - tests/repo-organization.test.ts
  - tickets/repo-workflow/RWF-003-ticket-metadata-validation.md
  - tickets/repo-workflow/RWF-000-parent.md
- Notes:
  - initial RED exited 1 because `../scripts/check-repo-organization` did not exist: 0 pass, 1 fail, 1 error
  - live-fixture regressions reproduced explanatory prose before a parent table and the `Parent: none` sentinel: 21 pass, 2 fail
  - final GREEN passed 24 tests with 48 assertions and 0 failures
  - live `check:repo` exited 1 with exactly 2 aggregated Task 8 findings: deprecated `docs/audits` and tracked root `debug.log`; ticket, template, manifest, and scratchboard violations: 0
  - template metadata search returned exactly 3 matches
  - bidirectional live assertion returned 17 conforming parents, 146 authoritative rows, 146 reverse child references, and 0 ticket violations
  - exported API separates pure parsing/reporting from injected snapshot loading and returns exit 0 for clean state, exit 1 for organization violations, and exit 2 with a distinct failure prefix for unexpected filesystem or Git errors
  - escaped-pipe review RED passed 24 tests and failed the 2 new regressions: escaped `\|` produced status `Parser B`, and inline-code `` `A | B` `` produced status `B``
  - escaped-pipe review GREEN passed 26 tests with 50 assertions and 0 failures after replacing naive splitting with a focused scanner for backslash escapes and matching backtick delimiter runs
  - review follow-up changed paths: `scripts/check-repo-organization.ts`, `tests/repo-organization.test.ts`, `tickets/repo-workflow/RWF-003-ticket-metadata-validation.md`, and `tickets/repo-workflow/RWF-000-parent.md`
  - post-fix live validation still reports only deprecated `docs/audits` and tracked root `debug.log`; 17 conforming parents, 146 authoritative rows, 146 reverse child references, and 0 ticket violations
  - residual follow-up: Task 8 must remove the two live hygiene findings before this ticket can close
