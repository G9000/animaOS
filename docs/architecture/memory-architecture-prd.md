# PRD: Advanced Memory Architecture

**Version**: 1.0
**Date**: 2026-03-18
**Author**: AnimaOS Engineering
**Status**: Draft
**Stakeholders**: Core Engineering
**Related documents**:
- [Implementation Plan](memory-implementation-plan.md) — detailed engineering spec with function signatures, schemas, and test plans
- [Repo Analysis](memory-repo-analysis.md) — comparative source-code analysis of Letta, Mem0, Nemori, MemOS, MemoryOS
- [Research Report](../thesis/research-report-2026-03-18.md) — March 2026 literature review and pattern discovery
- [Roadmap](../thesis/roadmap.md) — product-level phase definitions (Phases 9.5–10.7)

**Individual feature PRDs**:
- [F1: Hybrid Search](prds/F1-hybrid-search.md) — BM25 + Vector + RRF
- [F2: Heat Scoring](prds/F2-heat-scoring.md) — heat-based memory scoring
- [F3: Predict-Calibrate](prds/F3-predict-calibrate.md) — predict-calibrate consolidation
- [F4: Knowledge Graph](prds/F4-knowledge-graph.md) — SQLite-backed entity-relationship graph
- [F5: Async Sleep Agents](prds/F5-async-sleep-agents.md) — frequency-gated background orchestrator
- [F6: Batch Segmentation](prds/F6-batch-segmentation.md) — topic-coherent episode segmentation

---

## 1. Executive Summary

AnimaOS is a local-first, portable AI companion with encrypted memory persistence. Its memory system currently supports flat semantic facts, fixed-interval episode generation, keyword search via Jaccard similarity, and timer-based background consolidation. While functional, this architecture has known limitations: poor keyword recall, no relational structure between memories, redundant fact extraction, inefficient resource allocation for background processing, and topic-blind episode boundaries.

This PRD defines six features that address these limitations, drawn from source-code analysis of five leading AI memory frameworks (Letta, Mem0, Nemori, MemOS, MemoryOS) and validated against published research (2025–2026). Each feature is scoped to ship independently, with a clear dependency graph governing execution order.

**Outcome**: A memory system that retrieves more relevant context, extracts higher-quality facts, organizes knowledge relationally, allocates background compute efficiently, and segments episodes by topic coherence — making ANIMA feel like it genuinely knows the user, not just stores data about them.

---

## 2. Problem Statement

### 2.1 Current State

AnimaOS has a working memory pipeline (Phases 0–10 complete, 602 tests passing):

- **Storage**: SQLite + SQLCipher encrypted Core, `MemoryItem` table with embeddings
- **Retrieval**: Cosine similarity vector search + Jaccard keyword search + RRF fusion
- **Extraction**: Regex fast path + LLM extraction (cold, no awareness of existing knowledge)
- **Scoring**: Fixed-weight formula (`importance=0.4, recency=0.35, frequency=0.25`)
- **Episodes**: Fixed-size chunking (every 6 turns = one episode, contiguous only)
- **Background processing**: Consolidation on every turn; reflection on 5-minute inactivity timer

### 2.2 Gaps

| Gap | Impact | Evidence |
|-----|--------|----------|
| **Keyword search is weak** | Proper nouns, technical terms, and exact phrases are missed by Jaccard word overlap | Nemori's BM25+RRF showed higher recall than vector-only or Jaccard-only (UnifiedSearchEngine) |
| **No relational structure** | "User works at Google" and "User's sister Alice" are flat facts with no entity linking | Mem0's graph-augmented retrieval showed 26% accuracy improvement over flat vector search |
| **Redundant extraction** | LLM extracts facts it already knows, wasting tokens; duplicates caught by downstream dedup | Nemori's predict-calibrate reduced extraction to novel delta only (Free Energy Principle) |
| **Fixed retrieval scoring** | All memories scored with same weights regardless of actual usage patterns | MemoryOS's heat scoring allocates attention based on access frequency, depth, and recency |
| **Timer-based background work** | Consolidation runs on every turn (wasteful) or only after 5 minutes idle (delayed) | Letta's frequency-gated async agents run on configurable turn intervals with heat thresholds |
| **Topic-blind episodes** | Conversations switching topics mid-episode produce incoherent episode summaries | Nemori's batch segmenter groups by topic coherence with non-contiguous message indices |

### 2.3 User Impact

