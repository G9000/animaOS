# Competitor Source-Code Audit: Letta & Mem0 vs AnimaOS F1-F7

**Date**: 2026-03-19
**Status**: Research (read-only audit, no code changes)
**Scope**: Deep-dive into actual shipped source code of Letta and Mem0, compared feature-by-feature against AnimaOS memory PRDs F1-F7.

---

## Part 1: Letta Architecture

### 1.1 Memory Architecture Overview

Letta has a **three-tier memory model**:

| Tier | Name | Storage | Mutability | Lifetime |
|------|------|---------|------------|----------|
| Core Memory | In-context blocks (`persona`, `human`, custom) | PostgreSQL `blocks` table | Agent-editable via `core_memory_append`, `core_memory_replace` | Persistent, always in prompt |
| Recall Memory | Conversation messages | PostgreSQL `messages` table | Read-only after creation | Persistent, paginated |
| Archival Memory | Long-term passages | PostgreSQL `archival_passages` table (or Turbopuffer) | Agent-insertable via `archival_memory_insert` | Persistent, searched on demand |

**Key files**:
- `letta/schemas/block.py` (lines 15-98) -- `BaseBlock` with `value`, `limit`, `label`, `read_only`, `description` fields
- `letta/schemas/memory.py` (lines 67-77) -- `Memory` class containing `List[Block]` and `List[FileBlock]`
- `letta/schemas/passage.py` (lines 14-78) -- `Passage` with `text`, `embedding`, `embedding_config`, `tags`
- `letta/services/passage_manager.py` -- `PassageManager` for CRUD on archival passages
- `letta/services/agent_manager.py` (line 2371) -- `query_agent_passages_async()` for search

### 1.2 Search/Retrieval

**Letta uses hybrid search (vector + BM25 + RRF) but only via Turbopuffer**:

- `letta/helpers/tpuf_client.py` (line 822) -- BM25 ranking: `"rank_by": ("text", "BM25", query_text)`
- `letta/helpers/tpuf_client.py` (line 1446) -- `_reciprocal_rank_fusion()` with `k=60` (standard RRF constant)
- `letta/helpers/tpuf_client.py` (line 1457) -- `RRF score = vector_weight * (1/(k + rank)) + fts_weight * (1/(k + rank))`
- `letta/services/agent_manager.py` (line 2418) -- `search_mode="hybrid"` when using Turbopuffer

**SQL fallback path**: When not using Turbopuffer (native PostgreSQL), search falls back to pgvector cosine similarity only -- no BM25, no RRF. The SQL path at line 2434 uses `build_agent_passage_query()` which does embedding-based search only.

**What this means**: Letta's hybrid search is a **paid cloud feature** (Turbopuffer). Self-hosted Letta users get vector-only search. AnimaOS's F1 (in-process BM25 + RRF) would deliver hybrid search quality without external dependencies.

### 1.3 Block System (Core Memory)

Letta's blocks are **free-form text buffers** with character limits that the agent edits directly:

```python
# letta/schemas/block.py line 22
value: str = Field(..., description="Value of the block.")
limit: int = Field(CORE_MEMORY_BLOCK_CHAR_LIMIT, description="Character limit of the block.")
label: Optional[str] = Field(None, description="Label of the block.")
read_only: bool = Field(False, description="Whether the agent has read-only access.")
```

**Agent editing tools** (from `letta/schemas/memory.py` lines 584-617):
- `core_memory_append(label, content)` -- appends text with newline separator
- `core_memory_replace(label, old_content, new_content)` -- exact string replacement

**Rendering**: Blocks render into `<memory_blocks>` XML in the system prompt with metadata (chars_current, chars_limit, read_only). Three rendering modes: standard, line-numbered (Anthropic models), and git-backed.

