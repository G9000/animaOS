# RWF-000 - Repo Workflow Parent Tracker

- Status: in_progress
- Priority: P2
- Scope: repository metadata, documentation, workflow artifacts, validation, and draft-PR review
- Depends on: none
- Owner: Codex
- PRD: none
- Plan: docs/superpowers/plans/2026-07-15-repository-organization-project-management.md
- Created: 2026-06-26 17:18 MYT
- Updated: 2026-07-15 22:56 MYT
- Started: 2026-07-15 17:11 MYT
- Completed:

## Goal

Track improvements to the repo-native planning, ticket, and legacy scratchboard workflow.

## Child Ticket Order

This table is the execution order; dependency eligibility still controls when each ticket can be claimed.

| Ticket | Title | Status | Depends on |
| --- | --- | --- | --- |
| `RWF-005` | Add the anima project-management skill | `done` | none |
| `RWF-001` | Rebuild the canonical ticket initiative index | `done` | none |
| `RWF-002` | Mark scratchboard legacy and add migration checklist | `done` | `RWF-001` |
| `RWF-003` | Add ticket metadata validation | `done` | `RWF-001` |
| `RWF-004` | Reconcile repository documentation and hygiene | `done` | `RWF-002` |
| `RWF-006` | Validate, publish, and complete PR review | `backlog` | `RWF-001`, `RWF-002`, `RWF-003`, `RWF-004`, `RWF-005` |

## Deliverables

- Canonical ticket initiative index and scratchboard legacy/migration path
- Read-only repository organization validation
- Current repository documentation, audit, and tracked-log hygiene
- Repo-owned anima project-management skill and repository workflow integration
- Final local validation, authorized draft PR publication, current-head review, and metadata closeout

## Acceptance

- Every child ticket references this parent
- Parent status table reflects child progress
- Completed child tickets are listed below with timestamps
- All six required child tickets satisfy their acceptance criteria
- `RWF-006` and this parent close only after the implementation head has a clean current-head review
- The closeout metadata commit is pushed and a fresh exact `@codex review` request is posted

## Post-closeout Terminal Guard

- Review the final closeout head under the same current-head stopping rule without merging automatically.
- If actionable feedback invalidates acceptance, reapply the canonical owner gate before reopening the affected child, `RWF-006`, and this parent consistently; then fix, close, push, and review again.

## Completed Tickets

- 2026-07-15 20:06 MYT - `RWF-001` repaired bidirectional parent tracking and revalidated the canonical initiative index.
- 2026-07-15 20:35 MYT - `RWF-002` clarified the legacy-continuation and approved-cutover policy while preserving scratchboard history.
- 2026-07-15 21:33 MYT - `RWF-003` completed the read-only repository organization validator after final focused, live-clean, template, and bidirectional validation.
- 2026-07-15 21:58 MYT - `RWF-004` reconciled repository navigation and hygiene, including manifest-grounded contributor command and language maps.
- 2026-07-15 22:56 MYT - `RWF-005` completed the portable neutral v2 skill evaluation with multi-page and cursor-failure review coverage and no required skill refactor.

## Activity Log

