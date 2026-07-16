# IL-001 - Affect state vector with decay-to-baseline dynamics

- Status: done
- Priority: P1
- Scope: `apps/server/src/anima_server/services/agent/inner_life`, `apps/server/src/anima_server/models/runtime_consciousness.py`
- Parent: `IL-000`
- Depends on: none
- Owner: Claude
- PRD: docs/prds/presence/inner-life-v1.md
- Plan: docs/superpowers/plans/2026-07-15-inner-life-v1.md
- Created: 2026-07-15 16:55 MYT
- Updated: 2026-07-16 03:00 MYT
- Started: 2026-07-15 18:54 MYT
- Completed: 2026-07-15 19:55 MYT

## Goal

Persist a deterministic affect vector (valence, arousal, energy) with closed-form relaxation toward circadian-modulated baselines, updated by turn events and readable by prompt builders.

## Deliverables

- `inner_life/affect.py` pure update functions: turn-event deltas (clamped ±0.15), closed-form relaxation `b + (x−b)·exp(−Δt/τ)`, circadian baseline modulation, allostatic baseline shift.
- Persisted affect state as a `RuntimeBase` model (e.g. in `models/runtime_consciousness.py`) + alembic_runtime migration. Note: `models/agent_runtime.py` is the SQLCipher soul store despite its name — affect is rebuildable runtime state and must NOT go there or into vault export/import.
- Wiring: emotional_intelligence signals apply deltas post-turn; `build_agent_state()` and greeting/notice tone render adjectives + trajectory, never raw numbers.
- Config for taus and baselines (defaults per PRD IL1).

## Acceptance

- Identical event sequences produce identical trajectories (property tests).
- Relaxation matches closed-form fixtures; all components clamped to bounds.
- State survives restart; no LLM calls anywhere in the update path.

## Activity Log

- 2026-07-15 16:55 MYT - Ticket created.
- 2026-07-15 18:30 MYT - Rescoped affect model from agent_runtime.py (soul Base) to a RuntimeBase model per review — affect is rebuildable and must stay out of the vault.
- 2026-07-15 18:54 MYT - Started implementation on branch feature/il-001-affect-state-vector.
- 2026-07-15 19:55 MYT - Implemented affect vector (pure dynamics, RuntimeBase persistence, migration 027, consolidation/proactive wiring, 22 tests). Task review approved after one fix round (session isolation, energy rendering, allostatic recovery drain, first-read race).
- 2026-07-16 03:00 MYT - PR #98 review rounds (all findings valid, fixed): surfaced affect hint end-to-end + greeting tone; savepoint-scoped seed insert; tz-normalization on load; gated affect on accepted emotion signals; FOR UPDATE row lock on write path (incl. race fallback); exact closed-form circadian relaxation (replaces at-now approximation — tick-equivalence now holds with amplitude enabled); affect applied only after successful runtime commit (SQLite lock + batch-integrity semantics); api-client AgentStateData.affectHint sync; affect_state cleared on eval reset.

## Validation

- Commands:
  - `uv run --project apps/server pytest apps/server/tests/test_inner_life_affect.py` — 22/22 green
  - `bun run test` (full suite) — 2411 passed, 1 failed (test_dev_session_continuity::test_global_store_restores_snapshot_during_module_import, verified pre-existing by stash)
  - ruff clean
- Changed paths:
  - apps/server/alembic_runtime/versions/027_affect_state.py
  - apps/server/src/anima_server/config.py
  - apps/server/src/anima_server/models/__init__.py
  - apps/server/src/anima_server/models/runtime_consciousness.py
  - apps/server/src/anima_server/services/agent/consolidation.py
  - apps/server/src/anima_server/services/agent/inner_life/__init__.py
  - apps/server/src/anima_server/services/agent/inner_life/affect.py
  - apps/server/src/anima_server/services/agent/inner_life/store.py
  - apps/server/src/anima_server/services/agent/proactive.py
  - apps/server/tests/test_inner_life_affect.py
  - apps/server/src/anima_server/api/routes/consciousness.py
  - apps/server/src/anima_server/services/eval_reset.py
  - apps/server/tests/test_creation_flow.py
  - apps/server/tests/test_eval_harness.py
  - packages/api-client/src/types.ts
  - docs/prds/presence/inner-life-v1.md
  - tickets/inner-life-v1/IL-002-presence-tick-offline-catchup.md
- Notes:
  - update_allostatic_shift deliberately unwired; IL-002 presence tick is its caller (recorded in IL-002).
  - Circadian phase uses caller-supplied timezone (UTC at both wiring sites); true local-time resolution deferred to IL-002.
