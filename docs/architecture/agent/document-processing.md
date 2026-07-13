---
title: Document Processing Architecture
description: PDF upload, tiered parsing, checkpointed ingestion, structured chunking, hybrid RAG, agentic document tools, and chat citation behavior
category: architecture
---

# Document Processing Architecture

This document describes the current document-processing path in AnimaOS. The implemented user-facing document format is PDF. Documents are runtime context for chat and RAG; they are not automatically promoted into durable SQLCipher memory.

PDF ingestion also feeds the universal source-ingestion layer described in
[Source Ingestion](source-ingestion.md). The PDF workflow keeps its existing
`runtime_documents`, `runtime_document_chunks`, and `document_chunk` embeddings
for chat RAG, then mirrors indexed documents and chunks into `runtime_sources`
and `runtime_source_spans` so they can participate in OKF concept compilation,
bundle export, linting, and source evidence drilldown.

## Scope

Document processing covers five responsibilities:

1. Accept a PDF from the desktop chat UI.
2. Store the uploaded file under the local runtime data root.
3. Run a resumable PDF ingestion workflow that extracts text, chunks it, embeds chunks, and marks the document indexed.
4. Retrieve relevant chunks during chat and inject them as a high-priority document context block.
5. Render visible attachment/source pills so the user can see which PDF was attached or cited.

The source of truth is:

| Area | Implementation |
| --- | --- |
| Desktop upload and chat attachment UI | `apps/desktop/src/pages/chat/Chat.tsx` |
| API client methods | `packages/api-client/src/client.ts` |
| Document REST routes | `apps/server/src/anima_server/api/routes/documents.py` |
| Chat REST/SSE route | `apps/server/src/anima_server/api/routes/chat.py` |
| PDF workflow | `apps/server/src/anima_server/services/documents/pdf_workflow.py` |
| Document storage helpers | `apps/server/src/anima_server/services/documents/store.py` |
| PDF text extraction | `apps/server/src/anima_server/services/documents/pdf_text.py` |
| Chunking | `apps/server/src/anima_server/services/documents/chunking.py` |
| Chunk embedding | `apps/server/src/anima_server/services/documents/indexing.py` |
| Document RAG search | `apps/server/src/anima_server/services/documents/rag.py` |
| Source ingestion bridge | `apps/server/src/anima_server/services/ingestion/adapters/documents.py` |
| OKF/knowledge routes | `apps/server/src/anima_server/api/routes/knowledge.py` |
| Chat prompt assembly | `apps/server/src/anima_server/services/agent/service.py` |
| Message persistence and pills | `apps/server/src/anima_server/services/agent/persistence.py` |
| Runtime models | `apps/server/src/anima_server/models/runtime.py` |
| Runtime embedding model | `apps/server/src/anima_server/models/runtime_embedding.py` |

Image uploads are intentionally handled by a separate visual-memory path:
`runtime_image_assets`, `runtime_image_message_links`, `runtime_image_annotations`,
and `RuntimeEmbedding.source_type = "image_annotation"`. The PDF pipeline should not
copy PDFs into image assets. Future unified media recall should compose document
results and image results at the prompt/source-pill layer. The source-ingestion
layer now provides that shared evidence contract by mirroring PDF chunks and image
annotations into source spans while leaving the original document/image pipelines
as the source-specific operational paths.

## End-to-End Flow

