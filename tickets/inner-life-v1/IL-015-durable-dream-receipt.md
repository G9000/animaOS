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
- Updated: 2026-08-02 16:06 MYT
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

- 2026-08-02 13:27 MYT - PR #135 review round 1 (P1, regression introduced by this
  ticket): making the greeting path claim-without-surfacing left IL-003's
  three dream_residue paths still selecting on `surfaced` alone, so an
  initiative tick overlapping an unacknowledged greeting could voice the
  SAME dream through a second channel — duplicate disclosure of intimate
  content. All three (grow signal, material, post-fire marking) now use
  `offerable_dream_query`, so a live claim is invisible to initiatives
  while an EXPIRED claim is re-admitted (a greeting that never landed
  doesn't cost the dream). The initiative marking path sets `surfaced`
  directly and clears any claim — initiative delivery is confirmed by
  the PendingInitiative poll/ack, so it needs no second receipt.

- 2026-08-02 14:04 MYT - PR #135 review round 2 (P1, Codex, again a REAL regression from
  this ticket): the Dashboard's one-shot handoff stores a dream-bearing
  greeting whose fetch resolved after unmount, and that stored copy had no
  lifetime. Under IL-010 that was safe (the dream was already consumed);
  under IL-015 it is not — the claim expires, the dream becomes offerable
  again, and returning to the Dashboard hours later would voice the stored
  copy on top of whatever channel already spoke it. The greeting response
  now carries `ambientDreamExpiresAt` (`claimed_at + dream_claim_ttl_minutes`,
  stated by the server because the TTL is server config); the stash records
  it, prunes expired entries from sessionStorage on every read, refuses a
  response that arrives already stale, and the mount falls through to a
  fresh greeting when the stash died. A live response whose claim expired
  during a very slow request degrades to the dream-free `handoffMessage`
  rather than voicing it. Boundary verified in both directions: the client
  stops showing its copy no LATER than the server starts re-offering.

- 2026-08-02 14:37 MYT - PR #135 review round 3 (P1, Codex): round 2 made the client
  decide "is this dream still mine to voice?" by comparing a server
  timestamp against the DEVICE clock. That is check-then-act against a clock
  the server does not control — a skewed device or a render delayed past the
  deadline concludes "still mine" after the claim lapsed and another channel
  took the dream. The ack was also unscoped, so a stale client could mark the
  dream surfaced and clear a NEWER greeting's claim mid-disclosure. Both are
  now claim-scoped: `claimed_at` doubles as a claim-generation token, the
  greeting hands it to the client (`ambientDreamClaimToken`), and
  `POST /chat/greeting/dream-claim` re-asserts it atomically immediately
  before voicing — renewing the claim rather than surfacing it, so a client
  that dies before painting still loses nothing. Any uncertain answer (no
  token, locally expired, refused, request failed) voices the dream-free
  copy; a missing `handoffMessage` blanks the greeting rather than leaking
  the sentence. `acknowledge_dream` now requires the token too, so a
  superseded ack is a no-op that leaves the live claim intact. The local
  expiry check stays as a cheap pre-filter and as storage hygiene, but it is
  no longer load-bearing for correctness.

- 2026-08-02 15:19 MYT - PR #135 review round 4 (two P1s, Codex, both real):
  (1) The new confirm endpoint proved OWNERSHIP of the claim but not
  CONTINUING consent. A claim is taken when the greeting is generated —
  for a stashed greeting, up to a whole TTL earlier — so an opt-out in that
  window would still be answered with a dream. `confirm_claim` now re-reads
  the presence config under the same per-user `presence_consent_lock` the
  config PUT holds through its commit, and refuses unless the master switch
  is on and sharing is still `ambient`. The claim is deliberately NOT
  released on refusal: clearing it unconditionally would also clear a newer
  greeting's claim (the round-3 bug), and an unvoiced claim lapses within
  the TTL anyway.
  (2) Acknowledgement fired from the fetch handler, right after `setBrief`.
  Both dashboard surfaces that render the greeting (`profile`, `greeting`)
  can be CLOSED by the user, and closed nodes are filtered out of the graph
  — so a dream could be marked surfaced forever while nothing on screen ever
  voiced it, which is exactly the loss IL-015 exists to prevent. The receipt
  is now reported by whichever node actually rendered the dream, from an
  effect that runs after the commit, and deduped by claim generation
  (`dreamReceiptKey` = `dreamId:claimToken`) since both nodes report and
  effects re-run. A failed ack releases the dedupe so a later render retries.

