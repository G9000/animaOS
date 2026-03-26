---
title: "PRD: Three-Tier Cognitive Architecture + N-Agent Spawning"
description: Master PRD for splitting AnimaOS into Soul/Runtime/Archive tiers and enabling concurrent agent spawning
category: prd
version: "1.0"
---

# PRD: Three-Tier Cognitive Architecture + N-Agent Spawning

**Version**: 1.0
**Date**: 2026-03-26
**Status**: Approved
**Priority**: P0 — Core Architecture
**Stakeholders**: Core Engineering
**Related documents**:
- [Design Spec](../superpowers/specs/2026-03-26-three-store-n-agent-spawning-design.md) — detailed implementation spec with schemas and data flow
- [Thesis: Three-Tier Architecture](../thesis/three-tier-architecture.md) — cognitive science parallels (CLS, Baddeley, GWT, Engram)
- [Memory Architecture PRD](./memory-architecture.md) — existing memory system (Phases 0-10)
- [Engram Paper](../thesis/references/conditional-memory-engram-2026.pdf) — Cheng et al. 2026, arXiv:2601.07372 — validates static/dynamic separation at the neural level

**Phase specs** (see below for summary table):
- [P1: Embedded PostgreSQL](three-tier-architecture/P1-embedded-postgresql.md)
- [P2: Runtime Messages](three-tier-architecture/P2-runtime-messages.md)
- [P3: Self-Model Split](three-tier-architecture/P3-self-model-split.md)
- [P4: Write Boundary](three-tier-architecture/P4-write-boundary.md)
- [P5: Transcript Archive](three-tier-architecture/P5-transcript-archive.md)
- [P6: pgvector Embeddings](three-tier-architecture/P6-pgvector-embeddings.md)
- [P7: Concurrency Refactor](three-tier-architecture/P7-concurrency-refactor.md)
- [P8: N-Agent Spawning](three-tier-architecture/P8-n-agent-spawning.md)

---

## 1. Executive Summary

AnimaOS's current architecture stores everything — identity, memory, messages, runtime state — in a single SQLite/SQLCipher database. This creates three hard blockers for concurrent agent processing (N-agent spawning): SQLite's single-writer constraint, per-user turn serialization, and shared mutable state in the tool executor.

This PRD defines a three-tier architecture that physically separates enduring identity (Soul) from working cognition (Runtime) and verbatim experience records (Archive). The separation solves SQLite's single-writer limitation and enables N-agent spawning — one identity running multiple cognitive processes in parallel.

**Outcome**: An AI that can think about multiple things at once while maintaining a singular, portable identity. Copy `.anima/` to a USB stick, plug into a new machine, enter the passphrase, and the AI wakes up knowing who it is.

**Theoretical validation**: The Engram paper (Cheng et al., 2026) independently validates this architectural principle at the neural level. Their finding: physically separating static knowledge from dynamic computation improves reasoning more than knowledge recall (BBH +5.0 vs. MMLU +3.4), because pre-loaded knowledge frees LLM capacity for deeper thought. Our always-loaded soul blocks serve the same function — they eliminate the "reconstruction tax" where the LLM would waste tokens re-establishing identity and context. See the [thesis](../thesis/three-tier-architecture.md) for the full analysis.

---

## 2. Problem Statement

### 2.1 Current State

AnimaOS has a complete consciousness pipeline (Phases 0-10, 846 tests passing):

- **Storage**: Single SQLCipher database (`anima.db`) in `.anima/` directory
- **All data co-located**: identity blocks, messages, threads, runs, memories, emotions, intentions, knowledge graph, embeddings, session notes, tasks
- **Concurrency**: Per-user asyncio locks serialize all turns for a given user
- **Tool executor**: Singleton with mutable delegation state shared across the process
- **Embeddings**: In-memory vector store rebuilt on startup from `embedding_json` column

### 2.2 Blockers

| Blocker | Impact | Root Cause |
|---------|--------|------------|
| SQLite single-writer | Cannot have main agent + spawned agents writing concurrently | WAL mode allows concurrent reads but only one writer |
| Per-user turn lock | A spawned agent blocks the main conversation and vice versa | `turn_coordinator.py` serializes all turns per `user_id` |
| Singleton ToolExecutor | Shared mutable delegation state creates race conditions with concurrent agents | Process-wide `_tool_delegate` / `_delegated_tool_names` |
| Monolithic data model | Identity buried under transient state; portability means carrying noise | No physical separation between enduring and ephemeral data |

