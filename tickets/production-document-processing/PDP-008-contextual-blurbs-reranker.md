# PDP-008 - Contextual Chunk Blurbs and Optional Reranker

- Status: in_review
- Priority: P2
- Scope: `apps/server`
- Parent: `PDP-000`
- Depends on: `PDP-002`, `PDP-003`
- Owner: unassigned
- Created: 2026-07-10
- Updated: 2026-07-12 (implemented in worktree `production-doc-processing`)

## Goal

Layer the two highest-ROI retrieval-quality upgrades on top of hybrid search: Anthropic-style contextual chunk blurbs at ingestion time, and an optional local reranker stage at query time. Both are measured additions — gate each on the PDP-009 eval harness.

## Design

- **Contextual blurbs.** At ingestion (post-chunking, pre-embedding), generate a 50–100 token context line per chunk ("this chunk is from section X of document Y and covers Z") using the runtime's configured model; prepend to the chunk text for embedding and FTS indexing only — stored separately in span metadata, never shown as evidence text. Settings flag (`CONTEXTUAL_CHUNKS=off|on`), default off until evals justify the ingestion cost; budget-aware (skip for very large documents beyond a page cap, log the skip).
- **Reranker.** Optional cross-encoder stage after RRF fusion: over-fetch ~50, rerank, return top-k. Local model (Qwen3-Reranker-0.6B or BGE-reranker-v2-m3, both Apache-2.0) behind an extras group like the Docling tier; lazy-loaded, settings-gated (`RETRIEVAL_RERANKER=off|local`). Latency budget documented; degrade to fused order on failure or absence.
- Both features are pure additions behind flags — no behavior change when off.

## Acceptance

- With blurbs on, eval recall@5 improves on the gold set versus hybrid-only baseline (recorded in PDP-009 harness output); no evidence text in prompts contains blurb content.
- Reranker path improves nDCG on the gold set; with the extra absent or flag off, retrieval is byte-identical to PDP-002 behavior.
- Base install unaffected; CI matrix covers flags off/on.

## Validation

- Commands: eval harness runs (baseline vs blurbs vs blurbs+rerank), server test suite, ruff.
- Changed paths: `apps/server/src/anima_server/services/ingestion/` (blurb generation), `apps/server/src/anima_server/services/documents/rag.py` / retrieval (rerank stage), settings, `apps/server/pyproject.toml`, tests.

## Activity Log

- 2026-07-12 - Implemented (Claude session):
  - Contextual blurbs: `services/documents/contextual.py` generates a per-chunk context line with the runtime model (injectable client) inside the PDF workflow's `embedded` state, pre-embedding; stored in `chunk.metadata_json["context_blurb"]`, prepended to embedding text (`embed_document_chunks`) and BM25 index text (`_lexical_document_chunk_ranking`) only — stored content, content hashes, and evidence text stay raw chunk text. `ANIMA_CONTEXTUAL_CHUNKS=off|on` (default off), chunk-budget cap (`..._MAX_CHUNKS`, default 200, skip logged), model failure aborts blurbing without failing ingestion. Note: budget cap is chunk-count rather than page-count (equivalent intent; chunks are the unit the pipeline sees here).
  - Reranker: `services/documents/reranker.py` lazy-loads a CrossEncoder (`ANIMA_RETRIEVAL_RERANKER=off|local`, model `ANIMA_RETRIEVAL_RERANKER_MODEL`, default BGE-reranker-v2-m3, `reranker` extra with sentence-transformers); `search_document_chunks` over-fetches to `ANIMA_RETRIEVAL_RERANK_CANDIDATES` (50) post-fusion and returns the reranked top-k. Flag off / extra absent / load or scoring failure all degrade to the fused order (failure cached per process). Latency note documented in the module (~30-80ms/query on CPU at 50 candidates).
  - Tests: `test_contextual_rerank.py` — flag-off no-ops (byte-identical), blurb metadata + idempotent rerun, budget skip, model-failure tolerance, embedding-text prefix vs stored-content purity, blurb-only lexical hit with evidence purity, rerank ordering, extra-missing degradation, search-level rerank + over-fetch + off-flag baseline.
  - Deferred to PDP-009: recall@5 / nDCG measurements on the gold set gating default-on decisions; CI flag matrix.
