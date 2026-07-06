# OKF-006 - Markdown, text, and web adapters

- Status: done
- Priority: P1
- Scope: `apps/server/src/anima_server/services/ingestion/adapters`, `apps/server/src/anima_server/api/routes/knowledge.py`
- Parent: `OKF-000`
- Depends on: `OKF-002`, `OKF-004`
- Owner: Codex
- PRD: none
- Plan: `docs/superpowers/plans/2026-07-06-okf-llm-wiki-ingestion.md`
- Created: 2026-07-06 23:23 MYT
- Updated: 2026-07-07 00:57 MYT
- Started: 2026-07-07 00:50 MYT
- Completed: 2026-07-07 00:57 MYT

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
- 2026-07-07 00:50 MYT - Claimed by Codex; starting adapter and API tests before implementation.
- 2026-07-07 00:57 MYT - Completed markdown, text, and web capture adapters plus knowledge source API routes.

## Validation

- Commands:
  - `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test:server -- apps/server/tests/test_source_ingestion_adapters.py apps/server/tests/test_knowledge_api.py -q` - passed, 14 tests.
  - `uv run --project apps/server ruff check apps/server/src/anima_server/services/ingestion/adapters/text.py apps/server/src/anima_server/services/ingestion/adapters/web.py apps/server/src/anima_server/api/routes/knowledge.py apps/server/src/anima_server/main.py apps/server/tests/test_source_ingestion_adapters.py apps/server/tests/test_knowledge_api.py` - passed.
- Changed paths:
  - `apps/server/src/anima_server/services/ingestion/adapters/text.py`
  - `apps/server/src/anima_server/services/ingestion/adapters/web.py`
  - `apps/server/src/anima_server/api/routes/knowledge.py`
  - `apps/server/src/anima_server/main.py`
  - `apps/server/tests/test_source_ingestion_adapters.py`
  - `apps/server/tests/test_knowledge_api.py`
- Notes:
  - Web capture ingestion is caller-supplied readable text only; no live network fetching is performed.