### 2.3 The Identity Question

When everything lives in one database, the answer to "what IS this AI?" requires filtering through active messages, transient emotions, in-flight tasks, and compaction summaries to find the identity underneath. The system has data but not a clear self. Physical separation makes identity answerable by construction.

### 2.4 The Reconstruction Tax

Beyond the identity question, there is a cognitive efficiency argument. Neural architecture research (Cheng et al., "Conditional Memory via Scalable Lookup," arXiv:2601.07372, 2026) demonstrates that LLMs waste early layers reconstructing static knowledge that could be a simple lookup. Their Engram module achieved greater gains in reasoning (+5.0 BBH) than in knowledge recall (+3.4 MMLU) by offloading static patterns to O(1) memory lookups.

The same principle applies at the context window level. Without pre-loaded soul blocks (Tier 0 in the prompt budget), the LLM must reconstruct "who am I, who is this user, what do I know about them, how has the relationship evolved" from scattered contextual clues. This is the application-level reconstruction tax — reasoning capacity wasted on re-deriving context the AI should already know.

The three-tier architecture eliminates this tax: soul blocks are always loaded (zero retrieval cost), freeing the LLM's context window for actual reasoning about the user's situation. The soul tier is not just an identity store — it is a cognitive accelerator.

---

## 3. Solution: Three-Tier Cognitive Architecture

### 3.1 Tier Overview

| Tier | Store | Purpose | Durability |
|------|-------|---------|------------|
| **Soul** | SQLCipher (`anima.db`) in `.anima/` | Enduring identity, distilled knowledge, emotional patterns | Permanent, portable |
| **Runtime** | Embedded PostgreSQL in `.anima/runtime/pg_data/` | Active conversations, spawns, in-flight state | Ephemeral (TTL-pruned) |
| **Archive** | Encrypted JSONL in `.anima/transcripts/` | Full conversation transcripts | Retained (user-configurable) |

### 3.2 The Identity Filter

Every piece of data must answer: **"Does this define enduring identity, or is it just useful data?"**

- Enduring identity → Soul (SQLCipher)
- Active working state → Runtime (PostgreSQL)
- Verbatim experience → Archive (encrypted JSONL)

The distinction is durability, not significance. A user's current emotional state is significant but not identity.

### 3.3 Write Boundary Rule

**Runtime never writes to Soul. Only Consolidation does.**

This is a hard architectural invariant, not a convention. It prevents:
- Race conditions from concurrent writers
- Transient observations becoming permanent personality traits
- Noise pollution in the portable identity core

### 3.4 Consolidation Gateway

The only path from experience to identity. Reads from Runtime (PostgreSQL), writes to Soul (SQLCipher) and Archive (encrypted JSONL).

- Runs on conversation close (eager) and on inactivity timeout (fallback)
- Idempotent (dedupe by `source_tool_call_id`)
- Ordered processing (pending ops applied in `id` order)
- Advisory-locked per user (prevents concurrent consolidation runs)

### 3.5 PostgreSQL Deployment

**Embedded PostgreSQL** (`pgserver` or equivalent) — no Docker, no external dependency.

- PostgreSQL process starts/stops with the Python server via FastAPI lifespan
- Data directory: `.anima/runtime/pg_data/`
- `atexit` handler + stale lockfile recovery for crash safety
- CI environments use Docker PostgreSQL as fallback

### 3.6 Knowledge Graph Placement

**Split**: High-confidence, consolidated entities/relations are promoted to the Soul via the consolidation gateway. Raw/low-confidence extractions stay in Runtime. The same identity filter applies — the consolidation gateway decides what endures.

### 3.7 Portability Story

Copying `.anima/` to a new machine:
- Soul (`anima.db`) — works immediately
- Archive (`transcripts/`) — works immediately
- Runtime (`runtime/pg_data/`) — discarded and rebuilt

**Safety net**: UI "Export / Prepare for transfer" flow triggers full consolidation before transfer. Pending ops that haven't been consolidated are lost if the user copies mid-session without consolidating.

---

## 4. N-Agent Spawning

### 4.1 Core Claim

A single AI identity can run multiple cognitive processes in parallel without fragmenting its sense of self. This is one mind doing multiple things at once — not a team of agents.

### 4.2 Spawn Model

- Main agent calls `spawn_task(goal, context)` mid-turn
- Spawned agent gets: read-only soul snapshot, own PostgreSQL thread, own ToolExecutor, shared LLM client
- Spawn runs its step loop, writes only to PostgreSQL
- On completion, result enters main agent's context on next turn
- Spawns cannot: talk to user, modify core memory, spawn other spawns (initially)

