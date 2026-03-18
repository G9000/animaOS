# Competitor Audit PRD Corrections Summary

## Scope
- F1
- F4
- F5
- F7 (skipped)

## What Changed
- `F1`: Reframed the feature as a lexical-semantic candidate-generation upgrade, corrected the retrieval integration wording so it matches the actual call path, kept BM25/RRF explicitly self-hosted inside the Core, and added the BM25 corpus-coverage caveat. This addresses the audit finding that F1 was overstating system impact and blurring local-Core constraints.
- `F4`: Added an explicit graph lifecycle, limited stale-relation pruning to ingestion-time bounded candidate sets, required lightweight local BM25 reranking for graph triples, and made the SQLite/Core storage constraint explicit. This addresses the audit finding that competitor graph patterns had been imported too loosely and needed tighter lifecycle and architecture boundaries.
- `F5`: Kept the useful Letta-inspired orchestration mechanics while explicitly rejecting open-ended background agents, defined the completed-run restart cursor contract in `BackgroundTaskRun.result_json`, and clarified which tasks may use transcript-wide context versus current-turn or explicit-delta scope. This addresses the audit finding that the PRD needed tighter orchestration boundaries and concrete restart/audit semantics.
- `F7`: Skipped. The existing PRD already separated passive decay, active suppression, and user-initiated forgetting, and it already tied forgetting to derived-reference cleanup. No competitor-audit correction was required.

## What We Explicitly Rejected
- External graph backends
- Turbopuffer-style dependency assumptions
- Open-ended background LLM agents as the default model

## F7 Decision
- Skipped

F7 was intentionally left unchanged because it already matched the correction standard the audit was pushing toward. The document cleanly distinguishes passive decay from active suppression and from user-directed forgetting, and it already specifies derived-reference cleanup instead of treating deletion as a raw row removal. Changing it anyway would have added editorial churn without resolving a real inconsistency.
