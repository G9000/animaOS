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
- Startup catch-up: O(1) closed-form application of the gap (affect, pressures, retroactive dream-window evaluation), no tick replay.
- `presence_catchup` audit row per catch-up (gap length, components applied).

## Acceptance

- Simulated 3-week gap equals 30,240 individual ticks within float tolerance (test).
- Catch-up completes < 50 ms and generates no behavioral output.
- Tick loop is skipped cleanly while a turn is in flight (no state races).

## Activity Log

- 2026-07-15 16:55 MYT - Ticket created.

## Validation

- Commands:
  - `not run yet`
- Changed paths:
  - none
- Notes:
  - none
