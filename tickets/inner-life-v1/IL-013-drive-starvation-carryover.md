# IL-013 - Drive starvation carryover in initiative selection

- Status: in-progress
- Priority: P3
- Scope: `apps/server`
- Parent: `IL-000`
- Depends on: IL-003
- Owner: unassigned
- PRD: `docs/prd/inner-life-v1.md`
- Spec: none
- Plan: none
- Created: 2026-07-29 14:14 MYT
- Updated: 2026-07-29 15:16 MYT
- Started: 2026-07-29 14:14 MYT
- Completed:

## Goal

`dominant_drive` is a pure argmax over the above-threshold drives, so a
perennially high-pressure drive (e.g. `unresolved_thread` with a steady flow
of open foresight threads) can outrank a qualifying-but-lower drive on every
single fire, forever — classic scheduler starvation. `pattern_insight` and
`dream_residue` are the likely victims: they only reset when *surfaced*, so
their material can sit voiced-worthy for weeks without ever winning.

Give losing drives a bounded carryover: each time an above-threshold drive
loses the selection to another drive that actually fires, it accrues one
"loss"; at selection time a drive's effective rank is
`pressure + min(losses * boost_per_loss, boost_cap)`. The boost affects
**ranking only, never qualification** — a sub-threshold drive can't be
starved into firing; theta remains the quality bar. Losses reset when the
drive itself fires, and when its pressure is hard-reset (material gone).
Raw pressures in provenance stay untouched; the boost is logged alongside so
every fire decision remains fully explainable.

## Deliverables

- `services/agent/inner_life/drives.py` / `initiative.py`: pure selection
  takes a per-drive loss map; edge persists it (`drive_states.starvation_losses`,
  JSON), increments above-theta losers on a delivered fire, resets on
  fire/hard-reset. Defaults: boost_per_loss 0.03, cap 0.15.
- Runtime migration `033_drive_state_starvation` (idempotent column-add,
  house pattern of 032).
- Provenance: the decision's starvation boosts recorded in the
  `pressure_snapshot` JSON under a dedicated `starvation` key (additive,
  existing keys unchanged).
- Tests: a lower-pressure qualifying drive eventually wins after N losses;
  boost is capped; sub-threshold drives never boosted into qualifying;
  losses reset on fire and on hard reset; provenance shows raw + boost.

## Acceptance

- Deterministic test: with drive A at 0.9 and drive B at 0.80 (both above
  theta), A wins the first fires while B accrues losses; B wins as soon as
  its accumulated boost exceeds the 0.10 gap. A gap wider than the 0.15
  boost cap is never overcome — documented as intended (the cap bounds how
  much fairness can override pressure).
- Migration up/down clean on SQLite and Postgres variants.
- Full suite green.

## Activity Log

- 2026-07-29 14:14 MYT - Ticket created; implementation started on branch
  `il-011-013-inner-life-texture`.
- 2026-07-29 15:16 MYT - Implemented: `starvation_boost` + loss-aware
  `dominant_drive` (ranking only), `DriveDecision.starvation_snapshot`
  folded under a dedicated `starvation` key in the logged
  pressure_snapshot, edge bookkeeping (losers increment on DELIVERED fires
  only; winner and hard-reset drives clear), migration 033 smoke-tested
  up/down/up on SQLite with single-head verification.

## Validation

- Commands:
  - `uv run pytest tests/test_inner_life_initiative.py` — 98 passed (the one
    failure, `test_fetch_ack_route_end_to_end`, fails identically on
    unmodified main when the file runs standalone; it passes in-suite)
  - alembic 033 upgrade/downgrade/upgrade smoke on temp SQLite — clean
- Changed paths:
  - `apps/server/src/anima_server/services/agent/inner_life/initiative.py`
  - `apps/server/src/anima_server/models/runtime_consciousness.py`
  - `apps/server/src/anima_server/config.py`
  - `apps/server/alembic_runtime/versions/033_drive_state_starvation.py` (new)
  - `apps/server/tests/test_inner_life_initiative.py`
- Notes:
  - A pressure gap wider than the 0.15 boost cap is never overcome —
    intended: the cap bounds how much fairness can override pressure.