- 2026-08-02 15:46 MYT - PR #135 review round 5 (two P1s, Codex, both real, both
  client-side):
  (1) A React effect runs after the commit even when the window is in the
  background, so "mounted" is not "seen": a Dashboard that mounted hidden
  and was closed before ever being looked at still acknowledged the dream.
  Receipts are now gated on `document.visibilityState` — reported
  immediately when visible, otherwise deferred to the next
  `visibilitychange`, and never reported if the page is never shown. A
  shared `useDreamShownReceipt` hook carries the rule for both nodes.
  (2) The failed-ack path deleted the dedupe entry and stopped. Nothing
  re-renders on that deletion, so a transient network failure left a
  DISPLAYED dream unacknowledged; the claim then lapsed and the same
  narrative could be disclosed again. `deliverDreamReceipt` now retries on a
  bounded backoff (2s/6s/20s/60s), refuses to attempt past the claim
  deadline, and treats `acknowledged: false` as definitive rather than
  retryable.

- 2026-08-02 16:06 MYT - PR #135 review round 6 (P1, Codex — the residual I had flagged
  in round 5 and chosen to leave open, correctly pushed back on): deferring
  the receipt to the next reveal was not enough. A window hidden past the
  claim deadline still had the stale dream RENDERED, so revealing it put the
  text on screen while `deliverDreamReceipt` declined to ack an expired
  claim — leaving the row unsurfaced and offerable, i.e. a guaranteed later
  duplicate. Fixed by withholding rather than hiding after the fact: a dream
  is only ever rendered while the page is visible AND its claim is live
  (`displayableGreeting`), so nothing dream-bearing is painted into a window
  the user cannot see and there is no stale frame to expose. On reveal, a
  lapsed claim is re-confirmed first and a refusal strips the dream from the
  greeting for good. The receipt path reads the DISPLAYED greeting, so a
  withheld dream is never acknowledged either.

## Validation

- Commands:
  - `pytest tests/test_inner_life_ambient_dream.py` — 42 passed (2026-08-02 15:19 MYT)
  - alembic `20260802_0001` up/down/up on temp SQLite — clean, single head
  - `bunx tsc --noEmit` (apps/desktop) — clean
  - Full server suite (`pytest tests/ -p no:randomly`) — **3374 passed,
    0 failed, 2 skipped, 11 deselected**, re-run 2026-08-02 16:02 MYT after
    the round-5/6 desktop-only changes
  - `bun test tests/` (apps/desktop) — 138 passed, 0 failed (2026-08-02 16:06 MYT)
- Changed paths:
  - `apps/server/src/anima_server/services/agent/inner_life/dream_receipt.py` (new)
  - `apps/server/src/anima_server/models/agent_runtime.py`
  - `apps/server/alembic_core/versions/20260802_0001_dream_claim_receipt.py` (new)
  - `apps/server/src/anima_server/services/agent/proactive.py`
  - `apps/server/src/anima_server/services/agent/inner_life/initiative.py`
  - `apps/server/src/anima_server/api/routes/chat.py`
  - `apps/server/src/anima_server/config.py`
  - `packages/api-client/src/client.ts`, `packages/api-client/src/types.ts`
  - `apps/desktop/src/pages/dashboard/Dashboard.tsx`
  - `apps/desktop/src/lib/greetingCache.ts`
  - `apps/desktop/src/pages/dashboard/layout.ts`
  - `apps/desktop/src/pages/dashboard/nodes/node-types.ts`
  - `apps/desktop/src/pages/dashboard/nodes/GreetingNode.tsx`
  - `apps/desktop/src/pages/dashboard/nodes/ProfileNode.tsx`
  - `apps/desktop/src/pages/dashboard/nodes/useDreamShownReceipt.ts` (new)
  - `apps/desktop/src/hooks/usePageVisible.ts` (new)
  - `apps/desktop/tests/greetingCache.test.ts`
  - `apps/server/tests/test_inner_life_ambient_dream.py`
- Notes:
  - IL-010's accepted residual risk is now closed: a greeting whose response
    never reaches the browser leaves the claim to expire, and the dream is
    offered again instead of being lost.
