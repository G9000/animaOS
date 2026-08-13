# PCF-006 - Gallery, attachments, documents, and knowledge sources

- Status: done
- Priority: P1
- Scope: `apps/server` image, document parsing/tools/contextual retrieval, ingestion/compiler, and content services
- Parent: `PCF-000`
- Depends on: `PCF-003`, `PCF-005`
- Owner: Codex
- PRD: `docs/prds/portable-core-filesystem-v1.md`
- Plan: `docs/superpowers/plans/2026-07-12-portable-core-filesystem.md#task-6-gallery-attachments-and-original-documents`
- Created: 2026-07-12 06:07 MYT
- Updated: 2026-08-13 12:41 MYT
- Started: 2026-08-13 11:39 MYT
- Completed: 2026-08-13 12:41 MYT

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
- 2026-08-13 11:39 MYT - Codex claimed PCF-006 after PCF-005 completed locally at `d3ab653e` and PCF-003 was already done. Started on local stacked branch `codex/pcf-006-assets-documents`; the child and parent row moved to `in_progress` together. PCF-004 remains independently open on cost-deferred package evidence, and no PCF-006 publication, PR, review, monitoring, or merge action is authorized.
- 2026-08-13 12:41 MYT - Completed PCF-006 locally. One atomic inactive preparation now preserves gallery originals, agent avatars, original documents, pasted/Markdown content, raw HTML, normalized captures, and conversation attachment CoreFS URIs under stable `core.gallery`; native admission enforces binary/UTF-8 kind bounds and stable user/write policy. Authenticated bounded byte sources drive streaming image responses and pathless PDF parsing. Runtime deletion rebuilds unlock-only image, document, source, evidence, and deterministic concept/citation projections without network refetch, while lock/rebuild scrubs stale plaintext and post-cutover legacy mutations fail before Runtime access. PCF-008 remains the authenticated global authority/mutation activation boundary. No PCF-006 external action was authorized or performed.

## Validation

- Commands:
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run pytest -q <PCF-006 focused files plus test_corefs_indexer.py>` -> `219 passed, 1 deselected`
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run pytest -q <adjacent CoreFS conversation/migration, document API/store/reparse, and image regression files>` -> `121 passed`
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run pytest -q apps/server/tests/test_corefs_runtime_privacy.py` -> `52 passed`
  - `cargo test -p anima-corefs --test validation_batch` -> `9 passed`
  - `cargo clippy -p anima-corefs --all-targets -- -D warnings` -> passed
  - `PYO3_PYTHON=/Users/julio/animaOS/.venv/bin/python cargo check -p anima-core --features python` -> passed
  - `rustfmt --edition 2021 --check packages/anima-core/src/ffi.rs packages/anima-corefs/src/transaction.rs packages/anima-corefs/src/transaction/converter.rs packages/anima-corefs/tests/validation_batch.rs` -> passed
  - `bun run lint:server` -> passed
  - `bun run build` -> passed
  - `bun run check:repo` -> passed
  - `git diff --check` -> passed
- Changed paths:
  - `packages/anima-corefs/src/transaction.rs`, `packages/anima-corefs/src/transaction/converter.rs`, `packages/anima-corefs/tests/validation_batch.rs`
  - `packages/anima-core/src/ffi.rs`
  - `apps/server/src/anima_server/services/corefs/{asset_authority.py,asset_migration.py,conversation_migration.py,indexer.py,migration.py,writing_source.py}`
  - `apps/server/src/anima_server/services/{documents,images,ingestion}`, `apps/server/src/anima_server/services/agent/document_tools.py`, `apps/server/src/anima_server/services/sessions.py`
  - `apps/server/src/anima_server/api/routes/{consciousness.py,images.py,knowledge.py}`
  - `apps/server/tests/test_corefs_{assets,document_migration,knowledge_sources,indexer,migration}.py`, `apps/server/tests/test_document_{parsing,tools}.py`
  - `docs/superpowers/plans/2026-07-12-portable-core-filesystem.md`, `tickets/portable-core-filesystem/{PCF-000-portable-core-filesystem.md,PCF-006-assets-documents.md}`
- Notes:
  - The plan's `apps/server/tests/test_pdf_workflow.py` path does not exist; the canonical available `test_pdf_workflow_checkpoints.py` plus document parsing/store/reparse/API coverage passed.
  - Persistent Runtime private fields remain protected by PCF-003's unlock-derived sealing before PCF-008; PCF-006 adds canonical CoreFS rebuild sources and unlock-only post-deletion projections. PCF-008 owns the authenticated global cutover and writable CoreFS mutation activation.
  - PCF-007 remains dependency-ineligible solely because PCF-004 still awaits the user-deferred paid four-platform signed-package evidence. PCF-011 is the next dependency-eligible backlog child.
