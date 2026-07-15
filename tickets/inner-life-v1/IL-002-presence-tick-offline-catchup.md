# IL-002 - Presence tick loop and offline catch-up

- Status: backlog
- Priority: P1
- Scope: `apps/server/src/anima_server/main.py`, `apps/server/src/anima_server/services/agent/inner_life`
- Parent: `IL-000`
- Depends on: `IL-001`
- Owner: unassigned
- PRD: docs/prds/presence/inner-life-v1.md
- Plan: docs/superpowers/plans/2026-07-15-inner-life-v1.md
- Created: 2026-07-15 16:55 MYT
- Updated: 2026-07-15 17:10 MYT
- Started:
- Completed:

## Goal

Run inner-life dynamics on a 60 s background tick and apply the entire offline gap in closed form on startup, so state is continuous across restarts and absence.

## Deliverables

- `_periodic_presence_tick()` co-scheduled with existing sweeps: affect relaxation, pressure accumulation, idle counters, dream eligibility check.
- Startup catch-up: O(1) closed-form application of the gap (affect, pressures), no tick replay and no inline dream passes; if the gap held ≥ 1 eligible night window, schedule at most one deferred catch-up dream for the next idle window.
- `presence_catchup` audit row per catch-up (gap length, components applied, dream deferred yes/no).

## Acceptance

- Simulated 3-week gap equals 30,240 individual ticks within float tolerance for closed-form state (dream effects excluded by design) (test).
- Catch-up completes < 50 ms, zero LLM calls, and generates no behavioral output.
- A multi-night gap defers exactly one catch-up dream (test).
- Tick loop is skipped cleanly while a turn is in flight (no state races).

## Activity Log

- 2026-07-15 16:55 MYT - Ticket created.
- 2026-07-15 17:55 MYT - Catch-up no longer evaluates dream windows inline per review (O(1) conflict); defers at most one catch-up dream.

## Validation

- Commands:
  - `not run yet`
- Changed paths:
  - none
- Notes:
  - none
