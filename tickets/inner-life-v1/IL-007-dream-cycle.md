# IL-007 - Dream cycle (F5 extension)

- Status: in_progress
- Priority: P2
- Scope: `apps/server/src/anima_server/services/agent/sleep_agent.py`, `apps/server/src/anima_server/services/agent/inner_life`, `apps/server/src/anima_server/models`
- Parent: `IL-000`
- Depends on: `IL-001`, `IL-002`, `IL-006`
- Owner: Claude
- PRD: docs/prds/presence/inner-life-v1.md
- Plan: docs/superpowers/plans/2026-07-15-inner-life-v1.md
- Created: 2026-07-15 16:55 MYT
- Updated: 2026-07-21 MYT
- Started: 2026-07-21 MYT
- Completed:

## Goal

Add an idle-time dream cycle: during long-idle night windows, recombine important-but-cold memories and latent traces into a dream journal entry that feeds affect, initiative pressure, and optional "while you were away" surfacing.

## Deliverables

- Eligibility check on presence tick: idle ≥ 4 h, 00:00–06:00 local, ≤ 1 dream/night.
- Material selection: K = 3 items by `significance × coldness` where `coldness = 1 − rank_normalized(heat)` (raw F2 heat is unbounded), latent traces > 0.5 weight, one random old transcript fragment.
- Single extraction-model reflection pass producing a dream narrative; affect deltas at 25 % turn strength; touched memories reconsolidated at η = 0.02.
- `dream_journal` table (narrative, source refs, affect delta, timestamp), rolling cap 30, soul-store scoped.
- Share-worthy flagging raising `dream_residue` (IL-003); `presence_config.dream_sharing` gate (`off | on_ask | ambient`, default `on_ask`).

## Acceptance

- Never runs during an active session; nightly cap enforced.
- Journal entries carry full source provenance; identity-class content untouched.
- Affect deltas respect the 25 % scale (tests).

## Activity Log

- 2026-07-15 16:55 MYT - Ticket created.
- 2026-07-15 17:25 MYT - Dream sampling now uses rank-normalized coldness per review (raw heat exceeds 1, so `1 − heat` can go negative).

## Validation

- Commands:
  - `not run yet`
- Changed paths:
  - none
- Notes:
  - none
