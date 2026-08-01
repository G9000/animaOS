# IL-015 - Durable client receipt for ambient dream surfacing

- Status: in_progress
- Priority: P3
- Scope: `apps/server/src/anima_server/services/agent/proactive.py`, `apps/server/src/anima_server/api/routes`, `apps/server/alembic_core`, `apps/desktop`
- Parent: none
- Depends on: `IL-010`
- Owner: Claude
- PRD: docs/prds/presence/inner-life-v1.md
- Spec: none
- Plan: none
- Created: 2026-07-31 13:46 MYT
- Updated: 2026-08-02 04:10 MYT
- Started: 2026-08-02 04:10 MYT
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

- 2026-08-02 04:10 MYT - Claimed and implemented on worktree branch
  `worktree-il-015-dream-receipt`. Design as filed: `claimed_at` is a
  claim state distinct from `surfaced`, so a greeting claims a dream but
  only an explicit client acknowledgement surfaces it; an unacknowledged
  claim expires after `dream_claim_ttl_minutes` (default 10) and the
  dream is offered again. Asymmetry is deliberate: a lost ack costs one
  repeat after the TTL, a lost expiry would cost permanent silence.
  Server: `dream_receipt.py` (offerable query, acknowledge, release),
  migration `20260802_0001`, `POST /chat/greeting/dream-ack`,
  `ambientDreamId` on the greeting response. Client: acks on DISPLAY —
  not on fetch or stash — in both the fresh-fetch and one-shot paths.

## Validation

- Commands:
  - `uv run pytest tests/test_inner_life_ambient_dream.py` — 28 passed
  - alembic `20260802_0001` up/down/up on temp SQLite — clean, single head
  - `bunx tsc --noEmit` (apps/desktop) — clean
  - Full suite (`bun run test`) — **3363 passed, 0 failed, 10 skipped**,
    run 2026-08-02 04:52 MYT
  - `bun test tests/` (apps/desktop) — 111 passed, 0 failed
- Changed paths:
  - `apps/server/src/anima_server/services/agent/inner_life/dream_receipt.py` (new)
  - `apps/server/src/anima_server/models/agent_runtime.py`
  - `apps/server/alembic_core/versions/20260802_0001_dream_claim_receipt.py` (new)
  - `apps/server/src/anima_server/services/agent/proactive.py`
  - `apps/server/src/anima_server/api/routes/chat.py`
  - `apps/server/src/anima_server/config.py`
  - `packages/api-client/src/client.ts`, `packages/api-client/src/types.ts`
  - `apps/desktop/src/pages/dashboard/Dashboard.tsx`
  - `apps/server/tests/test_inner_life_ambient_dream.py`
- Notes:
  - IL-010's accepted residual risk is now closed: a greeting whose response
    never reaches the browser leaves the claim to expire, and the dream is
    offered again instead of being lost.
