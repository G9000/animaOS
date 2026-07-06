# OKF-002 - Source registry and adapter contract

- Status: done
- Priority: P1
- Scope: `apps/server/src/anima_server/services/ingestion`
- Parent: `OKF-000`
- Depends on: `OKF-001`
- Owner: Codex
- PRD: none
- Plan: `docs/superpowers/plans/2026-07-06-okf-llm-wiki-ingestion.md`
- Created: 2026-07-06 23:23 MYT
- Updated: 2026-07-07 00:30 MYT
- Started: 2026-07-07 00:27 MYT
- Completed: 2026-07-07 00:30 MYT

## Goal

Create a source-type-agnostic ingestion package with a registry, artifact/span storage helpers, and adapter contract for all source kinds.

## Deliverables

- `services/ingestion/` package with dataclasses and helper modules.
- Adapter base contract that emits normalized artifacts and spans.
- Source registry helpers for idempotent create/reuse behavior.
- Artifact/span replacement helpers with safe status transitions.
- Tests for adapter contract and failure behavior.

## Acceptance

- Registering the same source for the same user is idempotent by content or URI hash.
- Adapter outputs can represent page, time, line, row, cell, and image annotation locators.
- Failed adapters record failed run state and do not leave half-written spans.
- No implementation path is PDF-specific in the core contract.

## Activity Log

- 2026-07-06 23:23 MYT - Ticket created.
- 2026-07-07 00:27 MYT - Claimed by Codex, set status to `in_progress`, and started adapter contract tests.
- 2026-07-07 00:30 MYT - Added ingestion dataclasses, adapter base contract, registry helpers, artifact/span replacement helpers, and failure-run handling.

## Validation

- Commands:
  - `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test:server -- apps/server/tests/test_source_ingestion_models.py apps/server/tests/test_source_ingestion_adapters.py -q` - passed, 11 tests.
  - `uv run --project apps/server ruff check apps/server/src/anima_server/services/ingestion apps/server/tests/test_source_ingestion_adapters.py` - passed.
- Changed paths:
  - `apps/server/src/anima_server/services/ingestion/__init__.py`
  - `apps/server/src/anima_server/services/ingestion/models.py`
  - `apps/server/src/anima_server/services/ingestion/sources.py`
  - `apps/server/src/anima_server/services/ingestion/artifacts.py`
  - `apps/server/src/anima_server/services/ingestion/adapters/__init__.py`
  - `apps/server/src/anima_server/services/ingestion/adapters/base.py`
  - `apps/server/tests/test_source_ingestion_adapters.py`
- Notes:
  - Core adapter contract remains source-type agnostic; document/image-specific bridges are deferred to OKF-005.
