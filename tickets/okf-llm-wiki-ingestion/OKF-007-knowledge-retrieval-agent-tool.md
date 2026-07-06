# OKF-007 - Knowledge retrieval and agent tool

- Status: backlog
- Priority: P1
- Scope: `apps/server/src/anima_server/services/ingestion/retrieval.py`, `apps/server/src/anima_server/services/agent`
- Parent: `OKF-000`
- Depends on: `OKF-003`, `OKF-004`, `OKF-005`, `OKF-006`
- Owner: unassigned
- PRD: none
- Plan: `docs/superpowers/plans/2026-07-06-okf-llm-wiki-ingestion.md`
- Created: 2026-07-06 23:23 MYT
- Updated: 2026-07-06 23:23 MYT
- Started:
- Completed:

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

## Validation

- Commands:
  - `not run yet`
- Changed paths:
  - none
- Notes:
  - Expected focused command: `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test:server -- apps/server/tests/test_knowledge_retrieval.py apps/server/tests/test_agent_service.py -q`
