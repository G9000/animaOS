# VMI-004 - Image annotation and indexing pipeline

- Status: backlog
- Priority: P1
- Scope: `apps/server`
- Parent: `VMI-000`
- Depends on: `VMI-003`
- Owner: unassigned
- PRD: docs/prds/memory/visual-memory-image-assets-v1.md
- Plan: docs/superpowers/plans/2026-06-29-visual-memory-image-assets.md
- Created: 2026-06-29 10:53 MYT
- Updated: 2026-06-29 11:30 MYT
- Started:
- Completed:

## Goal

Index image-derived text so image assets become searchable and usable by memory retrieval. This is core production behavior, not optional enrichment. OCR/text extraction is included when the configured model/helper declares support.

## Deliverables

- Image annotation replacement helpers for upload context, metadata, optional vision captions, and capability-gated OCR/text extraction.
- Adapter-facing capability contract for `vision_caption` and `image_text_extraction`.
- Embedding upsert for every active `RuntimeImageAnnotation` row through `PgVecStore`.
- Search helper returning image assets and matching annotation snippets.
- Background captioning path that does not block chat response latency.
- Audit helper or query that detects active annotations missing current embeddings.
- Tests with mocked embedding, mocked vision caption behavior, and mocked image text-extraction capability.

## Acceptance

- Every uploaded image receives at least one context annotation when chat persistence succeeds.
- Every active image annotation receives a current `RuntimeEmbedding` row with `source_type = "image_annotation"`.
- Re-indexing an unchanged annotation is idempotent and does not create duplicate embedding rows.
- Image indexing works when no vision-capable model is configured.
- When image text extraction is supported and returns text, an `ocr_text` annotation is created and embedded.
- OCR/text extraction runs only when a configured model/helper declares support for image text extraction.
- Caption failures do not fail the chat turn.
- OCR/text-extraction failures do not fail the chat turn.
- Embeddings use `RuntimeEmbedding.source_type = "image_annotation"`.
- Search results are scoped to the owning user and return the parent image asset, not only raw annotation rows.

## Activity Log

- 2026-06-29 10:53 MYT - Ticket created.
- 2026-06-29 11:27 MYT - Clarified that OCR/text extraction is supported through capability-gated processing.
- 2026-06-29 11:30 MYT - Made OCR/text extraction an explicit VMI-004 deliverable and acceptance target.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - none