### 4.3 Concurrency

- Per-thread locking replaces per-user locking
- LLM semaphore gates concurrent inference requests
- No cross-lock dependencies between main and spawn threads

---

## 5. Goals and Non-Goals

### Goals

1. Physically separate enduring identity from working cognition
2. Enable N concurrent agent processes per user
3. Preserve `.anima/` portability (USB-stick story)
4. Maintain all existing consciousness features (self-model, emotions, inner monologue)
5. Zero data loss — consolidation gateway ensures all knowledge is eventually promoted
6. Full conversation transcripts archived and searchable

### Non-Goals

- Multi-user PostgreSQL (single embedded instance per user/machine)
- Cloud deployment or hosted PostgreSQL
- Spawn recursion (spawns spawning spawns — deferred)
- Real-time consolidation (seconds-to-minutes delay is acceptable)
- Changing LLM provider abstraction or system prompt architecture

---

## 6. Architecture Decisions Log

| Decision | Options Considered | Resolution | Rationale |
|----------|--------------------|------------|-----------|
| PostgreSQL deployment | (A) Docker Compose sidecar, (B) Embedded PG, (C) Docker for PG only | **B — Embedded PG** | Preserves zero-dependency, portable deployment. No Docker Desktop required. |
| Knowledge graph | Soul-only, Runtime-only, Split | **Split** | High-confidence to soul via consolidation, raw to runtime. Same gateway pattern. |
| Consolidation urgency | (A) Eager on thread close, (B) Significance-triggered, (C) Accept the gap | **A — Eager on close** | Simple, covers 90% of cases. Inactivity timeout as fallback. |
| Portability of pending ops | Portable WAL file, Accept loss | **Accept loss with safety net** | UI triggers consolidation before transfer. Runtime is ephemeral by design. |
| Implementation phasing | Bottom-up (infra first), Feature-vertical | **Bottom-up** | Each phase has clear testable boundary. Infra issues surface before app logic changes. |
| Prompt budget optimization | Fixed ratios, Adaptive per conversation mode, Empirical sweep | **Fixed ratios initially, empirical sweep post-P8** | Hand-tuned ratios are correct enough for ship. Engram's U-shaped allocation law provides methodology for optimization. |
| Memory retrieval gating | Cosine-only, Two-stage (cosine + context gate) | **Cosine initially, context gate post-P6** | Context-aware gating (Engram eq. 4) prevents topically related but situationally irrelevant memories from consuming budget. Requires pgvector first. |

---

## 7. Phase Overview

| Phase | Name | Scope | Depends On | PR |
|-------|------|-------|------------|-----|
| P1 | Embedded PostgreSQL | Dual-engine infrastructure, PG lifecycle, CI fallback | — | 1 PR |
| P2 | Runtime Messages | Move messages/threads/runs to PostgreSQL | P1 | 1 PR |
| P3 | Self-Model Split | Identity (soul) vs working state (runtime) | P2 | 1 PR |
| P4 | Write Boundary | Pending memory ops, rewire core_memory tools, consolidation reads PG | P3 | 1 PR |
| P5 | Transcript Archive | Encrypted JSONL export, sidecar indexes, `recall_transcript`, eager consolidation | P4 | 1 PR |
| P6 | pgvector Embeddings | Migrate embeddings from in-memory to pgvector | P2 | 1 PR |
| P7 | Concurrency Refactor | Per-thread locking, stateless ToolExecutor | P2 | 1 PR |
| P8 | N-Agent Spawning | SpawnManager, spawn tools, LLM semaphore | P4, P7 | 1 PR |

```
Dependency graph:

P1 ──> P2 ──> P3 ──> P4 ──> P5
         │                    │
         ├──> P6              │
         │                    │
         └──> P7 ─────────> P8
```

Note: P6 and P7 can run in parallel after P2. P8 requires both P4 (write boundary) and P7 (concurrency).

---

## 8. Success Criteria

| Criterion | Measurement |
|-----------|-------------|
| Dual-engine working | Tests pass against both SQLCipher and embedded PostgreSQL |
| Message concurrency | Two agent threads can write messages simultaneously without blocking |
| Write boundary enforced | No runtime code path imports soul-write functions; linter/test enforces this |
| Consolidation completes | Pending ops are promoted to soul within 60s of thread close |
| Portability preserved | Copy `.anima/` minus `runtime/`, start fresh server, AI retains identity |
| Transcript searchable | `recall_transcript` returns relevant snippets from archived conversations |
| Spawn completes | Spawned agent runs to completion, result appears in main agent context |
| No regression | All existing 846+ tests pass after each phase |

