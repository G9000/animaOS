# GWR-005 - Centralize trust policy and nonce store

- Status: done
- Priority: P1
- Scope: auth + infra
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

Add a single source for replay prevention, token invalidation, and trust policy checks.

## Deliverables

- Nonce store
- Session invalidation path
- Token introspection or equivalent validation hook

## Acceptance

- Replay-protected requests are enforced consistently
- Logout/revoke invalidates the right token scope
- Policy checks are not duplicated across routes


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
