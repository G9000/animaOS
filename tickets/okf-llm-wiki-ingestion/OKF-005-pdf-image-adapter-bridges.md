# OKF-005 - PDF and image adapter bridges

- Status: backlog
- Priority: P1
- Scope: `apps/server/src/anima_server/services/documents`, `apps/server/src/anima_server/services/images`, `apps/server/src/anima_server/services/ingestion/adapters`
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

Bridge existing PDF document and image annotation ingestion into the universal source/artifact/span model.

## Deliverables

- Document adapter bridge for `RuntimeDocument` and `RuntimeDocumentChunk`.
- Image adapter bridge for `RuntimeImageAsset` and `RuntimeImageAnnotation`.
- Workflow hooks after document indexing and image annotation indexing.
- Regression tests for current document RAG and image indexing behavior.

## Acceptance

- Indexed documents sync to `RuntimeSource` with page-based source spans.
- Image assets and annotations sync to `RuntimeSource` with image annotation locators.
- Existing `document_chunk` and `image_annotation` embeddings remain intact.
- Existing document and image retrieval tests still pass.
- Optional compiler queueing does not block existing ingestion completion.

## Activity Log

- 2026-07-06 23:23 MYT - Ticket created.

## Validation

- Commands:
  - `not run yet`
- Changed paths:
  - none
- Notes:
  - Expected focused command: `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test:server -- apps/server/tests/test_source_ingestion_adapters.py apps/server/tests/test_document_rag.py apps/server/tests/test_image_indexing.py -q`
