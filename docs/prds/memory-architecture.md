---
title: "PRD: Advanced Memory Architecture"
description: Product requirements for the next-generation memory architecture
category: prd
version: "1.0"
---

# PRD: Advanced Memory Architecture

**Version**: 1.0
**Date**: 2026-03-18
**Author**: AnimaOS Engineering
**Status**: Draft
**Stakeholders**: Core Engineering
**Related documents**:
- [Implementation Plan](../architecture/memory/memory-implementation-plan.md) — detailed engineering spec with function signatures, schemas, and test plans
- [Competitor Analysis](../thesis/competitor-analysis.md) — source-code-level comparison across all 5 competitors
- [Research Report](../thesis/research-report-2026-03-18.md) — March 2026 literature review and pattern discovery
- [Roadmap](../thesis/roadmap.md) — product-level phase definitions (Phases 9.5–10.7)

**Individual feature PRDs** (see [PRD Index](./README.md) for domain-organized view):
- [F1: Hybrid Search](memory/F1-hybrid-search.md) — BM25 + Vector + RRF
- [F2: Heat Scoring](memory/F2-heat-scoring.md) — heat-based memory scoring
- [F3: Predict-Calibrate](memory/F3-predict-calibrate.md) — predict-calibrate consolidation
- [F4: Knowledge Graph](memory/F4-knowledge-graph.md) — SQLite-backed entity-relationship graph
- [F5: Async Sleep Agents](memory/F5-async-sleep-agents.md) — frequency-gated background orchestrator
- [F6: Batch Segmentation](memory/F6-batch-segmentation.md) — topic-coherent episode segmentation
- [F7: Intentional Forgetting](memory/F7-intentional-forgetting.md) — passive decay, active suppression, cryptographic deletion
- [Encrypted Core v1](crypto/encrypted-core-v1.md) — encrypted-by-default Core
- [Cryptographic Hardening](crypto/crypto-hardening-plan.md) — per-domain DEKs, identity, vault hardening

---

## 1. Executive Summary

AnimaOS is a local-first, portable AI companion with encrypted memory persistence. Its memory system currently supports flat semantic facts, fixed-interval episode generation, keyword search via Jaccard similarity, and timer-based background consolidation. While functional, this architecture has known limitations: poor keyword recall, no relational structure between memories, redundant fact extraction, inefficient resource allocation for background processing, topic-blind episode boundaries, and no principled forgetting.

This PRD defines seven features that address these limitations, drawn from source-code analysis of five leading AI memory frameworks (Letta, Mem0, Nemori, MemOS, MemoryOS) and validated against published research (2025-2026). Each feature is scoped to ship independently, with a dependency graph governing execution order. Two additional crypto PRDs (Encrypted Core v1, Cryptographic Hardening) handle security in a parallel workstream.

**Outcome**: A memory system that retrieves more relevant context, extracts higher-quality facts, organizes knowledge relationally, allocates background compute efficiently, segments episodes by topic coherence, and forgets deliberately.

---

## 2. Problem Statement

### 2.1 Current State

AnimaOS has a working memory pipeline (Phases 0-10 complete, 602 tests passing):

- **Storage**: SQLite + SQLCipher encrypted Core, `MemoryItem` table with embeddings
- **Retrieval**: Cosine similarity vector search + Jaccard keyword search + RRF fusion
- **Extraction**: Regex fast path + LLM extraction (cold, no awareness of existing knowledge)
- **Scoring**: Fixed-weight formula (`importance=0.4, recency=0.35, frequency=0.25`)
- **Episodes**: Fixed-size chunking (every 6 turns = one episode, contiguous only)
- **Background processing**: Consolidation on every turn; reflection on 5-minute inactivity timer

### 2.2 Gaps

