# IL-015 - Durable client receipt for ambient dream surfacing

- Status: backlog
- Priority: P3
- Scope: `apps/server/src/anima_server/services/agent/proactive.py`, `apps/server/src/anima_server/api/routes`, `apps/server/alembic_core`, `apps/desktop`
- Parent: none
- Depends on: `IL-010`
- Owner: unassigned
- PRD: docs/prds/presence/inner-life-v1.md
- Spec: none
- Plan: none
- Created: 2026-07-31 13:46 MYT
- Updated: 2026-07-31 13:46 MYT
- Started:
- Completed:

Standalone follow-up beyond the closed Inner Life v1 scope — tracked in
`IL-000`'s "Follow-ups Beyond v1 Scope" section.

## Goal

IL-010 marks an ambient dream `surfaced` when it is claimed for a greeting,
before the HTTP response reaches the browser. If the page reloads, the tab
closes, or the connection drops in that window, the dream is consumed but
never voiced (raised on PR #130 review).

IL-010 accepted that deliberately: the alternative failure mode — re-serving
a dream that WAS displayed — breaks the consume-once promise and repeats
intimate content at the user, which is worse than occasional silence. Dreams
also recur nightly and the journal entry itself is never lost, only its
"unvoiced" flag.

Closing the gap properly needs a claim/acknowledge protocol, which is why it
is its own ticket:

- a claim state distinct from `surfaced` (schema + `alembic_core` migration),
  so an unacknowledged claim can expire and be re-offered;
- the greeting response carrying the dream id;
- an ack endpoint the client calls once it has durably rendered or stashed
  the greeting (mirroring IL-003's `PendingInitiative` deliver/ack pattern,
  which solved exactly this problem for initiatives);
- expiry semantics that re-offer only genuinely undelivered claims.

## Deliverables

- Design decision on claim expiry (how long an unacked claim is withheld
  before being re-offered) with the double-voicing risk explicitly bounded.
- Schema + migration, ack endpoint, client ack call, and tests covering the
  reload/tab-close/dropped-connection paths.

## Acceptance

- A greeting whose response never reaches the client leaves the dream
  available for a later greeting.
- A displayed dream is never voiced twice.

## Activity Log

- 2026-07-31 13:46 MYT - Filed from PR #130 review (P2 "Acknowledge dreams only after a
  durable client receipt"). IL-010 records the residual risk and this ticket
  carries the fix; the interim behavior is deliberately biased toward silence.

## Validation

- Commands:
  - `not run yet`
- Changed paths:
  - none
- Notes:
  - none
