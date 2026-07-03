# MPB-005 - Background memory service facades

- Status: backlog
- Priority: P2
- Scope: `apps/server/src/anima_server/services/memory`, `apps/server/src/anima_server/services/agent/pattern_synthesis.py`, `apps/server/src/anima_server/services/agent/foresight.py`, `apps/server/src/anima_server/services/agent/agent_experience.py`
- Parent: `MPB-000`
- Depends on: `MPB-002`
- Owner: unassigned
- PRD: docs/prds/memory/single-user-temporal-memory-v2.md
- Plan: docs/superpowers/plans/2026-07-03-memory-package-boundary-hardening.md
- Created: 2026-07-03 17:12 MYT
- Updated: 2026-07-03 17:12 MYT
- Started:
- Completed:

## Goal

Expose pattern synthesis, foresight, and procedural memory through stable `services.memory` facades for future orchestration work.

## Deliverables

- `services.memory.patterns` facade.
- `services.memory.foresight` facade.
- `services.memory.procedural` facade.
- Delegation tests for existing background memory implementations.

## Acceptance

- Existing sleep-time and post-turn behavior remains unchanged.
- New memory orchestration code can import stable facades from `services.memory`.
- Focused pattern, foresight, and procedural tests still pass.

## Activity Log

- 2026-07-03 17:12 MYT - Ticket created.

## Validation

- Commands:
  - `not run yet`
- Changed paths:
  - none
- Notes:
  - none
