---
title: "Competitor Memory Architecture Analysis"
author: AI Memory Researcher Agent
date: 2026-03-28
version: 1.1
sources: Source code analysis of Letta, Mem0, Nemori, MemOS, MemoryOS, EverMemOS repositories
---

# Competitor Memory Architecture Analysis

A source-code-level comparison of six AI memory systems against the AnimaOS thesis.

---

## 1. System Overviews

### Letta (formerly MemGPT)

- **Architecture**: 3-tier (core blocks + recall + archival) with PostgreSQL/pgvector
- **Key innovation**: Sleeptime multi-agent background processing with frequency gating
- **Paper**: arXiv:2504.13171 (Sleep-Time Compute, 2025)
- **License**: Apache 2.0

### Mem0

- **Architecture**: Dual-tier (vector store + optional Neo4j graph)
- **Key innovation**: Graph memory with embedding-based entity deduplication
- **Backend**: 26 vector store adapters, cloud-first with local option
- **License**: Apache 2.0

### Nemori

- **Architecture**: 2-tier (episodic + semantic) with JSONL + ChromaDB
- **Key innovation**: FEP-inspired predict-calibrate consolidation + non-contiguous batch segmentation
- **Paper**: arXiv:2508.03341 (2025)
- **License**: MIT

### MemOS

- **Architecture**: Multi-type memory OS (text + KV cache + LoRA + preference) with MemCube containers
- **Key innovation**: KV cache memory (pre-computed attention state as portable memory), multi-stage graph+vector+BM25 retrieval with iterative reasoning, MemCube portable containers
- **Backend**: Neo4j + Qdrant + Redis (cloud infra), or SQLite local plugin
- **Paper**: arXiv:2507.03724 (2025)
- **License**: Apache 2.0
- **Note**: LoRA/parametric memory is a placeholder (file explicitly says `# TODO: placeholder`). KV cache memory is functional but requires HuggingFace model access.

### MemoryOS

- **Architecture**: 3-tier hierarchy (short/mid/long-term) with JSON + FAISS
- **Key innovation**: Heat-based scoring triggering tier promotion and LTM analysis
- **Paper**: arXiv:2506.06326 (EMNLP 2025, +49% F1 on LoCoMo)
- **License**: MIT

### EverMemOS (added 2026-03-28)

- **Architecture**: 6-layer OS (Agentic → Memory → Retrieval → Business → Infrastructure → Core) with engram-inspired lifecycle
- **Key innovation**: MemCell/MemScene hierarchy (atomic facts → thematic scenes), Foresight signals (time-bounded predictions), progressive user profiling, agentic multi-round retrieval
- **Backend**: MongoDB + Elasticsearch (BM25) + Milvus (vector) + Redis (cache) — heavy cloud infra
- **Paper**: arXiv:2601.02163 (Jan 2026)
- **Benchmark**: **93% on LoCoMo** (current SOTA), strong on LongMemEval
- **License**: Open source
- **GitHub**: 3,300+ stars, active ecosystem (OpenClaw agent integration, Claude Code plugin, Live2D characters)
- **Note**: The most architecturally similar system to AnimaOS in the retrieval/consolidation layer, but with no encryption, no self-model, no emotional intelligence, no succession, and heavy cloud infrastructure requirements.

---

## 2. Dimension-by-Dimension Comparison

### 2.1 Memory Architecture

| System        | Tiers                                                                                            | Storage                                           | Portable                                              | Encrypted                                   |
| ------------- | ------------------------------------------------------------------------------------------------ | ------------------------------------------------- | ----------------------------------------------------- | ------------------------------------------- |
| **AnimaOS**   | 3-tier (Soul/SQLCipher + Runtime/embedded PG + Archive/encrypted JSONL) with typed memory blocks | SQLCipher + embedded PostgreSQL + encrypted JSONL | Yes (.anima/ directory)                               | Yes (SQLCipher + AES-256-GCM)               |
| **Letta**     | 3-tier (core blocks, recall, archival) + file + git-backed                                       | PostgreSQL + pgvector                             | No (server-bound)                                     | Optional single key                         |
| **Mem0**      | 2-tier (vector + graph)                                                                          | 26 vector backends + Neo4j                        | No (cloud or server)                                  | No                                          |
| **Nemori**    | 2-tier (episodic + semantic)                                                                     | JSONL files + ChromaDB                            | Partially (local files)                               | No                                          |
| **MemOS**     | Multi-type (text, KV cache, LoRA stub, preference)                                               | Neo4j + Qdrant + Redis / SQLite local             | Yes (MemCube directory dump/load, HuggingFace remote) | No (RBAC user management but no encryption) |
| **MemoryOS**  | 3-tier (short/mid/long-term)                                                                     | JSON files + FAISS                                | Partially (local files)                               | No                                          |
| **EverMemOS** | 6-layer OS (MemCell → MemScene hierarchy) with engram lifecycle                                  | MongoDB + Elasticsearch + Milvus + Redis          | No (heavy cloud infra)                                | No                                          |