**Comparison with AnimaOS**:
- AnimaOS `MemoryBlock` objects are assembled programmatically from DB-backed data (soul, self-model, facts, episodes, etc.)
- Letta blocks are opaque text edited by the LLM agent itself -- the agent IS the memory manager
- AnimaOS has structured extraction (regex + LLM) into typed `MemoryItem` rows; Letta has unstructured append/replace on text blobs
- Letta's approach is simpler but less structured; AnimaOS's is more rigid but enables scoring, search, and analytics

### 1.4 Sleeptime Agents (Background Processing)

**This is Letta's most sophisticated feature and the direct inspiration for AnimaOS F5.**

**Key file**: `letta/groups/sleeptime_multi_agent_v4.py`

**Architecture**:
```
SleeptimeMultiAgentV4 extends LettaAgentV3

step() -> foreground response -> run_sleeptime_agents()
stream() -> foreground stream -> run_sleeptime_agents() (in finally block)
```

**Frequency gating** (lines 122-128):
```python
turns_counter = None
if self.group.sleeptime_agent_frequency is not None and self.group.sleeptime_agent_frequency > 0:
    turns_counter = await self.group_manager.bump_turns_counter_async(group_id=self.group.id, actor=self.actor)

if self.group.sleeptime_agent_frequency is None or (
    turns_counter is not None and turns_counter % self.group.sleeptime_agent_frequency == 0
):
    # run sleeptime agents
```

**Last-processed message tracking** (line 135):
```python
last_processed_message_id = await self.group_manager.get_last_processed_message_id_and_update_async(
    group_id=self.group.id, last_processed_message_id=last_response_messages[-1].id, actor=self.actor
)
```

**Task execution** (lines 152-178):
- `_issue_background_task()` creates a `Run` record (status tracking)
- Uses `safe_create_task()` for fire-and-forget asyncio tasks
- Each sleeptime agent gets a **transcript of the conversation** as input
- The sleeptime agent is a full `LettaAgentV3` instance with its own memory blocks

**Sleeptime agent behavior** (from `letta/prompts/system_prompts/sleeptime_v2.py`):
- The sleeptime agent is told "You are NOT the primary agent. You are reviewing a conversation that already happened."
- Its primary role is memory management -- update memory blocks using `core_memory_append`, `core_memory_replace`, and `rethink`
- It can chain multiple memory edits before calling a "finish" tool

**State tracking** (from `letta/schemas/group.py` lines 43-45):
```python
sleeptime_agent_frequency: Optional[int] = Field(None)
turns_counter: Optional[int] = Field(None)
last_processed_message_id: Optional[str] = Field(None)
```

**Comparison with AnimaOS F5**:
| Feature | Letta (shipped) | AnimaOS F5 (planned) |
|---------|----------------|---------------------|
| Frequency gating | `turns_counter % frequency` | Same: `turn_count % SLEEPTIME_FREQUENCY` |
| Last-processed tracking | `last_processed_message_id` on Group model | Same pattern planned |
| Task tracking | `Run` table with status, metadata, timestamps | `BackgroundTaskRun` table -- same concept |
| Parallel execution | Each sleeptime agent runs as separate asyncio task | `asyncio.gather()` for independent tasks |
| Heat-based gating | **NOT IMPLEMENTED** | Planned: expensive ops gated by heat threshold |
| Task types | Generic -- sleeptime agent decides what to do | Structured: consolidation, graph, heat decay, episode, contradiction, profile |
| Error handling | try/except with Run status update | `return_exceptions=True` in gather + finally block |
| What sleeptime agents do | LLM-driven memory block edits (unstructured) | Structured tasks: regex+LLM extraction, graph ingestion, heat decay, contradiction scan, profile synthesis |

### 1.5 Consolidation/Compaction

Letta has **message compaction** (summarization), not memory consolidation:

- `letta/services/summarizer/compact.py` -- `CompactResult` with `summary_message`, `compacted_messages`
- `letta/services/summarizer/summarizer_sliding_window.py` -- sliding window summarization
- `letta/services/summarizer/summarizer_all.py` -- full summarization

