# SUM-006 - Salience-aware decay and soft evolution

- Status: done
- Priority: P1
- Scope: `apps/server/src/anima_server/services/agent`, `apps/server/src/anima_server/models`
- Parent: `SUM-000`
- Depends on: `SUM-003`, `SUM-004`
- Owner: Codex
- PRD: docs/prds/memory/single-user-temporal-memory-v2.md
- Plan: docs/superpowers/plans/2026-06-27-single-user-temporal-memory-v2.md
- Created: 2026-06-27 12:40 MYT
- Updated: 2026-07-01 21:18 MYT
- Started: 2026-07-01 21:18 MYT
- Completed: 2026-07-01 21:18 MYT

## Goal

Make memory decay and contradiction handling sensitive to emotional salience, identity stability, and gradual change.

## Deliverables

- Salience fields or sidecar model for memory class, emotional salience, stability, relationship proximity, and evidence strength.
- Extraction schema updates for salience.
- Heat scoring updates using decay classes.
- Soft evolution detector for preference and emotional changes.
- Sleep-time surfacing of possible memory drift.

## Acceptance

- Important identity and life-event memories decay more slowly than casual observations.
- Repeated low-grade emotional patterns can accumulate salience.
- Soft changes create evolution chains instead of deleting useful history.
- Tests cover decay math, salience parsing, and evolution handling.

## Activity Log

- 2026-06-27 12:40 MYT - Ticket created.
- 2026-07-01 21:18 MYT - Started implementation.
- 2026-07-01 21:18 MYT - Implemented additive salience metadata, decay-class heat scoring, soft evolution chains, sleep-time drift surfacing, extraction prompt updates, migrations, and focused tests.

## Validation

- Commands:
  - not run (verification not requested in this session)
- Changed paths:
  - apps/server/src/anima_server/models/agent_runtime.py
  - apps/server/src/anima_server/models/runtime_memory.py
  - apps/server/src/anima_server/services/agent/candidate_ops.py
  - apps/server/src/anima_server/services/agent/consolidation.py
  - apps/server/src/anima_server/services/agent/heat_scoring.py
  - apps/server/src/anima_server/services/agent/memory_salience.py
  - apps/server/src/anima_server/services/agent/memory_store.py
  - apps/server/src/anima_server/services/agent/provenance.py
  - apps/server/src/anima_server/services/agent/retrieval_feedback.py
  - apps/server/src/anima_server/services/agent/sleep_agent.py
  - apps/server/src/anima_server/services/agent/soul_writer.py
  - apps/server/src/anima_server/services/agent/templates/prompts/delta_extraction.md.j2
  - apps/server/src/anima_server/services/agent/templates/prompts/memory_extraction.md.j2
  - apps/server/alembic_core/versions/20260701_0003_add_memory_salience.py
  - apps/server/alembic_runtime/versions/019_candidate_salience.py
  - apps/server/tests/test_memory_salience.py
  - tickets/single-user-temporal-memory-v2/SUM-000-parent.md
  - tickets/single-user-temporal-memory-v2/SUM-006-salience-decay-soft-evolution.md
- Notes:
  - Validation was not run because this session requires explicit permission before verification.
