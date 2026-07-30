# IL-011 - Return acknowledgment: grounded held-thought greeting + reconnect energy texture

- Status: done
- Priority: P2
- Scope: `apps/server`
- Parent: none
- Depends on: IL-002, IL-003
- Owner: Claude
- PRD: docs/prds/presence/inner-life-v1.md
- Spec: none
- Plan: none
- Created: 2026-07-29 14:14 MYT
- Updated: 2026-07-30 12:10 MYT
- Started: 2026-07-29 14:14 MYT
- Completed: 2026-07-29 15:58 MYT

Standalone follow-up beyond the closed Inner Life v1 scope — tracked in
`IL-000`'s "Follow-ups Beyond v1 Scope" section, not as a child of that
`done` parent (v1 acceptance is judged over its original child table).

## Goal

Make returning after a real absence feel different from returning after an
hour — in two small, strictly grounded ways:

1. **Held thought in the greeting.** When the user comes back after a gap and
   an open foresight thread genuinely accumulated `unresolved_thread` pressure
   while they were away, the greeting context may carry that one thread as a
   "held thought" — "this stayed on my mind" — grounded in the actual
   persisted ForesightSignal and the actual accumulated pressure. Never
   confabulated: no pressure or no open signal → no held thought. This is the
   greeting-context sibling of IL-003's initiative material rule (every
   sentence traces to specific material).
2. **Subdued reconnect energy.** A long gap (≥ 48h) leaves the IL1 energy
   component slightly lowered at catch-up time — a bounded "quiet after long
   silence" texture. Deliberately energy-only: valence is untouched, because a
   sadness/guilt reading ("I was sad you left") is a manipulative texture we
   explicitly do not want; "subdued" is not "hurt". The dip is small, capped,
   and relaxes away through the normal IL1 dynamics.

## Deliverables

- `services/agent/proactive.py`: `GreetingContext.held_thought` — populated
  only when (a) `home_greeting_context_enabled` is on, (b) the absence gap is
  at least `greeting_held_thought_min_gap_hours`, (c) the runtime
  `unresolved_thread` pressure is at or above a floor, and (d) an open
  in-horizon ForesightSignal exists (its decrypted content is the thought).
  Wired into the greeting prompt and the static fallback.
- `services/agent/inner_life/catchup.py`: gaps ≥ 48h apply
  `energy -= min(gap_days * reconnect_energy_dip_per_day, reconnect_energy_dip_cap)`
  (defaults 0.01/day, cap 0.06), floor-clamped, recorded in the audit row's
  `components`. Valence and arousal untouched.
- Config knobs with defaults; tests for both mechanisms including the
  never-confabulate and consent-gate negatives.

## Acceptance

- Greeting context contains the held thought exactly when all four conditions
  hold; flipping any single one off yields no held thought.
- The held thought is verbatim-traceable to a persisted ForesightSignal row
  (decrypted via the standard field-crypto path), never synthesized.
- A 72h gap lowers energy by a bounded amount; a 4h gap doesn't; valence is
  bit-identical before/after the dip is applied.
- Full suite green.

## Activity Log

- 2026-07-29 14:14 MYT - Ticket created; implementation started on branch
  `il-011-013-inner-life-texture`.
- 2026-07-29 15:16 MYT - Implemented. Held thought: `GreetingContext.held_thought`
  resolved by `_resolve_held_thought` (consent gate -> gap floor -> pressure
  floor -> open in-horizon foresight, reusing IL3's single definition), DEK
  gate before the decrypted read, woven into the greeting LLM prompt with a
  no-invention instruction and into the static fallback. Reconnect texture:
  `catchup.reconnect_energy_dip` (48h floor, 0.01/day, 0.06 cap) applied
  after closed-form relaxation, energy-only, audit `components` records
  `reconnect_energy`. Equivalence tests updated to assert the dip as an
  explicit divergence.

- 2026-07-29 15:58 MYT - PR #128 review round 1: status normalized to the
  legal lifecycle (done, Owner: Claude, Completed stamped), reparented as a
  standalone follow-up per the IL-009 precedent, PRD link corrected to the
  canonical docs/prds/presence/inner-life-v1.md.
- 2026-07-29 16:48 MYT - PR #128 review round 2, two P2 grounding fixes in
  `_resolve_held_thought`: (1) stale-pressure guard — pressure counts only
  if the drive tick has processed the user's latest message
  (`last_user_turn_at >= last_message_at`), otherwise it's a pre-turn
  leftover the turn-reset hasn't reached; (2) the foresight horizon is now
  evaluated on the LOCAL calendar date (system zone, `tz` test seam),
  matching the tick's local-time discipline. Two regression tests.

- 2026-07-30 12:10 MYT - PR #128 review round 3: acceptance evidence completed in Validation — full-suite result (3163 passed / 0 failed on 43698cb) recorded.

## Validation

- Commands:
  - Full suite (`bun run test`) on `43698cb` — **3163 passed, 0 failed,
    10 skipped**
  - `uv run pytest tests/test_inner_life_held_thought.py` — 12 passed
  - `uv run pytest tests/test_inner_life_presence.py` — 28 passed
- Changed paths:
  - `apps/server/src/anima_server/services/agent/proactive.py`
  - `apps/server/src/anima_server/services/agent/inner_life/catchup.py`
  - `apps/server/src/anima_server/config.py`
  - `apps/server/tests/test_inner_life_held_thought.py` (new)
  - `apps/server/tests/test_inner_life_presence.py`
- Notes:
  - The dip applies at offline catch-up only (the absence handler); a gap
    spent with the server running is untouched by design — the presence tick
    keeps exact closed-form relaxation.
