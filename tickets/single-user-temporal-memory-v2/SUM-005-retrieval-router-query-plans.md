# SUM-005 - Retrieval router and query plans

- Status: done
- Priority: P1
- Scope: `apps/server/src/anima_server/services/agent`
- Parent: `SUM-000`
- Depends on: `SUM-003`, `SUM-004`
- Owner: Codex
- PRD: docs/prds/memory/single-user-temporal-memory-v2.md
- Plan: docs/superpowers/plans/2026-06-27-single-user-temporal-memory-v2.md
- Created: 2026-06-27 12:40 MYT
- Updated: 2026-07-01 14:19 MYT
- Started: 2026-07-01 14:00 MYT
- Completed: 2026-07-01 14:19 MYT

## Goal

Route memory retrieval by user intent instead of using one generic scoring strategy for every turn.

## Deliverables

- `retrieval_router.py` with deterministic route labels and query plan objects.
- Source-specific retrieval composition for profile, graph, memory items, episodes, transcripts, foresight, experiences, and skills.
- Trace output showing chosen route, sources, and scores.
- Prompt/tool guidance updates for `search_long_memory`.
- Regression probes for route correctness.

## Acceptance

- Router fixture suite reaches agreed accuracy on representative user turns.
- Emotional support queries retrieve relationship and emotional context.
- Factual recall queries retrieve evidence-backed exact or episodic records.
- Project continuity queries retrieve active project/profile/episode context.
- Retrieval traces are serializable for UI/debug inspection.

## Activity Log

- 2026-06-27 12:40 MYT - Ticket created.
- 2026-07-01 14:00 MYT - Claimed by Codex on stacked branch `codex/sum-005-retrieval-router-query-plans-stacked`, based on `codex/sum-004-structured-user-profile`.
- 2026-07-01 14:00 MYT - Added deterministic retrieval query plans, serializable retrieval traces, route-specific hybrid search limits, and recall guidance updates.
- 2026-07-01 14:19 MYT - Completed validation and marked ticket done.

## Validation

- Commands:
  - RED: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_retrieval_router.py -q` - failed before implementation because `retrieval_router` did not exist.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_retrieval_router.py -q` - 13 passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_retrieval_router.py apps/server/tests/test_search_long_memory_tool.py apps/server/tests/test_agent_service.py::test_run_agent_attaches_retrieval_router_trace_without_hits apps/server/tests/test_agent_service.py::test_run_agent_does_not_run_hidden_wide_evidence_retrieval apps/server/tests/test_agent_memory_blocks.py apps/server/tests/test_memory_scored_retrieval.py apps/server/tests/test_evidence_retrieval.py apps/server/tests/test_knowledge_graph.py apps/server/tests/test_user_profile.py -q` - 138 passed, 86 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test -- --maxfail=1 -q` - first reruns exposed stream/prompt compatibility regressions, then passed: 1757 passed, 1 skipped, 294 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run lint` - passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run build` - passed with existing Vite chunk-size warning.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run db:server:current` - passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run --project apps/server python -` smoke for `GET /health` - 200 ok.
- Changed paths:
  - apps/server/src/anima_server/services/agent/retrieval_router.py
  - apps/server/src/anima_server/services/agent/service.py
  - apps/server/src/anima_server/services/agent/state.py
  - apps/server/src/anima_server/services/agent/templates/system_prompt.md.j2
  - apps/server/src/anima_server/services/agent/tools.py
  - apps/server/tests/test_agent_service.py
  - apps/server/tests/test_retrieval_router.py
  - apps/server/tests/test_search_long_memory_tool.py
  - tickets/single-user-temporal-memory-v2/SUM-000-parent.md
  - tickets/single-user-temporal-memory-v2/SUM-005-retrieval-router-query-plans.md
- Notes:
  - Foresight, experiences, and skills are represented as explicit planned query-plan sources with `available=false` until later storage tickets provide durable source implementations.
  - Existing stream payload shape is preserved when a retrieval trace has no router query plan.
