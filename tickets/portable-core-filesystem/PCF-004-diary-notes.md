# PCF-004 - Diary, folders, drafts, and notes

- Status: backlog
- Priority: P1
- Scope: `apps/server` diary/CoreFS, `apps/desktop` Journal
- Parent: `PCF-000`
- Depends on: `PCF-003`
- Owner: unassigned
- PRD: `docs/prds/portable-core-filesystem-v1.md`
- Plan: `docs/superpowers/plans/2026-07-12-portable-core-filesystem.md#task-4-diary-folders-drafts-and-notes-vertical-slice`
- Created: 2026-07-12 06:07 MYT
- Updated: 2026-07-12 18:58 MYT
- Started:
- Completed:

## Goal

Make encrypted Markdown/folder/draft/note objects canonical while preserving existing diary API and UI behavior.

## Deliverables

- Diary/note codecs and idempotent SQLCipher conversion.
- First-class empty/custom folder support; unique `core.journal` and `core.notes` stable-role bindings; default `owner=user`/`agentAccess=write`; and attachment CoreFS URIs.
- Journal drafts migrated out of plaintext localStorage.
- Backend and Bun desktop tests.

## Acceptance

- Existing diary data, folders, covers, and attachments round-trip with stable IDs/hashes.
- Empty folders survive migration.
- Journal still resolves after its root is renamed/moved, and ANIMA can read/write private diary content unless the user explicitly lowers access.
- Standalone Notes resolve through the same stable folder ID after rename, move, and restart; their root defaults to `owner=user`/`agentAccess=write`.
- Journal drafts are encrypted Core objects and UI behavior remains functional.

## Activity Log

- 2026-07-12 06:07 MYT - Ticket created.
- 2026-07-12 17:34 MYT - Added stable Journal role and explicit private-diary ownership/access defaults.
- 2026-07-12 18:58 MYT - Added the `core.notes` root, ownership defaults, and rename/move/restart acceptance coverage.

## Validation

- Commands:
  - `not run yet`
- Changed paths:
  - none
- Notes:
  - Claim after PCF-003 is done.
