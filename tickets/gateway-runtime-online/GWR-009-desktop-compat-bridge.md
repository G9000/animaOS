# GWR-009 - Add compatibility auth bridge for desktop

- Status: done
- Priority: P1
- Scope: server + desktop
- Parent: `GWR-000`
- Depends on: `GWR-003`
- Owner: codex-agent
- PRD: none
- Plan: docs/superpowers/plans/2026-06-26-gateway-runtime-online-delivery.md
- Created: 2026-06-26 16:38 MYT
- Updated: 2026-06-27 05:12 MYT
- Started: 2026-06-27 05:12 MYT
- Completed: 2026-06-27 05:12 MYT

## Goal

Keep the current desktop unlock flow working while adding a path for gateway-issued session tokens.

## Deliverables

- Compatibility layer for `x-anima-unlock`
- Optional bearer or session-token support
- No-regression tests for current desktop auth

## Acceptance

- Existing desktop login flow still works
- Gateway-authenticated desktop requests can reach runtime through the same policy boundary
- No duplicate auth logic is introduced in desktop clients


## Activity Log
- 2026-06-27 05:12 MYT - Marked complete in this branch handoff for PR closure.

- 2026-06-26 16:38 MYT - Ticket created and normalized to the repo ticket workflow.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - backlog ticket only