This is about **context window management** (shortening message history), not about extracting structured facts from conversations. Letta does NOT have:
- Regex-based fact extraction
- LLM-based memory extraction from conversations
- Contradiction detection across memories
- Profile synthesis from accumulated facts

These are all things AnimaOS already has (consolidation.py, sleep_tasks.py) or plans (F3 predict-calibrate).

### 1.6 Forgetting Mechanisms

Letta has **no forgetting mechanism**:
- No decay scoring
- No heat/temperature
- No archival/deletion of old memories
- No supersession tracking
- `Passage` has an `is_deleted` soft-delete flag but no automated deletion logic
- Block text can be overwritten by the agent (via `core_memory_replace` with empty string), but this is manual, not systematic

---

## Part 2: Mem0 Architecture

### 2.1 Memory Architecture Overview

Mem0 uses a **flat vector store + optional graph store** model:

| Component | Storage | What it stores |
|-----------|---------|---------------|
| Vector memories | Pluggable vector store (26 backends) | Extracted facts as embedded text strings |
| Graph memories | Neo4j, Kuzu, or Memgraph | Entity-relationship triples |
| History | SQLite (`SQLiteManager`) | Memory change history |

**Key files**:
- `mem0/memory/main.py` -- `Memory` class, the main API
- `mem0/memory/graph_memory.py` -- `MemoryGraph` for Neo4j
- `mem0/memory/kuzu_memory.py` -- `MemoryGraph` for Kuzu (embedded graph)
- `mem0/configs/prompts.py` -- All LLM prompts
- `mem0/vector_stores/base.py` -- `VectorStoreBase` abstract class

### 2.2 Memory Add/Update Flow

**This is Mem0's most interesting feature -- the 2-step LLM extraction + update pipeline.**

When `Memory.add()` is called (`main.py` lines 283-386):

1. **Parallel execution**: Vector store add + graph add run in `ThreadPoolExecutor`
2. **Vector store path** (`_add_to_vector_store`, lines 388-602):
   a. Parse messages into text
   b. **LLM call 1 (Fact Extraction)**: Extract facts from conversation using `FACT_RETRIEVAL_PROMPT` or `USER_MEMORY_EXTRACTION_PROMPT`
   c. For each extracted fact: embed it, search vector store for similar existing memories (limit=5)
   d. **LLM call 2 (Memory Update)**: Send old memories + new facts to `DEFAULT_UPDATE_MEMORY_PROMPT`, which returns actions: ADD, UPDATE, DELETE, or NONE
   e. Execute each action: create new memory, update existing, or delete

**The update prompt** (`configs/prompts.py` lines 175-323) is notable -- it handles:
- ADD: new information not in existing memory
- UPDATE: information that supersedes existing memory (keeps same ID)
- DELETE: contradictory information (removes old memory)
- NONE: information already present (no change)

**UUID hallucination protection** (lines 496-499): Mem0 maps real UUIDs to integer IDs before sending to the LLM, preventing UUID hallucination in LLM responses.

### 2.3 Graph Memory

**Key file**: `mem0/memory/graph_memory.py`

**The `add()` flow** (lines 76-94):
1. `_retrieve_nodes_from_data()` -- LLM extracts entities via tool calling (`EXTRACT_ENTITIES_TOOL`)
2. `_establish_nodes_relations_from_data()` -- LLM extracts relations via tool calling (`RELATIONS_TOOL`)
3. `_search_graph_db()` -- search for existing similar entities using embedding similarity
4. `_get_delete_entities_from_search_output()` -- LLM decides what existing relations should be deleted
5. `_delete_entities()` -- delete outdated relations
6. `_add_entities()` -- add/merge new entities and relations

**Entity deduplication** (lines 617-690):
- `_search_source_node()` and `_search_destination_node()` use **embedding similarity** with configurable threshold (default 0.7)
- Search is done via Neo4j `vector.similarity.cosine()` function
- If an existing node with similarity >= threshold is found, the new entity is merged with it (not duplicated)

**Entity normalization** (line 225):
```python
entity_type_map = {k.lower().replace(" ", "_"): v.lower().replace(" ", "_") for k, v in entity_type_map.items()}
```

