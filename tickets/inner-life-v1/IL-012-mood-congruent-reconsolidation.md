# IL-012 - Mood-congruent reconsolidation target

- Status: done
- Priority: P2
- Scope: `apps/server`
- Parent: none
- Depends on: IL-006
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

IL-006's recall nudge currently drifts a memory's `emotional_salience` toward
the *magnitude* of the current affect vector — `(|valence| + arousal) / 2` —
which is sign-blind: recalling a heavy memory during a strongly *positive*
state pulls its charge UP exactly as a distressed state would. That inverts
the dynamic the reconsolidation literature actually describes (and the one a
companion should embody): retrieval in a safe, settled state is precisely
what softens a charged memory; retrieval under distress is what sensitizes
it.

Make the reconsolidation target mood-congruent: **negative valence and
arousal contribute to the target charge; positive valence contributes
nothing.** Recalling while settled-and-positive therefore drifts charged
memories *down* (they get lighter), while distressed recall still drifts them
up. Everything else — η, the lifetime drift cap, provenance logging, exact
reconstructability, the identity exemption — is unchanged.

## Deliverables

- `services/agent/reconsolidation.py`: new pure
  `mood_congruent_magnitude(valence, arousal) = clamp01((max(0, -valence) + arousal) / 2)`
  used by `resolve_current_affect_magnitude`; the symmetric
  `affect_magnitude` retained only if another caller still needs it,
  otherwise removed.
- Tests: positive-settled recall softens a charged memory; negative-aroused
  recall amplifies as before; drift cap and provenance behavior unchanged;
  dream-path η (`DEFAULT_DREAM_ETA`) still bounded.

## Acceptance

- Sign matters: `target(+0.6, 0.1) = 0.05 < target(-0.6, 0.1) = 0.35` — for
  equal arousal, a positive state always yields a target no higher than the
  matching negative state.
- Flagship regression: a charged memory (salience 0.8) recalled during a
  joyful, excited state (valence +0.9, arousal 0.9) now *softens*
  (target 0.45 < 0.8); under the old symmetric target (0.9) it was amplified
  toward the ceiling.
- High arousal still charges regardless of valence sign (activation is real
  signal); only the valence contribution is one-sided.
- `original_salience_from_log` still reconstructs originals exactly.
- Full suite green.

## Activity Log

- 2026-07-29 14:14 MYT - Ticket created; implementation started on branch
  `il-011-013-inner-life-texture`.
- 2026-07-29 15:16 MYT - Implemented: `mood_congruent_magnitude` replaces the
  symmetric `affect_magnitude` (its only caller was
  `resolve_current_affect_magnitude`); η, drift cap, provenance, identity
  exemption untouched. Five new tests including the flagship
  joyful-recall-softens regression; downstream consumers
  (retrieval-feedback sync, dream edge) re-run green.

- 2026-07-29 15:58 MYT - PR #128 review round 1: status normalized to the
  legal lifecycle (done, Owner: Claude, Completed stamped), reparented as a
  standalone follow-up per the IL-009 precedent, PRD link corrected to the
  canonical docs/prds/presence/inner-life-v1.md.

- 2026-07-30 12:10 MYT - PR #128 review round 3: acceptance evidence completed in Validation — full-suite result (3163 passed / 0 failed on 43698cb) recorded.

## Validation

- Commands:
  - Full suite (`bun run test`) on `43698cb` — **3163 passed, 0 failed,
    10 skipped**
  - `uv run pytest tests/test_inner_life_reconsolidation.py` — 34 passed
  - `uv run pytest tests/test_retrieval_feedback.py tests/test_inner_life_dream_edge.py` — 36 passed
- Changed paths:
  - `apps/server/src/anima_server/services/agent/reconsolidation.py`
  - `apps/server/tests/test_inner_life_reconsolidation.py`
- Notes:
  - none
