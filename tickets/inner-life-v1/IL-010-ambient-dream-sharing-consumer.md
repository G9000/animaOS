# IL-010 - Implement the "ambient" dream-sharing consumer

- Status: in_progress
- Priority: P3
- Scope: `apps/server/src/anima_server/services/agent/proactive.py`, `apps/server/src/anima_server/services/agent/inner_life`, `apps/desktop/src/pages/Presence.tsx`
- Parent: none
- Depends on: `IL-007`, `IL-008`
- Owner: Claude
- PRD: docs/prds/presence/inner-life-v1.md
- Spec: none
- Plan: docs/superpowers/plans/2026-07-15-inner-life-v1.md
- Created: 2026-07-29 11:56 MYT
- Updated: 2026-07-31 13:46 MYT
- Started: 2026-07-30 16:27 MYT
- Completed:

## Goal

Give `presence_config.dream_sharing = "ambient"` real behavior. The IL-007 PRD
defines it as "the companion may weave a dream into greetings", but the only
server-side consumer today distinguishes `"off"` from non-off (the IL3
`dream_residue` gate) — `"ambient"` currently behaves identically to
`"on_ask"`. PR #123 review flagged offering a no-op mode in the desktop UI, so
the option was removed from the Presence selector pending this ticket (the
backend contract still accepts and round-trips the value).

## Deliverables

- A greeting/ambient consumer: when `dream_sharing == "ambient"`, the
  greeting/proactive context path (`proactive.py` / `build_agent_state()`
  ambient line) may reference the most recent share-worthy, unsurfaced
  `dream_journal` entry (marking it surfaced when actually voiced).
- `"on_ask"` remains ask-or-IL3-fire only; `"off"` remains fully suppressed
  (both already enforced).
- Re-add "Ambient" to the desktop Presence selector once the consumer exists.
- Tests: ambient weaves at most one dream reference per greeting, marks the
  dream surfaced, and never triggers when the mode is `on_ask`/`off`.

## Acceptance

- With `dream_sharing="ambient"` and a share-worthy unsurfaced dream, a
  greeting can carry a dream reference; the referenced dream stops re-raising
  `dream_residue` (surfaced).
- With `on_ask`/`off`, greeting output is unchanged (tests).
- The desktop selector offers Ambient again, wired to the working mode.

## Activity Log

- 2026-07-29 11:56 MYT - Ticket created from PR #123 review (P2: "Implement
  Ambient before offering it"); the Presence selector's Ambient option was
  removed in the same commit pending this consumer.

- 2026-07-30 16:27 MYT - Claimed and started by Claude (backlog ->
  in_progress -> done in one branch: `il-010-ambient-dream-consumer`).
- 2026-07-30 16:44 MYT - Implemented: `proactive._resolve_ambient_dream`
  (consent: master switch AND dream_sharing == "ambient"; DEK gate; most
  recent share-worthy unsurfaced dream_journal row; consume-once — marked
  surfaced and COMMITTED on hand-off so a dream is never voiced twice and
  stops re-raising dream_residue). Woven into the greeting LLM prompt
  (pinned to the stated narrative, framed AS a dream) and the static
  fallback. on_ask/off untouched (tested). Ambient option restored in the
  desktop Presence selector. 10 tests in
  `tests/test_inner_life_ambient_dream.py`.

- 2026-07-30 18:47 MYT - PR #130 review round 1 (P1 + 2 P2), completion
  re-stamped after acceptance-affecting fixes: (1) consumption moved OUT
  of gather_greeting_context — that gatherer is shared with agent-state
  and reflection paths that never render the dream, so ambient dreams
  were burned invisibly; generate_greeting is now the only consumer.
  (2) Voicing is deterministic: the claimed dream's sentence (single
  source, `_ambient_dream_sentence`) is appended to the LLM greeting and
  rendered by the static paths — never entrusted to the model's
  discretion (the old prompt said "may mention"). (3) The claim is an
  atomic conditional update (WHERE surfaced = 0, rowcount checked) — a
  rival claiming between select and update leaves the loser silent.
  Three regression tests incl. an interleaved-rival race.

