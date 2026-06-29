# VMI-003 - Chat ingestion and public attachment compatibility

- Status: backlog
- Priority: P1
- Scope: `apps/server`
- Parent: `VMI-000`
- Depends on: `VMI-002`
- Owner: unassigned
- PRD: docs/prds/memory/visual-memory-image-assets-v1.md
- Plan: docs/superpowers/plans/2026-06-29-visual-memory-image-assets.md
- Created: 2026-06-29 10:53 MYT
- Updated: 2026-06-29 10:53 MYT
- Started:
- Completed:

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

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - none

