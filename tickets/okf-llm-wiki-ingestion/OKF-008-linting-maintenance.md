# OKF-008 - Bundle linting and maintenance

- Status: done
- Priority: P2
- Scope: `apps/server/src/anima_server/services/ingestion/lint.py`, `apps/server/src/anima_server/api/routes/knowledge.py`
- Parent: `OKF-000`
- Depends on: `OKF-003`, `OKF-004`
- Owner: Codex
- PRD: none
- Plan: `docs/superpowers/plans/2026-07-06-okf-llm-wiki-ingestion.md`
- Created: 2026-07-06 23:23 MYT
- Updated: 2026-07-07 01:19 MYT
- Started: 2026-07-07 01:12 MYT
- Completed: 2026-07-07 01:19 MYT

## Goal

Add linting and maintenance checks for OKF-compatible knowledge bundles.

## Deliverables

- Lint service with structured findings.
- Lint endpoint under the knowledge API.
- Tests for broken links, uncited claims, duplicate concepts, stale concepts, contradictions, and orphan sources.

## Acceptance

- Lint returns findings with stable `code`, `severity`, target ids, and message.
- Broken concept links are detected.
- Claims without source links are detected.
- Stale concepts are detected after source span content changes.
- Contradictions are represented as links or findings rather than silently merged.
- Lint supports source, concept, and user scope.

## Activity Log

- 2026-07-06 23:23 MYT - Ticket created.
- 2026-07-07 01:12 MYT - Claimed by Codex; starting lint tests before implementation.
- 2026-07-07 01:19 MYT - Completed structured lint service and knowledge API lint endpoint.

## Validation

- Commands:
  - `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test:server -- apps/server/tests/test_okf_import_export.py apps/server/tests/test_llm_wiki_compiler.py -q` - passed, 10 tests.
  - `uv run --project apps/server ruff check apps/server/src/anima_server/services/ingestion/lint.py apps/server/src/anima_server/api/routes/knowledge.py apps/server/tests/test_llm_wiki_compiler.py apps/server/tests/test_okf_import_export.py` - passed.
- Changed paths:
  - `apps/server/src/anima_server/services/ingestion/lint.py`
  - `apps/server/src/anima_server/api/routes/knowledge.py`
  - `apps/server/tests/test_llm_wiki_compiler.py`
- Notes:
  - Broken links include links to inactive target concepts; FK-invalid links are prevented by the runtime schema.
