# PDP-002 - Hybrid Retrieval: BM25 + RRF Fusion

- Status: in_progress
- Priority: P0
- Scope: `apps/server`
- Parent: `PDP-000`
- Depends on: none
- Owner: unassigned
- Created: 2026-07-10
- Updated: 2026-07-10 (implemented in worktree `production-doc-processing`)

## Goal

Add lexical retrieval alongside dense vectors and fuse with reciprocal-rank fusion, for both document chunks and source spans. Dense-only retrieval misses exact terms, names, IDs, and error codes; hybrid is the settled production pattern.

## Design

(Revised during implementation: the repo already ships a hybrid stack for memory search -- `services/agent/bm25_index.py` (`BM25Index`, rank_bm25 with unicode tokenization and overlap fallback) and `_reciprocal_rank_fusion` in `services/agent/embeddings.py` (RRF k=60, Rust binding with Python fallback). Reusing it beats introducing a Postgres-only `tsvector` path: no migration, dialect-agnostic (works on the SQLite test DB and pg prod), and one lexical implementation across memory, documents, and knowledge.)

- Lexical arm: build a `BM25Index` per query over the live corpus (document chunks scoped to user/documents; knowledge spans/concepts over the embedding-joined rows). Per-query build is O(corpus) but fine at local-first scale; revisit with caching if profiling says otherwise.
- Query path: dense arm unchanged (pgvector via `PgVecStore.search_by_vector` for chunks; Python cosine for spans/concepts), lexical arm over-fetched (limit*4), fused with `_reciprocal_rank_fusion` (RRF k=60), top-k returned. Reported `similarity`/`score` stays the dense score for transparency; ranking is fused.
- Wired into `search_document_chunks` (`services/documents/rag.py`) and `_concept_hits`/`_span_hits` (`services/ingestion/retrieval.py`, feeding `retrieve_knowledge` and `search_knowledge_bundle`).
- Graceful degradation: lexical arm failure logs debug and falls back to dense-only ordering -- never to empty.
- Preserved contract: empty query embedding still returns [] for document search (pinned by test); knowledge retrieval keeps its existing text fallback.

## Non-goals

- Reranker (PDP-008). Postgres FTS/`tsvector`/pg_textsearch -- unnecessary given the in-process BM25 reuse; revisit only if corpus scale outgrows per-query indexing.

## Acceptance

- A query containing an exact rare token (e.g. an error code present verbatim in one chunk) retrieves that chunk top-3 even when embeddings alone miss it (regression test with a stub embedding function that returns a poor vector).
- Paraphrase queries retain current dense behavior (no regression in `test_document_rag.py`).
- Fusion and degradation covered by unit tests (exact-token promotion, lexical-failure fallback to dense order).

## Validation

- Commands: server test suite (rag + knowledge retrieval + chat context + agent subsets), ruff.
- Changed paths: `apps/server/src/anima_server/services/documents/rag.py`, `apps/server/src/anima_server/services/ingestion/retrieval.py`, tests.

## Activity Log

- 2026-07-10 - Implemented (Claude): hybrid dense+BM25 with RRF fusion in `search_document_chunks` (`services/documents/rag.py`: `_dense_document_chunk_ranking`, `_lexical_document_chunk_ranking`) and in `_concept_hits`/`_span_hits` (`services/ingestion/retrieval.py`: shared `_lexical_ranking`). Reuses `BM25Index` and `_reciprocal_rank_fusion` from the memory stack; design section revised accordingly (no tsvector migration).
- Validation: new tests `test_search_document_chunks_lexical_arm_promotes_exact_token_match`, `test_search_document_chunks_degrades_to_dense_when_lexical_arm_fails`, `test_retrieve_knowledge_lexical_arm_promotes_exact_token_span`; full rag/knowledge/chat/agent subsets green; ruff clean.
