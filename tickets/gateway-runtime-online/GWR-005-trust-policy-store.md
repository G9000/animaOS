# GWR-005 - Centralize trust policy and nonce store

- Status: backlog
- Priority: P1
- Scope: auth + infra
- Parent: `GWR-000`
- Depends on: `GWR-003`
- Owner: unassigned
- PRD: none
- Plan: docs/superpowers/plans/2026-06-26-gateway-runtime-online-delivery.md
- Created: 2026-06-26 16:38 MYT
- Updated: 2026-06-26 16:38 MYT
- Started:
- Completed:

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

- 2026-06-26 16:38 MYT - Ticket created and normalized to the repo ticket workflow.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - backlog ticket only
