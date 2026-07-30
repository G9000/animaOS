# IL-010 - Implement the "ambient" dream-sharing consumer

- Status: done
- Priority: P3
- Scope: `apps/server/src/anima_server/services/agent/proactive.py`, `apps/server/src/anima_server/services/agent/inner_life`, `apps/desktop/src/pages/Presence.tsx`
- Parent: none
- Depends on: `IL-007`, `IL-008`
- Owner: Claude
- PRD: docs/prds/presence/inner-life-v1.md
- Spec: none
- Plan: docs/superpowers/plans/2026-07-15-inner-life-v1.md
- Created: 2026-07-29 11:56 MYT
- Updated: 2026-07-30 16:44 MYT
- Started: 2026-07-30 16:27 MYT
- Completed: 2026-07-30 16:44 MYT

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

## Validation

- Commands:
  - `uv run pytest tests/test_inner_life_ambient_dream.py` — 10 passed
  - Full suite (`bun run test`) — **3176 passed, 0 failed, 10 skipped**,
    run 2026-07-30 17:11 MYT
- Changed paths:
  - `apps/server/src/anima_server/services/agent/proactive.py`
  - `apps/server/tests/test_inner_life_ambient_dream.py` (new)
  - `apps/desktop/src/pages/Presence.tsx`
- Notes:
  - The surfaced-mark commit lives inside the resolver (greeting sessions
    never commit otherwise); greeting paths are read-only apart from it.
