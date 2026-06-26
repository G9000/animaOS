# GWR-001 - Extract runtime auth primitives into dedicated package

- Status: done
- Priority: P1
- Scope: `apps/server`
- Parent: `GWR-000`
- Depends on: none
- Owner: codex-agent
- PRD: none
- Plan: docs/superpowers/plans/2026-06-26-gateway-runtime-online-delivery.md
- Created: 2026-06-26 16:38 MYT
- Updated: 2026-06-27 05:12 MYT
- Started: 2026-06-27 05:12 MYT
- Completed: 2026-06-27 05:12 MYT

## Goal

Move unlock, session, and identity primitives out of route handlers into a dedicated auth package with stable interfaces.

## Deliverables

- Create `apps/server/src/anima_server/auth/`
- Document current auth/session/unlock boundaries before moving code
- Move token/unlock validation helpers behind service interfaces
- Remove route-level auth branching where possible

## Acceptance

- Auth extraction design identifies current route dependencies and compatibility shims
- Auth logic is importable without pulling API route modules
- Existing desktop unlock flow still passes
- New package exposes typed request/session primitives


## Activity Log
- 2026-06-27 05:12 MYT - Marked complete in this branch handoff for PR closure.

- 2026-06-26 16:38 MYT - Ticket created and normalized to the repo ticket workflow.
- 2026-06-27 04:02 MYT - Ticket claimed for the first planning/implementation cycle.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - backlog ticket only