**AnimaOS thesis alignment**: The portable encrypted Core is a genuine differentiator. EverMemOS is the strongest competitor in retrieval/consolidation (93% LoCoMo SOTA) but requires MongoDB + Elasticsearch + Milvus + Redis — the opposite of portable. No competitor offers passphrase-sovereign encrypted storage.

### 2.2 Consolidation & Learning

| System        | Method                                                                                                                  | Trigger                                           | Background?                                  | CLS-aligned?                                  |
| ------------- | ----------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------- | -------------------------------------------- | --------------------------------------------- |
| **AnimaOS**   | Regex + LLM extraction, conflict resolution, quick + deep reflection                                                    | Per-turn extraction + inactivity-triggered sleep  | Partially (sleep tasks are inactivity-gated) | Yes (quick=hippocampal, deep=neocortical)     |
| **Letta**     | Context compaction + sleeptime agents                                                                                   | Context overflow + post-turn frequency-gated      | Yes (asyncio background tasks)               | Not explicitly                                |
| **Mem0**      | LLM fact extraction + ADD/UPDATE/DELETE decision                                                                        | Synchronous on every add()                        | No                                           | No                                            |
| **Nemori**    | Predict-calibrate (FEP) + batch segmentation                                                                            | Buffer threshold (20 messages)                    | Yes (ThreadPoolExecutor)                     | Yes (episodic->semantic distillation)         |
| **MemOS**     | LLM extraction + graph reorganizer (background thread) + NLI conflict detection                                         | Async via Redis Streams scheduler                 | Yes (Redis Streams task queue)               | No (engineering-driven taxonomy)              |
| **MemoryOS**  | Heat-threshold LTM analysis                                                                                             | Heat > 5.0 + capacity overflow                    | Synchronous within add_memory()              | Loosely (fast capture -> slow consolidation)  |
| **EverMemOS** | Episodic Trace Formation → Semantic Consolidation (MemCell → MemScene) + Foresight signals + progressive user profiling | Per-conversation extraction + async consolidation | Yes (agentic layer orchestration)            | Engram-inspired (episodic→semantic lifecycle) |

**Key finding**: EverMemOS achieves SOTA (93% LoCoMo) with a three-phase pipeline: Episodic Trace Formation, Semantic Consolidation, and Reconstructive Recollection. The Foresight signals (time-bounded predictions extracted from conversation) are a novel contribution AnimaOS does not have. Letta's sleeptime agents remain the most production-ready background system. Nemori's predict-calibrate is the most theoretically principled consolidation (FEP-grounded).

### 2.3 Retrieval Strategy

| System        | Search Method                                                                                                                      | Scoring                                                                      | Reranking                                                           |
| ------------- | ---------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------- | ------------------------------------------------------------------- |
| **AnimaOS**   | In-memory cosine similarity on persisted embeddings + contextual gating                                                            | importance + recency + frequency + contextual gate (planned: heat, BM25+RRF) | No                                                                  |
| **Letta**     | pgvector + Turbopuffer hybrid (vector + FTS + RRF)                                                                                 | Similarity score, optional RRF                                               | Via Turbopuffer                                                     |
| **Mem0**      | Vector similarity + graph traversal                                                                                                | Cosine similarity                                                            | Yes (5 providers: Cohere, HuggingFace, LLM, etc.)                   |
| **Nemori**    | BM25 + ChromaDB vector + RRF (k=60)                                                                                                | Reciprocal Rank Fusion                                                       | NOR-LIFT (stub, not implemented)                                    |
| **MemOS**     | Multi-stage: TaskGoalParser -> GraphRetriever (vector+BM25+FTS) -> Reranker -> Reasoner; AdvancedSearcher adds iterative multi-hop | Combined graph+vector+BM25                                                   | Yes (cosine + optional BGE reranker)                                |
| **MemoryOS**  | FAISS IndexFlatIP + keyword Jaccard                                                                                                | Semantic + keyword combined                                                  | No (heapq top-K by combined score)                                  |
| **EverMemOS** | BM25 (Elasticsearch) + vector (Milvus) + RRF fusion + agentic multi-round recall                                                   | Combined BM25+vector+RRF                                                     | Yes (reranker models + agentic retrieval with iterative refinement) |

