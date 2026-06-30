# VMI-003 - Chat ingestion and public attachment compatibility

- Status: done
- Priority: P1
- Scope: `apps/server`
- Parent: `VMI-000`
- Depends on: `VMI-002`
- Owner: Codex
- PRD: docs/prds/memory/visual-memory-image-assets-v1.md
- Plan: docs/superpowers/plans/2026-06-29-visual-memory-image-assets.md
- Created: 2026-06-29 10:53 MYT
- Updated: 2026-06-29 12:05 MYT
- Started: 2026-06-29 12:01 MYT
- Completed: 2026-06-29 12:05 MYT

## Goal

Route new chat image uploads through image assets while preserving current chat history and attachment fetch behavior.

## Deliverables

- Chat turn preparation returns stored attachments backed by `RuntimeImageAsset`.
- User message persistence creates `RuntimeImageMessageLink` rows.
- Chat history serializes image attachments with backward-compatible fields.
- Authenticated attachment fetch resolves both new image asset links and legacy attachment metadata.
- Tests for new and legacy chat image messages.

## Acceptance

- Desktop chat can upload and render images without API contract breakage.
- Message ownership is checked before returning image bytes.
- Historical messages with only `content_json.attachments` still render.
- Failed turn preparation cleans up newly created unlinked transient files where safe.

## Activity Log

- 2026-06-29 10:53 MYT - Ticket created.
- 2026-06-29 12:01 MYT - Claimed by Codex after completing `VMI-002`; starting chat ingestion compatibility tests.
- 2026-06-29 12:05 MYT - Routed chat preparation through runtime image assets, persisted message-image links, added `assetId` attachment metadata, and kept legacy fetch fallback.

## Validation

- Commands:
  - `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test:server -- apps/server/tests/test_chat_image_assets.py -q`
  - `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test:server -- apps/server/tests/test_chat_attachments.py -q`
- Changed paths:
  - apps/server/src/anima_server/api/routes/chat.py
  - apps/server/src/anima_server/services/agent/attachments.py
  - apps/server/src/anima_server/services/agent/persistence.py
  - apps/server/src/anima_server/services/agent/service.py
  - apps/server/src/anima_server/services/agent/state.py
  - apps/server/tests/test_chat_image_assets.py
- Notes:
  - Existing chat attachment serialization remains backward compatible; new attachments include optional `assetId`.