```mermaid
sequenceDiagram
    participant UI as Desktop Chat UI
    participant API as FastAPI documents/chat routes
    participant WF as PDF ingestion workflow
    participant RT as Runtime PostgreSQL
    participant VEC as pgvector embeddings
    participant Agent as Agent prompt assembly
    participant LLM as Model

    UI->>API: POST /api/documents/pdf
    API->>RT: create runtime_workflow_runs row
    API->>UI: workflowId and uploaded document metadata
    UI->>API: POST /api/documents/workflows/{id}/resume
    API->>WF: resume pdf_ingestion
    WF->>RT: register runtime_documents row
    WF->>RT: store runtime_document_chunks rows
    WF->>VEC: upsert embeddings source_type=document_chunk
    WF->>RT: mark document status indexed
    UI->>API: POST /api/chat with documentIds
    API->>Agent: run/stream turn with selected document ids
    Agent->>VEC: search selected document chunks
    Agent->>LLM: document directive + document context
    LLM->>API: assistant response
    API->>RT: persist assistant document_source pill
    UI->>API: refresh thread messages
    API->>UI: messages with PDF and Cited PDF pills
```

## Upload And Workflow Creation

The desktop chat page accepts PDF files through the same attachment control as images. When the user selects a PDF, `Chat.tsx` starts indexing immediately:

1. `api.documents.uploadPdf(userId, file, threadId)` posts multipart form data to `POST /api/documents/pdf`.
2. The server validates the unlock session, optional thread ownership, PDF MIME type, non-empty body, configured size limit, and `%PDF-` file header.
3. The server sanitizes the filename, computes SHA-256, writes the file under the configured data root, and starts a `pdf_ingestion` workflow.
4. The desktop immediately calls `api.documents.resumeWorkflow(workflowId)`.
5. When resume returns a document id, the selected PDF becomes eligible for chat submission.

There is also `POST /api/documents/workflows/pdf` for starting a workflow from an already-stored relative document path. Both routes use `PDFIngestionRequest` and create a row in `runtime_workflow_runs`.

## Runtime Storage Model

Documents live in the runtime PostgreSQL store, not the encrypted soul database.

| Runtime table | Purpose |
| --- | --- |
| `runtime_workflow_runs` | One resumable workflow run, including `workflow_type`, `status`, `current_state`, input, result, and error JSON |
| `runtime_workflow_checkpoints` | Ordered idempotent checkpoints for each completed or waiting workflow state |
| `runtime_documents` | Per-user document metadata: filename, MIME type, storage path, SHA-256, size, status, indexed timestamp, optional thread/workflow ids |
| `runtime_document_chunks` | Extracted text chunks with chunk index, page range, content hash, token count, section title, and metadata |
| `runtime_sources`, `runtime_source_artifacts`, `runtime_source_spans` | Universal source-ingestion mirror for indexed documents and chunks |
| `runtime_knowledge_concepts`, `runtime_knowledge_concept_sources`, `runtime_knowledge_links` | Optional compiled OKF/LLM-wiki concepts, citations, and links derived from source spans |
| `embeddings` | pgvector rows for document chunks and other runtime-search sources |
| `runtime_messages` | User/assistant chat rows, including serialized document attachment/source pills in `content_json` |

`runtime_documents` is unique on `(user_id, sha256)`, so uploading the same file for the same user reuses the existing document row. The document row is still runtime state: it can support local retrieval and workflow resumption, but it is not the portable memory authority.

## Checkpointed PDF Ingestion

The PDF workflow is state-based and resumable. `resume_pdf_ingestion_workflow()` calls `run_pdf_ingestion_until_wait_or_done()`, which loads the latest completed checkpoint and continues from the next state.

The current workflow states are:

| State | Behavior |
| --- | --- |
| `created` | Workflow row exists but no document work has been recorded yet |
| `file_registered` | Creates or reuses a `runtime_documents` row |
| `text_extracted` | Reads the PDF from the stored path and stores page text in the checkpoint output |
| `chunked` | Converts page text into `runtime_document_chunks` rows |
| `embedded` | Embeds every unembedded chunk into the runtime `embeddings` table |
| `indexed` | Confirms the document is fully indexed |
| `summarized` | Produces a workflow summary object |
| `facts_proposed` | Stages candidate facts, if the dependency supplies any |
| `awaiting_approval` | Pauses with summary and proposed facts in `result_json` |
| `memory_saved` | Creates `MemoryCandidate` rows for approved proposed facts |
| `memory_rejected` | Completes without creating memory candidates |
| `completed` | Terminal workflow state |

