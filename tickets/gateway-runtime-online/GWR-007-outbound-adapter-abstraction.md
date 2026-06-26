# GWR-007 - Add outbound adapter abstraction

- Status: backlog
- Priority: P2
- Scope: `apps/anima-mod` + adapters
- Parent: `GWR-000`
- Depends on: `GWR-006`
- Owner: unassigned
- PRD: none
- Plan: docs/superpowers/plans/2026-06-26-gateway-runtime-online-delivery.md
- Created: 2026-06-26 16:38 MYT
- Updated: 2026-06-26 16:38 MYT
- Started:
- Completed:

## Goal

Create a thin adapter layer for sending normalized runtime outputs back to external channels.

## Deliverables

- Adapter interface for outbound messages
- Mapping from runtime outputs to provider payloads
- Separation between cognition output and transport formatting

## Acceptance

- Runtime services emit normalized outputs
- Provider adapters own transport-specific formatting
- Adding a new channel does not require editing cognition logic


## Activity Log

- 2026-06-26 16:38 MYT - Ticket created and normalized to the repo ticket workflow.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - backlog ticket only
