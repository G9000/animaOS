# RWF-006 - Validate, publish, and complete PR review

- Status: backlog
- Priority: P1
- Scope: repository validation, Git metadata, draft PR, Codex review, ticket closeout
- Parent: `RWF-000`
- Depends on: `RWF-001`, `RWF-002`, `RWF-003`, `RWF-004`, `RWF-005`
- Owner: unassigned
- PRD: none
- Spec: docs/superpowers/specs/2026-07-15-repository-organization-cleanup-design.md; docs/superpowers/specs/2026-07-15-anima-project-management-skill-design.md
- Plan: docs/superpowers/plans/2026-07-15-repository-organization-project-management.md
- Created: 2026-07-15 17:11 MYT
- Updated: 2026-07-15 17:27 MYT
- Started:
- Completed:

## Goal

Carry the repository-workflow implementation through final validation and a clean current-head Codex review, then commit project closeout and guard the final metadata head without merging.

## Deliverables

- Record focused organization, skill, workspace, build, diff, and scope validation
- Publish or update the explicitly user-authorized draft PR with the required review contract
- Request and monitor thread-aware Codex review until the implementation head is clean
- After that clean implementation head, close child and parent metadata in one commit, push it, and request review of the final closeout head

## Acceptance

- `RWF-001` through `RWF-005` are `done`, focused organization and skill checks pass, the required build passes, and final diff/scope checks are clean
- With explicit user authorization, a scoped draft PR is opened or updated with `Summary`, `Scope`, `Review focus`, `Out of scope`, and `Validation` sections
- The exact standalone comment `@codex review` is posted after each head requiring review
- Before closeout, thread-aware `reviewThreads(first: 100)` evidence shows the latest Codex review targets the implementation `headRefOid` and zero unresolved non-outdated actionable threads remain
- Non-actionable threads have evidence-based dispositions, required checks pass on the clean implementation head, and no merge occurs without separate user authorization
- The closeout metadata commit records `RWF-006` and `RWF-000` as `done`, is pushed, and receives a fresh exact `@codex review` request

## Post-closeout Terminal Guard

- Review the final closeout head with the same current-head, required-check, disposition, and zero-actionable-thread stopping rule.
- If actionable feedback invalidates acceptance, reopen the affected child, `RWF-006`, and `RWF-000` consistently, fix and validate narrowly, close again, push, re-request `@codex review`, and repeat.
- Leave the draft PR unmerged unless the user separately authorizes merge.

## Activity Log

- 2026-07-15 17:11 MYT - Ticket created from the approved repository-organization and project-management skill specs and implementation plan.
- 2026-07-15 17:27 MYT - Clarified clean implementation-head closeout followed by a terminal final-head review guard.

## Validation

- Commands:
  - `not run yet`
- Changed paths:
  - none
- Notes:
  - backlog ticket only; publication and merge remain separately authorized actions
