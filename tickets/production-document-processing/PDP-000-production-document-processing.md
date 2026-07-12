# PDP-000 - Production Document Processing Parent Tracker

- Status: todo
- Priority: P1
- Scope: `apps/server`, `packages/api-client`, `apps/desktop`, `docs/architecture`
- Parent: none
- Depends on: none
- Owner: unassigned
- Model: none
- PRD: none
- Plan: none
- Created: 2026-07-10
- Updated: 2026-07-10

## Goal

Upgrade document processing (PDF, markdown, HTML, plain text) to production grade across every pipeline layer — parsing, chunking, indexing, retrieval, and knowledge compilation — while keeping the existing sources → artifacts/spans → OKF concepts schema, checkpointed workflows, and memory boundary unchanged.

## Background

Diagnosis (2026-07-10) found the schema and workflow skeleton sound but every layer's internals weak:

- Parsing: `pypdf` only, no OCR, HTML adapter expects caller-extracted text, scanned PDFs hard-fail.
- Chunking: paragraph-blind 1800-char chunks, no overlap, no structure awareness (`services/documents/chunking.py`).
- Retrieval: dense-only pgvector; `content_preview` stored "for BM25 support" but never used.
- Prompt injection: `_build_document_context_block` retrieves 5 chunks and truncates each to 900 chars (`services/agent/service.py:1763`, `:2102`) — ~3% of a large PDF per turn, with no way for the agent to fetch more.
- Compiler: `compile_source_knowledge` (`services/ingestion/document_compiler.py`) is a deterministic stub (one summary + one topic per span), opt-in only, never auto-triggered.

External research (2025–2026 state of the art) confirmed the target stack: Docling for parsing (MIT; AGPL alternatives excluded), structure-aware chunking with parent-section retrieval and contextual blurbs, hybrid BM25+dense+RRF retrieval, agentic retrieval tools over one-shot injection, and compile-at-ingest knowledge pages (DeepWiki / GraphRAG-community-summary pattern) — which validates the existing OKF layer as the compilation target.

## Architecture Decisions

1. **Tiered parsing.** Fast path stays light (pypdf for born-digital PDFs, trafilatura for HTML, native markdown/text). Docling is an optional extra (`anima-server[docling]`), lazy-loaded, invoked when fast-path output fails quality checks or OCR is needed. Rejected on license: pymupdf4llm (AGPL), Marker (commercial), Chunkr (AGPL). Rejected on hosting: LlamaParse.
2. **One normalized intermediate.** Every format parses to structured markdown + heading tree + locators before chunking; downstream layers are format-agnostic.
3. **Hybrid retrieval in Postgres.** pgvector + `tsvector` + RRF (k=60) in SQL. No new infrastructure; AGPL Postgres extensions (pg_search, VectorChord-bm25) excluded.
4. **Agentic document tools.** The agent searches/reads documents through tools in its loop; the fixed injection remains only as a first-turn cheap path with corrected limits.
5. **OKF stays the compilation layer.** Real LLM wired into the existing `compile_source_to_concepts` contract; sleep-agent auto-compile; deterministic stub kept as no-LLM fallback. GraphRAG/RAPTOR/LightRAG rejected as heavier, less readable, less portable versions of the same goal.
6. **Memory boundary unchanged.** No automatic promotion into SQLCipher memory; Soul Writer path untouched.

## Child Tickets

| Ticket | Title | Status | Depends on |
| --- | --- | --- | --- |
| `PDP-001` | Chat grounding quick wins: injection limits and chunk overlap | in_review | none |
| `PDP-002` | Hybrid retrieval: BM25 + RRF fusion | in_review | none |
| `PDP-003` | Structured markdown intermediate and structure-aware chunking | in_review | none |
| `PDP-004` | Tiered parsing: Docling quality tier and OCR fallback | in_review | `PDP-003` |
| `PDP-005` | HTML and web capture extraction | in_review | `PDP-003` |
| `PDP-006` | Agentic document tools | in_review | `PDP-002` |
| `PDP-007` | LLM-wiki compiler wiring and sleep-agent auto-compile | in_review | `PDP-002`, `PDP-003` |
| `PDP-008` | Contextual chunk blurbs and optional reranker | in_review | `PDP-002`, `PDP-003` |
| `PDP-009` | Retrieval eval harness, docs, and final validation | in_review | `PDP-001` through `PDP-008` |

