# GWR-004 - Add device enrollment and revocation API

- Status: backlog
- Priority: P1
- Scope: server API + auth
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

- 2026-06-26 16:38 MYT - Ticket created and normalized to the repo ticket workflow.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - backlog ticket only