Every completed state appends a `runtime_workflow_checkpoints` row with an idempotency key of the form `pdf:{workflow_run_id}:{state_name}`. This lets the workflow resume after partial progress without duplicating completed work.

### Restart And Repair Behavior

The workflow deliberately reuses durable intermediate artifacts:

- If a document is already indexed and has chunks plus embeddings, resume can skip extraction and chunking.
- If the workflow resumes after `text_extracted`, it can reuse the page text stored in checkpoint output.
- If it resumes after `chunked`, it can reuse stored chunks and continue embedding.
- If document chunks changed, `replace_document_chunks()` deletes stale chunk rows and stale `document_chunk` embeddings before inserting replacements.
- If a document is marked indexed but embeddings are missing after an embedding-table reset, document search and approval paths attempt to re-embed missing chunks.

## Tiered Text Extraction And Structured Chunking

Parsing is tiered (`services/documents/parsing.py`, `ANIMA_DOCUMENT_PARSER_TIER`):

- **fast** — `pypdf` only. Rejects unreadable PDFs, attempts empty-password
  decryption, rejects locked PDFs, extracts page-by-page, and normalizes
  spacing.
- **quality** — Docling (optional `anima-server[docling]` extra, lazy-loaded):
  layout analysis, table structure, and OCR for scanned pages, emitting
  markdown per page.
- **auto** (default) — fast path first, escalating to Docling when extraction
  quality looks poor or pages are scanned. Without the extra installed,
  scanned PDFs fail with a clear message naming the extra.

Chunking is structure-aware (`chunk_pages_structured()` over the
`services/ingestion/structured.py` intermediate):

- Page text (plain or Docling markdown) is parsed into heading/paragraph/
  table/code blocks and grouped into heading-path sections.
- Chunks follow section boundaries: small adjacent sections merge toward the
  target size, oversized sections split at paragraph boundaries with a
  200-character overlap carried between parts, and tables/code are atomic.
- Each chunk records `chunk_index`, `content_text`, `page_start`, `page_end`,
  and `section_title` (the heading path, e.g. `Guide > Inspection`).

With `ANIMA_CONTEXTUAL_CHUNKS=on` (default off), each chunk additionally gets
an LLM-generated context blurb at the embed step, stored in chunk metadata
and prepended to the embedding and lexical-index text only — evidence text
shown to the model or user never includes it.

## Embedding And Indexing

`embed_document_chunks()` finds chunks that do not have a current matching embedding row. A matching embedding row must have:

- the same `user_id`,
- `source_type = "document_chunk"`,
- `source_id = runtime_document_chunks.id`,
- matching `content_hash`.

For each missing chunk, it calls the configured embedding function, then writes through `PgVecStore.upsert_source()` into the runtime `embeddings` table. Document chunk embeddings use:

| Embedding field | Value |
| --- | --- |
| `source_type` | `document_chunk` |
| `source_id` | `runtime_document_chunks.id` |
| `category` | `document` |
| `importance` | `3` |
| `content_hash` | SHA-256 of chunk text |
| `content_preview` | first 200 characters for debugging and BM25 support |

If every current chunk has an embedding, the document status becomes `indexed` and `indexed_at` is set. If an embedding provider returns no vector for any chunk, the document stays `registered` so callers know indexing is incomplete.

After chunk indexing, the document adapter bridge can sync the document into
`runtime_sources` and its chunks into `runtime_source_spans` with page locators.
This sync is additive: it does not delete or replace the document chunk rows or
their `document_chunk` embeddings.

## Document RAG Search

`search_document_chunks()` retrieves indexed document chunks with hybrid
dense + lexical retrieval.

The search path is:

