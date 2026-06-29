# VMI-002 - Central image storage service

- Status: done
- Priority: P1
- Scope: `apps/server`
- Parent: `VMI-000`
- Depends on: `VMI-001`
- Owner: Codex
- PRD: docs/prds/memory/visual-memory-image-assets-v1.md
- Plan: docs/superpowers/plans/2026-06-29-visual-memory-image-assets.md
- Created: 2026-06-29 10:53 MYT
- Updated: 2026-06-29 12:01 MYT
- Started: 2026-06-29 11:57 MYT
- Completed: 2026-06-29 12:01 MYT

## Goal

Create the server-side image storage service that validates uploaded image bytes, stores binaries in the central user media path, and registers deduped image asset rows.

## Deliverables

- New `anima_server.services.images` package.
- Safe user-scoped path resolution for `users/<user_id>/media/images/`.
- Registration helper that writes image bytes once per `(user_id, sha256)`.
- Cleanup helper for orphaned transient image assets.
- Tests for validation, dedupe, path safety, and file deletion.

## Acceptance

- Supported MIME types remain PNG, JPEG, WebP, and GIF.
- Magic-byte validation still protects declared MIME types.
- Reuploading the same image for one user reuses the existing asset row and file.
- Reuploading the same bytes with a different filename updates metadata only if the service explicitly records aliases; it must not create a second binary.
- Absolute or cross-user storage paths are rejected.
- Orphan cleanup only deletes files below the allowed media root.

## Activity Log

- 2026-06-29 10:53 MYT - Ticket created.
- 2026-06-29 11:57 MYT - Claimed by Codex after completing `VMI-001`; starting storage-service tests.
- 2026-06-29 12:01 MYT - Added central image storage service, shared MIME/magic validation, path safety, dedupe, and safe file deletion tests.

## Validation

- Commands:
  - `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test:server -- apps/server/tests/test_image_assets.py -q`
  - `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test:server -- apps/server/tests/test_chat_attachments.py -q`
- Changed paths:
  - apps/server/src/anima_server/services/agent/attachments.py
  - apps/server/src/anima_server/services/images/__init__.py
  - apps/server/src/anima_server/services/images/models.py
  - apps/server/src/anima_server/services/images/store.py
  - apps/server/tests/test_image_assets.py
- Notes:
  - Chat attachment validation now uses the central image MIME/magic-byte helpers; chat persistence is handled by `VMI-003`.
