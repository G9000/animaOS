# SUM-008 - Foresight signals

- Status: done
- Priority: P2
- Scope: `apps/server/src/anima_server/models`, `apps/server/src/anima_server/services/agent`
- Parent: `SUM-000`
- Depends on: `SUM-002`
- Owner: Codex
- PRD: docs/prds/memory/single-user-temporal-memory-v2.md
- Plan: docs/superpowers/plans/2026-06-27-single-user-temporal-memory-v2.md
- Created: 2026-06-27 12:40 MYT
- Updated: 2026-07-03 03:36 MYT
- Started: 2026-07-03 02:54 MYT
- Completed: 2026-07-03 03:36 MYT

## Goal

Implement future-oriented memory so Anima can remember commitments, expected events, and follow-up opportunities.

## Deliverables

- F8 `ForesightSignal` model and migration.
- Consolidation extraction for future events and expected outcomes.
- Relative date resolution against conversation timestamp.
- Lifecycle sweep for active, due, occurred, stale, and cancelled signals.
- Retrieval and proactive prompt integration.

## Acceptance

- Future events are extracted without requiring explicit task creation.
- Relative dates resolve deterministically in tests.
- Stale/due lifecycle transitions are covered.
- Foresight signals remain evidence-backed.

## Activity Log

- 2026-06-27 12:40 MYT - Ticket created.
- 2026-07-03 02:54 MYT - Claimed by Codex on branch `codex/sum-008-009-foresight-procedural`, based on PR #67 head.
- 2026-07-03 03:36 MYT - Completed with foresight signal storage, extraction, lifecycle sweep, prompt integration, and validation.

## Validation

- Commands:
  - RED: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_foresight.py` - failed before implementation because `ForesightSignal` was not exported from `anima_server.models`.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_foresight.py` - 4 passed, 4 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_foresight.py apps/server/tests/test_agent_experience.py` - 8 passed, 8 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_agent_memory_blocks.py apps/server/tests/test_prompt_budget.py apps/server/tests/test_sleep_agent.py apps/server/tests/test_agent_service.py` - 64 passed, 24 warnings.
  - `bun run db:server:heads` - `20260703_0002 (head)`.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run lint:server` - passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run build` - passed with existing Vite chunk-size warning.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server -- --maxfail=1 -q` - 1775 passed, 1 skipped, 310 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run --project apps/server python -` - health smoke for `GET /health`: 200 ok.
- Changed paths:
  - apps/server/alembic_core/versions/20260701_0003_add_memory_salience.py
  - apps/server/alembic_core/versions/20260703_0001_create_foresight_signals.py
  - apps/server/src/anima_server/models/__init__.py
  - apps/server/src/anima_server/models/agent_runtime.py
  - apps/server/src/anima_server/services/agent/consolidation.py
  - apps/server/src/anima_server/services/agent/foresight.py
  - apps/server/src/anima_server/services/agent/memory_blocks.py
  - apps/server/src/anima_server/services/agent/prompt_budget.py
  - apps/server/src/anima_server/services/agent/sleep_tasks.py
  - apps/server/tests/test_foresight.py
- Notes:
  - Added a defensive guard to the inherited salience migration so stamped legacy soul databases missing `memory_items` can reach metadata repair; this unblocked full-suite validation.