| Gap | Impact | Evidence |
|-----|--------|----------|
| **Keyword search is weak** | Proper nouns, technical terms missed | Nemori's BM25+RRF showed higher recall |
| **No relational structure** | Flat facts with no entity linking | Mem0's graph showed 26% accuracy improvement |
| **Redundant extraction** | LLM extracts facts it already knows | Nemori's predict-calibrate extracts delta only |
| **Fixed retrieval scoring** | All memories scored identically | MemoryOS's heat scoring reflects actual usage |
| **Timer-based background work** | Every turn (wasteful) or 5-min idle (delayed) | Letta's frequency-gated agents are configurable |
| **Topic-blind episodes** | Mixed-topic episode summaries | Nemori's batch segmenter groups by topic coherence |
| **No forgetting** | Monotonic growth; outdated facts compete | Richards & Frankland (2017): forgetting is functional |

---

## 3. Workstreams and Dependencies

The features are **not a linear sequence**. They form independent workstreams that converge at F5 (orchestration). Work across streams can happen in parallel.

```
  RETRIEVAL STREAM          KNOWLEDGE STREAM         LIFECYCLE STREAM        CRYPTO STREAM
  ================          ================         ================        ==============

  F1: Hybrid Search         F4: Knowledge Graph      F7: Intentional         Encrypted Core v1
  (BM25+Vector+RRF)        (SQLite graph)              Forgetting                  |
        |                         |                       |                  Crypto Hardening
        v                         |                  (benefits from F2)       (Phases 0-5)
  F2: Heat Scoring                |
        |                         |
        v                         |
  F3: Predict-Calibrate           |
        |                         |
        +------------+------------+
                     |
                     v
           F5: Async Sleep Agents  (ORCHESTRATION -- converges all streams)
                     |
                     v
           F6: Batch Segmentation
```

### Dependency Rules

| Feature | Hard Dependencies | Soft Dependencies |
|---------|-------------------|-------------------|
| **F1** | None | -- |
| **F2** | None | Benefits from F1 |
| **F3** | F1 (uses `hybrid_search()`) | -- |
| **F4** | None | Benefits from F1 for entity-name matching |
| **F5** | F2, F3, F4 (orchestrates them) | -- |
| **F6** | None | Benefits from F5 for orchestration |
| **F7** | None | Benefits from F2 (cold items as decay candidates) |
| **Crypto** | Independent stream | -- |

### Parallel Execution Map

| After... | You can start... |
|----------|-----------------|
| Nothing (day 1) | F1, F4, F7, Crypto (all independent) |
| F1 ships | F2, F3 preparation, F4 integration |
| F2 ships | F3, F7 integration (heat visibility floor) |
| F1 + F2 + F3 + F4 all ship | F5 (orchestration) |
| F5 ships | F6 (batch segmentation) |

---

## 4. Feature Summaries

Each feature has its own detailed PRD. This section provides the key requirements and acceptance criteria.

### Retrieval Stream

**F1: Hybrid Search** (P0, Phase 9.7) — Replace Jaccard with BM25Okapi for the keyword leg. Existing RRF infrastructure unchanged. See [F1 PRD](memory/F1-hybrid-search.md).

**F2: Heat Scoring** (P1, Phase 10.4) — Persistent `heat` column: `H = alpha*access + beta*depth + gamma*recency + delta*importance`. Updated on access, decayed during sleep. See [F2 PRD](memory/F2-heat-scoring.md).

**F3: Predict-Calibrate** (P1, Phase 10.3) — Two-step FEP-inspired extraction: predict expected knowledge, extract only the delta. Quality gates filter noise. See [F3 PRD](memory/F3-predict-calibrate.md).

### Knowledge Stream

**F4: Knowledge Graph** (P1, Phase 9.5) — SQLite tables for entities + relations. LLM tool calling for extraction. Embedding-based entity dedup. Graph traversal via SQL JOINs. See [F4 PRD](memory/F4-knowledge-graph.md).

### Lifecycle Stream

**F7: Intentional Forgetting** (P1, Phase 10.5) — Passive decay (heat visibility floor), active suppression (3x decay for superseded), user-initiated cryptographic deletion with derived-reference cleanup. See [F7 PRD](memory/F7-intentional-forgetting.md).

### Orchestration

**F5: Async Sleep Agents** (P2, Phase 10.6) — Frequency-gated background orchestrator. Heat-threshold gating. Parallel independent tasks. Run tracking. See [F5 PRD](memory/F5-async-sleep-agents.md).

