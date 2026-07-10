# Production Document Processing Tickets

This folder tracks the production-grade document processing initiative: tiered parsing (Docling), structure-aware chunking, hybrid retrieval, agentic document tools, and real LLM-wiki compilation into the existing OKF knowledge layer.

- Parent tracker: `PDP-000-production-document-processing.md`
- Builds on: `tickets/okf-llm-wiki-ingestion` (schema, adapters, OKF import/export, lint — all kept as-is)

The initiative replaces the weak internals of each pipeline layer (pypdf-only parsing, paragraph-blind chunking, dense-only retrieval, 5-chunk/900-char prompt injection, stub compiler) without changing the sources → artifacts/spans → concepts schema or the memory boundary.

Architecture decisions locked in `PDP-000`:

1. Parse: tiered — cheap fast path (pypdf/trafilatura/native markdown) with Docling (MIT) as an optional, lazy-loaded quality tier for complex layouts, tables, and scanned pages.
2. Chunk: structure-aware from the heading tree, 256–512 token targets with overlap, section paths in span metadata, parent-section retrieval.
3. Index: pgvector dense + Postgres full-text, fused with reciprocal-rank fusion in SQL.
4. Retrieve: agentic document tools for the agent loop; fixed prompt injection kept only as the cheap first-turn path with sane limits.
5. Compile: OKF concept pages via a real LLM wired into the existing compiler contract, auto-queued by the sleep agent, with span-level citations.
6. Explicitly skipped (benchmarked as not worth it): semantic/late chunking, ColPali, RAPTOR, full GraphRAG.