These gaps manifest as:
- ANIMA forgetting things the user mentioned by exact name (keyword miss)
- ANIMA not connecting related people/places/projects in the user's life (no graph)
- ANIMA re-asking about things it already knows (redundant extraction, poor retrieval)
- Irrelevant memories surfacing while important ones are buried (fixed scoring)
- Consolidation either running too often (every turn) or too late (5-min idle)
- Episode summaries mixing unrelated topics ("we talked about work and then cooking" as one blob)

---

## 3. Goals and Non-Goals

### 3.1 Goals

1. **Higher-precision memory retrieval** through BM25 lexical search fused with vector similarity via RRF
2. **Relational understanding** of the user's world through a SQLite-backed knowledge graph
3. **Higher-quality fact extraction** by predicting expected knowledge before extracting the delta
4. **Activity-based resource allocation** via heat scoring that gates expensive background operations
5. **Efficient background compute** through frequency-gated async agents replacing timer-based triggers
6. **Topic-coherent episodes** through LLM-driven batch segmentation with non-contiguous grouping

### 3.2 Non-Goals

- **External infrastructure**: No Neo4j, ChromaDB, Redis, PostgreSQL, or Docker. All state stays in SQLite within `.anima/`.
- **Closed LLM providers**: No OpenAI, Anthropic, or Google APIs. Ollama, OpenRouter, and vLLM only.
- **Multi-user support**: The Core remains fundamentally single-user.
- **UI changes**: This PRD covers backend memory architecture only.
- **Emotional model evolution**: Migrating from 12-category to dimensional emotion representation is a separate effort.
- **Intentional forgetting**: Cryptographic deletion and GDPR governance (Roadmap Phase 10.5) are a separate PRD.
- **KV cache pre-computation**: Deferred until local inference is the primary mode.

---

## 4. Features

### F1: Hybrid Search (BM25 + Vector + RRF)

**Roadmap**: Phase 9.7
**Priority**: P0 — Foundation for all subsequent features

#### Problem
The keyword leg of hybrid search uses Jaccard word overlap (`_text_similarity()` in `vector_store.py`), which lacks term frequency weighting, inverse document frequency, and document length normalization. Proper nouns and technical terms are frequently missed.

#### Solution
Replace Jaccard with BM25Okapi (`rank-bm25` library) as the keyword search backend. The existing RRF fusion infrastructure (`_reciprocal_rank_fusion()`, `hybrid_search()` in `embeddings.py`) remains unchanged — only the keyword leg is swapped.

#### Requirements

| ID | Requirement | Priority |
|----|-------------|----------|
| F1.1 | `BM25Index` class wrapping `rank-bm25.BM25Okapi`, built lazily per user from `MemoryVector.content` | Must |
| F1.2 | `bm25_search()` function returning `(item_id, score)` pairs ranked descending | Must |
| F1.3 | Index invalidation hooks on `upsert()`, `delete()`, and `rebuild()` in `OrmVecStore` | Must |
| F1.4 | `hybrid_search()` in `embeddings.py` uses `bm25_search()` instead of `search_by_text()` | Must |
| F1.5 | Per-user index cache with thread-safe `Lock` | Must |
| F1.6 | Incremental `add_document()` / `remove_document()` for single-item changes | Should |

#### Acceptance Criteria
- Searching for "PostgreSQL" returns the memory containing that exact term, even if the embedding model under-represents it
- All 602 existing tests pass without modification
- BM25 index builds in < 100ms for 1,000 memories
- Memory overhead < 5 MB for 10,000 memories at 50 tokens each

#### Data Model Changes
None. BM25 indices are in-memory only.

#### New Dependencies
`rank-bm25` (~15 KB, pure Python) — the only new pip dependency across all 6 features.

---

### F2: Heat-Based Memory Scoring

**Roadmap**: Phase 10.4
**Priority**: P1

#### Problem
`_retrieval_score()` in `memory_store.py` uses fixed weights (`importance=0.4, recency=0.35, frequency=0.25`). All memories are scored identically regardless of actual access patterns. There is no mechanism to trigger consolidation based on memory activity.

#### Solution
Replace fixed-weight scoring with a heat model: `H = alpha * access_count + beta * interaction_depth + gamma * recency_decay`, where `recency_decay = exp(-hours_since / tau)`. Heat is persisted as a column on `MemoryItem`, updated on access, and decayed during sleep tasks.

#### Requirements

