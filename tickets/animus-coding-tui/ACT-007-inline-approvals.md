# ACT-007 - Add inline approvals

- Status: backlog
- Priority: P1
- Scope: `apps/animus`, `apps/server`
- Parent: `ACT-000`
- Depends on: `ACT-001`, `ACT-004`, `ACT-005`
- Owner: unassigned
- PRD: docs/prds/animus/rust-coding-tui-v1.md
- Plan: docs/superpowers/plans/2026-06-27-animus-rust-coding-tui.md
- Created: 2026-06-26 18:51 MYT
- Updated: 2026-06-27 03:00 MYT
- Started:
- Completed:

## Goal

Add tool-aware inline approval prompts to the Rust TUI.

## Deliverables

- Pending approval state.
- Separate shell execution and file-change approval display models.
- Question/generic approval renderers.
- Accept, accept-for-session, policy-amendment accept where available, decline, and cancel decisions.
- `approval_response` send path with expected IDs.

## Acceptance

- Approval UI clearly shows what action is being requested.
- Decisions round-trip to server correctly.
- Approval reducer tests cover accept, accept-for-session, decline, cancel, and remembered/policy decision flows.

## Activity Log

- 2026-06-26 18:51 MYT - Ticket created.
- 2026-06-27 03:00 MYT - Revised for Rust inline approvals.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - backlog ticket only
