# RWF-000 - Repo Workflow Parent Tracker

- Status: in_progress
- Priority: P2
- Scope: repository metadata, documentation, workflow artifacts, validation, and draft-PR review
- Depends on: none
- Owner: Codex
- PRD: none
- Plan: docs/superpowers/plans/2026-07-15-repository-organization-project-management.md
- Created: 2026-06-26 17:18 MYT
- Updated: 2026-07-15 18:53 MYT
- Started: 2026-07-15 17:11 MYT
- Completed:

## Goal

Track improvements to the repo-native planning, ticket, and legacy scratchboard workflow.

## Child Ticket Order

This table is the execution order; dependency eligibility still controls when each ticket can be claimed.

| Ticket | Title | Status | Depends on |
| --- | --- | --- | --- |
| `RWF-005` | Add the anima project-management skill | `in_progress` | none |
| `RWF-001` | Rebuild the canonical ticket initiative index | `backlog` | none |
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
- If actionable feedback invalidates acceptance, reopen the affected child, `RWF-006`, and this parent consistently, then fix, close, push, and review again.

## Completed Tickets

- none

## Activity Log

- 2026-06-26 17:18 MYT - Parent tracker created for repo workflow improvements.
- 2026-07-15 17:11 MYT - Codex started the initiative on branch `codex/repo-organization-project-management` using `docs/superpowers/plans/2026-07-15-repository-organization-project-management.md`.
- 2026-07-15 17:27 MYT - Clarified execution order and the two-phase implementation-review and closeout-head guard.
- 2026-07-15 17:34 MYT - Added the `RWF-002` dependency to `RWF-004` before repository hygiene validation.
- 2026-07-15 17:39 MYT - Synchronized `RWF-005` to `in_progress` after Codex claimed it on branch `codex/repo-organization-project-management` in worktree `.worktrees/repo-organization-project-management`.
- 2026-07-15 17:49 MYT - Recorded RED baseline evidence for `RWF-005`; the child remains `in_progress` pending skill creation and forward evaluation.
- 2026-07-15 18:11 MYT - Added and officially validated the minimal GREEN project-management skill; `RWF-005` remains `in_progress` pending repository integration and forward evaluation.
- 2026-07-15 18:53 MYT - Integrated `RWF-005` into `AGENTS.md` and the canonical PRD/ticket workflow, including state-safe parent synchronization and explicitly authorized current-head PR review; kept the parent and child row `in_progress` pending forward evaluation.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - tickets/repo-workflow/RWF-000-parent.md
- Notes:
  - tracker only
