# ACT-008 - Run smoke tests, docs, and tracker cleanup

- Status: backlog
- Priority: P2
- Scope: `apps/animus`, `apps/server`, `docs`, `tickets`
- Parent: `ACT-000`
- Depends on: `ACT-004`, `ACT-005`, `ACT-006`, `ACT-007`
- Owner: unassigned
- PRD: docs/prds/animus/coding-tui-v1.md
- Plan: docs/superpowers/plans/2026-06-26-animus-coding-tui.md
- Created: 2026-06-26 18:51 MYT
- Updated: 2026-06-26 18:51 MYT
- Started:
- Completed:

## Goal

Validate the short-port implementation end to end, update docs, and close out tracker state.

## Deliverables

- Build and test results recorded.
- Manual smoke-test notes for chat, delegated tool execution, approval, cancel, reconnect, and spawn visibility.
- Usage docs or README notes updated.
- Parent tracker and child ticket validation sections updated.

## Acceptance

- `bun run build` result is recorded.
- `bun run test` result is recorded.
- `/health` smoke check result is recorded.
- Animus can complete a representative coding turn through ANIMA.
- Remaining issues are captured as follow-up tickets instead of hidden in chat context.

## Activity Log

- 2026-06-26 18:51 MYT - Ticket created.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - backlog ticket only