1. Resolve live chunk ids from `runtime_documents` joined to `runtime_document_chunks`.
2. Generate an embedding for the user query.
3. Repair indexed documents that are missing current vectors, when possible.
4. Dense arm: pgvector through `PgVecStore.search_by_vector()`.
5. Lexical arm: BM25 over the same live chunks (reusing the memory-search
   `BM25Index`); a lexical failure degrades to dense-only ordering, never to
   empty results.
6. Fuse both rankings with reciprocal-rank fusion (k=60).
7. Optional rerank stage (`ANIMA_RETRIEVAL_RERANKER=local`, `reranker`
   extra): over-fetch to `ANIMA_RETRIEVAL_RERANK_CANDIDATES` (50), score with
   a local cross-encoder, return top-k. Flag off, extra absent, or any
   failure keeps the fused order.
8. Hydrate hits back into chunk and document rows.
9. Return `DocumentRagResult` objects with filename, page range, section title, chunk text, and similarity.

Retrieval quality is measured by the eval harness in
`apps/server/tests/test_retrieval_eval.py` (marker `retrieval_eval`,
non-default): a gold corpus plus ~30 queries reporting recall@5, recall@15,
and nDCG@10 per configuration. Pipeline changes are gated on those aggregate
numbers, not tuned against individual queries.

When called with explicit `document_ids`, search is constrained to those documents. When called without document ids, the route-level document search API can search all indexed documents for the user. Chat prompt assembly uses explicit or active thread document ids rather than searching every document by default.

## Chat Grounding

The chat API accepts `documentIds` separately from image attachments. The desktop sends `documentIds` only after the PDF upload workflow returns an indexed document id.

During turn preparation:

1. `append_user_message()` persists the user message.
2. Selected document ids are stored as `document_attachment` pills on the user message.
3. `_assemble_turn_context()` resolves the effective document ids:
   - explicit ids from the current request win,
   - otherwise it reuses the latest visible document attachment/source pills in the same thread.
4. `_build_document_context_block()` builds a first-turn primer: up to
   `ANIMA_DOCUMENT_CONTEXT_CHUNK_LIMIT` (15) relevant chunks passed through
   whole (bounded only by the `ANIMA_DOCUMENT_CONTEXT_CHUNK_CHAR_CAP` safety
   cap, 2500), compiled-knowledge hits, the selected-document list, and a
   hint that the document tools exist.
5. If document context exists, the model receives only:
   - a per-turn `user_directive`,
   - the `document_context` block,
   - optional same-day context.

Personal memory blocks are intentionally omitted for document-grounded turns. This prevents ambiguous prompts such as "what do you see" from being answered from relationship memory or image-style context when a PDF can answer the question.

The document directive tells the model:

- the user is primarily asking about the selected PDF,
- document context should be used first when relevant,
- ambiguous wording should be interpreted as asking about the selected PDF,
- personal memory and stylistic inference should not replace document evidence,
- the injected excerpts are an orientation sample: investigate with the
  document tools before concluding content is missing,
- missing evidence should be reported plainly.

## Agentic Document Tools

The injected block is only a primer; the agent investigates documents
iteratively through tools registered in the core tool set
(`services/agent/document_tools.py`):

| Tool | Behavior |
| --- | --- |
| `search_documents(query, document_ids, scope, limit)` | Hybrid retrieval over indexed chunks; defaults to the conversation's active documents, `scope="all"` searches the whole library |
| `get_document_outline(document_id)` | Section tree (section paths, page ranges, sizes) from chunk `section_title`; per-chunk page outline for legacy documents without structure |
| `read_document_section(document_id, section_path, page_start, page_end, start_chunk)` | Full section or page-range text, bounded per call by `ANIMA_DOCUMENT_TOOL_READ_CHAR_LIMIT` (6000) with `start_chunk` continuation |

Guardrails:

- Total tool-fetched document text per turn is capped by
  `ANIMA_DOCUMENT_TOOL_TURN_CHAR_BUDGET` (40k chars); over-budget calls
  return a truncation notice, never an error.
