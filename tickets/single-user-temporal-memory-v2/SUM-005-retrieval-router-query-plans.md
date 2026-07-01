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
- Updated: 2026-07-01 16:01 MYT
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
- 2026-07-01 14:51 MYT - Addressed PR #72 Codex review comments for response-schema query plan visibility, emotional-route precedence, and lowercase relationship targets.
- 2026-07-01 15:05 MYT - Addressed PR #72 Codex rereview comment for applying route memory category filters to live hybrid retrieval and injected context fragments.
- 2026-07-01 15:23 MYT - Addressed PR #72 Codex rereview comment for applying memory category filters before semantic/BM25 candidate limits truncate route-matching memories.
- 2026-07-01 15:39 MYT - Addressed PR #72 Codex rereview comment for keeping generic "I need to know/remember" recall out of foresight routing while preserving explicit future commitments.
- 2026-07-01 15:51 MYT - Addressed PR #72 Codex rereview comments for keeping "feel like" preference phrasing out of emotional routing and routing role-only relationship questions to relationship context.
- 2026-07-01 16:01 MYT - Addressed PR #72 Codex rereview comment for matching `due` as a word-boundary foresight cue so preference words like "fondue" do not route to foresight.

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
  - RED: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_retrieval_router.py apps/server/tests/test_chat.py::test_chat_returns_retrieval_metadata_when_present apps/server/tests/test_chat.py::test_chat_history_returns_persisted_retrieval_metadata -q` - PR #72 review regressions failed before fixes with lowercase relationship routing, emotional-route precedence, and stripped `queryPlan`.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_retrieval_router.py apps/server/tests/test_chat.py apps/server/tests/test_search_long_memory_tool.py apps/server/tests/test_agent_service.py::test_run_agent_attaches_retrieval_router_trace_without_hits apps/server/tests/test_agent_service.py::test_run_agent_does_not_run_hidden_wide_evidence_retrieval -q` - PR #72 review focused suite: 38 passed, 2 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run lint` - PR #72 review fix lint: passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run build` - PR #72 review fix build: passed with existing Vite chunk-size warning.
  - RED: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_agent_service.py::test_run_agent_applies_retrieval_router_memory_category_filters -q` - PR #72 rereview regression failed before fix because preference routes still injected both `fact` and `preference` fragments.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_agent_service.py::test_run_agent_applies_retrieval_router_memory_category_filters apps/server/tests/test_hybrid_retrieval.py::TestHybridSearchIntegration::test_hybrid_search_filters_by_memory_categories -q` - PR #72 rereview regressions: 2 passed, 2 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_retrieval_router.py apps/server/tests/test_chat.py apps/server/tests/test_search_long_memory_tool.py apps/server/tests/test_agent_service.py::test_run_agent_attaches_retrieval_router_trace_without_hits apps/server/tests/test_agent_service.py::test_run_agent_applies_retrieval_router_memory_category_filters apps/server/tests/test_agent_service.py::test_run_agent_does_not_run_hidden_wide_evidence_retrieval apps/server/tests/test_hybrid_retrieval.py::TestHybridSearchIntegration::test_hybrid_search_filters_by_memory_categories -q` - PR #72 rereview focused suite: 40 passed, 4 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run lint` - PR #72 rereview fix lint: passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run build` - PR #72 rereview fix build: passed with existing Vite chunk-size warning.
  - RED: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_hybrid_retrieval.py::TestHybridSearchIntegration::test_hybrid_search_applies_category_filters_before_candidate_limit -q` - PR #72 capped-pool regression failed before fix because an unfiltered top-1 fact displaced a matching preference.
  - RED: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_bm25_index.py::TestRustBackedKeywordSearch::test_bm25_search_applies_categories_before_candidate_limit -q` - PR #72 capped-pool regression failed before fix because BM25 did not accept category-filtered search.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_retrieval_router.py apps/server/tests/test_chat.py apps/server/tests/test_search_long_memory_tool.py apps/server/tests/test_agent_service.py::test_run_agent_attaches_retrieval_router_trace_without_hits apps/server/tests/test_agent_service.py::test_run_agent_applies_retrieval_router_memory_category_filters apps/server/tests/test_agent_service.py::test_run_agent_does_not_run_hidden_wide_evidence_retrieval apps/server/tests/test_hybrid_retrieval.py::TestHybridSearchIntegration::test_hybrid_search_filters_by_memory_categories apps/server/tests/test_hybrid_retrieval.py::TestHybridSearchIntegration::test_hybrid_search_applies_category_filters_before_candidate_limit apps/server/tests/test_bm25_index.py::TestRustBackedKeywordSearch::test_bm25_search_applies_categories_before_candidate_limit -q` - PR #72 capped-pool focused suite: 42 passed, 5 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_hybrid_retrieval.py apps/server/tests/test_bm25_index.py -q` - hybrid/BM25 suite: 68 passed, 18 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run lint` - PR #72 capped-pool fix lint: passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run build` - PR #72 capped-pool fix build: passed with existing Vite chunk-size warning.
  - RED: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_retrieval_router.py::test_generic_need_to_recall_does_not_force_foresight apps/server/tests/test_retrieval_router.py::test_need_to_with_future_commitment_remains_foresight -q` - PR #72 generic need-to regression failed before fix because ordinary recall turns routed to foresight.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_retrieval_router.py::test_generic_need_to_recall_does_not_force_foresight apps/server/tests/test_retrieval_router.py::test_need_to_with_future_commitment_remains_foresight -q` - PR #72 generic need-to regressions: 5 passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_retrieval_router.py apps/server/tests/test_chat.py apps/server/tests/test_search_long_memory_tool.py apps/server/tests/test_agent_service.py::test_run_agent_attaches_retrieval_router_trace_without_hits apps/server/tests/test_agent_service.py::test_run_agent_applies_retrieval_router_memory_category_filters apps/server/tests/test_agent_service.py::test_run_agent_does_not_run_hidden_wide_evidence_retrieval apps/server/tests/test_hybrid_retrieval.py::TestHybridSearchIntegration::test_hybrid_search_filters_by_memory_categories apps/server/tests/test_hybrid_retrieval.py::TestHybridSearchIntegration::test_hybrid_search_applies_category_filters_before_candidate_limit apps/server/tests/test_bm25_index.py::TestRustBackedKeywordSearch::test_bm25_search_applies_categories_before_candidate_limit -q` - PR #72 generic need-to focused suite: 47 passed, 5 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run lint` - PR #72 generic need-to fix lint: passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run build` - PR #72 generic need-to fix build: passed with existing Vite chunk-size warning.
  - RED: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_retrieval_router.py::test_feel_like_preference_phrase_routes_as_preference_lookup apps/server/tests/test_retrieval_router.py::test_role_only_relationship_questions_route_to_relationship_context -q` - PR #72 feel-like/role-only regression failed before fix because feel-like preference routed to emotional support and role-only relationship questions routed to general recall.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_retrieval_router.py::test_feel_like_preference_phrase_routes_as_preference_lookup apps/server/tests/test_retrieval_router.py::test_role_only_relationship_questions_route_to_relationship_context -q` - PR #72 feel-like/role-only regressions: 4 passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_retrieval_router.py apps/server/tests/test_chat.py apps/server/tests/test_search_long_memory_tool.py apps/server/tests/test_agent_service.py::test_run_agent_attaches_retrieval_router_trace_without_hits apps/server/tests/test_agent_service.py::test_run_agent_applies_retrieval_router_memory_category_filters apps/server/tests/test_agent_service.py::test_run_agent_does_not_run_hidden_wide_evidence_retrieval apps/server/tests/test_hybrid_retrieval.py::TestHybridSearchIntegration::test_hybrid_search_filters_by_memory_categories apps/server/tests/test_hybrid_retrieval.py::TestHybridSearchIntegration::test_hybrid_search_applies_category_filters_before_candidate_limit apps/server/tests/test_bm25_index.py::TestRustBackedKeywordSearch::test_bm25_search_applies_categories_before_candidate_limit -q` - PR #72 feel-like/role-only focused suite: 51 passed, 5 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run lint` - PR #72 feel-like/role-only fix lint: passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run build` - PR #72 feel-like/role-only fix build: passed with existing Vite chunk-size warning.
  - RED: `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_retrieval_router.py::test_due_substring_in_preference_word_does_not_route_to_foresight -q` - PR #72 due-word regression failed before fix because "fondue" routed to foresight instead of preference lookup.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_retrieval_router.py::test_due_substring_in_preference_word_does_not_route_to_foresight -q` - PR #72 due-word regression: 1 passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test:server apps/server/tests/test_retrieval_router.py apps/server/tests/test_chat.py apps/server/tests/test_search_long_memory_tool.py apps/server/tests/test_agent_service.py::test_run_agent_attaches_retrieval_router_trace_without_hits apps/server/tests/test_agent_service.py::test_run_agent_applies_retrieval_router_memory_category_filters apps/server/tests/test_agent_service.py::test_run_agent_does_not_run_hidden_wide_evidence_retrieval apps/server/tests/test_hybrid_retrieval.py::TestHybridSearchIntegration::test_hybrid_search_filters_by_memory_categories apps/server/tests/test_hybrid_retrieval.py::TestHybridSearchIntegration::test_hybrid_search_applies_category_filters_before_candidate_limit apps/server/tests/test_bm25_index.py::TestRustBackedKeywordSearch::test_bm25_search_applies_categories_before_candidate_limit -q` - PR #72 due-word focused suite: 52 passed, 5 warnings.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run lint` - PR #72 due-word fix lint: passed.
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run build` - PR #72 due-word fix build: passed with existing Vite chunk-size warning.
- Changed paths:
  - apps/server/src/anima_server/schemas/chat.py
  - apps/server/src/anima_server/services/agent/bm25_index.py
  - apps/server/src/anima_server/services/agent/embeddings.py
  - apps/server/src/anima_server/services/agent/retrieval_router.py
  - apps/server/src/anima_server/services/agent/service.py
  - apps/server/src/anima_server/services/agent/state.py
  - apps/server/src/anima_server/services/agent/templates/system_prompt.md.j2
  - apps/server/src/anima_server/services/agent/tools.py
  - apps/server/tests/test_chat.py
  - apps/server/tests/test_agent_service.py
  - apps/server/tests/test_bm25_index.py
  - apps/server/tests/test_hybrid_retrieval.py
  - apps/server/tests/test_retrieval_router.py
  - apps/server/tests/test_search_long_memory_tool.py
  - packages/api-client/src/types.ts
  - packages/standard-templates/src/chat/types.ts
  - tickets/single-user-temporal-memory-v2/SUM-000-parent.md
  - tickets/single-user-temporal-memory-v2/SUM-005-retrieval-router-query-plans.md
- Notes:
  - Foresight, experiences, and skills are represented as explicit planned query-plan sources with `available=false` until later storage tickets provide durable source implementations.
  - Existing stream payload shape is preserved when a retrieval trace has no router query plan.
  - `queryPlan` is now preserved through normal chat/history/approval response schemas and client-facing retrieval trace types.
  - Route `memory_categories` filters are now applied to `hybrid_search` and enforced again before adaptive filtering/citation construction so trace scope and injected context match.
  - Category-filtered hybrid retrieval now applies the filter inside semantic vector search and BM25 document selection before per-leg candidate limits are applied.
  - Generic "I need to know/remember" recall turns no longer force foresight routing; explicit temporal/commitment cues still do.
  - "Feel like" preference phrasing now falls through to preference routing unless explicit emotional cues are present, and role-only "who is my ..." relationship questions route to relationship context.
  - The foresight `due` cue now uses word-boundary matching so preference terms containing the substring, such as "fondue", stay on preference lookup.
