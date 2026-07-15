# Inner Life v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Anima a continuous inner life: a deterministic affect state that evolves between turns and across absence, drive-pressure accumulators feeding an opt-in push initiative channel, and dynamic memory processes — latent trace crystallization, forgetting as distillation, recall reconsolidation, and an idle-time dream cycle.

**Architecture:** All dynamics are pure functions `(state, event, Δt) → state` in a new `services/agent/inner_life/` package; DB writes and notifications live at the edges. Closed-form relaxation makes offline catch-up O(1). No LLM calls anywhere except initiative message generation (on fire) and dream narrative generation (≤ 1/night), both on the extraction model. Portable state (dream journal, latent traces, tendency claims) goes to the SQLCipher soul store; everything else is rebuildable runtime state.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0, SQLCipher SQLite, embedded runtime PostgreSQL, Alembic (core + runtime trees), Jinja2 prompts, pytest, Tauri desktop shell for OS notifications.

---

## Scope

Implements PRD `docs/prds/presence/inner-life-v1.md` (IL1–IL7). Docs-only artifacts already exist; this plan sequences the code. Out of scope: autonomous tool use while idle, voice delivery channels, multi-user presence, any physiology-simulation framing.

## Planning Inputs

- PRD: `docs/prds/presence/inner-life-v1.md`
- Tickets: `tickets/inner-life-v1/IL-000` … `IL-007`
- Extended features: F2 (`heat_scoring.py`), F5 (`sleep_agent.py`), F7 (`forgetting.py`), F8 foresight (consumer)
- Architecture docs: `docs/architecture/agent/brain-system.md`, `docs/architecture/memory/memory-system.md`

## File Map

| Area | Main files |
| --- | --- |
| Dynamics core | new `apps/server/src/anima_server/services/agent/inner_life/` (`affect.py`, `pressures.py`, `catchup.py`, `dream.py`, `__init__.py`) |
| Durable models | `apps/server/src/anima_server/models/agent_runtime.py`, `models/presence.py`, new inner-life tables |
| Migrations | `apps/server/alembic_core/versions/` (soul: latent_traces, dream_journal, tendency claims), `apps/server/alembic_runtime/versions/` (runtime: affect state, pressures, audit rows) |
| Background loops | `apps/server/src/anima_server/main.py` (`_periodic_presence_tick`), `services/agent/sleep_agent.py`, `sleep_tasks.py` |
| Initiative | new `inner_life/initiative.py`, `services/agent/proactive.py`, `presence_config.py`, desktop notification bridge under `apps/desktop` |
| Memory dynamics | `services/agent/consolidation.py`, `forgetting.py`, `claims.py`, `heat_scoring.py`, `memory_salience.py`, `provenance.py` |
| Prompts | `services/agent/templates/prompts/` (initiative message, dream narrative) |
| Tests | new `apps/server/tests/test_inner_life_*.py` per feature |

## Execution Order

### Phase 0: Substrate — affect vector (IL-001)

Purpose: land the state primitive everything else reads.

- [ ] Create `inner_life/` package with pure `affect.py`: clamped turn deltas, closed-form relaxation, circadian baseline, allostatic shift.
- [ ] Runtime migration for persisted affect state + config for taus/baselines.
- [ ] Wire emotional_intelligence post-turn deltas; render adjectives + trajectory into `build_agent_state()` and greeting tone.
- [ ] Property tests: determinism, clamping, closed-form fixtures, restart persistence.
- [ ] Validation: `bun run test` focused suite; no LLM calls on the update path (assert via test double).

### Phase 1: Continuity — presence tick and offline catch-up (IL-002)

- [ ] Add `_periodic_presence_tick()` (60 s) co-scheduled with existing sweeps; skip cleanly while a turn is in flight.
- [ ] Implement `catchup.py`: O(1) gap application (affect, pressures, retroactive dream windows) + `presence_catchup` audit row.
- [ ] Equivalence test: 3-week gap == 30,240 ticks within float tolerance; catch-up < 50 ms; no behavioral output during catch-up.

### Phase 2: Memory dynamics (IL-004, IL-005, IL-006 — parallelizable, standalone)

Purpose: the three trace-level mechanisms; none depend on Phases 0–1.

- [ ] IL-004: soul migration for `latent_traces`; consolidation floor-band hook with EMA fold; sleep-time crystallization task with full evidence provenance; weekly decay + cap.
- [ ] IL-005: distillation step in the F7 decay path (tendency claims, content-free tombstones, `mode: distilled` audit); right-to-forget precedence on explicit delete; class exemptions; F7 regression suite stays green.
- [ ] IL-006: reconsolidation hook on context inclusion in retrieval assembly; η = 0.05, lifetime drift cap Σ|Δ| ≤ 0.3, per-adjustment provenance, identity-class exemption; reduced-strength mode (η = 0.02) exported for the dream cycle.
- [ ] Validation: per-feature tests; export audit proves tombstones content-free; retrieval latency delta < 1 ms/item.

### Phase 3: Initiative — drives and push channel (IL-003)

Purpose: the first user-visible behavior; lands only after continuity exists.

- [ ] `pressures.py`: five accumulators with growth/reset rules and persisted state; fed by foresight, pattern synthesis, contact cadence, topic diversity, dream residue (stub until Phase 4).
- [ ] Gate chain in `initiative.py`: presence_config opt-in (default off) → quiet hours → adaptive cooldown (24 h base, closeness-scaled, back-off on unanswered) → rate caps (1/day, 3/week) → idle-only.
- [ ] Delivery: OS notification via Tauri bridge with adapter seam; drive-tagged message prompt (generic check-in filler prohibited and asserted in tests).
- [ ] Initiative provenance log (drive, pressure snapshot, gate states, text) + presence_config UI fields.
- [ ] Validation: zero-initiative guarantees under disabled/quiet/capped states; traceability tests; back-off behavior.

### Phase 4: Dream cycle (IL-007)

- [ ] `dream.py`: eligibility (idle ≥ 4 h, 00:00–06:00 local, ≤ 1/night), material selection (`significance × (1 − heat)`, latent traces > 0.5, one archive fragment).
- [ ] Extraction-model narrative pass; affect deltas at 25 % scale; IL-006 reduced-strength touch; soul migration for `dream_journal` (cap 30).
- [ ] Share-worthy flagging → `dream_residue` pressure; `presence_config.dream_sharing` gate (default `on_ask`).
- [ ] Validation: never during active sessions; nightly cap; provenance completeness; identity-class content untouched.

### Phase 5: Instrumentation and rollout

- [ ] Metrics per PRD §6: initiative acceptance rate, unanswered back-off events, crystallized-memory retrieval yield, catch-up timings.
- [ ] Rollout order: ship Phases 0–2 dark (no user-visible change), then Phase 4 with `dream_sharing: on_ask`, then Phase 3 behind explicit opt-in.
- [ ] Update `docs/architecture/agent/brain-system.md` and memory PRD index status rows.
- [ ] Full validation: `bun run test`, migration up/down on both alembic trees, vault export/import round-trip including new soul tables.

## Migration Notes

- Soul-store (alembic_core): `latent_traces`, `dream_journal`, tendency-claim namespace — all included in vault export/import from day one.
- Runtime (alembic_runtime): affect state, pressure state, `presence_catchup` + initiative provenance — rebuildable, excluded from vault.
- Order: runtime migrations may land per-phase; soul migrations must land with the feature that writes them (Phases 2 and 4).
