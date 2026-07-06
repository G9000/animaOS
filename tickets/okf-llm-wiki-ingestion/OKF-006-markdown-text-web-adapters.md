# OKF-006 - Markdown, text, and web adapters

- Status: backlog
- Priority: P1
- Scope: `apps/server/src/anima_server/services/ingestion/adapters`, `apps/server/src/anima_server/api/routes/knowledge.py`
- Parent: `OKF-000`
- Depends on: `OKF-002`, `OKF-004`
- Owner: unassigned
- PRD: none
- Plan: `docs/superpowers/plans/2026-07-06-okf-llm-wiki-ingestion.md`
- Created: 2026-07-06 23:23 MYT
- Updated: 2026-07-06 23:23 MYT
- Started:
- Completed:

## Goal

Add first new source adapters for markdown, plain text, and web captures under the universal ingestion contract.

## Deliverables

- Markdown/plain text adapter with heading and paragraph-aware span extraction.
- Web capture adapter that accepts URL metadata plus caller-supplied readable content.
- Knowledge source API endpoints for text, markdown, and web capture ingestion.
- API tests for adapter behavior and source creation.

## Acceptance

- Markdown and plain text ingestion reject empty content and create source/artifact/span rows.
- Web capture ingestion stores source URL and preserves canonical URL/title metadata when provided.
- Tests do not rely on live network fetching.
- Compiler run records are created where the route requests compilation.

## Activity Log

- 2026-07-06 23:23 MYT - Ticket created.

## Validation

- Commands:
  - `not run yet`
- Changed paths:
  - none
- Notes:
  - Expected focused command: `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test:server -- apps/server/tests/test_source_ingestion_adapters.py apps/server/tests/test_knowledge_api.py -q`
