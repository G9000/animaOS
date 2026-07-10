# PDP-003 - Structured Markdown Intermediate and Structure-Aware Chunking

- Status: todo
- Priority: P1
- Scope: `apps/server`
- Parent: `PDP-000`
- Depends on: none
- Owner: unassigned
- Created: 2026-07-10
- Updated: 2026-07-10

## Goal

Normalize every parsed format to one intermediate — structured markdown with a heading tree and locators — and chunk along that structure instead of blind paragraphs. Downstream layers (indexing, tools, compiler) become format-agnostic.

## Design

- **Intermediate representation.** A `StructuredDocument` value object: ordered blocks (heading level, paragraph, table, code, figure-caption) each carrying a locator (page and/or char offset) and a section path derived from the heading hierarchy (e.g. `"3. Results > 3.2 Retention"`). Serialized as a `runtime_source_artifacts` row (`kind="structured_markdown"`) so it is rebuildable and inspectable. Existing artifact kinds unchanged.
- **Producers.** Markdown adapter parses its own heading tree (it already preserves headings in span metadata — extend to full tree). PDF fast path maps page text into the structure with best-effort heading detection. Docling (PDP-004) and trafilatura (PDP-005) map their native trees in directly.
- **Chunker.** Split on section boundaries, merge small sections, cap sections with recursive splitting to a 256–512 token target (approximate tokens as chars/4; no tokenizer dependency yet). Tables and code blocks atomic. Overlap only within a section. Each chunk/span records `section_path`, locators, and its parent-section span id.
- **Parent-section retrieval.** Store section-level spans alongside chunk-level spans (`span_kind="section"` vs `"chunk"`). Retrieval matches on chunks, can hand the model the parent section. Embeddings only for chunk spans initially.
- **Compatibility.** `chunk_pages` for the legacy PDF path keeps working; the new chunker is used when a structured artifact exists. `runtime_document_chunks.section_title` gets populated from the section path.

## Acceptance

- Ingesting a markdown file with nested headings produces spans whose `section_path` matches the document outline.
- A PDF ingested via the fast path still produces valid chunks (fallback structure = per-page sections).
- Chunk sizes fall within target bounds except atomic tables/code.
- Parent-section span reachable from any chunk span; round-trips through source read API (`GET /api/knowledge/sources/{id}`).
- Existing adapter and ingestion tests green; new chunker unit tests cover merge/cap/atomic rules.

## Validation

- Commands: server test suite (ingestion + adapters + chunking subsets), ruff.
- Changed paths: `apps/server/src/anima_server/services/ingestion/` (models, adapters, new chunker), `apps/server/src/anima_server/services/documents/chunking.py`, tests.
