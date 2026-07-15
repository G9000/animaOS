# RWF-000 - Repo Workflow Parent Tracker

- Status: in_progress
- Priority: P2
- Scope: repository metadata, documentation, workflow artifacts, validation, and draft-PR review
- Depends on: none
- Owner: Codex
- PRD: none
- Plan: docs/superpowers/plans/2026-07-15-repository-organization-project-management.md
- Created: 2026-06-26 17:18 MYT
- Updated: 2026-07-15 20:06 MYT
- Started: 2026-07-15 17:11 MYT
- Completed:

## Goal

Track improvements to the repo-native planning, ticket, and legacy scratchboard workflow.

## Child Ticket Order

This table is the execution order; dependency eligibility still controls when each ticket can be claimed.

| Ticket | Title | Status | Depends on |
| --- | --- | --- | --- |
| `RWF-005` | Add the anima project-management skill | `in_progress` | none |
| `RWF-001` | Rebuild the canonical ticket initiative index | `done` | none |
| `RWF-002` | Mark scratchboard legacy and add migration checklist | `backlog` | `RWF-001` |
| `RWF-003` | Add ticket metadata validation | `backlog` | `RWF-001` |
| `RWF-004` | Reconcile repository documentation and hygiene | `backlog` | `RWF-002` |
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

## Validation

- Commands:
  - `rg -n '\.codex-skill-staging/anima-project-management/SKILL\.md|@codex review|reviewThreads|headRefOid|Owner: unassigned|Project Management Skill' AGENTS.md docs/ops/prd-ticket-workflow.md`
  - `rg -n 'close an initiative|record the pushed commit OID|headRefOid.*recorded pushed OID' AGENTS.md docs/ops/prd-ticket-workflow.md`
  - `python C:\Users\leoca\.codex\skills\.system\skill-creator\scripts\quick_validate.py .codex-skill-staging/anima-project-management`
  - `(Get-Content .codex-skill-staging/anima-project-management/SKILL.md | Measure-Object -Word).Words`
  - `rg -n 'Action-Scoped External Authority|pageInfo|owner gate|parent .*Completed|missing authority|permission blocker' AGENTS.md docs/ops/prd-ticket-workflow.md .codex-skill-staging/anima-project-management/SKILL.md docs/superpowers/specs/2026-07-15-anima-project-management-skill-design.md docs/superpowers/plans/2026-07-15-repository-organization-project-management.md`
  - `rg -n '^- (PRD|Spec|Plan): none\r?$' tickets/TEMPLATE.md`
  - `git diff --check`
  - `git diff --cached --check`
  - `git diff --name-only HEAD`
- Changed paths:
  - .codex-skill-staging/anima-project-management/SKILL.md
  - AGENTS.md
  - docs/ops/prd-ticket-workflow.md
  - docs/superpowers/specs/2026-07-15-anima-project-management-skill-design.md
  - docs/superpowers/specs/2026-07-15-repository-organization-cleanup-design.md
  - docs/superpowers/plans/2026-07-15-repository-organization-project-management.md
  - tickets/TEMPLATE.md
  - tickets/repo-workflow/RWF-003-ticket-metadata-validation.md
  - tickets/repo-workflow/RWF-005-anima-project-management-skill.md
  - tickets/repo-workflow/RWF-000-parent.md
- Notes:
  - exact Task 4 terminology and review-head guard searches exited 0
  - working and staged diff checks exited 0
  - Task 4 review follow-up contained exactly the four changed paths listed above
  - quality follow-up official skill validation exited 0; the checklist is 694 words and the interface YAML hash is unchanged
  - action-scope, full-pagination, ownership-safe reopen, parent-closeout, early-merge blocker, and template-contract searches passed
  - template metadata has exactly 3 matches; expected link targets exist; forbidden contradiction/path search returned no matches; working diff check passed with exactly the 10 listed paths
