# IL-016 - Require session spread before a latent trace crystallizes

- Status: backlog
- Priority: P3
- Scope: `apps/server/src/anima_server/services/agent/latent_traces.py`, `apps/server/src/anima_server/services/agent/inner_life/latent.py`
- Parent: none
- Depends on: `IL-004`
- Owner: unassigned
- PRD: docs/prds/presence/inner-life-v1.md
- Spec: docs/superpowers/specs/2026-08-02-il-014-subthreshold-crystallization-design.md
- Plan: none
- Created: 2026-08-02 13:42 MYT
- Updated: 2026-08-02 13:42 MYT
- Started:
- Completed:

Standalone follow-up beyond the closed Inner Life v1 scope — tracked in
`IL-000`'s "Follow-ups Beyond v1 Scope" section.

## Goal

IL-004 accumulates sub-threshold candidates into a per-topic `LatentTrace`
and crystallizes a memory once cumulative weight crosses the threshold. Weight
can accumulate entirely **within one conversation**, so a single long session
about one subject can crystallize a memory that reads as a recurring pattern
("you keep coming back to this") when it was really one occasion discussed at
length.

Require contributions from at least two distinct sessions before a trace is
eligible to crystallize, so a crystallized memory means *recurring* rather than
*discussed at length*.

## Deliverables

- Track distinct source sessions per trace (`evidence_refs` already records
  source message ids; derive the thread/session from them rather than adding a
  column if that is sufficient).
- `should_crystallize` additionally requires `distinct_sessions >= 2`
  (configurable, default 2).
- Traces that meet the weight threshold but not the session threshold stay
  pending rather than being dropped — they crystallize on the next session that
  touches the topic.
- Tests: single-session accumulation over threshold does NOT crystallize;
  the same weight spread over two sessions does.

## Acceptance

- A trace whose evidence all comes from one session never crystallizes on
  weight alone.
- Existing IL-004 crystallization behavior is otherwise unchanged (no
  regression in the IL-004 test suite).

## Activity Log

- 2026-08-02 13:42 MYT - Filed from the IL-014 investigation (see Spec): IL-014 turned out to
  be already delivered by IL-004, and this session-spread rule is the one
  genuine refinement that fell out of the comparison.

## Validation

- Commands:
  - `not run yet`
- Changed paths:
  - none
- Notes:
  - none