- 2026-06-26 17:18 MYT - Parent tracker created for repo workflow improvements.
- 2026-07-15 17:11 MYT - Codex started the initiative on branch `codex/repo-organization-project-management` using `docs/superpowers/plans/2026-07-15-repository-organization-project-management.md`.
- 2026-07-15 17:27 MYT - Clarified execution order and the two-phase implementation-review and closeout-head guard.
- 2026-07-15 17:34 MYT - Added the `RWF-002` dependency to `RWF-004` before repository hygiene validation.
- 2026-07-15 17:39 MYT - Synchronized `RWF-005` to `in_progress` after Codex claimed it on branch `codex/repo-organization-project-management` in worktree `.worktrees/repo-organization-project-management`.
- 2026-07-15 17:49 MYT - Recorded RED baseline evidence for `RWF-005`; the child remains `in_progress` pending skill creation and forward evaluation.
- 2026-07-15 18:11 MYT - Added and officially validated the minimal GREEN project-management skill; `RWF-005` remains `in_progress` pending repository integration and forward evaluation.
- 2026-07-15 18:53 MYT - Integrated `RWF-005` into `AGENTS.md` and the canonical PRD/ticket workflow, including state-safe parent synchronization and explicitly authorized current-head PR review; kept the parent and child row `in_progress` pending forward evaluation.
- 2026-07-15 18:59 MYT - Completed Task 4 review follow-up by making initiative closeout explicit, guarding the initial review request on PR-head synchronization, and recording the integration validation below; kept the parent and `RWF-005` row `in_progress` pending forward evaluation.
- 2026-07-15 19:13 MYT - Aligned the canonical project-management authority, pagination, reopen, parent-closeout, early-merge, and template-validator contracts; recorded the clarified `RWF-003` scope without claiming it, kept its row `backlog`, and kept `RWF-005`/parent `in_progress` pending forward evaluation.
- 2026-07-15 19:37 MYT - Synchronized `RWF-001` to `in_progress` after Codex claimed it on branch `codex/repo-organization-project-management` in worktree `.worktrees/repo-organization-project-management`; preserved parent ownership and `in_progress` state.
- 2026-07-15 19:46 MYT - Completed `RWF-001`, synchronized its row and completed history to `done`, and kept `RWF-000` `in_progress` with its owner unchanged because `RWF-005` and other required children remain open.
- 2026-07-15 20:02 MYT - Reopened `RWF-001` for the missing `VMI-008` parent-row acceptance defect, removed its current completed-history entry, preserved the child completion timestamp `2026-07-15 19:46 MYT` in history, and kept `RWF-000` `in_progress` with ownership unchanged.
- 2026-07-15 20:02 MYT - Revised the unclaimed `RWF-003` planning contract to enforce bidirectional child-reference and parent-row coverage; kept its row `backlog`, owner `unassigned`, and implementation unstarted.
- 2026-07-15 20:06 MYT - Re-completed `RWF-001` after the `VMI-008` repair and 146-row bidirectional validation, re-added one current completed-history entry, and kept `RWF-000` `in_progress` with its owner unchanged while required children remain open.
- 2026-07-15 20:23 MYT - Synchronized `RWF-002` to `in_progress` after Codex claimed it on branch `codex/repo-organization-project-management` in worktree `.worktrees/repo-organization-project-management`; preserved parent ownership and `in_progress` state.
- 2026-07-15 20:25 MYT - Completed `RWF-002`, synchronized its row and one completed-history entry to `done`, and kept `RWF-000` `in_progress` with its owner unchanged while required children remain open.
- 2026-07-15 20:34 MYT - Reopened `RWF-002` for the legacy-continuation versus cutover policy contradiction, removed its current completed-history entry, preserved the child completion timestamp `2026-07-15 20:25 MYT` in history, and kept `RWF-000` `in_progress` with ownership unchanged.
- 2026-07-15 20:35 MYT - Re-completed `RWF-002` after clarifying the legacy-continuation and approved-cutover policy, re-added one current completed-history entry, and kept `RWF-000` `in_progress` with its owner unchanged while required children remain open.
- 2026-07-15 20:43 MYT - Synchronized `RWF-003` to `in_progress` after Codex claimed it on branch `codex/repo-organization-project-management` in worktree `.worktrees/repo-organization-project-management`; confirmed `RWF-001` is `done`, preserved parent ownership, and kept the parent `in_progress`.
- 2026-07-15 20:53 MYT - Recorded material `RWF-003` progress: the strict-TDD validator passes 24 focused tests and the live bidirectional ticket check, while its row and the parent remain `in_progress` pending Task 8 cleanup of `docs/audits` and tracked `debug.log`; parent ownership remains unchanged.
- 2026-07-15 21:06 MYT - Recorded the material `RWF-003` review follow-up: escaped and inline-code pipes now preserve authoritative table columns with 26 focused tests passing; kept the row and parent `in_progress`, preserved parent ownership, and left Task 8 hygiene as the remaining closeout dependency.
- 2026-07-15 21:14 MYT - Corrected the 21:06 `RWF-003` inline-code model to GFM consecutive-backslash parity after second review; recorded 29 focused tests and a clean ticket graph, kept the row and parent `in_progress`, and preserved parent ownership while Task 8 hygiene remains open.
- 2026-07-15 21:19 MYT - Recorded the material third `RWF-003` review follow-up: immediate equal-width GFM delimiters now gate authoritative parent tables, 32 focused tests and the live ticket graph pass, and the row and parent remain `in_progress` with ownership unchanged pending Task 8 hygiene.
- 2026-07-15 21:25 MYT - Synchronized `RWF-004` to `in_progress` after Codex claimed it on branch `codex/repo-organization-project-management` in worktree `.worktrees/repo-organization-project-management`; confirmed `RWF-002` is `done`, preserved parent ownership, and kept the parent `in_progress`.
- 2026-07-15 21:33 MYT - Completed `RWF-004`, synchronized its row and one completed-history entry to `done`, and kept `RWF-000` `in_progress` with ownership unchanged because `RWF-005` and `RWF-006` remain open.
- 2026-07-15 21:33 MYT - Completed `RWF-003` after the final 32-test suite, clean live organization run, three-field template check, and 146-row bidirectional assertion; synchronized its row and one completed-history entry to `done` and kept the parent `in_progress` for `RWF-005` and `RWF-006`.
- 2026-07-15 21:43 MYT - Reopened `RWF-004` for the acceptance-breaking root-build orchestration wording, removed its current completed-history entry, preserved the child completion timestamp `2026-07-15 21:33 MYT` in history, kept `RWF-003` done/history untouched, and preserved parent ownership and `in_progress` state.
- 2026-07-15 21:45 MYT - Re-completed `RWF-004` after aligning the directory map with the exact root `package.json` build sequence, re-added one current completed-history entry, and kept `RWF-000` `in_progress` with ownership unchanged for `RWF-005` and `RWF-006`.
- 2026-07-15 21:56 MYT - Reopened `RWF-004` for the acceptance-breaking contributor command and language-map errors, removed its current completed-history entry, preserved the child completion timestamp `2026-07-15 21:45 MYT` in history, kept `RWF-003` done/history untouched, and preserved parent ownership and `in_progress` state.
- 2026-07-15 21:58 MYT - Re-completed `RWF-004` after correcting the contributor command and language map from root supervisor and manifest evidence, re-added one current completed-history entry, and kept `RWF-000` `in_progress` with ownership unchanged for `RWF-005` and `RWF-006`.
- 2026-07-15 22:07 MYT - Recorded material `RWF-005` forward-evaluation progress after resuming its existing Codex-owned `in_progress` state without a new claim; kept the child row and parent `in_progress` and preserved parent ownership.
- 2026-07-15 22:17 MYT - Completed `RWF-005`, synchronized its row and one current completed-history entry to `done`, and kept `RWF-000` `in_progress` with ownership unchanged because `RWF-006` remains `backlog` and is the initiative's publication/review closeout.
- 2026-07-15 22:38 MYT - Reopened `RWF-005` for acceptance-breaking leading and non-portable forward-evaluation prompts, removed its current completed-history entry, preserved the prior completion timestamp `2026-07-15 22:17 MYT` in parent activity, returned its row to `in_progress`, and kept `RWF-000` `in_progress` with ownership unchanged.
- 2026-07-15 22:56 MYT - Re-completed `RWF-005` after six portable neutral v2 sub-runs passed, added one current completion-history entry, and kept `RWF-000` `in_progress` with ownership unchanged because `RWF-006` remains the authorized end-to-end publication/review closeout.

