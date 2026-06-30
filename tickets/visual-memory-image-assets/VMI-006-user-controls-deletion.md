# VMI-006 - User controls, deletion, and desktop/API client updates

- Status: done
- Priority: P1
- Scope: `apps/server`, `apps/desktop`, `packages/api-client`
- Parent: `VMI-000`
- Depends on: `VMI-003`, `VMI-004`
- Owner: Codex
- PRD: docs/prds/memory/visual-memory-image-assets-v1.md
- Plan: docs/superpowers/plans/2026-06-29-visual-memory-image-assets.md
- Created: 2026-06-29 10:53 MYT
- Updated: 2026-06-29 12:33 MYT
- Started: 2026-06-29 12:23 MYT
- Completed: 2026-06-29 12:33 MYT

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
- 2026-06-29 12:23 MYT - Claimed by Codex after completing `VMI-005`; starting deletion semantics.
- 2026-06-29 12:33 MYT - Added image deletion service, image API routes, thread cleanup, API client methods, and desktop chat attachment controls.

## Validation

- Commands:
  - `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test:server -- apps/server/tests/test_image_deletion.py -q`
  - `bun test packages/api-client/tests/client.test.ts`
  - `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test:server -- apps/server/tests/test_chat_image_assets.py apps/server/tests/test_image_assets.py apps/server/tests/test_chat_attachments.py -q`
  - `bun run lint:desktop`
- Changed paths:
  - `apps/server/src/anima_server/services/images/deletion.py`
  - `apps/server/src/anima_server/api/routes/images.py`
  - `apps/server/src/anima_server/api/routes/threads.py`
  - `apps/server/src/anima_server/main.py`
  - `apps/server/src/anima_server/schemas/images.py`
  - `apps/server/src/anima_server/schemas/chat.py`
  - `apps/server/src/anima_server/services/agent/state.py`
  - `apps/server/src/anima_server/services/agent/attachments.py`
  - `apps/server/src/anima_server/services/agent/thread_manager.py`
  - `packages/api-client/src/types.ts`
  - `packages/api-client/src/client.ts`
  - `packages/api-client/tests/client.test.ts`
  - `apps/desktop/src/pages/chat/Chat.tsx`
  - `apps/server/tests/test_image_deletion.py`
- Notes:
  - Thread deletion now removes orphaned transient image assets/files while retaining explicitly retained or durable assets.
