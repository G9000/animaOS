# OKF-005 - PDF and image adapter bridges

- Status: done
- Priority: P1
- Scope: `apps/server/src/anima_server/services/documents`, `apps/server/src/anima_server/services/images`, `apps/server/src/anima_server/services/ingestion/adapters`
- Parent: `OKF-000`
- Depends on: `OKF-002`, `OKF-004`
- Owner: codex
- Model: 5.5
- PRD: none
- Plan: `docs/superpowers/plans/2026-07-06-okf-llm-wiki-ingestion.md`
- Created: 2026-07-06 23:23 MYT
- Updated: 2026-07-07 13:02 MYT
- Started: 2026-07-07 00:40 MYT
- Completed: 2026-07-07 00:48 MYT

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
- 2026-07-07 00:40 MYT - Claimed by Codex; starting adapter bridge tests before implementation.
- 2026-07-07 00:48 MYT - Completed document and image source bridges with workflow hooks and focused regression validation.

- 2026-07-07 13:02 MYT - Ticket metadata normalized: status done, owner codex, model 5.5.

## Validation
- Commands:
  - `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test:server -- apps/server/tests/test_source_ingestion_adapters.py -q` - passed, 8 tests.
  - `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test:server -- apps/server/tests/test_image_indexing.py::test_index_image_asset_creates_context_metadata_and_current_embeddings apps/server/tests/test_pdf_workflow_checkpoints.py::test_pdf_workflow_syncs_indexed_document_chunks_to_source_spans -q` - passed, 2 tests.
  - `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test:server -- apps/server/tests/test_source_ingestion_adapters.py apps/server/tests/test_pdf_workflow_checkpoints.py::test_pdf_workflow_syncs_indexed_document_chunks_to_source_spans apps/server/tests/test_document_rag.py apps/server/tests/test_image_indexing.py -q` - passed, 46 tests.
  - `uv run --project apps/server ruff check apps/server/src/anima_server/services/ingestion/adapters/documents.py apps/server/src/anima_server/services/ingestion/adapters/images.py apps/server/src/anima_server/services/documents/pdf_workflow.py apps/server/src/anima_server/services/images/indexing.py apps/server/tests/test_source_ingestion_adapters.py apps/server/tests/test_image_indexing.py apps/server/tests/test_pdf_workflow_checkpoints.py` - passed.
- Changed paths:
  - `apps/server/src/anima_server/services/ingestion/adapters/documents.py`
  - `apps/server/src/anima_server/services/ingestion/adapters/images.py`
  - `apps/server/src/anima_server/services/documents/pdf_workflow.py`
  - `apps/server/src/anima_server/services/images/indexing.py`
  - `apps/server/tests/test_source_ingestion_adapters.py`
  - `apps/server/tests/test_image_indexing.py`
  - `apps/server/tests/test_pdf_workflow_checkpoints.py`
- Notes:
  - Existing document chunk and image annotation embedding tables remain authoritative; source spans are synchronized alongside them.