**F6: Batch Segmentation** (P2, Phase 10.7) — LLM-driven topic-coherent episode boundaries with non-contiguous grouping. See [F6 PRD](memory/F6-batch-segmentation.md).

### Crypto Stream

**Encrypted Core v1** (P0) — Encrypted-by-default Core with SQLCipher. See [Encrypted Core PRD](crypto/encrypted-core-v1.md).

**Cryptographic Hardening** (P1) — Per-domain DEKs, core identity keypair, vault hardening, integrity attestation. See [Crypto Hardening PRD](crypto/crypto-hardening-plan.md).

---

## 5. Technical Constraints

| Constraint | Details |
|------------|---------|
| **Database** | SQLite + SQLCipher only. No PostgreSQL, Redis, Neo4j, or ChromaDB. |
| **LLM providers** | Ollama, OpenRouter, vLLM only. No OpenAI, Anthropic, or Google. |
| **State location** | All state in `.anima/` directory. Portable-by-default. |
| **Backend** | Python/FastAPI at `apps/server/`. |
| **Test baseline** | 602 existing tests must pass after each feature ships. |
| **New dependencies** | `rank-bm25` only (~15 KB, pure Python). |
| **Migrations** | 5 total Alembic migrations across all 7 features. |
| **New tables** | 4 total: `kg_entities`, `kg_relations`, `background_task_runs`, `forget_audit_log`. |

---

## 6. Success Metrics

| Metric | Baseline | Target |
|--------|----------|--------|
| Keyword recall (exact term in top-5) | Low (Jaccard misses proper nouns) | > 90% |
| Extraction precision (genuinely new facts) | ~40% | > 80% after 20+ facts |
| Episode topic coherence | Mixed-topic common | Single-topic when batch segmentation activates |
| Background task efficiency | Every turn | Every 3rd turn + heat gating |
| Entity coverage | 0 entities | Key people/places/orgs after 10 conversations |
| Test suite | 602 passing | 602+ passing |

---

## 7. Out of Scope (Future Work)

| Topic | Why deferred |
|-------|--------------|
| Emotional model evolution (12-category to dimensional) | Requires VAD vs categorical research decision |
| Memory governance / constitutional rules | CMA paper provides framework; needs design PRD |
| KV cache pre-computation | Requires HuggingFace model access; MemOS has reference implementation |
| World model synthesis | F4 provides graph substrate; narrative layer not yet specified |
| User profiling framework | MemoryOS's 90-dimension framework is a reference |
| Multi-stage iterative retrieval | MemOS's AdvancedSearcher; consider as retrieval v2 |
| Preference memory (explicit/implicit) | MemOS has dedicated pipeline |
| NLI-based conflict detection | MemOS deploys NLI microservice; could speed up conflict resolution |

---

## 8. References

| Source | What we adopted |
|--------|-----------------|
| Nemori (arXiv 2508.03341) | BM25+RRF, predict-calibrate, batch segmentation |
| Mem0 (GitHub) | Graph via LLM tool calling, embedding-based entity dedup, **BM25 graph reranking (F4.16)**, **LLM-driven relation pruning (F4.17)**, **UUID hallucination protection (F3.11, F4.18)** |
| MemoryOS (arXiv 2506.06326) | Heat formula, heat-triggered consolidation |
| Letta (arXiv 2504.13171) | Frequency-gated async agents, turn counting |
| MemOS (arXiv 2507.03724) | MemCube portability validation |
| Richards & Frankland (2017) | Forgetting as functional feature |

### Source-Code Audits

| Audit | Location |
|-------|----------|
| [Letta & Mem0 Audit](memory/competitor-audit-letta-mem0.md) | Deep-dive source-code comparison, March 2026 |
| [Nemori, MemOS, MemoryOS Audit](../architecture/memory/memory-repo-analysis.md) | Comparative source-code analysis |
| [Competitor Analysis](../thesis/competitor-analysis.md) | High-level thesis-level comparison across all 5 competitors |