---

## 9. Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Embedded PG instability on Windows | Medium | High | CI tests on Windows; Docker fallback for CI; manual testing on target |
| Consolidation data loss (crash mid-run) | Low | High | Idempotent ops, atomic file writes, advisory locks |
| Performance regression from dual-engine | Low | Medium | Benchmark before/after; PG connection pooling |
| Phase 2 migration breaks existing tests | Medium | Medium | Parallel test suites during migration; feature flag for runtime engine |
| pgvector extension unavailable in embedded PG | Low | Medium | P6 is independent; fallback to in-memory if needed |

---

## 10. What Stays the Same

- SQLCipher encryption for soul data
- `.anima/` directory as the portable core
- Recovery phrase system (BIP39)
- All consciousness features (self-model, emotions, inner monologue)
- LLM provider abstraction (OpenAI-compatible client)
- System prompt architecture
- Consolidation extraction logic (reads from different source, same pipeline)

---

## 11. Future Directions (Post-P8)

### 11.1 Context Allocation Optimization

The prompt budget (`prompt_budget.py`) allocates a fixed character budget across four tiers: identity (always loaded), self-model (working state), dynamic retrieval (semantic search), and background context (episodes, goals). These ratios are hand-tuned.

The Engram paper's Sparsity Allocation methodology is directly applicable: fix the total budget, sweep the tier allocation ratios, measure response quality. A U-shaped curve is expected — too much identity context crowds out dynamic retrieval, too little forces reconstruction waste. Empirically determining the optimal allocation for different conversation modes (emotional support, knowledge Q&A, reminiscing) is a tractable research contribution.

### 11.2 Context-Aware Memory Gating

Current semantic retrieval finds candidate memories by cosine similarity to the latest query. The Engram paper's context-aware gating mechanism (eq. 4) adds a second stage: each candidate is scored against the full conversation trajectory. Memories that are topically related but situationally irrelevant (e.g., "user's dog Max" when the conversation is about exercise routines) are suppressed before they consume context budget.

Implementation: after pgvector retrieval (P6), add a lightweight relevance check — cross-encoder or embedding-based comparison of each candidate against the last N messages. This becomes the 5th factor in the multi-factor retrieval model (see whitepaper Section 9.1).

### 11.3 Frequency-Aware Memory Promotion

Memory access patterns likely follow a Zipfian distribution — a small number of core facts are accessed in nearly every conversation. Tracking access frequency per memory enables dynamic tier promotion: memories that cross a frequency threshold are promoted to always-loaded status (Tier 0), regardless of their data type label. A "fact" that the AI needs every session is functionally identity, even if the identity filter would not classify it as such.

Implementation: add `access_count` and `last_accessed` to `MemoryItem`. In `prompt_budget.py`, override tier assignment for high-frequency memories. This addresses the "soul might be too small" concern without expanding it by policy.

### 11.4 Adaptive Context Allocation

Rather than a fixed prompt budget, adapt tier budgets based on detected conversation mode:

- **Emotional support** → more self-model and emotional context blocks
- **Knowledge Q&A** → more fact and semantic retrieval blocks
- **Reminiscing** → more episode and growth log blocks

This is the application-level equivalent of Engram's gating applied to entire memory tiers. No existing companion system implements conversation-mode-adaptive context allocation.

### 11.5 Model-Level Memory Readiness

When Engram-style conditional memory modules become available in open-source models (Ollama, vLLM), the three-tier architecture is already the right application-level complement. The soul/runtime/archive separation maps to: model-level Engram for general world knowledge (O(1) lookup, zero context window cost) + application-level soul for evolving personal knowledge (transparent, editable, continuously updated). No architectural changes needed — the separation is already in place.

---

## 12. Open Questions (Deferred)

1. **Spawn recursion** — Should spawns spawn? Disabled initially. Revisit after P8 ships.
2. **Spawn UI** — How does the frontend show spawn progress? WebSocket events TBD.
3. **Sidecar encryption** — Should transcript sidecar indexes be encrypted? Unencrypted initially for fast filtering.
4. **Result merging** — How does the main agent incorporate spawn results? Memory block injection vs system message. Decision in P8 spec.
