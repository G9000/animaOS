# IL-006 - Recall reconsolidation (F2 extension)

- Status: in_progress
- Priority: P2
- Scope: `apps/server/src/anima_server/services/agent/heat_scoring.py`, `apps/server/src/anima_server/services/agent/memory_salience.py`, `apps/server/src/anima_server/services/agent/provenance.py`
- Parent: `IL-000`
- Depends on: none
- Owner: Claude
- PRD: docs/prds/presence/inner-life-v1.md
- Plan: docs/superpowers/plans/2026-07-15-inner-life-v1.md
- Created: 2026-07-15 16:55 MYT
- Updated: 2026-07-19 03:35 MYT
- Started: 2026-07-19 03:35 MYT
- Completed:

## Goal

Make recall a write: when a memory is rendered into context, nudge its salience/affect toward the present (η = 0.05), bounded, provenance-logged, and reversible from the log.

## Deliverables

- Reconsolidation hook on context inclusion (not on scoring): refresh relevance recency, nudge emotional salience toward current turn affect, allow stability class upgrades only.
- Lifetime drift cap Σ|Δ| ≤ 0.3 per item; per-adjustment provenance entries preserving original values.
- Exemption: identity-class memories get confidence refresh only, no affect nudging.
- Reduced-strength mode (η = 0.02) for dream-cycle touches (consumed by IL-007).

## Acceptance

- Fires only on context inclusion; drift cap enforced; originals reconstructable from provenance (tests).
- Identity exemption verified.
- No measurable retrieval latency regression (< 1 ms per included item).

## Activity Log

- 2026-07-15 16:55 MYT - Ticket created.

## Validation

- Commands:
  - `not run yet`
- Changed paths:
  - none
- Notes:
  - none
