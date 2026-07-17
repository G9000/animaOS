# IL-002 - Presence tick loop and offline catch-up

- Status: done
- Priority: P1
- Scope: `apps/server/src/anima_server/main.py`, `apps/server/src/anima_server/services/agent/inner_life`
- Parent: `IL-000`
- Depends on: `IL-001`
- Owner: Claude
- PRD: docs/prds/presence/inner-life-v1.md
- Plan: docs/superpowers/plans/2026-07-15-inner-life-v1.md
- Created: 2026-07-15 16:55 MYT
- Updated: 2026-07-16 13:30 MYT
- Started: 2026-07-16 11:42 MYT
- Completed: 2026-07-16 13:30 MYT

## Goal

Run inner-life dynamics on a 60 s background tick and apply the entire offline gap in closed form on startup, so state is continuous across restarts and absence.

## Deliverables

- `_periodic_presence_tick()` co-scheduled with existing sweeps: affect relaxation + allostatic accumulation (`update_allostatic_shift` from IL-001 — implemented and tested there but deliberately unwired until this tick; this ticket is its first caller). Pressure accumulation (IL-003) and dream eligibility checks (IL-007) do not exist yet — per IL-002's scope boundaries the tick composes only what IL-001 shipped; no stubs were added for either, to avoid churn when those tickets land.
- Startup catch-up: O(1) closed-form application of the gap (affect + allostatic only), no tick replay and no inline dream passes; if the gap held ≥ 1 eligible night window (≥ 4h idle inside any local 00:00–06:00 span), sets a `dream_deferred` marker on the audit row — a data flag only, for IL-007 to consume later, at most one per catch-up regardless of gap length.
- `presence_catchup` audit row per catch-up (`gap_seconds`, `components`, `dream_deferred`); new runtime migration `028_presence_catchup` (head, chained from `027_affect_state`).
- Closed IL-001's deferred "true local-time resolution" item: the tick/catch-up wiring re-views the stored (UTC) `updated_at` in the same tz as the local `now` before calling `relax` (via `presence.to_local_view`/`to_utc_view`), so circadian phase reads off local hour-of-day, not UTC hour-of-day — without changing `affect.py`'s semantics.

## Acceptance

- Simulated 3-week gap equals 30,240 individual ticks within float tolerance for closed-form state (threshold-indicator state — the allostatic accumulator — matches within one tick quantum, as it is quantization-dependent by construction; dream effects excluded by design) (tests — `test_catchup_equivalence_over_three_weeks` from arousal 0.9 exercising the threshold crossing, `test_catchup_equivalence_below_threshold_regime` at 1e-6 for the pure-exponential regime).
- Catch-up completes < 50 ms, zero LLM calls, and generates no behavioral output (measured ~11 ms for 20 users with a 21-day gap each on SQLite; source-level no-LLM assertions in the test suite).
- A multi-night gap defers exactly one catch-up dream marker (test — `test_catchup_writes_one_audit_row_with_correct_gap_and_dream_deferred`); a no-night gap leaves it `False` (test).
- Tick loop is skipped cleanly while a turn is in flight: a user with an active `RuntimeThread` and `last_message_at` within `presence_active_window_seconds` (120 s default) is skipped entirely, not merely lock-avoided (test — `test_active_user_is_skipped`).

## Activity Log

- 2026-07-15 16:55 MYT - Ticket created.
- 2026-07-15 17:55 MYT - Catch-up no longer evaluates dream windows inline per review (O(1) conflict); defers at most one catch-up dream.
- 2026-07-15 19:20 MYT - IL-001 review: allostatic update is implemented in inner_life/affect.py but unwired by design; this tick is its caller.
- 2026-07-16 11:42 MYT - Started implementation on branch feature/il-002-presence-tick.
- 2026-07-16 13:30 MYT - Implemented `presence.py` (tick) and `catchup.py` (offline catch-up), migration 028, config fields, main.py wiring, eval_reset coverage, and `test_inner_life_presence.py`. Closed IL-001's deferred true-local-time item at the tick/catch-up wiring layer (see Deliverables). Pressure accumulation and dream eligibility intentionally excluded — not yet built (IL-003/IL-007). Focused suite green; full suite run before commit.
- 2026-07-17 - Review fix round: (1) allostatic catch-up now piecewise-exact around the single downward threshold crossing — `arousal_threshold_crossing_time` solver added to `affect.py` (bisection on the closed-form arousal to 1e-9 h, O(1)), composed as two calls to `update_allostatic_shift` in `presence.apply_idle_gap`, used by both tick and catch-up; regression test for the 4h-gap-from-0.9 scenario. (2) DST-safe local time: `system_zoneinfo()` resolves the real IANA zone (TZ env, /etc/localtime, fixed-offset fallback with warning), threaded through tick/catch-up as an injectable `tz` seam; night-window and elapsed arithmetic normalized to UTC instants (CPython's intra-zone wall-clock rule mis-measured DST-spanning intervals); DST borderline tests added. PRD/ticket acceptance now states the allostatic accumulator matches within one tick quantum (quantization-dependent by construction).

## Validation

- Commands:
  - `uv run --project apps/server pytest apps/server/tests/test_inner_life_presence.py apps/server/tests/test_inner_life_affect.py apps/server/tests/test_eval_harness.py -q` → all passed
  - `bun run test` (full suite) → see commit message / report for result summary
- Changed paths:
  - `apps/server/src/anima_server/services/agent/inner_life/presence.py` (new)
  - `apps/server/src/anima_server/services/agent/inner_life/catchup.py` (new)
  - `apps/server/src/anima_server/services/agent/inner_life/__init__.py`
  - `apps/server/src/anima_server/main.py`
  - `apps/server/src/anima_server/config.py`
  - `apps/server/src/anima_server/models/runtime_consciousness.py`
  - `apps/server/src/anima_server/models/__init__.py`
  - `apps/server/src/anima_server/services/eval_reset.py`
  - `apps/server/alembic_runtime/versions/028_presence_catchup.py` (new)
  - `apps/server/tests/test_inner_life_presence.py` (new)
- Notes:
  - Migration up/downgrade/re-upgrade verified in isolation against SQLite; full alembic-runtime chain can't run end-to-end against SQLite (migration 005 uses `CREATE EXTENSION vector`, Postgres-only) — this is pre-existing and unrelated to 028.
