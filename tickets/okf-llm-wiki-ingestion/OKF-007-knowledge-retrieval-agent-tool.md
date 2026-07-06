# OKF-007 - Knowledge retrieval and agent tool

- Status: done
- Priority: P1
- Scope: `apps/server/src/anima_server/services/ingestion/retrieval.py`, `apps/server/src/anima_server/services/agent`
- Parent: `OKF-000`
- Depends on: `OKF-003`, `OKF-004`, `OKF-005`, `OKF-006`
- Owner: Codex
- PRD: none
- Plan: `docs/superpowers/plans/2026-07-06-okf-llm-wiki-ingestion.md`
- Created: 2026-07-06 23:23 MYT
- Updated: 2026-07-07 01:10 MYT
- Started: 2026-07-07 00:59 MYT
- Completed: 2026-07-07 01:10 MYT

## Goal

Add retrieval over compiled knowledge concepts and raw source spans, then expose it to the agent through a bounded tool.

## Deliverables

- Concept embedding upsert using `RuntimeEmbedding.source_type = "knowledge_concept"`.
- Source span embedding upsert using `RuntimeEmbedding.source_type = "source_span"`.
- Combined retrieval service returning concepts, evidence spans, and links.
- Agent tool such as `search_knowledge_bundle`.
- Tests for retrieval ranking, citations, user isolation, and no English keyword heuristics.

## Acceptance

- Broad queries retrieve compiled concepts first.
- Evidence-heavy queries include citable source spans.
- Retrieval is user-scoped.
- The agent tool returns concept summaries plus evidence refs without promoting personal memory.
- No hardcoded English query-word routing is introduced.

## Activity Log

- 2026-07-06 23:23 MYT - Ticket created.
- 2026-07-07 00:59 MYT - Claimed by Codex; starting retrieval and agent tool tests before implementation.
- 2026-07-07 01:10 MYT - Completed knowledge concept/source-span embedding upserts, vector retrieval, and the agent search tool.

## Validation

- Commands:
  - `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test:server -- apps/server/tests/test_knowledge_retrieval.py apps/server/tests/test_agent_service.py::test_search_knowledge_bundle_tool_returns_concepts_and_evidence -q` - passed, 3 tests.
  - `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test:server -- apps/server/tests/test_knowledge_retrieval.py apps/server/tests/test_agent_service.py -q` - failed, 32 passed and 1 unrelated existing context-pill assertion failed in `test_run_agent_persists_context_message_pills`.
  - `uv run --project apps/server ruff check apps/server/src/anima_server/services/ingestion/retrieval.py apps/server/src/anima_server/services/agent/tools.py apps/server/tests/test_knowledge_retrieval.py apps/server/tests/test_agent_service.py` - passed.
- Changed paths:
  - `apps/server/src/anima_server/services/ingestion/retrieval.py`
  - `apps/server/src/anima_server/services/agent/tools.py`
  - `apps/server/tests/test_knowledge_retrieval.py`
  - `apps/server/tests/test_agent_service.py`
- Notes:
  - Retrieval ranks by embeddings only; no English keyword routing was added.
