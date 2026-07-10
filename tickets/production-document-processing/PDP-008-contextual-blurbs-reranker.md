# PDP-008 - Contextual Chunk Blurbs and Optional Reranker

- Status: todo
- Priority: P2
- Scope: `apps/server`
- Parent: `PDP-000`
- Depends on: `PDP-002`, `PDP-003`
- Owner: unassigned
- Created: 2026-07-10
- Updated: 2026-07-10

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
