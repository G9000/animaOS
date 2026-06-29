# VMI-006 - User controls, deletion, and desktop/API client updates

- Status: backlog
- Priority: P1
- Scope: `apps/server`, `apps/desktop`, `packages/api-client`
- Parent: `VMI-000`
- Depends on: `VMI-003`, `VMI-004`
- Owner: unassigned
- PRD: docs/prds/memory/visual-memory-image-assets-v1.md
- Plan: docs/superpowers/plans/2026-06-29-visual-memory-image-assets.md
- Created: 2026-06-29 10:53 MYT
- Updated: 2026-06-29 11:23 MYT
- Started:
- Completed:

## Goal

Add user-facing controls and server behavior for removing image links, forgetting image assets, and cleaning up orphaned transient files.

## Deliverables

- Authenticated image endpoints for fetch, remove link, forget asset, and retention-state update.
- Thread deletion cleanup that unlinks orphaned transient image files.
- API client type/method updates for image asset ids and deletion actions.
- Minimal desktop chat controls for remove-from-chat and forget-image actions.
- Tests for deletion semantics and API client behavior.

## Acceptance

- Removing an image from a chat message does not delete a reused or durable image asset.
- Forgetting an image globally removes links, embeddings, annotations, asset row, and file where safe.
- Deleting a thread removes orphaned transient image files.
- Deletion tests verify there are no dangling embeddings for forgotten image annotations.
- Pending composer image removal continues to work.
- API client tests and desktop typecheck pass.

## Activity Log

- 2026-06-29 10:53 MYT - Ticket created.
- 2026-06-29 11:23 MYT - Added VMI-004 dependency because global image deletion must remove annotation embedding rows.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - none