| ID | Requirement | Priority |
|----|-------------|----------|
| F2.1 | `heat` column (Float, default 0.0) on `MemoryItem` with composite index `(user_id, heat)` | Must |
| F2.2 | `compute_heat()` implementing the MemoryOS formula with configurable `alpha`, `beta`, `gamma` | Must |
| F2.3 | `update_heat_on_access()` recomputing heat when memories are touched during retrieval | Must |
| F2.4 | `decay_all_heat()` batch-updating all items during sleep tasks | Must |
| F2.5 | `get_hottest_items()` / `get_coldest_items()` for hot-memory surfacing and cold-memory archival candidates | Should |
| F2.6 | `_retrieval_score()` replaced with `compute_heat()` as the base score, blended with query embedding similarity | Must |
| F2.7 | `get_memory_items_scored()` uses `ORDER BY heat DESC` from the database instead of in-Python sorting | Should |

#### Acceptance Criteria
- A memory accessed 10 times in the last hour has a higher heat than one accessed once 7 days ago
- After 48 hours without access, a memory's heat decays by at least 75%
- First `run_sleep_tasks()` after migration backfills heat for all existing items using their `reference_count` and `last_referenced_at`
- All existing tests pass

#### Data Model Changes
1 Alembic migration: add `heat` column and index to `memory_items`.

---

### F3: Predict-Calibrate Consolidation

**Roadmap**: Phase 10.3
**Priority**: P1
**Depends on**: F1 (Hybrid Search)

#### Problem
`consolidate_turn_memory_with_llm()` in `consolidation.py` calls `extract_memories_via_llm()` which prompts the LLM with no awareness of existing knowledge. It extracts everything from scratch, producing duplicates that `store_memory_item()` then filters downstream. This wastes LLM tokens and misses opportunities to detect contradictions early.

