# ACT-006 - Add inline approval flow

- Status: backlog
- Priority: P1
- Scope: `apps/animus/src/ui`, `apps/animus/src/tools`
- Parent: `ACT-000`
- Depends on: `ACT-001`, `ACT-003`
- Owner: unassigned
- PRD: docs/prds/animus/coding-tui-v1.md
- Plan: docs/superpowers/plans/2026-06-26-animus-coding-tui.md
- Created: 2026-06-26 18:51 MYT
- Updated: 2026-06-26 18:51 MYT
- Started:
- Completed:

## Goal

Upgrade basic approval prompts into inline, tool-aware approval flows that support allow, deny, and remember decisions where safe.

## Deliverables

- Approval state hook.
- Approval renderer switch for shell, file write/edit, question, and generic approvals.
- Round-trip `approval_response` messages to the server.
- Permission-rule integration for "always allow" behavior.

## Acceptance

- Approval UI clearly shows what action is being requested.
- Allow, deny, and always behave consistently.
- Approval decisions return to the server with the right identifiers.
- Approval tests or smoke test cover at least shell and file approval paths.

## Activity Log

- 2026-06-26 18:51 MYT - Ticket created.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - backlog ticket only