## Validation

### Historical initiative snapshots (superseded for current RWF-005 closeout)

The commands, changed-path inventories, and result counts below are preserved from Task 4 integration, skill thinning, and Tasks 7-8 organization/hygiene work. They are phase-specific historical snapshots, not a claim that every listed path changed in the current closeout. Labels such as the former four-file, ten-file, focused-test, and manifest counts describe those earlier validated scopes and are superseded by the exact current results below.

- Commands (historical):
  - `rg -n '\.codex-skill-staging/anima-project-management/SKILL\.md|@codex review|reviewThreads|headRefOid|Owner: unassigned|Project Management Skill' AGENTS.md docs/ops/prd-ticket-workflow.md`
  - `rg -n 'close an initiative|record the pushed commit OID|headRefOid.*recorded pushed OID' AGENTS.md docs/ops/prd-ticket-workflow.md`
  - `python <codex-home>\skills\.system\skill-creator\scripts\quick_validate.py .codex-skill-staging/anima-project-management` (historical local root normalized for portability)
  - `(Get-Content .codex-skill-staging/anima-project-management/SKILL.md | Measure-Object -Word).Words`
  - `rg -n 'Action-Scoped External Authority|pageInfo|owner gate|parent .*Completed|missing authority|permission blocker' AGENTS.md docs/ops/prd-ticket-workflow.md .codex-skill-staging/anima-project-management/SKILL.md docs/superpowers/specs/2026-07-15-anima-project-management-skill-design.md docs/superpowers/plans/2026-07-15-repository-organization-project-management.md`
  - `rg -n '^- (PRD|Spec|Plan): none\r?$' tickets/TEMPLATE.md`
  - `bun test tests/repo-organization.test.ts`
  - `bun test tests/dev-root.test.ts tests/repo-organization.test.ts`
  - `bun run check:repo`
  - `bunx nx show projects`
  - targeted old audit-path, moved-link, debug tracking/ignore, and source-hotspot checks
  - `git diff --check`
  - `git diff --cached --check`
  - `git diff --name-only HEAD`
