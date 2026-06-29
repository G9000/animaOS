# VMI-002 - Central image storage service

- Status: backlog
- Priority: P1
- Scope: `apps/server`
- Parent: `VMI-000`
- Depends on: `VMI-001`
- Owner: unassigned
- PRD: docs/prds/memory/visual-memory-image-assets-v1.md
- Plan: docs/superpowers/plans/2026-06-29-visual-memory-image-assets.md
- Created: 2026-06-29 10:53 MYT
- Updated: 2026-06-29 10:53 MYT
- Started:
- Completed:

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

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - none