- Every lookup is ownership-scoped: another user's documents read as
  nonexistent.
- Documents cited by tools fold into `document_source` pills on the
  assistant message (deduplicated against the injected-context pills), so
  provenance UX works even for turns that started without a document block.

## Active Document Follow-Ups

Follow-up turns do not need to resend `documentIds` from the UI. The backend treats the latest visible document pill in the thread as the active document context:

1. `_recent_thread_document_ids()` scans recent visible messages in reverse sequence order.
2. It ignores internal assistant/tool machinery.
3. It looks for `document_attachment` and `document_source` pills.
4. It validates that the referenced document still belongs to the user.
5. It stops at the most recent message containing document pills.

This is state-based, not phrase-based. The backend does not guess from strings like "tell me more" or "what about it". Attaching a different PDF creates newer document pills, so the active document shifts to the newer selection.

## Citation Pills

Pills are small provenance badges stored in message `content_json` and returned through chat history and thread display APIs.

| Pill kind | Written on | Meaning |
| --- | --- | --- |
| `document_attachment` | user message | The user attached or selected this PDF for the turn |
| `document_source` | assistant message or visible `send_message` tool row | The answer used retrieved context from this PDF |
| `image_source` | assistant message or visible `send_message` tool row | The answer used image input |

Assistant source pills carry the document filename and document id when known. The desktop renders `document_attachment` as `PDF <filename>` and `document_source` as `Cited PDF <filename>`.

The desktop also refreshes thread messages after a completed response. This matters for follow-up turns: an optimistic streamed assistant message may not know the active document, but the persisted server message does.

## Document Memory Boundary

Uploaded PDFs do not automatically become long-term memory.

The boundary is:

- Raw document metadata, text chunks, and chunk embeddings live in runtime PostgreSQL.
- Retrieved chunks are prompt context for the current turn.
- The workflow may stage proposed facts in `awaiting_approval`.
- Only approved proposals create `MemoryCandidate` rows.
- Soul Writer promotion is still required before candidates become durable `MemoryItem` records in SQLCipher.

The default API dependency currently indexes and summarizes PDFs but does not propose facts by default. The approval path exists for workflows that provide proposed facts.

## API Surface

| Endpoint | Purpose |
| --- | --- |
| `POST /api/documents/pdf` | Upload a PDF file, store it, and create a PDF ingestion workflow |
| `POST /api/documents/workflows/pdf` | Start a PDF workflow for an already stored relative path |
| `GET /api/documents/workflows/{workflow_id}` | Read workflow status, result, and checkpoints |
| `POST /api/documents/workflows/{workflow_id}/resume` | Continue ingestion from the latest completed checkpoint |
| `POST /api/documents/workflows/{workflow_id}/approve-memory` | Approve staged PDF memory proposals |
| `POST /api/documents/search` | Search indexed document chunks |
| `GET /api/knowledge/sources` | List universal source-ingestion rows, including mirrored PDFs |
| `GET /api/knowledge/concepts` | List compiled OKF/LLM-wiki concept pages derived from source spans |
| `POST /api/chat` | Send chat text, images, context messages, today context, and selected `documentIds` |
| `GET /api/chat/history` | Return messages with serialized attachments, retrieval traces, and pills |

## Failure Modes

| Failure | Behavior |
| --- | --- |
| Unsupported upload type | `POST /api/documents/pdf` returns a 400 |
| Empty upload | 400 |
| File larger than configured attachment limit | 413 |
| Invalid PDF header | 400 |
| Unreadable PDF | resume returns an error |
| Password-protected encrypted PDF | resume returns an error |
| No extractable text (scanned PDF) | with the `docling` extra, OCR runs; without it, resume fails with a message naming the extra |
| Embedding provider unavailable or returns no vector | document remains unindexed; callers can resume later |
| Stale chunk embeddings | search and approval paths try to re-embed or reject incomplete indexing |

## Flags And Extras