- Changed paths (historical initiative scope):
  - .codex-skill-staging/anima-project-management/SKILL.md
  - .gitignore
  - AGENTS.md
  - README.md
  - debug.log (removed from tracking; ignored local file preserved)
  - docs/architecture/system/directory-structure.md
  - docs/audit/2026-06-11-agent-server-audit.md
  - docs/ops/prd-ticket-workflow.md
  - docs/superpowers/plans/2026-07-14-dual-session-scope-a6.md
  - docs/superpowers/specs/2026-07-15-anima-project-management-skill-design.md
  - docs/superpowers/specs/2026-07-15-repository-organization-cleanup-design.md
  - docs/superpowers/plans/2026-07-15-repository-organization-project-management.md
  - tickets/TEMPLATE.md
  - tickets/agent-server-audit-remediation/ASR-001-dual-session-scope.md
  - tickets/repo-workflow/RWF-003-ticket-metadata-validation.md
  - tickets/repo-workflow/RWF-004-repository-documentation-hygiene.md
  - tickets/repo-workflow/RWF-005-anima-project-management-skill.md
  - tickets/repo-workflow/RWF-000-parent.md
- Notes (historical):
  - exact Task 4 terminology and review-head guard searches exited 0
  - working and staged diff checks exited 0
  - Task 4 review follow-up contained exactly the four changed paths listed above
  - quality follow-up official skill validation exited 0; the checklist is 694 words and the interface YAML hash is unchanged
  - action-scope, full-pagination, ownership-safe reopen, parent-closeout, early-merge blocker, and template-contract searches passed
  - template metadata has exactly 3 matches; expected link targets exist; forbidden contradiction/path search returned no matches; working diff check passed with exactly the 10 listed paths
  - Task 8 final focused suite passed 32 tests with 59 assertions and 0 failures; live `check:repo` passed
  - Task 8 bidirectional live assertion returned 17 conforming parents, 146 authoritative rows, 146 reverse child references, and 0 ticket violations
  - Task 8 targeted live audit links, debug ignore/untracking, moved-path resolution, and production-source exclusion passed
  - Task 8 review follow-up matched the exact root `scripts.build` value, clarified the Nx-selected projects versus the subsequent Animus Cargo check, and revalidated focused tests, live organization, diff, and source scope
  - Task 8 quality follow-up matched root dev/build scripts, verified uv/Bun launcher specs and 14 current language-family manifests, passed 44 focused tests and the live organization check, and kept `RWF-003` untouched

