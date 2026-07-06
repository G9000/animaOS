# OKF-001 - Runtime source and concept schema

- Status: done
- Priority: P1
- Scope: `apps/server/src/anima_server/models`, `apps/server/alembic_runtime/versions`
- Parent: `OKF-000`
- Depends on: none
- Owner: Codex
- PRD: none
- Plan: `docs/superpowers/plans/2026-07-06-okf-llm-wiki-ingestion.md`
- Created: 2026-07-06 23:23 MYT
- Updated: 2026-07-07 00:26 MYT
- Started: 2026-07-07 00:16 MYT
- Completed: 2026-07-07 00:26 MYT

## Goal

Create the runtime schema for universal sources, artifacts, spans, OKF-style concepts, concept citations, concept links, and bundle run records.

## Deliverables

- SQLAlchemy runtime models for source ingestion and compiled knowledge concepts.
- Runtime Alembic migration for the new tables, indexes, and constraints.
- Model exports in `apps/server/src/anima_server/models/__init__.py`.
- Focused model tests in `apps/server/tests/test_source_ingestion_models.py`.

## Acceptance

- Tests can insert sources, artifacts, spans, concepts, concept-source links, concept links, and bundle run rows.
- Every new table is user-scoped.
- Source-derived artifacts and spans cascade safely without deleting compiled concepts unless an explicit recompile path does so.
- Required constraints and indexes from the plan are present.

## Activity Log

- 2026-07-06 23:23 MYT - Ticket created.
- 2026-07-07 00:16 MYT - Claimed by Codex, set status to `in_progress`, and started schema/model test work.
- 2026-07-07 00:26 MYT - Added runtime source/concept models, runtime migration, focused model tests, and validation.

## Validation

- Commands:
  - `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test:server -- apps/server/tests/test_source_ingestion_models.py -q` - passed, 5 tests.
  - `uv run --project apps/server ruff check apps/server/src/anima_server/models/runtime.py apps/server/src/anima_server/models/__init__.py apps/server/alembic_runtime/versions/1c3df376a170_add_source_knowledge_ingestion.py apps/server/tests/test_source_ingestion_models.py` - passed.
- Changed paths:
  - `apps/server/src/anima_server/models/runtime.py`
  - `apps/server/src/anima_server/models/__init__.py`
  - `apps/server/alembic_runtime/versions/1c3df376a170_add_source_knowledge_ingestion.py`
  - `apps/server/tests/test_source_ingestion_models.py`
- Notes:
  - Runtime migration was created against `alembic_runtime` with `down_revision = "021_repair_profile_update_candidates"`.