**Mention counting** (lines 454, 462, etc.): Entities and relations track `mentions` count, incremented on each upsert via `coalesce(mentions, 0) + 1`.

**BM25 reranking of graph search results** (lines 117-130):
```python
search_outputs_sequence = [[item["source"], item["relationship"], item["destination"]] for item in search_output]
bm25 = BM25Okapi(search_outputs_sequence)
tokenized_query = query.split(" ")
reranked_results = bm25.get_top_n(tokenized_query, search_outputs_sequence, n=5)
```

**Comparison with AnimaOS F4**:
| Feature | Mem0 (shipped) | AnimaOS F4 (planned) |
|---------|---------------|---------------------|
| Graph storage | Neo4j / Kuzu / Memgraph (external) | SQLite tables (`kg_entities`, `kg_relations`) |
| Entity extraction | LLM tool calling (`EXTRACT_ENTITIES_TOOL`) | Same approach planned |
| Relation extraction | LLM tool calling (`RELATIONS_TOOL`) | Same approach planned |
| Entity dedup | Embedding similarity (threshold 0.7) | Embedding similarity (threshold 0.85) + normalized name matching |
| Entity normalization | `lower().replace(" ", "_")` | `normalize_entity_name()` -- same approach |
| Mention tracking | `mentions` counter on nodes/edges | `mentions` counter on `kg_entities`/`kg_relations` |
| Relation deletion | LLM decides what to delete on each add | Not planned -- only additive |
| Graph traversal | Cypher queries (unlimited depth) | SQL JOINs (max depth 2) |
| Entity cap per turn | No explicit cap | 5 entities max (`maxItems: 5`) |
| BM25 on graph results | Yes -- reranks graph search results | Not planned for graph results |

### 2.4 Search

**Mem0 search** (`main.py` lines 763-861):
1. Embed query
2. Search vector store for similar memories (cosine similarity)
3. Optionally search graph store in parallel
4. Optionally rerank results using a pluggable reranker (Cohere, etc.)
5. Apply score threshold filtering

**No BM25 on vector search**: Mem0's vector store search is pure embedding similarity. BM25 is only used for graph result reranking, not for the main memory search.

**Pluggable reranker** (`mem0/reranker/`): Supports Cohere reranker for post-retrieval reranking.

**Comparison with AnimaOS F1**:
| Feature | Mem0 (shipped) | AnimaOS F1 (planned) |
|---------|---------------|---------------------|
| Vector search | Yes (26 backends) | Yes (in-memory cosine) |
| BM25 keyword search | Only on graph results, not main search | Yes -- full BM25 on all memories |
| RRF fusion | No | Yes (k=60, already infrastructure exists) |
| Reranker | Pluggable (Cohere) | Not planned (RRF handles fusion) |
| Hybrid search | No -- vector only for main path | Yes -- BM25 + vector + RRF |

### 2.5 Heat Scoring / Decay

**Mem0 has NO heat scoring, decay, or temporal weighting of any kind.**

- No access count tracking
- No recency weighting
- No time-decay functions
- No hot/cold memory classification
- Score exists only as vector similarity score from search results
- `created_at` and `updated_at` timestamps exist but are not used for ranking

### 2.6 Forgetting / Deletion

Mem0 has **LLM-driven deletion** but no passive decay:

- **On add**: The LLM can return `DELETE` events for existing memories that are contradicted by new information (see `DEFAULT_UPDATE_MEMORY_PROMPT` lines 263-292)
- **On graph add**: `_get_delete_entities_from_search_output()` asks the LLM which existing relations should be deleted
- **Manual API**: `Memory.delete(memory_id)` for explicit deletion
- **Reset**: `Memory.reset()` clears all memories

This is **active deletion only** -- no passive decay, no gradual fading, no heat-based archival.

### 2.7 Vector Store Backends

