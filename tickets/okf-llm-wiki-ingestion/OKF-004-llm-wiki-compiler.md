# OKF-004 - LLM-wiki compiler

- Status: backlog
- Priority: P1
- Scope: `apps/server/src/anima_server/services/ingestion/compiler.py`
- Parent: `OKF-000`
- Depends on: `OKF-001`, `OKF-002`
- Owner: unassigned
- PRD: none
- Plan: `docs/superpowers/plans/2026-07-06-okf-llm-wiki-ingestion.md`
- Created: 2026-07-06 23:23 MYT
- Updated: 2026-07-06 23:23 MYT
- Started:
- Completed:

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

## Validation

- Commands:
  - `not run yet`
- Changed paths:
  - none
- Notes:
  - Expected focused command: `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test:server -- apps/server/tests/test_llm_wiki_compiler.py -q`
