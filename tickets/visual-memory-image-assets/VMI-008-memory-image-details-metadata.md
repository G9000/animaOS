# VMI-008 - Memory image details metadata enrichment

- Status: done
- Priority: P2
- Scope: `apps/server`, `apps/desktop`, `packages/api-client`
- Parent: `VMI-000`
- Depends on: `VMI-006`
- Owner: Codex
- PRD: docs/prds/memory/visual-memory-image-assets-v1.md
- Plan: docs/superpowers/plans/2026-06-29-visual-memory-image-assets.md
- Created: 2026-07-01 13:04 MYT
- Updated: 2026-07-01 13:04 MYT
- Started: 2026-07-01 13:04 MYT
- Completed: 2026-07-01 13:04 MYT

## Goal

Improve the visual memory image detail experience by showing embedded metadata per image so users can verify identity and provenance without opening the raw source.

## Deliverables

- Added detail panel metadata rows in memory image gallery for:
  - MIME type, formatted size, thread/message/entry identifiers, and fingerprint hash when available.
- Surface per-source metadata in related-source rows, including metadata snippets (type/size/hash).
- Kept duplicate source counting and related source navigation behavior unchanged.
- Extended server chat attachment payload to include `sha256`.
- Extended API client attachment type to include `sha256`.
- Extended desktop image model/reference mapping to carry and render optional metadata.

## Acceptance

- Memory image details show richer non-editable metadata for both chat and diary sources.
- Related sources show source-level metadata when available.
- Chat and diary attachments expose `sha256` in a consistent shape to desktop.
- No behavior change to existing image open/delete/replacement flows.

## Activity Log

- 2026-07-01 13:04 MYT - Created ticket for metadata visibility in memory image detail panel.
- 2026-07-01 13:04 MYT - Added server/schema/api-client/desktop metadata propagation and UI rendering.

## Validation

- Commands were not run in this pass.
- Changed paths:
  - apps/desktop/src/lib/image-memories.ts
  - apps/desktop/src/pages/memory/MemoryImages.tsx
  - apps/server/src/anima_server/schemas/chat.py
  - apps/server/src/anima_server/services/agent/state.py
  - packages/api-client/src/types.ts