Mem0 supports 26 vector store backends:
`azure_ai_search, azure_mysql, baidu, cassandra, chroma, databricks, elasticsearch, faiss, langchain, milvus, mongodb, neptune_analytics, opensearch, pgvector, pinecone, qdrant, redis, s3_vectors, supabase, upstash_vector, valkey, vertex_ai_vector_search, weaviate`

All implement `VectorStoreBase` with: `create_col, insert, search, delete, update, get, list_cols, delete_col, col_info, list, reset`.

---

## Part 3: Feature-by-Feature Comparison Against AnimaOS F1-F7

### F1: Hybrid Search (BM25 + Vector + RRF)

| Aspect | Letta | Mem0 | AnimaOS F1 |
|--------|-------|------|-----------|
| BM25 | Yes, via Turbopuffer only (paid cloud) | Only for graph result reranking | Planned: in-process `rank-bm25` for all memories |
| Vector search | pgvector or Turbopuffer | 26 pluggable backends | In-memory cosine similarity |
| RRF fusion | Yes, in Turbopuffer client (k=60) | No | Planned: existing RRF infrastructure (k=60) |
| Self-hosted hybrid | **No** -- SQL path is vector-only | **No** -- vector-only main search | **Yes** -- fully local, no external deps |

**Verdict**: AnimaOS F1 would be the **only self-hosted system with true hybrid search**. Letta's hybrid search requires Turbopuffer. Mem0 doesn't have hybrid search at all for its main memory path. This is a genuine differentiator.

### F2: Heat-Based Memory Scoring

| Aspect | Letta | Mem0 | AnimaOS F2 |
|--------|-------|------|-----------|
| Persistent score | No | No | Planned: `heat` column on `MemoryItem` |
| Access tracking | No | No | Existing: `reference_count`, `last_referenced_at` |
| Time decay | No | No | Planned: `exp(-hours/tau)` |
| Hot/cold classification | No | No | Planned: `get_hottest_items()`, `get_coldest_items()` |
| Gating signal | No | No | Planned: heat thresholds for expensive background ops |

**Verdict**: **Neither competitor has any form of heat scoring**. AnimaOS would be unique in having persistent, decay-aware memory scoring. This is entirely novel relative to both Letta and Mem0.

### F3: Predict-Calibrate Consolidation

| Aspect | Letta | Mem0 | AnimaOS F3 |
|--------|-------|------|-----------|
| Fact extraction | Agent-driven (sleeptime agent edits blocks) | LLM extracts facts from conversation | LLM extraction + regex fast path |
| Prediction step | No | No | Planned: predict expected knowledge before extraction |
| Delta extraction | No | Partial -- LLM update prompt compares new vs existing | Planned: extract ONLY surprising/new/contradictory |
| Quality gates | No | No | Planned: persistence, specificity, utility, independence |
| Cold-start handling | N/A | No existing-knowledge awareness on first call | Planned: fallback to direct extraction when < 5 facts |

**Mem0's approach is closest**: Its 2-step pipeline (extract facts -> compare with existing -> ADD/UPDATE/DELETE) is conceptually similar but operates at a different level. Mem0 compares new facts against existing stored facts (via vector search), then asks the LLM to decide on ADD/UPDATE/DELETE/NONE. This is **reactive** -- it catches conflicts after extraction.

AnimaOS F3's predict-calibrate is **proactive** -- it predicts expected content before extraction, extracting only the delta. This should produce fewer, higher-quality facts per turn and catch contradictions earlier.

**Verdict**: AnimaOS F3 is more sophisticated than either competitor. Mem0's 2-step pipeline is the closest analog but works at a different level.

### F4: Knowledge Graph

