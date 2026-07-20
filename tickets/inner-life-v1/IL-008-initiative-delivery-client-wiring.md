# IL-008 - Wire push-initiative delivery into the client

- Status: backlog
- Priority: P2
- Scope: `packages/api-client`, `apps/desktop`, `apps/server/src/anima_server/services/agent/inner_life/delivery.py`
- Parent: `IL-000`
- Depends on: `IL-003`
- Owner: unassigned
- PRD: docs/prds/presence/inner-life-v1.md
- Plan: docs/superpowers/plans/2026-07-15-inner-life-v1.md
- Created: 2026-07-21 MYT
- Started:
- Completed:

## Goal

Make a fired push-initiative actually reach the user. IL-003 delivered the
server side: the `InitiativeDelivery` seam, the pollable `PendingInitiative`
default adapter, and the fetch/ack API route. It intentionally did NOT build
the client side (the brief scoped out `apps/desktop`/Tauri code, which had no
notification bridge). Until this ticket lands, an opted-in user gets rows in
`pending_initiatives` that nothing surfaces — the unprompted message never
appears.

This is not a correctness bug in IL-003: push initiative is off by default,
and when off nothing is ever created. It is the last-mile integration that
turns the shipped seam into a user-visible feature.

## Deliverables

- `packages/api-client` methods for the fetch + acknowledge pending-initiative
  endpoints exposed by `api/routes/presence.py`.
- A desktop poll/display path (`apps/desktop`) that surfaces a pending
  initiative and acknowledges it, OR a real `OSNotificationDelivery` adapter
  implementing the `InitiativeDelivery` seam via the Tauri notification shell.
- End-to-end coverage: opt in -> drive fires -> client receives and
  acknowledges -> `PendingInitiative` marked delivered/acknowledged.

## Acceptance

- An opted-in user actually receives a fired initiative through the shipped
  client (not only a `pending_initiatives` row).
- Acknowledgement round-trips and clears/marks the pending row.

## Context

Raised by Codex review on PR #115 (P2): "Wire the pollable delivery into the
supported client." Confirmed intentional deferral per the IL-003 brief; filed
here so it is tracked rather than lost.

## Activity Log

- 2026-07-21 MYT - Ticket created from PR #115 review feedback.

## Validation

- Commands:
  - `not run yet`
- Changed paths:
  - none
- Notes:
  - none
