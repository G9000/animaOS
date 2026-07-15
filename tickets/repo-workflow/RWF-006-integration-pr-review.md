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
- Updated: 2026-07-15 17:11 MYT
- Started:
- Completed:

## Goal

Carry the completed repository-workflow implementation through final validation, explicitly authorized draft-PR publication, current-head Codex review, and reviewed project-metadata closeout without merging.

## Deliverables

- Record focused organization, skill, workspace, build, diff, and scope validation
- Publish or update the explicitly user-authorized draft PR with the required review contract
- Request and monitor thread-aware Codex review on every relevant head
- Close child and parent metadata only after the implementation head is clean, then push and review the final closeout head

## Acceptance

- `RWF-001` through `RWF-005` are `done`, focused organization and skill checks pass, the required build passes, and final diff/scope checks are clean
- With explicit user authorization, a scoped draft PR is opened or updated with `Summary`, `Scope`, `Review focus`, `Out of scope`, and `Validation` sections
- The exact standalone comment `@codex review` is posted after each head requiring review
- Thread-aware `reviewThreads(first: 100)` evidence shows the latest Codex review targets the current `headRefOid` and zero unresolved non-outdated actionable threads remain
- Non-actionable threads have evidence-based dispositions, required checks pass on the reviewed head, and no merge occurs without separate user authorization
- The final child/parent closeout commit is pushed, `@codex review` is requested again, and the final metadata head satisfies the same current-head stopping rule

## Activity Log

- 2026-07-15 17:11 MYT - Ticket created from the approved repository-organization and project-management skill specs and implementation plan.

## Validation

- Commands:
  - `not run yet`
- Changed paths:
  - none
- Notes:
  - backlog ticket only; publication and merge remain separately authorized actions