| Aspect | Letta | Mem0 | AnimaOS F4 |
|--------|-------|------|-----------|
| Graph storage | **None** | Neo4j / Kuzu / Memgraph | Planned: SQLite (`kg_entities`, `kg_relations`) |
| Entity extraction | N/A | LLM tool calling | Planned: LLM tool calling (same approach) |
| Relation extraction | N/A | LLM tool calling (2-step) | Planned: LLM tool calling |
| Entity dedup | N/A | Embedding similarity (0.7 threshold) | Planned: normalized name + embedding (0.85 threshold) |
| Relation deletion | N/A | LLM decides on each add | Not planned |
| Graph traversal | N/A | Cypher queries | Planned: SQL JOINs (depth 2) |
| Portable/encrypted | N/A | Requires external Neo4j/Kuzu | Yes -- inside SQLite Core |

**Risk identified**: Mem0 has **LLM-driven relation deletion** during `add()`. Their `DELETE_RELATIONS_SYSTEM_PROMPT` asks the LLM to identify outdated/contradictory relations and remove them. AnimaOS F4 only adds -- it does not prune stale relations. Over time, the graph could accumulate outdated information.

**Opportunity**: AnimaOS's SQLite-backed graph is **portable and encrypted** within the Core. Mem0 requires Neo4j (external server) or Kuzu (embedded but separate file). AnimaOS's approach is architecturally simpler and maintains the single-.anima-directory portability guarantee.

### F5: Async Sleep-Time Agents

| Aspect | Letta | Mem0 | AnimaOS F5 |
|--------|-------|------|-----------|
| Background processing | Sleeptime multi-agent (v1-v4) | **None** -- all synchronous | Planned: unified async orchestrator |
| Frequency gating | `turns_counter % frequency` | N/A | Same: `turn_count % SLEEPTIME_FREQUENCY` |
| Last-processed tracking | `last_processed_message_id` on Group | N/A | Same pattern planned |
| Task tracking | `Run` table with status | N/A | `BackgroundTaskRun` table |
| Heat-based gating | **No** | N/A | Planned: expensive ops gated by heat |
| What runs in background | LLM agent that edits memory blocks | N/A | Structured tasks: consolidation, graph, heat decay, contradiction, profile |
| Parallelism | Each sleeptime agent is a separate task | N/A | `asyncio.gather()` with `return_exceptions=True` |

**Key insight**: Letta's sleeptime agents are **full LLM agents** that receive the conversation transcript and decide what to do (edit blocks, search archival, etc.). AnimaOS's approach is **structured tasks** with specific functions for each operation. This is a fundamental design difference:

- Letta: more flexible, but unpredictable (the LLM decides what to update)
- AnimaOS: more predictable, but less flexible (specific extraction/analysis functions)

AnimaOS's structured approach is better for a single-user personal AI where reliability matters more than flexibility.

### F6: Batch Episode Segmentation

| Aspect | Letta | Mem0 | AnimaOS F6 |
|--------|-------|------|-----------|
| Episode concept | **None** -- no episodic memory | **None** -- no episodic memory | Existing: `MemoryEpisode` with LLM summaries |
| Topic segmentation | N/A | N/A | Planned: LLM-driven non-contiguous grouping |
| Message compaction | Yes -- sliding window summarization | N/A | N/A (different purpose) |

**Verdict**: Neither competitor has episodic memory at all. AnimaOS's existing episode system and planned batch segmentation is entirely unique. This is a strong differentiator grounded in Tulving's episodic/semantic distinction -- something both competitors completely lack.

### F7: Intentional Forgetting

| Aspect | Letta | Mem0 | AnimaOS F7 |
|--------|-------|------|-----------|
| Passive decay | **None** | **None** | Planned: heat-based visibility floor |
| Active suppression | **None** | LLM-driven DELETE on contradiction | Planned: derived-reference cleanup on supersession |
| User-initiated forget | Block text can be manually overwritten | `Memory.delete(id)` -- hard delete | Planned: `forget_memory()` with cascade cleanup |
| Topic-scoped forget | **None** | **None** | Planned: `forget_by_topic()` with confirmation |
| Derived reference cleanup | **None** | **None** | Planned: scan episodes, growth log, behavioral rules |
| Forget audit trail | **None** | **None** | Planned: `ForgetAuditLog` (what happened, not what was forgotten) |
| Accelerated decay for corrected items | **None** | **None** | Planned: `SUPERSEDED_DECAY_MULTIPLIER` |

