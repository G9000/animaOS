# OKF-009 - API client and desktop knowledge library

- Status: done
- Priority: P2
- Scope: `packages/api-client`, `apps/desktop/src/pages/knowledge`, `apps/desktop/src/components/knowledge`
- Parent: `OKF-000`
- Depends on: `OKF-007`, `OKF-008`
- Owner: codex
- Model: 5.5
- PRD: none
- Plan: `docs/superpowers/plans/2026-07-06-okf-llm-wiki-ingestion.md`
- Created: 2026-07-06 23:23 MYT
- Updated: 2026-07-07 13:02 MYT
- Started: 2026-07-07 01:20 MYT
- Completed: 2026-07-07 01:32 MYT

## Goal

Expose source and concept reads through the API client and add a minimal desktop knowledge library surface.

## Deliverables

- API client types for sources, artifacts, spans, concepts, links, lint findings, and OKF import/export results.
- API client methods for source/concept read, compile, search, import/export, and lint.
- Desktop knowledge library route with source list, concept list, concept markdown body, citations, lint action, and OKF export action.
- Desktop build/typecheck validation.

## Acceptance

- Client methods are typed and compile.
- Desktop surface is a working library view, not a landing page.
- User can inspect sources and concepts.
- Concept view shows source citations.
- Lint and export actions are wired to API client methods or clear disabled states if backend route gating requires it.

## Activity Log

- 2026-07-06 23:23 MYT - Ticket created.
- 2026-07-07 01:20 MYT - Claimed by Codex; starting API client and desktop knowledge-library implementation.
- 2026-07-07 01:32 MYT - Added knowledge API list/search/import/export routes, API client methods, and the desktop knowledge-library surface.

- 2026-07-07 13:02 MYT - Ticket metadata normalized: status done, owner codex, model 5.5.

## Validation
- Commands:
  - `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test:server -- apps/server/tests/test_knowledge_api.py -q` - passed, 6 tests.
  - `bun test packages/api-client/tests/client.test.ts` - passed, 17 tests.
  - `bun run build:desktop` - passed; Vite emitted the existing large-chunk warning.
  - `uv run --project . ruff check src/anima_server/api/routes/knowledge.py tests/test_knowledge_api.py` from `apps/server` - passed.
- Changed paths:
  - `apps/server/src/anima_server/api/routes/knowledge.py`
  - `apps/server/tests/test_knowledge_api.py`
  - `packages/api-client/src/client.ts`
  - `packages/api-client/src/types.ts`
  - `packages/api-client/tests/client.test.ts`
  - `apps/desktop/src/App.tsx`
  - `apps/desktop/src/components/layout/nav-items.ts`
  - `apps/desktop/src/components/knowledge/KnowledgeConceptViewer.tsx`
  - `apps/desktop/src/components/knowledge/KnowledgeSourceList.tsx`
  - `apps/desktop/src/pages/knowledge/KnowledgeLibrary.tsx`
- Notes:
  - Added minimal backend read/list/search/export/import routes needed by the client and desktop surface because the prior backend only exposed individual source/concept read, source creation, compile queueing, and lint.
