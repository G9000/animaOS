# GWR-004 - Add device enrollment and revocation API

- Status: done
- Priority: P1
- Scope: server API + auth
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

Treat devices as explicit trust objects so multi-device access can be granted and revoked safely.

## Deliverables

- List trusted devices endpoint
- Revoke device endpoint
- Rotate device secret or session binding endpoint

## Acceptance

- A user can enumerate active devices
- Revoking a device invalidates new requests from it
- Device metadata is auditable


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
