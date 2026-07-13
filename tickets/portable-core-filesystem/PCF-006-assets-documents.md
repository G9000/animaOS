# PCF-006 - Gallery, attachments, documents, and knowledge sources

- Status: backlog
- Priority: P1
- Scope: `apps/server` image/document/content services
- Parent: `PCF-000`
- Depends on: `PCF-003`, `PCF-005`
- Owner: unassigned
- PRD: `docs/prds/portable-core-filesystem-v1.md`
- Plan: `docs/superpowers/plans/2026-07-12-portable-core-filesystem.md#task-6-gallery-attachments-and-original-documents`
- Created: 2026-07-12 06:07 MYT
- Updated: 2026-07-12 17:34 MYT
- Started:
- Completed:

## Goal

Move original user-owned images, attachments, documents, pasted text/Markdown, and captured web knowledge into encrypted Core objects while leaving derived ingestion/search state rebuildable in unlocked memory.

## Deliverables

- Binary object streaming, hashes, references, and deletion behavior.
- Image/document source converters covering agent avatar, chat/diary attachments, gallery originals, and original document uploads.
- Canonical text/Markdown/web snapshot objects; Runtime source rows keep only safe Core references, hashes, locators, and progress.
- PDF extraction/reindex through authenticated CoreFS range streams without plaintext temp files.
- Message/gallery/document CoreFS URI reconciliation.
- Stable `core.gallery` role binding so user rename/move never breaks Gallery resolution.
- Derived chunk/OCR/source-span/concept/preview/vector plaintext kept only in unlock-scoped process memory; safe workflow metadata remains in Runtime.

## Acceptance

- Original bytes and metadata survive transfer and Runtime deletion.
- Agent identity assets and message/document references use CoreFS URIs; no feature route returns or trusts legacy host paths.
- Derived indexes rebuild.
- Runtime deletion rebuilds knowledge/document/image derivations from canonical captured sources without network refetch.
- Reference/deletion tests prevent orphaning or host-path escape.
- Gallery resolves by stable role/ID across rename, move, transfer, and restart.

## Activity Log

- 2026-07-12 06:07 MYT - Ticket created.
- 2026-07-12 17:34 MYT - Added stable Gallery role/ID requirements.

## Validation

- Commands:
  - `not run yet`
- Changed paths:
  - none
- Notes:
  - Claim after PCF-003 and PCF-005 are done.