**Key finding**: EverMemOS has the most complete retrieval pipeline — BM25 + vector + RRF + agentic multi-round recall. This is the benchmark AnimaOS's F1 (hybrid search) should target. The agentic retrieval mode (LLM decides what to search for next based on initial results) is a pattern AnimaOS should study.

### 2.4 Self-Model & Identity

| System        | Self-Model                                                                                 | Identity Persistence                               | Growth Tracking            | Inner Monologue        |
| ------------- | ------------------------------------------------------------------------------------------ | -------------------------------------------------- | -------------------------- | ---------------------- |
| **AnimaOS**   | 5-section structured model (identity, inner_state, working_memory, growth_log, intentions) | Soul directive + dynamic identity in system prompt | Growth log with versioning | Quick + deep monologue |
| **Letta**     | Single free-text persona block                                                             | Persona block (agent-editable)                     | Block history (audit only) | No                     |
| **Mem0**      | None                                                                                       | None                                               | None                       | No                     |
| **Nemori**    | None                                                                                       | None                                               | None                       | No                     |
| **MemOS**     | None (infrastructure layer, not an agent)                                                  | None                                               | None                       | No                     |
| **MemoryOS**  | None (user profiling only)                                                                 | None                                               | None                       | No                     |
| **EverMemOS** | Progressive user profiling (auto-updated from conversations)                               | User profile persistence                           | None                       | No                     |

**Key finding**: This remains AnimaOS's strongest differentiator. EverMemOS adds progressive user profiling (auto-built from conversations), which is closer to AnimaOS's human memory block than Letta's persona — but it is a profile _of the user_, not a self-model _of the AI_. No competitor has structured AI self-model with identity evolution, growth tracking, or inner monologue.

### 2.5 Emotional Intelligence

| System        | Emotion Detection                                               | Affect Model                     | Behavioral Adaptation                    |
| ------------- | --------------------------------------------------------------- | -------------------------------- | ---------------------------------------- |
| **AnimaOS**   | 12-signal taxonomy with confidence + trajectory                 | Rolling signal buffer, synthesis | Guardrails, attentional (not diagnostic) |
| **Letta**     | None                                                            | None                             | None                                     |
| **Mem0**      | None                                                            | None                             | None                                     |
| **Nemori**    | None (emotions explicitly excluded as noise)                    | None                             | None                                     |
| **MemOS**     | None                                                            | None                             | None                                     |
| **MemoryOS**  | None (90-dim user personality profiling has emotion dimensions) | None                             | None                                     |
| **EverMemOS** | None                                                            | None                             | None                                     |

**Key finding**: AnimaOS is alone in this category. No competitor models emotions at all, including the SOTA EverMemOS. Nemori actively discards emotional signals. MemoryOS profiles user personality traits (Big Five, etc.) but does not track real-time emotional states.

### 2.6 Ownership, Privacy & Encryption

| System        | Data Location                                    | Encryption                          | User Ownership                      | Vault/Export                                 |
| ------------- | ------------------------------------------------ | ----------------------------------- | ----------------------------------- | -------------------------------------------- |
| **AnimaOS**   | Local SQLite in .anima/                          | SQLCipher + AES-256-GCM field-level | User owns everything                | Encrypted vault export/import                |
| **Letta**     | Server PostgreSQL                                | Optional single key                 | Server/org owns                     | Agent serialization (admin)                  |
| **Mem0**      | Cloud API or local vector store                  | None                                | Cloud: Mem0 owns. Local: user owns  | None                                         |
| **Nemori**    | Local JSONL + ChromaDB                           | None                                | User owns (local files)             | None                                         |
| **MemOS**     | Neo4j + Qdrant + Redis (cloud) or SQLite (local) | None (RBAC only)                    | MemCube directory-level portability | MemCube dump/load + HuggingFace remote repos |
| **MemoryOS**  | Local JSON files                                 | None                                | User owns (local files)             | None                                         |
| **EverMemOS** | MongoDB + Elasticsearch + Milvus + Redis (cloud) | None                                | Server/operator owns                | None                                         |

