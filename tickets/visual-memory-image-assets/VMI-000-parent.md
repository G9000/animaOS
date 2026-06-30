# VMI-000 - Visual Memory Image Assets Parent Tracker

- Status: done
- Priority: P1
- Scope: `apps/server`, `apps/desktop`, `packages/api-client`, `docs/prds/memory`, `docs/superpowers/plans`, `tickets/visual-memory-image-assets`
- Depends on: none
- Owner: Codex
- PRD: docs/prds/memory/visual-memory-image-assets-v1.md
- Plan: docs/superpowers/plans/2026-06-29-visual-memory-image-assets.md
- Created: 2026-06-29 10:53 MYT
- Updated: 2026-06-29 13:02 MYT
- Started: 2026-06-29 11:49 MYT
- Completed: 2026-06-29 13:02 MYT

## Goal

Track the initiative that turns chat images into central visual memory assets with indexing, retrieval, proactive use, and honest deletion semantics.

## Child Ticket Order

| Ticket | Title | Status | Depends on |
| --- | --- | --- | --- |
| `VMI-001` | Runtime image asset schema | `done` | none |
| `VMI-002` | Central image storage service | `done` | `VMI-001` |
| `VMI-003` | Chat ingestion and public attachment compatibility | `done` | `VMI-002` |
| `VMI-004` | Image annotation and indexing pipeline | `done` | `VMI-003` |
| `VMI-005` | Agent retrieval and proactive image use | `done` | `VMI-004` |
| `VMI-006` | User controls, deletion, and desktop/API client updates | `done` | `VMI-003`, `VMI-004` |
| `VMI-007` | Legacy backfill, docs, and final validation | `done` | `VMI-005`, `VMI-006` |

## Deliverables

- Runtime image asset, message link, and annotation tables.
- Central per-user image media storage under `ANIMA_DATA_DIR`.
- Chat image uploads linked to image assets while preserving existing chat rendering.
- Image-derived text annotations indexed through runtime embeddings.
- Capability-gated OCR/text-extraction annotations for models/helpers that support reading visible image text.
- Agent retrieval and proactive follow-up support for image assets.
- User-facing controls to remove image links or forget image assets.
- Thread deletion cleanup for orphaned transient image files.
- Idempotent backfill for existing chat attachment files.
- Updated architecture documentation and validation records.

## Core Production Path

The first production-ready slice is `VMI-001` through `VMI-006` together:

- `VMI-001` establishes image identity, provenance, and annotation schema.
- `VMI-002` guarantees duplicate uploads reuse the same user-owned image asset and binary.
- `VMI-003` makes chat messages link to image assets instead of owning image blobs.
- `VMI-004` creates and embeds image annotations through `RuntimeEmbedding.source_type = "image_annotation"`.
- `VMI-005` lets Anima retrieve, cite, and proactively ask about indexed image assets.
- `VMI-006` provides deletion and retention safety for users and files.

`VMI-007` is required before closing the initiative, but it is the legacy backfill, docs, and final validation pass.

## Acceptance

- Every child ticket references this parent.
- Parent status table reflects child progress.
- Chat image upload still works with existing supported MIME types.
- Reuploading the same image for one user stores one binary and one active image asset row.
- Every active image annotation has a current runtime embedding.
- Supported OCR/text extraction creates an embedded `ocr_text` annotation, while unsupported OCR never blocks upload.
- Indexed images can be searched or retrieved by the agent without exposing another user's images.
- Deleting a thread no longer leaves orphaned transient chat image files.
- Forgetting an image removes active links, annotations, embeddings, asset row, and safe-to-delete file.
- Legacy chat messages with old attachment metadata remain readable until backfilled.
- No cloud image storage, mandatory OCR dependency, or full PDF/video media ingestion is introduced.

## Completed Tickets

- `VMI-001` - Runtime image asset schema
- `VMI-002` - Central image storage service
- `VMI-003` - Chat ingestion and public attachment compatibility
- `VMI-004` - Image annotation and indexing pipeline
- `VMI-005` - Agent retrieval and proactive image use
- `VMI-006` - User controls, deletion, and desktop/API client updates
- `VMI-007` - Legacy backfill, docs, and final validation

## Activity Log

- 2026-06-29 10:53 MYT - Parent tracker created for visual memory image asset planning.
- 2026-06-29 11:14 MYT - Clarified that `VMI-001` through `VMI-006` are the core production path, with dedupe and annotation embeddings required.
- 2026-06-29 11:23 MYT - Updated deletion dependency to include image indexing because forgetting an image must clean embedding rows.
- 2026-06-29 11:30 MYT - Added OCR/text extraction as explicit capability-gated indexing work and documented future PDF/video/GIF extension boundaries.
- 2026-06-29 11:49 MYT - Claimed by Codex; parent set to `in_progress` and `VMI-001` started.
- 2026-06-29 11:57 MYT - Completed `VMI-001`; started `VMI-002`.
- 2026-06-29 12:01 MYT - Completed `VMI-002`; started `VMI-003`.
- 2026-06-29 12:05 MYT - Completed `VMI-003`; started `VMI-004`.
- 2026-06-29 12:10 MYT - Completed `VMI-004`; started `VMI-005`.
- 2026-06-29 12:23 MYT - Completed `VMI-005`; started `VMI-006`.
- 2026-06-29 12:33 MYT - Completed `VMI-006`; started `VMI-007`.
- 2026-06-29 13:02 MYT - Completed `VMI-007`; parent tracker closed with validation caveats recorded.

## Validation

- Commands:
  - `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test:server -- apps/server/tests/test_image_asset_models.py apps/server/tests/test_image_assets.py apps/server/tests/test_chat_image_assets.py apps/server/tests/test_image_indexing.py apps/server/tests/test_image_retrieval_context.py apps/server/tests/test_proactive_image_memory.py apps/server/tests/test_image_backfill.py apps/server/tests/test_chat_attachments.py apps/server/tests/test_dashboard_api.py::test_proactive_notice_endpoint_accepts_custom_instruction apps/server/tests/test_document_store.py -q` - 67 passed, 3 warnings.
  - `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test:server -- apps/server/tests/test_image_deletion.py -q` - 4 passed.
  - `bun test packages/api-client/tests/client.test.ts` - 16 passed.
  - `bun run lint` - passed.
  - `git diff --check` - passed.
  - `bun run build` - passed.
  - `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run db:server:current` - passed.
  - `uv run --project . alembic -c alembic_runtime.ini heads` from `apps/server` - `018_image_assets (head)`.
  - `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test` - 1689 passed, 1 skipped, 1 order-dependent creation-flow failure; the failing test and full `test_creation_flow.py` pass in isolation.
- Changed paths:
  - apps/server
  - apps/desktop/src/pages/chat/Chat.tsx
  - packages/api-client
  - docs/architecture
  - docs/superpowers/plans/2026-06-29-visual-memory-image-assets.md
  - tickets/visual-memory-image-assets
- Notes:
  - Docs-code-sync remains blocked by pre-existing broken path references outside the visual-memory docs.
  - Runtime Alembic `current` timed out against the configured local PostgreSQL URL; revision graph validation reports `018_image_assets` as head.
