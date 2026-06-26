# ACT-002 - Define source adaptation boundary and license notes

- Status: backlog
- Priority: P1
- Scope: `apps/animus`
- Parent: `ACT-000`
- Depends on: none
- Owner: unassigned
- PRD: docs/prds/animus/coding-tui-v1.md
- Plan: docs/superpowers/plans/2026-06-26-animus-coding-tui.md
- Created: 2026-06-26 18:51 MYT
- Updated: 2026-06-26 18:51 MYT
- Started:
- Completed:

## Goal

Create a clean source-adaptation boundary so portable coding UI patterns can be reused without copying reference backend semantics or brand assets.

## Deliverables

- `apps/animus/NOTICE.md` or equivalent source note.
- List of copied/adapted/reference-only upstream UI files.
- No upstream brand names, logos, ASCII art, or cloud/ADE copy in Animus.
- Clear rule that ANIMA backend/memory/runtime remains authoritative.

## Acceptance

- Adapted files are traceable to Apache-2.0-compatible source where needed.
- Brand-excluded assets are not present.
- Upstream-specific backend/client imports are not introduced into Animus.
- Future implementers know what can be copied and what must be rewritten.

## Activity Log

- 2026-06-26 18:51 MYT - Ticket created.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - backlog ticket only