**Key finding**: AnimaOS is the only system with encryption at rest. EverMemOS, despite being SOTA on benchmarks, requires four cloud services and has no encryption or user ownership model. The combination of local-first + encrypted + portable + vault export is unique. MemOS's MemCube has portability but no encryption.

### 2.7 Forgetting

| System       | Passive Decay                       | Active Suppression                           | User-Initiated Delete                                  | Theoretical Basis              |
| ------------ | ----------------------------------- | -------------------------------------------- | ------------------------------------------------------ | ------------------------------ |
| **AnimaOS**  | Planned (F7: heat visibility floor) | Planned (F7: 3x decay for superseded)        | Planned (F7: hard delete + cascade cleanup)            | Richards & Frankland 2017, CLS |
| **Letta**    | None (context eviction only)        | None                                         | Admin delete                                           | None                           |
| **Mem0**     | None                                | LLM-driven contradiction delete              | Yes (delete by ID or filter)                           | None                           |
| **Nemori**   | None                                | None                                         | Yes (delete episode/semantic)                          | None                           |
| **MemOS**    | None                                | Working memory FIFO eviction (size limit 20) | Memory status lifecycle (activated->archived->deleted) | None                           |
| **MemoryOS** | None                                | None                                         | LFU eviction (capacity-based)                          | OS memory management           |

**Key finding**: No system has principled forgetting. Mem0's LLM-driven contradiction deletion is the most sophisticated current implementation. AnimaOS's F7 PRD is the most theoretically grounded plan, but it's not yet implemented. MemoryOS's LFU eviction is capacity-driven, not relevance-driven.

### 2.8 Theoretical Grounding

