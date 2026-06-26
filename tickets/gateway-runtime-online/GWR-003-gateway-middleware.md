# GWR-003 - Add auth and rate middleware in API layer

- Status: backlog
- Priority: P1
- Scope: server API ingress
- Parent: `GWR-000`
- Depends on: `GWR-002`, `GWR-010`
- Owner: unassigned
- PRD: none
- Plan: docs/superpowers/plans/2026-06-26-gateway-runtime-online-delivery.md
- Created: 2026-06-26 16:38 MYT
- Updated: 2026-06-26 16:57 MYT
- Started:
- Completed:

## Goal

Centralize request validation, replay protection, and rate enforcement before requests reach runtime services.

## Deliverables

- Auth middleware for unlock or bearer session validation
- Nonce/request-id validation
- Basic rate limiting at ingress

## Acceptance

- Protected endpoints pass through one middleware chain
- Duplicate/replayed requests are rejected
- Failed auth does not invoke runtime services


## Activity Log

- 2026-06-26 16:38 MYT - Ticket created and normalized to the repo ticket workflow.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - backlog ticket only
