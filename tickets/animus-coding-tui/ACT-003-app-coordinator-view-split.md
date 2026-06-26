# ACT-003 - Split Animus app coordinator and render view

- Status: backlog
- Priority: P1
- Scope: `apps/animus/src/ui`
- Parent: `ACT-000`
- Depends on: `ACT-001`, `ACT-002`
- Owner: unassigned
- PRD: docs/prds/animus/coding-tui-v1.md
- Plan: docs/superpowers/plans/2026-06-26-animus-coding-tui.md
- Created: 2026-06-26 18:51 MYT
- Updated: 2026-06-26 18:51 MYT
- Started:
- Completed:

## Goal

Refactor the current large Animus root UI into production coding TUI focused units: coordinator, render-only view, submit handler, conversation loop, and approval flow.

## Deliverables

- `App.tsx` becomes a thin root wrapper.
- New `AppCoordinator.tsx` owns state/effects.
- New `AppView.tsx` owns rendering.
- Submit and conversation-loop logic are extracted into hooks.
- Existing behavior remains working before richer features land.

## Acceptance

- No large behavior regression in current chat/tool/approval flow.
- Files have clear responsibilities and can be tested independently.
- `bun run build` succeeds for Animus after the split.

## Activity Log

- 2026-06-26 18:51 MYT - Ticket created.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - backlog ticket only

