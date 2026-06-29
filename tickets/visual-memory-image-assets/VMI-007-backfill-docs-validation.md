# VMI-007 - Legacy backfill, docs, and final validation

- Status: backlog
- Priority: P2
- Scope: `apps/server`, `docs/architecture`, `tickets/visual-memory-image-assets`
- Parent: `VMI-000`
- Depends on: `VMI-005`, `VMI-006`
- Owner: unassigned
- PRD: docs/prds/memory/visual-memory-image-assets-v1.md
- Plan: docs/superpowers/plans/2026-06-29-visual-memory-image-assets.md
- Created: 2026-06-29 10:53 MYT
- Updated: 2026-06-29 11:30 MYT
- Started:
- Completed:

## Goal

Provide an idempotent migration path for existing chat image attachments and update documentation so future agents understand the visual memory model.

## Deliverables

- Backfill helper for legacy `content_json.attachments`.
- Tests for idempotent backfill, missing-file reporting, and fetch compatibility.
- Architecture doc updates for storage, indexing, OCR/text extraction capability detection, retrieval, proactive behavior, deletion, and future PDF/video/GIF extension boundaries.
- Final validation recorded in this ticket and parent tracker.

## Acceptance

- Running backfill more than once does not duplicate image assets or links.
- Missing legacy files are reported without aborting the full backfill.
- Architecture docs explain the central store and chat link model.
- Architecture docs explain that PDFs keep using the document pipeline, GIFs are v1 image assets without frame-level analysis, and video/timecoded media indexing is future work.
- Full repo validation is run or any environment-sensitive blocker is recorded with exact command output.

## Activity Log

- 2026-06-29 10:53 MYT - Ticket created.
- 2026-06-29 11:30 MYT - Added docs requirement for OCR capability detection and future media extension boundaries.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - none