Suggested order: PDP-001 and PDP-002 first (days, biggest user-visible payoff), then PDP-003 → PDP-004/PDP-005 in parallel, then PDP-006/PDP-007, then PDP-008, PDP-009 last.

## Deliverables

- Corrected document context injection and overlapping chunks (immediate quality fix).
- Hybrid dense+lexical retrieval for document chunks and source spans.
- Format-agnostic structured-markdown intermediate with heading-aware chunking and parent-section retrieval.
- Docling-backed quality parsing tier with OCR for scanned PDFs, behind an optional dependency.
- Real HTML extraction (trafilatura) in the web adapter.
- Agent tools for iterative document search and reading.
- Real LLM compilation into OKF concepts with sleep-agent triggers and cross-source merge.
- Optional contextual-retrieval blurbs and local reranker stage.
- Retrieval eval harness with a gold query set; updated architecture docs.

## Acceptance

- Every child ticket completed with validation recorded.
- Existing PDF RAG, image annotation, and OKF import/export behavior preserved (regression suites green).
- A multi-page PDF question that previously failed from truncated context answers correctly using the new retrieval path.
- Scanned PDFs ingest successfully when the Docling extra is installed, and fail with a clear message when it is not.
- Compiled concepts carry span citations and merge across sources; lint stays green on the demo corpus.
- Memory boundary regressions: no document content reaches `MemoryItem` without the existing approval path.

## Activity Log

- 2026-07-10 - Epic created from diagnosis + SOTA research + truememory comparison (session work, Claude).
- 2026-07-10 - PDP-001 and PDP-002 implemented in worktree `production-doc-processing`; PDP-002 design revised to reuse the existing BM25Index/RRF memory-search stack instead of a tsvector migration.
- 2026-07-11 - PDP-003 implemented: `services/ingestion/structured.py` intermediate + chunker, markdown adapter emits structured artifact/section spans; PDF workflow switch and section_title population deferred to PDP-004 as designed.
- 2026-07-11 - PDP-004 implemented: tiered parsing (`services/documents/parsing.py`), docling optional extra, PDF workflow on structured chunker with section titles. Manual with-extra validation pending.
- 2026-07-11 - PDP-001 follow-up: document context limits moved from module constants to config-backed settings per the ticket's wording.
- 2026-07-11 - PDP-005 implemented: trafilatura extraction (raw HTML mode + `.html` upload + opt-in SSRF-guarded URL fetch, default off), raw_html artifact retained for idempotent re-extraction.
- 2026-07-12 - PDP-006 implemented: search_documents / get_document_outline / read_document_section agent tools with per-turn text budget, ownership scoping, tool-citation document_source pills, and primer + directive updates.
- 2026-07-12 - PDP-007 implemented: LLM wired into compile_source_to_concepts (merge-aware prompt, write-time citation enforcement, deterministic fallback), sleep-agent knowledge_autocompile task with policy/budget/cooldown.
- 2026-07-12 - PDP-008 implemented: contextual chunk blurbs (flag-gated, embedding/lexical-only) and optional local cross-encoder reranker (flag + extra gated, degrades to fused order); eval gating deferred to PDP-009.
- 2026-07-12 - PDP-009 implemented: retrieval eval harness (30-query gold corpus; hybrid ≥ dense recorded: recall@5 0.867 vs 0.833, nDCG@10 0.828 vs 0.817), architecture docs rewritten, changelog entry. Epic complete in code; closing the parent awaits the deferred manual items in PDP-009 (extras-config suite, desktop build, end-to-end scanned-PDF pass).
- 2026-07-12 - Codex review (PR #87) addressed, all four findings fixed with regression tests: (P1) compiler prompts now batch so every evidence span is visible to the model instead of silently truncating at 80; (P2) RRF ties prefer the lexical exact-token hit (lexical list fuses first); (P2) merged sections keep every section path addressable via chunk metadata consumed by the outline/read tools; (P2) section titles bounded to the 255-char column at chunk insert.
- 2026-07-12 - Codex round-2 findings fixed with regression tests: (P1) duplicate slugs across compile batches coalesce (citations/tags union) so later batches no longer overwrite earlier-batch evidence; (P2) an untitled preamble merged with a single titled section keeps that section addressable (metadata path + outline gate on merged paths); (P2) read_document_section continues inside oversized chunks via start_offset so atomic table/code chunk tails stay reachable.

## Validation

- Commands:
  - See `PDP-009` for final validation commands.
- Changed paths:
  - `tickets/production-document-processing`
- Notes:
  - Parent tracker only; child tickets define executable validation.