**Mem0's DELETE mechanism** is the closest: When new information contradicts existing memories, the LLM can return DELETE events. But this is:
1. Only triggered during `add()` -- not passive
2. No derived reference cleanup
3. No audit trail
4. No decay curve

**Verdict**: AnimaOS F7 is significantly more sophisticated than either competitor. The derived-reference cleanup (scanning episodes, growth log, behavioral rules for citations of forgotten memories) is entirely novel.

---

## Part 4: Risk Assessment

### 4.1 Where AnimaOS's Plans Are WEAKER Than Shipped Competitors

| Risk | Source | Impact | Mitigation |
|------|--------|--------|------------|
| **No relation deletion in KG** | Mem0 has LLM-driven relation deletion on every `add()` | AnimaOS graph could accumulate stale relations over time | Add a deletion/pruning step to F4, perhaps during sleep tasks |
| **Single vector store backend** | Mem0 supports 26 backends; Letta uses pgvector/Turbopuffer | AnimaOS is locked to in-process SQLite vectors | Acceptable for single-user; portability is the tradeoff |
| **No reranker** | Mem0 has pluggable rerankers (Cohere) | Post-retrieval reranking could improve quality | RRF fusion in F1 serves a similar purpose |
| **Sleeptime agent flexibility** | Letta's sleeptime agents are full LLM agents that can do anything | AnimaOS's structured tasks are less flexible | Structured tasks are more predictable -- better for personal AI reliability |
| **No graph search BM25** | Mem0 BM25-reranks graph search results | AnimaOS graph search is SQL-only (no BM25 on triples) | Could add BM25 reranking of graph results in a future iteration |

### 4.2 Where AnimaOS's Plans Are STRONGER

| Advantage | Why it matters |
|-----------|---------------|
| **Self-hosted hybrid search (F1)** | Only system with BM25+vector+RRF without external dependencies |
| **Heat scoring (F2)** | Neither competitor has any temporal/usage-based memory scoring |
| **Predict-calibrate (F3)** | More sophisticated than Mem0's reactive approach; Free Energy Principle grounding |
| **Portable encrypted graph (F4)** | SQLite graph inside the Core -- no Neo4j dependency |
| **Structured background tasks (F5)** | More predictable than Letta's open-ended LLM agents |
| **Episodic memory (F6)** | Neither competitor has episodic memory at all |
| **Intentional forgetting (F7)** | Derived-reference cleanup, passive decay, audit trail -- all novel |
| **Consciousness architecture** | Self-model, emotional intelligence, inner monologue -- neither competitor attempts this |
| **Open model requirement** | Both competitors default to OpenAI; AnimaOS works with Ollama/OpenRouter only |

---

## Part 5: Actionable Findings

### 5.1 Things to Add to AnimaOS PRDs

1. **F4 should include relation pruning**: Add a step during sleep tasks (or on each `ingest_conversation_graph()` call) where the LLM evaluates whether existing relations should be deleted based on new information. Mem0's `DELETE_RELATIONS_SYSTEM_PROMPT` is a good reference. Without this, the graph will grow monotonically and accumulate stale data.

2. **F4 should consider BM25 reranking of graph search results**: Mem0 uses `BM25Okapi` to rerank graph search results before returning them. This is cheap (the result set is already small) and improves relevance.

3. **F5 should consider the "conversation transcript" approach**: Letta's sleeptime agents receive the full conversation transcript as input. AnimaOS's structured tasks receive individual messages. For tasks like graph ingestion and profile synthesis, having the full conversation context may produce better results.

4. **Mem0's UUID hallucination protection is worth adopting**: Mem0 maps real UUIDs to integer IDs before sending to the LLM (lines 496-499). This prevents the LLM from hallucinating or corrupting memory IDs. AnimaOS should consider a similar pattern wherever memory IDs are included in LLM prompts.

### 5.2 Things NOT to Change

