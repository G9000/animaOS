# ACT-004 - Add rich transcript and tool rendering

- Status: backlog
- Priority: P1
- Scope: `apps/animus/src/ui`
- Parent: `ACT-000`
- Depends on: `ACT-003`
- Owner: unassigned
- PRD: docs/prds/animus/coding-tui-v1.md
- Plan: docs/superpowers/plans/2026-06-26-animus-coding-tui.md
- Created: 2026-06-26 18:51 MYT
- Updated: 2026-06-26 18:51 MYT
- Started:
- Completed:

## Goal

Replace the simple chat list with a normalized transcript and richer tool-call rendering suitable for coding sessions.

## Deliverables

- Transcript item model for user, assistant, reasoning, command, tool, approval, spawn, and error entries.
- Raw frame normalization.
- Static/committed transcript rendering.
- Specialized tool renderers for shell, file, search, todo, and spawn-related output.
- Output clipping for long tool results.

## Acceptance

- Streaming and committed output remain visually stable.
- Tool calls are readable without exposing huge raw JSON blocks.
- Long outputs are clipped with clear truncation markers.
- Normalization tests cover representative server frames.

## Activity Log

- 2026-06-26 18:51 MYT - Ticket created.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - backlog ticket only

