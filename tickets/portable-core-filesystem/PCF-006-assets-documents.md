# PCF-006 - Gallery, attachments, documents, and knowledge sources

- Status: backlog
- Priority: P1
- Scope: `apps/server` image, document parsing/tools/contextual retrieval, ingestion/compiler, and content services
- Parent: `PCF-000`
- Depends on: `PCF-003`, `PCF-005`
- Owner: unassigned
- PRD: `docs/prds/portable-core-filesystem-v1.md`
- Plan: `docs/superpowers/plans/2026-07-12-portable-core-filesystem.md#task-6-gallery-attachments-and-original-documents`
- Created: 2026-07-12 06:07 MYT
- Updated: 2026-07-13 20:47 MYT
- Started:
- Completed:

## Goal

Move original user-owned images, attachments, documents, pasted text/Markdown, and captured web knowledge into encrypted Core objects while leaving derived ingestion/search state rebuildable in unlocked memory.

## Deliverables

- Binary object streaming, hashes, references, and deletion behavior.
- Image/document source converters covering agent avatar, chat/diary attachments, gallery originals, and original document uploads.
- Canonical text/Markdown sources plus original raw-HTML and normalized web-snapshot revisions; Runtime source rows keep only safe Core references, hashes, locators, and progress.
- Document parsing, PDF extraction/reindex, and agent document reads through authenticated CoreFS byte/range sources without host-path authority or plaintext temp files.
- Message/gallery/document CoreFS URI reconciliation.
- Stable `core.gallery` role binding so user rename/move never breaks Gallery resolution.
- Derived chunk/OCR/source-span/context-blurb/concept/citation/preview/vector plaintext kept only in unlock-scoped process memory; safe workflow metadata remains in Runtime.
- Explicit migration of current `RuntimeDocument`, `RuntimeDocumentChunk`, `RuntimeSourceArtifact`, `RuntimeSourceSpan`, `RuntimeKnowledgeConcept`, and `RuntimeKnowledgeConceptSource` plaintext fields.
- Regression coverage for the merged in-memory reranker, structured-document, HTML-extraction, and guarded web-fetch adapters so they cannot introduce a new persistent or host-path authority boundary unnoticed.

## Acceptance

- Original bytes and metadata survive transfer and Runtime deletion.
- Agent identity assets and message/document references use CoreFS URIs; no feature route returns or trusts legacy host paths.
- Derived indexes rebuild.
- Runtime deletion rebuilds knowledge/document/image derivations from canonical captured sources without network refetch.
- Original raw HTML and its normalized snapshot survive transfer so re-extraction and exact-revision rebuild are both possible offline.
- Agent document tools read the unlocked in-memory index and canonical CoreFS sources, while contextual blurbs and compiled concept/citation bodies leave no plaintext PostgreSQL copy.
- Reference/deletion tests prevent orphaning or host-path escape.
- Gallery resolves by stable role/ID across rename, move, transfer, and restart.

## Activity Log

- 2026-07-12 06:07 MYT - Ticket created.
- 2026-07-12 17:34 MYT - Added stable Gallery role/ID requirements.
- 2026-07-13 20:47 MYT - Added merged document parsing, document tools, contextual ranking, and compiler boundaries; classified current plaintext fields and expanded the existing ingestion/retrieval regression matrix.

## Validation

- Commands:
  - `not run yet`
- Changed paths:
  - none
- Notes:
  - Claim after PCF-003 and PCF-005 are done.
