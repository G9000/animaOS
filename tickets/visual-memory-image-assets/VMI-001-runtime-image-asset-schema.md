# VMI-001 - Runtime image asset schema

- Status: done
- Priority: P1
- Scope: `apps/server`
- Parent: `VMI-000`
- Depends on: none
- Owner: Codex
- PRD: docs/prds/memory/visual-memory-image-assets-v1.md
- Plan: docs/superpowers/plans/2026-06-29-visual-memory-image-assets.md
- Created: 2026-06-29 10:53 MYT
- Updated: 2026-06-29 11:57 MYT
- Started: 2026-06-29 11:49 MYT
- Completed: 2026-06-29 11:57 MYT

## Goal

Add runtime schema support for first-class image assets, message-image provenance links, and image annotations.

## Deliverables

- `RuntimeImageAsset`, `RuntimeImageMessageLink`, and `RuntimeImageAnnotation` SQLAlchemy models.
- Runtime Alembic migration for the three tables, constraints, and indexes.
- Model tests for insert, ownership, link uniqueness, annotation uniqueness, and cascade behavior.

## Acceptance

- A user can own multiple image assets deduped by checksum.
- A runtime message can link to multiple image assets.
- Image annotations are schema-compatible with VMI-004 embedding via `RuntimeEmbedding.source_type = "image_annotation"`.
- Deleting a message removes message links without deleting durable image assets.
- Focused model tests pass.

## Activity Log

- 2026-06-29 10:53 MYT - Ticket created.
- 2026-06-29 11:23 MYT - Clarified that schema supports required VMI-004 annotation embedding instead of optional later indexing.
- 2026-06-29 11:49 MYT - Claimed by Codex; starting schema tests before model implementation.
- 2026-06-29 11:57 MYT - Added runtime image asset, message link, and annotation models with migration and focused tests.

## Validation

- Commands:
  - `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test:server -- apps/server/tests/test_image_asset_models.py -q`
  - `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test:server -- apps/server/tests/test_document_store.py -q`
  - `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run db:server:revision -- "add image assets"` (failed: helper targets core Alembic and local DB was not current)
  - `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; uv run --project apps/server alembic -c apps/server/alembic_runtime.ini revision --autogenerate -m "add image assets"` (timed out connecting to configured local PostgreSQL runtime URL; no partial revision created)
- Changed paths:
  - apps/server/alembic_runtime/versions/018_image_assets.py
  - apps/server/src/anima_server/models/__init__.py
  - apps/server/src/anima_server/models/runtime.py
  - apps/server/tests/conftest_runtime.py
  - apps/server/tests/test_image_asset_models.py
- Notes:
  - Runtime migration was written manually from the reviewed model definitions because autogeneration was blocked by local database state/runtime connection.
