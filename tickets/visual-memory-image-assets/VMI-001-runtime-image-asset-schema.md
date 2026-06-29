# VMI-001 - Runtime image asset schema

- Status: backlog
- Priority: P1
- Scope: `apps/server`
- Parent: `VMI-000`
- Depends on: none
- Owner: unassigned
- PRD: docs/prds/memory/visual-memory-image-assets-v1.md
- Plan: docs/superpowers/plans/2026-06-29-visual-memory-image-assets.md
- Created: 2026-06-29 10:53 MYT
- Updated: 2026-06-29 11:23 MYT
- Started:
- Completed:

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

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - none