| Setting / extra | Default | Effect |
| --- | --- | --- |
| `ANIMA_DOCUMENT_PARSER_TIER` | `auto` | `fast` (pypdf), `quality` (Docling), `auto` (escalate on poor quality/scans) |
| `anima-server[docling]` extra | not installed | Enables the quality parsing tier and OCR |
| `ANIMA_DOCUMENT_CONTEXT_CHUNK_LIMIT` | 15 | Chunks retrieved for the injected primer |
| `ANIMA_DOCUMENT_CONTEXT_CHUNK_CHAR_CAP` | 2500 | Safety cap on primer chunk text (no routine truncation) |
| `ANIMA_DOCUMENT_TOOL_TURN_CHAR_BUDGET` | 40000 | Per-turn cap on tool-fetched document text |
| `ANIMA_DOCUMENT_TOOL_READ_CHAR_LIMIT` | 6000 | Per-call cap for `read_document_section` |
| `ANIMA_CONTEXTUAL_CHUNKS` | `off` | LLM context blurbs prepended to embedding/lexical index text |
| `ANIMA_RETRIEVAL_RERANKER` | `off` | `local` enables the cross-encoder rerank stage |
| `anima-server[reranker]` extra | not installed | sentence-transformers for the local reranker |
| `ANIMA_KNOWLEDGE_COMPILER` | `llm` | OKF concept compilation backend (`deterministic` forces the stub) |
| `ANIMA_KNOWLEDGE_AUTOCOMPILE` | `markdown_only` | Sleep-agent auto-compile policy (`off`, `markdown_only`, `all`) |

## Test Coverage

Important regression coverage lives in:

| Test file | Coverage |
| --- | --- |
| `apps/server/tests/test_documents_api.py` | document route validation and workflow API behavior |
| `apps/server/tests/test_pdf_text.py` | PDF text extraction behavior |
| `apps/server/tests/test_document_parsing.py` | tiered parsing, quality escalation, Docling extra absence |
| `apps/server/tests/test_document_chunking.py` | paragraph chunking, overlap, structured chunking |
| `apps/server/tests/test_structured_document.py` | markdown/page structure parsing and section chunking |
| `apps/server/tests/test_document_rag.py` | embedding, pgvector source rows, hybrid search, stale vector repair |
| `apps/server/tests/test_contextual_rerank.py` | contextual blurbs and reranker gating/degradation |
| `apps/server/tests/test_pdf_workflow_checkpoints.py` | checkpoint resume, idempotency, approvals, embedding reset repair |
| `apps/server/tests/test_chat_document_context.py` | document context block construction |
| `apps/server/tests/test_document_tools.py` | agentic document tools, budget, ownership, citation pills |
| `apps/server/tests/test_agent_service.py` | chat document grounding, memory exclusion, source pills, active document follow-ups |
| `apps/server/tests/test_agent_persistence.py` | persistence of message pills and retrieval metadata |
| `apps/server/tests/test_prompt_budget.py` | document context priority in prompt budgeting |
| `apps/server/tests/test_retrieval_eval.py` | retrieval quality eval harness (non-default marker) |

## Current Constraints

- PDF is the chat upload format; HTML files and web captures ingest through the knowledge routes (`/api/knowledge/sources/html`, `/sources/web-capture`), and markdown/text through their source endpoints.
- Chat images use the central image asset/indexing path, not the PDF document workflow.
- OCR requires the `docling` extra; without it, scanned PDFs fail with a clear message.
- Document chunks are runtime context, not encrypted soul memory.
- The source-ingestion mirror is runtime evidence and compiled knowledge, not encrypted soul memory.
- Citation pills identify the document, not exact chunk ids or page-level inline citations.
- Chat document grounding starts from explicit or active thread documents; the agent can widen to the whole library only through `search_documents(scope="all")`.
- Runtime pgvector availability and embedding provider availability determine whether indexing/search can complete.