- 2026-07-30 20:55 MYT - PR #130 review round 2 (2 P2s), completion
  re-stamped — durable client handoff: the greeting response now carries
  `ambientDream: true` when it voices a consumed dream, and the
  Dashboard treats such greetings as ONE-SHOT: (1) never cached, so the
  5-minute session cache can't replay the same surfaced dream on every
  remount; (2) a fetch that resolves after the Dashboard unmounted
  stashes the dream-bearing message in a one-shot slot the next mount
  takes exactly once — the consumed dream is displayed, not discarded.
  Cache helpers extracted to `lib/greetingCache.ts` with 5 bun tests.

- 2026-07-30 23:53 MYT - PR #130 review round 3 (P1 + 2 P2), completion
  re-stamped: (1) the one-shot replay now re-checks consent against a
  FRESH presence config before displaying — an opt-out between the stash
  and the next mount wins and the stash is discarded (unknown consent
  prefers silence); (2) ambient surfacing drains the runtime
  dream_residue pressure and its starvation history exactly like the
  initiative fire path, so voiced-dream pressure can't transfer to the
  next unrelated dream; (3) the one-shot slot became a FIFO QUEUE —
  concurrent in-flight consumptions each survive to a later mount
  instead of the second overwriting the first. Tests: 14 server + 8
  cache (queue FIFO, peek-without-consume, consent gate).

- 2026-07-31 03:52 MYT - PR #130 review round 4 (2 P2s), completion re-stamped:
  (1) the claim is now a SINGLE conditional UPDATE (candidate folded in
  as a scalar subquery, RETURNING the narrative) issued after ending the
  consent read's transaction — under WAL, the old select-then-update
  raised SQLITE_BUSY_SNAPSHOT on the losing connection instead of
  rowcount 0; a residual lock race maps to silence via OperationalError.
  (2) the Dashboard dequeues a stashed greeting only while MOUNTED and
  after consent verifies — an unmount mid-check leaves the queue intact
  (it holds the only durable copy), withdrawn consent clears it, and
  unknown consent keeps it for a mount that can verify.

- 2026-07-31 13:46 MYT - PR #130 review round 5: ticket REOPENED to in_progress and
  `Completed` cleared (last provisional completion was 2026-07-31 03:52 MYT) — per
  the tracked-work review workflow a child stays open until review
  establishes a clean implementation head, with metadata closeout
  afterwards; the earlier per-round re-stamping recorded acceptance
  while actionable defects were still open. Round-5 fixes: the dream
  claim now holds the per-user presence_consent_lock from a FRESH
  consent re-read through the claim commit (the unlocked pre-check
  could otherwise authorize voicing after an opt-out committed);
  durable client receipt is deferred to IL-015 with the rationale
  recorded there and in the PR thread.

## Validation

- Commands:
  - `uv run pytest tests/test_inner_life_ambient_dream.py` — 15 passed
  - Full suite on the round-5 head — **3181 passed, 0 failed, 10
    skipped**, run 2026-07-31 14:16 MYT
- Changed paths:
  - `apps/server/src/anima_server/services/agent/proactive.py`
  - `apps/server/src/anima_server/api/routes/chat.py`
  - `packages/api-client/src/types.ts`
  - `apps/desktop/src/lib/greetingCache.ts` (new)
  - `apps/desktop/src/pages/dashboard/Dashboard.tsx`
  - `apps/desktop/tests/greetingCache.test.ts` (new)
  - `apps/server/tests/test_inner_life_ambient_dream.py` (new)
  - `apps/desktop/src/pages/Presence.tsx`
- Notes:
  - Residual risk (accepted, tracked as `IL-015`): a dream claimed for a
    greeting whose HTTP response never reaches the browser (reload, tab
    close, dropped connection) stays surfaced without being voiced. The
    conservative direction — silence rather than re-voicing — is deliberate;
    a stronger guarantee needs a claim/ack protocol (schema + endpoint).
  - The surfaced-mark commit lives inside the resolver (greeting sessions
    never commit otherwise); greeting paths are read-only apart from it.