#### Solution
Wrap the extraction pipeline with a two-step predict-calibrate cycle (Nemori's Free Energy Principle pattern):
1. **Predict**: Retrieve relevant existing facts via `hybrid_search()`, generate a prediction of what the conversation likely contains
2. **Extract delta**: Compare prediction with actual conversation, extract only surprises, corrections, and genuinely new information
3. **Quality gates**: Filter through persistence, specificity, utility, and independence tests

Cold-start mode (< 5 existing facts) falls back to direct extraction (current behavior).

#### Requirements

| ID | Requirement | Priority |
|----|-------------|----------|
| F3.1 | `predict_episode_knowledge()` generating predictions from existing facts + conversation summary | Must |
| F3.2 | `extract_knowledge_delta()` extracting only novel/contradictory/surprising statements | Must |
| F3.3 | `apply_quality_gates()` filtering through 4 tests: persistence (true in 6 months?), specificity (concrete?), utility (helps predict future?), independence (understandable without context?) | Must |
| F3.4 | `predict_calibrate_extraction()` orchestrating the full pipeline with hybrid_search retrieval | Must |
| F3.5 | Cold-start fallback when existing facts < 5 (use current direct extraction) | Must |
| F3.6 | `consolidate_turn_memory_with_llm()` routes to predict-calibrate when facts > 0, direct extraction otherwise | Must |
| F3.7 | Low temperature (0.3) for prediction prompt | Should |

#### Acceptance Criteria
- When the user mentions their job for the 5th time, the system extracts zero new facts (prediction matches reality)
- When the user contradicts a previous fact ("actually I moved to Berlin"), the system extracts the correction as delta
- Quality gates reject "we talked about food" (not specific) but accept "User's favorite restaurant is Sushi Dai in Tokyo" (specific, persistent, useful)
- Net LLM token usage per turn decreases in steady state (after 20+ facts stored)

#### Data Model Changes
None.

---

### F4: Knowledge Graph

**Roadmap**: Phase 9.5
**Priority**: P1
**Independent** (benefits from F1)

#### Problem
AnimaOS stores flat semantic facts with no relational structure. "User works at Google", "Alice is User's sister", and "User lives in Berlin" exist as independent strings. There is no way to traverse relationships (who does the user know? what's connected to their job?) or deduplicate entities ("NYC" vs "New York City").

#### Solution
Two new SQLite tables (`kg_entities`, `kg_relations`) storing entities and typed relationships, extracted via LLM tool calling during consolidation (Mem0 pattern). Graph traversal via SQL JOINs (max depth 2) produces context strings injected as a `knowledge_graph` memory block.

#### Requirements

| ID | Requirement | Priority |
|----|-------------|----------|
| F4.1 | `KGEntity` model: `name`, `name_normalized`, `entity_type` (person/place/org/concept), `description`, `mentions`, `embedding_json` | Must |
| F4.2 | `KGRelation` model: `source_id`, `destination_id`, `relation_type`, `mentions`, `source_memory_id` | Must |
| F4.3 | `extract_entities_and_relations()` using LLM structured tool calls (EXTRACT_ENTITIES_TOOL schema) | Must |
| F4.4 | `upsert_entity()` with normalized-name dedup and `mentions` increment | Must |
| F4.5 | `upsert_relation()` with source/destination entity lookup | Must |
| F4.6 | `deduplicate_entity()` via embedding similarity (threshold 0.85) for alias resolution ("NYC" = "New York City") | Should |
| F4.7 | `search_graph()` traversing from entity names with configurable max depth (default 2) and result limit (default 20) | Must |
| F4.8 | `graph_context_for_query()` extracting entities from user query, returning context strings for prompt injection | Must |
| F4.9 | `ingest_conversation_graph()` called in `run_background_memory_consolidation()` after fact extraction | Must |
| F4.10 | `knowledge_graph` memory block added to `build_runtime_memory_blocks()` | Must |
| F4.11 | Cap entity extraction at 5 entities per conversation turn to prevent graph explosion | Must |
| F4.12 | REST API for viewing entities and relations (optional) | Could |

#### Acceptance Criteria
- After conversations mentioning "Alice (sister)", "Alice's birthday", and "Google (workplace)", the graph contains entities Alice (person), Google (org) with typed relations `sister_of`, `works_at`
- Querying "what do you know about my family?" traverses the graph and surfaces Alice and her relationships
- "NYC" and "New York City" are deduplicated into one entity
- Graph traversal completes in < 50ms for 1,000 entities with depth=2

#### Data Model Changes
1 Alembic migration: create `kg_entities` and `kg_relations` tables (2 new tables).

---

### F5: Async Sleep-Time Agents

**Roadmap**: Phase 10.6
**Priority**: P2
**Depends on**: F2 (Heat Scoring), F3 (Predict-Calibrate), F4 (Knowledge Graph)

#### Problem
Background memory processing has two modes, both suboptimal: (1) `schedule_background_memory_consolidation()` fires on every turn via `asyncio.create_task()` with no frequency gating, and (2) `schedule_reflection()` fires after 5 minutes of inactivity. There is no middle ground — no heat-threshold gating, no turn counting, no parallel execution of independent tasks, and no tracking of what was already processed.

#### Solution
A frequency-gated orchestrator (`sleep_agent.py`) that replaces both trigger paths. It counts turns per user, fires background agents every N turns (configurable), gates expensive operations by heat threshold, runs independent tasks in parallel, and tracks all runs in a `background_task_runs` table for debugging.

#### Requirements

| ID | Requirement | Priority |
|----|-------------|----------|
| F5.1 | In-memory per-user turn counter with `bump_turn_counter()` and `should_run_sleeptime()` | Must |
| F5.2 | `SLEEPTIME_FREQUENCY` config (default: every 3 turns) | Must |
| F5.3 | `run_sleeptime_agents()` orchestrator dispatching parallel independent tasks (consolidation, graph ingestion, heat decay, episode check) and sequential expensive tasks (contradiction scan, profile synthesis, deep monologue) | Must |
| F5.4 | Heat-threshold gating: expensive tasks only fire when accumulated heat > `HEAT_THRESHOLD_CONSOLIDATION` | Must |
| F5.5 | `BackgroundTaskRun` model tracking task type, status, result, errors, and timestamps | Must |
| F5.6 | `_issue_background_task()` wrapping each task with tracking, error capture, and `finally`-block cleanup | Must |
| F5.7 | `last_processed_message_id` tracking to prevent reprocessing on restart | Should |
| F5.8 | `force=True` bypass for inactivity-triggered full suite (existing 5-min timer) | Must |
| F5.9 | `schedule_background_memory_consolidation()` routes through the frequency-gated orchestrator | Must |

#### Acceptance Criteria
- After 3 messages, background consolidation fires; after messages 1 and 2, it does not
- With heat below threshold, contradiction scan and profile synthesis are skipped
- Independent tasks (consolidation, graph ingestion, heat decay) run in parallel
- All background task runs are recorded in `background_task_runs` with correct status
- 5-minute inactivity timer still triggers the full suite (force mode)
- Process restart does not reprocess already-consolidated messages

#### Data Model Changes
1 Alembic migration: create `background_task_runs` table (1 new table).

---

### F6: Batch Episode Segmentation

**Roadmap**: Phase 10.7
**Priority**: P2
**Independent** (benefits from F5 for orchestration)

#### Problem
`maybe_generate_episode()` in `episodes.py` creates one episode per 6 contiguous turns. If a conversation switches topics ("work → cooking → work again"), the episode mixes topics. There is no way to produce non-contiguous topic groupings.

#### Solution
When the message buffer exceeds 8 turns, send all messages to the LLM for batch topic segmentation. The LLM returns groups of message indices (non-contiguous allowed), and each group becomes a separate episode. Falls back to sequential chunking for < 8 messages or LLM failure.

#### Requirements

| ID | Requirement | Priority |
|----|-------------|----------|
| F6.1 | `segment_messages_batch()` sending numbered messages to LLM, receiving `list[list[int]]` of topic groups | Must |
| F6.2 | Non-contiguous index support: `[[1,2,3], [8,10,11], [4,5,6,7,9,12]]` is valid | Must |
| F6.3 | `BATCH_THRESHOLD` config (default: 8 messages) gating when batch segmentation activates | Must |
| F6.4 | Low temperature (0.2) for deterministic segmentation | Must |
| F6.5 | Fallback to single-episode on LLM failure or parse error | Must |
| F6.6 | Validation: all message indices must be covered (no dropped messages) | Must |
| F6.7 | `message_indices_json` column on `MemoryEpisode` storing the 1-based indices used | Must |
| F6.8 | `segmentation_method` column on `MemoryEpisode` (`sequential` or `batch_llm`) | Must |
| F6.9 | `maybe_generate_episode()` routes to batch segmentation when buffer >= threshold | Must |
| F6.10 | Sequential method preserved for < 8 messages (backward compatible) | Must |

#### Acceptance Criteria
- A 12-message conversation about "work, cooking, work" produces 2 episodes: one about work (messages 1-3, 8-12) and one about cooking (messages 4-7)
- Episodes with < 8 messages still use the current sequential method
- `segmentation_method` column correctly reflects which method was used
- LLM timeout or malformed response falls back to a single episode containing all messages
- No messages are dropped during segmentation (index validation)

#### Data Model Changes
1 Alembic migration: add `message_indices_json` and `segmentation_method` columns to episode table.

---

## 5. Dependency Graph and Execution Order

```
                    F1: Hybrid Search (BM25 + Vector + RRF)
                        |
              +---------+---------+
              |                   |
    F2: Heat Scoring         F4: Knowledge Graph
              |                   |
              v                   |
    F3: Predict-Calibrate         |
              |                   |
              v                   |
    F5: Async Sleep Agents  <-----+
              |
              v
    F6: Batch Episode Segmentation
```

| Execution Order | Feature | Rationale |
|-----------------|---------|-----------|
| 1st | F1: Hybrid Search | Foundation — every subsequent feature retrieves memories; better retrieval = better everything |
| 2nd | F2: Heat Scoring | Low complexity, high value; adds the scoring column all other features consume |
| 3rd | F4: Knowledge Graph | Independent of F2/F3; highest novelty; simple SQLite tables |
| 4th | F3: Predict-Calibrate | Depends on F1 for retrieval; modifies the consolidation pipeline (riskiest integration) |
| 5th | F5: Async Sleep Agents | Orchestration layer; must come after the things it orchestrates |
| 6th | F6: Batch Segmentation | Lowest urgency; current fixed chunking works acceptably |

F2 and F4 can execute in parallel after F1 ships.

---

## 6. Technical Constraints

| Constraint | Details |
|------------|---------|
| **Database** | SQLite + SQLCipher only. No PostgreSQL, Redis, Neo4j, or ChromaDB. |
| **LLM providers** | Ollama, OpenRouter, vLLM only. No OpenAI, Anthropic, or Google. |
| **State location** | All state in `.anima/` directory. Portable-by-default. |
| **Backend** | Python/FastAPI at `apps/server/`. No Bun/TypeScript. |
| **Test baseline** | 602 existing tests must pass after each feature ships. |
| **New dependencies** | `rank-bm25` only (~15 KB, pure Python). No spaCy, numpy, neo4j, or chromadb. |
| **Migrations** | 4 total Alembic migrations across all 6 features. |
| **New tables** | 3 total: `kg_entities`, `kg_relations`, `background_task_runs`. |

---

## 7. Success Metrics

| Metric | Baseline | Target | Measured by |
|--------|----------|--------|-------------|
| **Keyword recall** (exact term found in top-5 results) | Low (Jaccard misses proper nouns) | > 90% for exact term queries | Unit test: search for known proper nouns |
| **Extraction precision** (% of extracted facts that are genuinely new) | ~40% (many duplicates caught by dedup) | > 80% after 20+ facts stored | Count of facts passing quality gates vs total extracted |
| **Episode topic coherence** | Mixed-topic episodes common | Single-topic episodes when batch segmentation activates | Manual review of episode summaries |
| **Background task efficiency** | Consolidation on every turn | Consolidation on every 3rd turn + heat gating | Count of `background_task_runs` per conversation |
| **Entity coverage** | 0 entities tracked | Key people, places, orgs extracted and linked | Count of `kg_entities` after 10 conversations |
| **Test suite** | 602 passing | 602+ passing (additive tests only) | CI |

---

## 8. Risks and Mitigations

| Risk | Severity | Mitigation |
|------|----------|------------|
| **BM25 index staleness** | Low | Invalidation hooks on every content mutation; worst case = slightly stale results for one turn |
| **LLM entity extraction quality** | Medium | Structured tool calling (function_call), 5-entity cap per turn, aggressive dedup via normalized names + embedding similarity |
| **Predict-calibrate prompt sensitivity** | Medium | Low temperature (0.3), downstream `store_memory_item()` dedup as safety net, cold-start fallback |
| **SQLite graph traversal at scale** | Low | Depth capped at 2 hops, result limit 20; < 1,000 entities for personal AI = fast |
| **Race conditions in parallel background tasks** | Medium | Each task opens own DB session via `db_factory()`; SQLite WAL mode handles concurrent reads |
| **Heat formula tuning** | Low | Configurable `alpha/beta/gamma` constants; first sleep-tasks run backfills from existing `reference_count` |
| **Batch segmentation LLM failure** | Low | Fallback to single-episode on any parse error; index validation ensures no dropped messages |
| **Migration on existing data** | Low | `heat` defaults to 0.0, backfilled on first sleep-tasks run; new tables start empty |

---

## 9. Rollout Plan

Each feature ships as an independent PR with:
1. New service file(s) with unit tests
2. Modified file(s) with integration tests
3. Alembic migration (if applicable)
4. Regression test confirmation (all 602+ tests pass)

Features are gated by the dependency graph (Section 5). Features F2 and F4 can be developed in parallel after F1 merges.

No feature flags required — all changes are additive (new search backend, new scoring formula, new tables, new orchestrator). The old paths are replaced, not toggled.

---

## 10. Out of Scope (Future PRDs)

| Topic | Roadmap Phase | Why deferred |
|-------|---------------|--------------|
| Intentional Forgetting (cryptographic deletion, GDPR) | 10.5 | Separate privacy/compliance PRD |
| Emotional model evolution (12-category → dimensional) | Unassigned | Requires research decision on VAD vs categorical |
| Memory governance / constitutional rules | Unassigned | Thesis-level design, not yet specified as requirements |
| KV cache pre-computation | Future | Depends on vLLM/local inference being primary mode |
| World model synthesis | Unassigned | Relationship with knowledge graph (F4) needs clarification |
| Affective Sovereignty metrics (IOS, AMR) | Unassigned | Requires emotional model evolution first |
| CLS sampling strategy for deep reflection | Unassigned | Open question in inner-life.md Section 13.6 |

---

## 11. References

| Source | What we adopted |
|--------|-----------------|
| Nemori (arXiv 2508.03341) | BM25+RRF hybrid search, predict-calibrate consolidation, batch episode segmentation |
| Mem0 (GitHub) | SQLite knowledge graph via LLM tool calling, entity dedup via embedding similarity |
| MemoryOS (GitHub) | Heat scoring formula, max-heap ranking, heat-triggered consolidation |
| Letta / UC Berkeley (arXiv 2504.13171) | Frequency-gated async sleep-time agents, turn counting, last-processed tracking |
| MemOS (arXiv 2507.03724) | Validated AnimaOS's portable-Core approach (MemCube comparison) |
| CMA / Animesis (arXiv 2603.04740) | Memory-as-ontology validation (referenced in thesis, not adopted in this PRD) |
