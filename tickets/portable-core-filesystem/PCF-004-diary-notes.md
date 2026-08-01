# PCF-004 - Diary, folders, drafts, and notes

- Status: in_progress
- Priority: P1
- Scope: `apps/server` diary/CoreFS, `apps/desktop` Journal
- Parent: `PCF-000`
- Depends on: `PCF-003`
- Owner: Codex
- PRD: `docs/prds/portable-core-filesystem-v1.md`
- Plan: `docs/superpowers/plans/2026-07-12-portable-core-filesystem.md#task-4-diary-folders-drafts-and-notes-vertical-slice`
- Created: 2026-07-12 06:07 MYT
- Updated: 2026-08-02 04:06 MYT
- Started: 2026-08-02 04:06 MYT
- Completed:

## Goal

Make encrypted sanitized-HTML diary objects plus CoreFS folder, draft, and note objects canonical while preserving the existing rich Journal API and UI behavior without leaving embedded media inline.

## Deliverables

- Versioned sanitized-HTML diary codec, Markdown/sanitized-HTML note codecs, and idempotent SQLCipher conversion; plain diary text becomes escaped HTML paragraphs without lossy Markdown conversion.
- First-class empty/custom folder support; unique `core.journal` and `core.notes` stable-role bindings; default `owner=user`/`agentAccess=write`; and attachment CoreFS URIs.
- Inline `data:` media decoded under MIME/size limits, deduplicated into encrypted CoreFS binary objects, and replaced with stable CoreFS URIs before atomic publication.
- Journal drafts migrated out of plaintext localStorage.
- Backend and Bun desktop tests covering current `Journal.tsx`, content selection, HTML sanitization, covers, and attachments.

## Acceptance

- Existing diary data, folders, covers, and attachments round-trip with stable IDs/hashes.
- Current Tiptap formatting, attachment-only entries, cover-only entries, and valid inline images round-trip; canonical diary HTML contains no base64 `data:` URLs.
- Plain-text and HTML legacy bodies use the same versioned sanitization contract, and malformed/oversized embedded media cannot partially publish a diary revision.
- Empty folders survive migration.
- Journal still resolves after its root is renamed/moved, and ANIMA can read/write private diary content unless the user explicitly lowers access.
- Standalone Notes resolve through the same stable folder ID after rename, move, and restart; their root defaults to `owner=user`/`agentAccess=write`.
- Journal drafts are encrypted Core objects and UI behavior remains functional.

## Activity Log

- 2026-07-12 06:07 MYT - Ticket created.
- 2026-07-12 17:34 MYT - Added stable Journal role and explicit private-diary ownership/access defaults.
- 2026-07-12 18:58 MYT - Added the `core.notes` root, ownership defaults, and rename/move/restart acceptance coverage.
- 2026-07-13 20:47 MYT - Reconciled migration with the merged Tiptap Journal: canonicalized sanitized HTML, extracted inline media to CoreFS objects, and added the current content/sanitizer helpers and tests to scope.
- 2026-08-02 04:06 MYT - Claimed PCF-004 from clean `main` at `51678d08680747d90ff0c03c0e091331456ae837` after PCF-003 completion. Executing the approved Task 4 plan directly in the main checkout per user direction, with test-first backend and Journal slices followed by independent specification and quality review.

## Validation

- Commands:
  - `not run yet`
- Changed paths:
  - none
- Notes:
  - Claim after PCF-003 is done.