1. **SQLite-only graph (F4)**: Despite Mem0's 3 graph backends, AnimaOS's SQLite approach is correct for the portable-Core architecture. Do not add Neo4j.

2. **Structured tasks over LLM agents (F5)**: Letta's approach of full LLM agents for background processing is more flexible but less reliable. AnimaOS's structured approach is the right call for a personal AI that must be predictable.

3. **In-process BM25 (F1)**: Letta requires Turbopuffer for hybrid search. AnimaOS's in-process approach is the correct architectural choice for a single-user system.

4. **No external vector store (F1)**: Mem0's 26-backend approach is for a cloud platform serving many users. AnimaOS is a single-user system where in-process vectors are sufficient and avoid dependency complexity.

### 5.3 Confidence Levels

| Claim | Confidence |
|-------|-----------|
| Letta has no heat scoring / decay | HIGH -- searched entire codebase, no decay/heat/scoring logic found |
| Mem0 has no passive decay | HIGH -- searched entire codebase, confirmed |
| Letta's hybrid search requires Turbopuffer | HIGH -- confirmed by reading SQL fallback path |
| Neither competitor has episodic memory | HIGH -- no episode/segment concept in either codebase |
| Mem0's graph deletion is LLM-driven on every add | HIGH -- confirmed by reading `_get_delete_entities_from_search_output()` |
| AnimaOS F3 predict-calibrate is more sophisticated than Mem0's approach | MEDIUM -- Mem0's 2-step reactive pipeline is effective; predict-calibrate's advantage depends on implementation quality |
| Letta's sleeptime agents are less reliable than structured tasks | MEDIUM -- depends on the quality of the sleeptime agent prompt and the specific LLM model used |

---

## Appendix: Key File Paths Referenced

### Letta
- `letta/groups/sleeptime_multi_agent_v4.py` -- Core sleeptime agent orchestration
- `letta/schemas/block.py` -- Block data model
- `letta/schemas/memory.py` -- Memory (block collection) + prompt rendering
- `letta/schemas/passage.py` -- Archival memory passage model
- `letta/schemas/group.py` -- Group with `sleeptime_agent_frequency`, `turns_counter`, `last_processed_message_id`
- `letta/services/passage_manager.py` -- Passage CRUD
- `letta/services/agent_manager.py` -- `query_agent_passages_async()`, `search_agent_archival_memory_async()`
- `letta/services/summarizer/compact.py` -- Message compaction
- `letta/helpers/tpuf_client.py` -- Turbopuffer hybrid search + RRF
- `letta/prompts/system_prompts/sleeptime_v2.py` -- Sleeptime agent system prompt
- `letta/functions/function_sets/base.py` -- `archival_memory_search()`, `archival_memory_insert()`
- `letta/services/tool_executor/core_tool_executor.py` -- Tool execution for archival search/insert

### Mem0
- `mem0/memory/main.py` -- `Memory` class: `add()`, `search()`, `get_all()`, `delete()`
- `mem0/memory/graph_memory.py` -- `MemoryGraph`: entity extraction, dedup, BM25 reranking
- `mem0/memory/kuzu_memory.py` -- Kuzu (embedded) graph backend
- `mem0/memory/memgraph_memory.py` -- Memgraph backend
- `mem0/memory/base.py` -- `MemoryBase` abstract class
- `mem0/graphs/tools.py` -- `EXTRACT_ENTITIES_TOOL`, `RELATIONS_TOOL`, `DELETE_MEMORY_TOOL_GRAPH`
- `mem0/graphs/utils.py` -- `EXTRACT_RELATIONS_PROMPT`, `DELETE_RELATIONS_SYSTEM_PROMPT`
- `mem0/configs/prompts.py` -- `FACT_RETRIEVAL_PROMPT`, `DEFAULT_UPDATE_MEMORY_PROMPT`
- `mem0/vector_stores/base.py` -- `VectorStoreBase` interface
- `mem0/reranker/` -- Pluggable reranker support
