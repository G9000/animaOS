# OKF-004 - LLM-wiki compiler

- Status: done
- Priority: P1
- Scope: `apps/server/src/anima_server/services/ingestion/compiler.py`
- Parent: `OKF-000`
- Depends on: `OKF-001`, `OKF-002`
- Owner: Codex
- PRD: none
- Plan: `docs/superpowers/plans/2026-07-06-okf-llm-wiki-ingestion.md`
- Created: 2026-07-06 23:23 MYT
- Updated: 2026-07-07 00:37 MYT
- Started: 2026-07-07 00:34 MYT
- Completed: 2026-07-07 00:37 MYT

## Goal

Compile normalized source spans into maintained OKF-style concept pages with links and citations.

## Deliverables

- Compiler service for initial, refresh, and repair modes.
- Prompt templates for source compilation, concept merge, and link detection.
- Strict JSON model-output schema and validation.
- Deterministic merge rules for existing concepts.
- Tests with fake model outputs and malformed model outputs.

## Acceptance

- Compiler creates source summary, topic, entity, claim, question, and decision concepts from spans.
- Concepts cite source spans through structured concept-source links.
- Concepts link to other concepts through typed links.
- Existing concepts update rather than duplicate on exact slug or high-confidence title/type match.
- Malformed model output records a failed bundle run and leaves existing concepts intact.

## Activity Log

- 2026-07-06 23:23 MYT - Ticket created.
- 2026-07-07 00:34 MYT - Claimed by Codex, set status to `in_progress`, and started compiler tests with fake model output.
- 2026-07-07 00:37 MYT - Added compiler service, prompt templates, deterministic merge rules, citation/link writes, and malformed-output failure handling.

## Validation

- Commands:
  - `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test:server -- apps/server/tests/test_source_ingestion_models.py apps/server/tests/test_source_ingestion_adapters.py apps/server/tests/test_okf_import_export.py apps/server/tests/test_llm_wiki_compiler.py -q` - passed, 19 tests.
  - `uv run --project apps/server ruff check apps/server/src/anima_server/services/ingestion/compiler.py apps/server/tests/test_llm_wiki_compiler.py` - passed.
- Changed paths:
  - `apps/server/src/anima_server/services/ingestion/compiler.py`
  - `apps/server/src/anima_server/services/ingestion/prompts/compile_source.md.j2`
  - `apps/server/src/anima_server/services/ingestion/prompts/merge_concepts.md.j2`
  - `apps/server/src/anima_server/services/ingestion/prompts/detect_links.md.j2`
  - `apps/server/tests/test_llm_wiki_compiler.py`
- Notes:
  - Compiler model boundary is injectable and tested with deterministic fake outputs; no provider calls are made.
