# IL-001 - Affect state vector with decay-to-baseline dynamics

- Status: backlog
- Priority: P1
- Scope: `apps/server/src/anima_server/services/agent/inner_life`, `apps/server/src/anima_server/models/agent_runtime.py`
- Parent: `IL-000`
- Depends on: none
- Owner: unassigned
- PRD: docs/prds/presence/inner-life-v1.md
- Plan: docs/superpowers/plans/2026-07-15-inner-life-v1.md
- Created: 2026-07-15 16:55 MYT
- Updated: 2026-07-15 17:10 MYT
- Started:
- Completed:

## Goal

Persist a deterministic affect vector (valence, arousal, energy) with closed-form relaxation toward circadian-modulated baselines, updated by turn events and readable by prompt builders.

## Deliverables

- `inner_life/affect.py` pure update functions: turn-event deltas (clamped ±0.15), closed-form relaxation `b + (x−b)·exp(−Δt/τ)`, circadian baseline modulation, allostatic baseline shift.
- Persisted affect columns/table on agent runtime + migration.
- Wiring: emotional_intelligence signals apply deltas post-turn; `build_agent_state()` and greeting/notice tone render adjectives + trajectory, never raw numbers.
- Config for taus and baselines (defaults per PRD IL1).

## Acceptance

- Identical event sequences produce identical trajectories (property tests).
- Relaxation matches closed-form fixtures; all components clamped to bounds.
- State survives restart; no LLM calls anywhere in the update path.

## Activity Log

- 2026-07-15 16:55 MYT - Ticket created.

## Validation

- Commands:
  - `not run yet`
- Changed paths:
  - none
- Notes:
  - none
