# GWR-009 - Add compatibility auth bridge for desktop

- Status: backlog
- Priority: P1
- Scope: server + desktop
- Parent: `GWR-000`
- Depends on: `GWR-003`
- Owner: unassigned
- PRD: none
- Plan: docs/superpowers/plans/2026-06-26-gateway-runtime-online-delivery.md
- Created: 2026-06-26 16:38 MYT
- Updated: 2026-06-26 16:57 MYT
- Started:
- Completed:

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

- 2026-06-26 16:38 MYT - Ticket created and normalized to the repo ticket workflow.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - backlog ticket only
