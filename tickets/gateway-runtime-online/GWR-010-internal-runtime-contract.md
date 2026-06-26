# GWR-010 - Add internal gateway-to-runtime contract

- Status: done
- Priority: P2
- Scope: gateway + runtime
- Parent: `GWR-000`
- Depends on: `GWR-002`
- Owner: codex-agent
- PRD: none
- Plan: docs/superpowers/plans/2026-06-26-gateway-runtime-online-delivery.md
- Created: 2026-06-26 16:38 MYT
- Updated: 2026-06-27 05:12 MYT
- Started: 2026-06-27 05:12 MYT
- Completed: 2026-06-27 05:12 MYT

## Goal

Define the internal contract the gateway uses to call runtime services once those layers are separated.

## Deliverables

- Typed request/response contract for chat and related runtime actions
- Error translation policy
- Timeout and retry rules

## Acceptance

- Gateway-runtime calls use a stable contract instead of route internals
- Runtime errors map cleanly to gateway responses
- Contract is documented and testable


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
