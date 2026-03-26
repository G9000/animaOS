---
title: PRDs Index
description: Product requirement documents for AnimaOS, organized by domain
category: prd
---

# PRDs

**Master PRD**: [Advanced Memory Architecture](./memory-architecture.md) -- umbrella document linking all features, workstreams, and dependencies.

**Implementation Plan**: [Memory Implementation Plan](../architecture/memory/memory-implementation-plan.md) -- detailed engineering spec with function signatures, schemas, and test plans.

---

## Memory (`memory/`)

All memory system features — retrieval, scoring, consolidation, knowledge structure, forgetting, and background orchestration.

| PRD | Phase | Priority | Status | Summary |
|-----|-------|----------|--------|---------|
| [F1 — Hybrid Search](memory/F1-hybrid-search.md) | 9.7 | P0 | Draft | BM25 + vector + RRF for higher-recall retrieval |
| [F2 — Heat-Based Scoring](memory/F2-heat-scoring.md) | 10.4 | P1 | Draft | Persistent heat score replacing fixed-weight retrieval formula |
| [F3 — Predict-Calibrate](memory/F3-predict-calibrate.md) | 10.3 | P1 | Draft | FEP-inspired predict-then-extract for higher-quality fact extraction |
| [F4 — Knowledge Graph](memory/F4-knowledge-graph.md) | 9.5 | P1 | Draft | SQLite-backed entity-relationship graph |
| [F5 — Async Sleep Agents](memory/F5-async-sleep-agents.md) | 10.6 | P2 | Draft | Frequency-gated background orchestrator |
| [F6 — Batch Segmentation](memory/F6-batch-segmentation.md) | 10.7 | P2 | Draft | LLM-driven topic-coherent episode boundaries |
| [F7 — Intentional Forgetting](memory/F7-intentional-forgetting.md) | 10.5 | P1 | Draft | Passive decay, active suppression, cryptographic deletion |

### Build Order

```
Start here (no deps):  F1, F4, F7
After F1:              F2, then F3
After F2+F3+F4:        F5
After F5:              F6
```

## Three-Tier Architecture (`three-tier-architecture/`)

Master PRD: [Three-Tier Cognitive Architecture + N-Agent Spawning](./three-tier-architecture.md)

Physical separation of Soul (SQLCipher) / Runtime (embedded PostgreSQL) / Archive (encrypted JSONL) + concurrent agent spawning.

| PRD | Phase | Priority | Status | Summary |
|-----|-------|----------|--------|---------|
| [P1 — Embedded PostgreSQL](three-tier-architecture/P1-embedded-postgresql.md) | P1 | P0 | Draft | Dual-engine infrastructure, PG lifecycle, CI fallback |
| [P2 — Runtime Messages](three-tier-architecture/P2-runtime-messages.md) | P2 | P0 | Draft | Move messages/threads/runs to PostgreSQL |
| [P3 — Self-Model Split](three-tier-architecture/P3-self-model-split.md) | P3 | P0 | Draft | Identity (soul) vs working state (runtime) |
| [P4 — Write Boundary](three-tier-architecture/P4-write-boundary.md) | P4 | P0 | Draft | Pending memory ops, consolidation gateway |
| [P5 — Transcript Archive](three-tier-architecture/P5-transcript-archive.md) | P5 | P1 | Draft | Encrypted JSONL export, sidecar indexes, eager consolidation |
| [P6 — pgvector Embeddings](three-tier-architecture/P6-pgvector-embeddings.md) | P6 | P1 | Draft | Migrate embeddings from in-memory to pgvector |
| [P7 — Concurrency Refactor](three-tier-architecture/P7-concurrency-refactor.md) | P7 | P0 | Draft | Per-thread locking, stateless ToolExecutor |
| [P8 — N-Agent Spawning](three-tier-architecture/P8-n-agent-spawning.md) | P8 | P0 | Draft | SpawnManager, spawn tools, LLM semaphore |

### Build Order

```
P1 ──> P2 ──> P3 ──> P4 ──> P5
         │                    │
         ├──> P6              │
         │                    │
         └──> P7 ─────────> P8
```

---

## Crypto (`crypto/`)

| PRD | Phase | Priority | Status | Summary |
|-----|-------|----------|--------|---------|
| [Encrypted Core v1](crypto/encrypted-core-v1.md) | 1 | P0 | Draft | Encrypted-by-default Core with SQLCipher |
| [Cryptographic Hardening](crypto/crypto-hardening-plan.md) | 1.5 | P1 | Draft | Per-domain DEKs, core identity keypair, vault hardening |
