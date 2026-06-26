# GWR-006 - Standardize webhook and third-party ingress contract

- Status: done
- Priority: P2
- Scope: server API
- Parent: `GWR-000`
- Depends on: `GWR-003`, `GWR-010`
- Owner: codex-agent
- PRD: none
- Plan: docs/superpowers/plans/2026-06-26-gateway-runtime-online-delivery.md
- Created: 2026-06-26 16:38 MYT
- Updated: 2026-06-27 05:12 MYT
- Started: 2026-06-27 05:12 MYT
- Completed: 2026-06-27 05:12 MYT

## Goal

Normalize third-party message ingress so external channels do not directly touch runtime internals.

## Deliverables

- `POST /api/webhook/{provider}` style entry contract
- Provider payload normalization
- Idempotency and signature verification boundary

## Acceptance

- External payloads are transformed into one internal request shape
- Duplicate webhook events are safely ignored
- Provider-specific parsing stays outside cognition services


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