### Current portable neutral v2 RWF-005 closeout validation

The full executable replay-manifest parser, exact YAML/frontmatter commands, privacy assertion, and fixture-cleanup routine are recorded under `RWF-005` current validation. This parent preserves the initiative-level commands and results without treating older phase path inventories as current.

- Exact commands:
  - `$validator = Join-Path $env:USERPROFILE '.codex\skills\.system\skill-creator\scripts\quick_validate.py'; python $validator .codex-skill-staging/anima-project-management`
  - `(Get-Content .codex-skill-staging/anima-project-management/SKILL.md | Measure-Object -Word).Words`
  - `rg -n '\.codex-skill-staging/anima-project-management/SKILL\.md' AGENTS.md`
  - `$audit = 'docs/audit/skills/2026-07-15-anima-project-management-evaluation.md'; if (@(rg -n '^## Portable neutral v2 Scenario ' $audit).Count -ne 6) { throw 'v2 scenario count mismatch' }; if (@(rg -n '^- Agent: `/root/task9_implementer/v2_' $audit).Count -ne 6) { throw 'v2 agent count mismatch' }; if (@(rg -n '^### Equivalent `collaboration\.spawn_agent` argument object$' $audit).Count -ne 6) { throw 'replay manifest count mismatch' }; if (@(rg -n '^- Result: PASS on first accepted run$' $audit).Count -ne 6) { throw 'v2 pass count mismatch' }; if (@(rg -n '^### Superseded forward v1 result:' $audit).Count -ne 5) { throw 'v1 superseded count mismatch' }`
  - `bun test tests/repo-organization.test.ts`
  - `bun run check:repo`
  - `$remaining = @(Get-ChildItem -Force -Directory -Filter '.tmp-eval-*'); $tracked = @(git ls-files -- '.tmp-eval-*'); if ($remaining.Count -ne 0 -or $tracked.Count -ne 0) { throw 'Temporary evaluation fixture remains' }`
  - `git diff --check`
  - `$changed = @(git diff --name-only HEAD); $expected = @('docs/audit/skills/2026-07-15-anima-project-management-evaluation.md','tickets/repo-workflow/RWF-000-parent.md','tickets/repo-workflow/RWF-005-anima-project-management-skill.md'); if (@($changed | Where-Object { $_ -notin $expected }).Count -ne 0 -or @($expected | Where-Object { $_ -notin $changed }).Count -ne 0) { throw 'Changed-path scope mismatch' }; if (@($changed | Where-Object { $_ -match '^(apps|packages)/.*/src/|^apps/desktop/src-tauri/' }).Count -ne 0) { throw 'Production source changed' }; if (git diff --name-only HEAD -- .codex-skill-staging/anima-project-management/SKILL.md) { throw 'Unexpected staged-skill change' }`
- Results:
  - six portable neutral v2 evaluators passed scenarios 1-4, multi-page 5A, and cursor-failure 5B; v1 is preserved but superseded and no skill refactor was required
  - replay-manifest parsing passed for six exact collaboration argument objects; privacy validation found zero machine-specific user paths
  - official skill validation passed; the unchanged checklist is 694 words; exact frontmatter/interface and `AGENTS.md` path checks passed
  - focused repository validation passed with 32 tests, 59 assertions, and 0 failures; `bun run check:repo` passed
  - safe cleanup left zero `.tmp-eval-*` directories and zero tracked fixture paths; `git diff --check` passed
  - current scope contains exactly the three paths below with zero production-source or personal-skill changes
  - `RWF-000` remains `in_progress`, `Owner: Codex` is unchanged, `RWF-005` is `done`, and `RWF-006` remains `backlog` pending the real authorized PR loop
- Current changed paths:
  - docs/audit/skills/2026-07-15-anima-project-management-evaluation.md
  - tickets/repo-workflow/RWF-005-anima-project-management-skill.md
  - tickets/repo-workflow/RWF-000-parent.md