| System       | Frameworks Cited                                                                                       | Fidelity                                                                       |
| ------------ | ------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------ |
| **AnimaOS**  | CLS (McClelland & O'Reilly), GWT (Baars), PP/AIF (Friston/Clark), TCE (Barrett), EST (Zacks & Swallow) | High — named researchers, mapped to specific components, tensions acknowledged |
| **Letta**    | None explicit. Implicit: working memory (blocks), consolidation (compaction), sleep (sleeptime agents) | Low — engineering metaphors without theory                                     |
| **Mem0**     | Tulving taxonomy (enum names only)                                                                     | Minimal — enum labels don't match implementation                               |
| **Nemori**   | FEP (Friston), EST (Zacks & Tversky), CLS (McClelland & O'Reilly)                                      | Moderate — predict-calibrate maps FEP, segmenter maps EST, two-tier maps CLS   |
| **MemOS**    | OS memory hierarchy; text/activation/parametric/preference split is engineering-driven                 | Low — no named frameworks in code, paper framing is aspirational               |
| **MemoryOS** | OS memory hierarchy, Big Five personality                                                              | Low — OS metaphor with psychometric user profiling                             |

**Key finding**: AnimaOS and Nemori are the only systems with genuine cognitive science grounding. AnimaOS is broader (4 frameworks vs Nemori's 3) and more explicit about where theory maps to implementation. Nemori's FEP application is the most faithful to its source theory.

---

## 3. What AnimaOS Can Learn From Each Competitor

### From Letta

1. **Sleeptime frequency gating** — `turns_counter % frequency == 0` is simple and effective. PRD F5 already adopts this.
2. **Shared blocks via junction table** — enables multiple agents to read/write the same memory block. Useful if AnimaOS adds multi-agent.
3. **Compaction summarization prompts** — Letta's structured summary format (goals, events, details, errors, next steps, lookup hints) is more disciplined than naive summarization.
4. **Line-numbered memory rendering** — enables precise LLM edits to memory blocks.

### From Mem0

1. **Graph memory with embedding-based node dedup** — more robust than string matching. PRD F4 should specify this.
2. **UUID-to-integer mapping** for LLM calls — prevents UUID hallucination during memory update decisions.
3. **Reranking pipeline** — post-retrieval reranking with multiple provider options (future enhancement).
4. **LLM-driven ADD/UPDATE/DELETE/NONE decision framework** — explicit decision taxonomy for memory consolidation.

### From Nemori

1. **Predict-calibrate consolidation** — PRD F3 adopts this directly.
2. **Non-contiguous batch segmentation** — PRD F6 adopts this directly.
3. **BM25 + vector + RRF hybrid search** — PRD F1 adopts this directly.
4. **Provenance linking** — semantic memories link back to source episodes. AnimaOS should ensure fact->episode provenance is tracked.

### From MemOS

1. **MemCube portability concept** — validates AnimaOS's .anima/ portable Core approach. MemOS's dump/load + HuggingFace remote repos is a mature portability story (without encryption).
2. **Multi-stage retrieval pipeline** — TaskGoalParser -> GraphRetriever -> Reranker -> Reasoner is significantly more sophisticated than flat vector search. The AdvancedSearcher's iterative multi-hop retrieval (up to 3 reasoning stages) is worth studying for future enhancement.
3. **KV cache as memory** — genuinely novel concept: pre-computed attention state stored as portable memory and injected as `past_key_values`. Not applicable to AnimaOS's API-only LLM approach (Ollama/OpenRouter), but conceptually important for the thesis.
4. **NLI model for fast conflict detection** — MemOS deploys a separate NLI microservice to detect contradictions without LLM calls. Could speed up AnimaOS's conflict resolution.
5. **Preference memory with explicit/implicit split** — structured distinction between stated preferences and inferred preferences. AnimaOS stores both as generic facts.
6. **Memory versioning** — `TextualMemoryItem` has `metadata.history` and `metadata.version`. AnimaOS's self-model has versioning but memory items don't.
7. **LoRA memory is vaporware** — the concept (portable fine-tuning as memory) is compelling but the code is `b"Placeholder"`. Worth tracking but not a real competitive threat.

### From MemoryOS

1. **Heat formula** — `H = alpha*N + beta*L + gamma*R`. PRD F2 adopts this with an added `importance` factor.
2. **90-dimension user profiling** — AnimaOS has no dedicated user profiling system. The self-model tracks the AI's understanding of the user, but a structured profiling framework could improve user modeling.
3. **Session-based mid-term memory** — grouping interactions into sessions with linked pages provides conversation continuity that episode-based systems miss.

### From EverMemOS

1. **Foresight signals** — Time-bounded predictions extracted from conversation (e.g., "user has a presentation next Thursday"). AnimaOS's intentions system tracks goals but does not explicitly extract temporal predictions. Foresight signals could feed into the proactive companion (Phase 7) to anticipate user needs.
2. **MemScene thematic grouping** — MemCells are organized into thematic MemScenes (clusters of related facts). AnimaOS groups facts by type (preference, goal, relationship) but not by theme. Thematic clustering could improve retrieval — searching within a relevant MemScene narrows the search space.
3. **Agentic multi-round retrieval** — The retrieval layer can iteratively refine queries: initial search → LLM evaluates results → refined search → final answer. AnimaOS does single-pass retrieval. Agentic retrieval is expensive but produces significantly better recall for complex multi-hop questions.
4. **Progressive user profiling** — Auto-built and continuously updated user profiles from conversation history. AnimaOS has the `human` memory block (updated via `update_human_memory` tool) but it is agent-initiated, not systematically extracted. A consolidation-driven profile update (like EverMemOS) would be more comprehensive.
5. **93% LoCoMo accuracy** — The benchmark to beat. AnimaOS should run LoCoMo (eval harness is scaffolded in `apps/server/eval/`) and measure where the gap is. The gap is likely in retrieval sophistication (single-pass vs. agentic) and hybrid search (not yet implemented).

---

## 4. AnimaOS Competitive Position

### Unique Strengths (No Competitor Has)

| Capability                                          | Why It Matters                                                                                                |
| --------------------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| **Structured 5-section self-model**                 | The AI develops identity, tracks growth, maintains inner state. No competitor does this.                      |
| **Emotional intelligence (12-signal + trajectory)** | The AI notices how the user feels and adapts. Every competitor ignores emotion.                               |
| **Encrypted portable Core**                         | User-sovereign data. Copy to USB, plug in elsewhere, enter passphrase. No competitor offers this.             |
| **Digital succession**                              | What happens when the owner dies. No competitor addresses this.                                               |
| **Inner monologue (quick + deep)**                  | The AI reflects between conversations. Letta's sleeptime agents are functional but lack the reflective depth. |
| **Soul directive**                                  | A persistent identity anchor that constrains self-model evolution. No equivalent elsewhere.                   |
| **Intentional forgetting (planned)**                | Theoretically grounded forgetting with derived-reference cleanup. No competitor has principled forgetting.    |

### Parity or Behind

| Capability                           | AnimaOS Status              | Best Competitor                                                            |
| ------------------------------------ | --------------------------- | -------------------------------------------------------------------------- |
| **Overall benchmark accuracy**       | Not yet evaluated           | EverMemOS (93% LoCoMo — current SOTA)                                      |
| **Hybrid search**                    | Planned (F1)                | EverMemOS (BM25+vector+RRF, production, Elasticsearch+Milvus)              |
| **Agentic retrieval**                | None                        | EverMemOS (iterative multi-round retrieval with LLM-guided refinement)     |
| **Background consolidation**         | Inactivity-triggered        | Letta (frequency-gated, incremental, async)                                |
| **Knowledge graph**                  | Planned (F4)                | Mem0 (Neo4j + embedding dedup, production)                                 |
| **Predict-calibrate**                | Planned (F3)                | Nemori (FEP-based, production)                                             |
| **Heat scoring**                     | Planned (F2)                | MemoryOS (production, EMNLP-validated)                                     |
| **Batch segmentation**               | Planned (F6)                | Nemori (non-contiguous, production)                                        |
| **User profiling**                   | Informal (via self-model)   | EverMemOS (progressive auto-profiling), MemoryOS (90-dimension)            |
| **Foresight / temporal predictions** | Partial (intentions system) | EverMemOS (Foresight signals — time-bounded predictions from conversation) |
| **Reranking**                        | None                        | EverMemOS (reranker models), Mem0 (5 providers)                            |
| **Multi-stage retrieval**            | None                        | MemOS (TaskGoalParser -> GraphRetriever -> Reranker -> Reasoner)           |
| **Preference memory**                | Stored as generic facts     | MemOS (explicit/implicit split with dedicated pipeline)                    |

### The Real Differentiator

The competitive landscape has converged on memory _storage and retrieval_ — everyone does vector search, everyone extracts facts, several do hybrid search. The gap is no longer "who remembers?" but:

1. **Who understands?** (self-model, emotional intelligence, inner life)
2. **Who belongs to the user?** (encryption, portability, sovereignty)
3. **What happens at the end?** (succession, forgetting, continuity)

AnimaOS is the only system addressing all three. The F-series PRDs (F1-F7) close the retrieval/consolidation gap with the best competitors. The existing implementation (Phases 0-10) provides the self-model and emotional depth no competitor has.

---

## 5. Risks and Honest Gaps

| Risk                       | Details                                                                                                                                                                                                |
| -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Execution gap**          | AnimaOS's strongest retrieval/consolidation features (F1-F7) are planned, not shipped. Competitors have production implementations.                                                                    |
| **LLM model quality**      | AnimaOS is constrained to Ollama/OpenRouter open models. Competitors can use closed-source cloud models. This affects extraction quality, tool calling reliability (F4 risk), and consolidation depth. |
| **Scale testing**          | No competitor analysis included scale benchmarks for AnimaOS. MemoryOS's EMNLP results (+49% F1 on LoCoMo) are validated; AnimaOS's approach is not benchmarked.                                       |
| **No user profiling**      | MemoryOS's 90-dimension personality framework produces structured user models. AnimaOS's self-model tracks the AI's self-understanding but has no equivalent structured user profile.                  |
| **Single-user assumption** | All competitors support multi-user/multi-tenant. AnimaOS is fundamentally single-user, which simplifies crypto but limits deployment scenarios.                                                        |

---

## References

- Letta source: `letta/` (Apache 2.0), arXiv:2504.13171
- Mem0 source: `mem0/` (Apache 2.0)
- Nemori source: `nemori/` (MIT), arXiv:2508.03341
- MemOS source: `MemOS/` (Apache 2.0), arXiv:2507.03724
- MemoryOS source: `MemoryOS/` (MIT), arXiv:2506.06326
- EverMemOS: arXiv:2601.02163, GitHub: `EverMind-AI/EverMemOS` (3,300+ stars)
- AnimaOS thesis: `docs/thesis/whitepaper.md`, `docs/thesis/inner-life.md`
- AnimaOS PRDs: `docs/prds/F1-F7`
