# IL-003 - Drive accumulators and push initiative channel

- Status: backlog
- Priority: P1
- Scope: `apps/server/src/anima_server/services/agent/inner_life`, `apps/server/src/anima_server/services/agent/proactive.py`, `apps/server/src/anima_server/models/presence.py`, `apps/desktop`
- Parent: `IL-000`
- Depends on: `IL-001`, `IL-002`
- Owner: unassigned
- PRD: docs/prds/presence/inner-life-v1.md
- Plan: docs/superpowers/plans/2026-07-15-inner-life-v1.md
- Created: 2026-07-15 16:55 MYT
- Updated: 2026-07-15 17:10 MYT
- Started:
- Completed:

## Goal

Let the companion send an unprompted message when a named drive pressure crosses threshold — off by default, strictly gated, and fully provenance-logged.

## Deliverables

- Pressure accumulators per PRD IL3 table (`unresolved_thread`, `pattern_insight`, `relational`, `novelty`, `dream_residue`) with growth/reset rules and persisted state.
- Gate chain: presence_config opt-in (default off), quiet hours, adaptive cooldown (24 h base, closeness-scaled, back-off on unanswered), rate caps (1/day, 3/week), idle-only.
- Outbound delivery via OS notification (Tauri shell) with adapter seam for other channels.
- Drive-tagged message generation (small LLM call) that must speak from the accumulated material; generic check-in filler prohibited in prompt and validated in tests.
- Initiative provenance log: drive, pressure snapshot, gate states, generated text.
- presence_config UI fields for opt-in, quiet hours, caps.

## Acceptance

- Zero initiatives possible while disabled, in quiet hours, or above caps (tests).
- Every fired initiative traceable to a named drive with recorded pressures.
- Unanswered initiatives increase cooldown; any user turn resets `relational`.

## Activity Log

- 2026-07-15 16:55 MYT - Ticket created.

## Validation

- Commands:
  - `not run yet`
- Changed paths:
  - none
- Notes:
  - none
