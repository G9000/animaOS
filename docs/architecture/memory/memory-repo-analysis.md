---
title: Memory Framework Repository Analysis
description: Comparative analysis of Letta, Mem0, Nemori, MemOS, and MemoryOS
category: research
date: 2026-03-18
---

# Memory Framework Repository Analysis
**Date**: 2026-03-18
**Repos Analyzed**: Letta, Mem0, Nemori, MemOS, MemoryOS

---

## 1. Letta (Sleep-Time Compute)

### Architecture
- **Agent hierarchy**: `LettaAgentV2` -> `LettaAgentV3` -> `SleeptimeMultiAgentV4`
- V3 strips down V2: no inner thoughts in kwargs, no heartbeats, supports non-tool returns
- V4 wraps V3 with sleep-time agent orchestration

### Sleep-Time Mechanism (Key Innovation)
- `SleeptimeMultiAgentV4.step()` calls `super().step()` (foreground agent), then `run_sleeptime_agents()` asynchronously
- **Configurable frequency**: `sleeptime_agent_frequency` controls how often background agents run (e.g., every N turns)
- **Turn counter**: `bump_turns_counter_async()` tracks turns; sleeptime agents fire when `counter % frequency == 0`
- **Background tasks**: Each sleeptime agent ID gets an `_issue_background_task()` with the last response messages
- **Last-processed tracking**: `get_last_processed_message_id_and_update_async()` ensures sleeptime agents only process new messages
- **Stream support**: Sleep-time agents run in `finally` block of stream, ensuring they execute even on GeneratorExit

### Key Pattern: Context Window Management
- V3 has `context_token_estimate` for proactive summarization/eviction
- `_compute_tool_return_truncation_chars()`: dynamic cap at 20% of context window x 4 chars/token, min 5k chars
- `compact_messages()` for context compaction with `CompactionSettings`

### Relevance to AnimaOS
- **Sleep-time pattern is directly implementable**: Run background consolidation/reflection agents after each conversation turn
- **Frequency gating**: Don't run expensive background work every turn; use a counter
- **Message tracking**: Track last-processed message ID to avoid reprocessing
- AnimaOS's existing `consolidation.py` and `inner_monologue.py` could adopt this async background pattern

---

## 2. Mem0 (Graph Memory)

### Architecture
- `MemoryGraph` class backed by Neo4j for knowledge graph storage
- Entity extraction via LLM tool calling (`EXTRACT_ENTITIES_TOOL`, `RELATIONS_TOOL`)
- BM25 reranking of graph search results

### Graph Memory Mechanism (Key Innovation)
- **Dual extraction**: Extracts both entities and relations from conversations via structured LLM tool calls
- **Entity deduplication**: Uses LLM to detect when new entities match existing ones (e.g., "NYC" = "New York City")
- **Relation types**: Source entity -> relation -> destination entity (e.g., "User" -> "lives_in" -> "San Francisco")
- **Threshold-based filtering**: Configurable similarity threshold for graph search results
- **BM25 reranking**: After graph traversal, results are reranked using BM25 for lexical relevance

### Key Pattern: Structured Entity Extraction
```
EXTRACT_ENTITIES_TOOL = {
    "type": "function",
    "function": {
        "name": "extract_entities",
        "parameters": {
            "entities": [{"name": str, "type": str, "description": str}]
        }
    }
}
```

### Relevance to AnimaOS
- **Knowledge graph layer is a critical gap**: AnimaOS has semantic facts but no relational structure between them
- **Entity extraction via tool calling**: More reliable than free-form LLM extraction
- **Graph + vector hybrid search**: Mem0 combines graph traversal with vector similarity -- AnimaOS should do the same
- **No Neo4j needed**: AnimaOS can implement a lightweight graph in SQLite (entities table + relations table)

---

## 3. Nemori (Event Segmentation + Predict-Calibrate)

### Architecture
- Clean domain-driven design: `core/`, `generation/`, `search/`, `models/`, `infrastructure/`
- `MemorySystem` orchestrates: message buffering -> boundary detection -> episode generation -> semantic extraction
- Per-user processing locks via `MessageBufferManager`

### Message Buffer System
- `MessageBufferManager`: Per-user `RLock` (user-level locking, not global)
- Buffer has `min` and `max` thresholds; episode creation triggers at `buffer_size >= buffer_size_min`
- Buffer timeout disabled (explicit design choice) -- only size-based triggers

### Batch Segmentation (Key Innovation)
- `BatchSegmenter`: Accumulates messages to threshold, then uses LLM to group into episodes
- **Non-continuous grouping**: Episodes can have non-continuous message indices based on topic coherence
  - Example: `[[1,2,3], [4,5,6,7], [8,10,11], [9,12]]` -- messages 9 and 12 share a topic distinct from 8,10,11
- Low temperature (0.2) for consistent segmentation
- Fallback: if LLM fails, all messages become one episode

### Prediction-Correction Engine (Key Innovation)
- **Two-step learning cycle** based on Free Energy Principle:
  1. **Predict**: Given episode title + existing knowledge, predict what the episode contains
  2. **Extract**: Compare prediction with actual content, extract only the *delta* (new knowledge)
- **Cold start mode**: When no existing knowledge exists, extract directly from first episode
- **ChromaDB vector search** for retrieving relevant existing statements (with fallback to random sampling)
- High-value knowledge filtering: persistence test, specificity test, utility test, independence test

### Unified Search Engine
- **Hybrid search**: BM25 + ChromaDB vector search in parallel via `ThreadPoolExecutor`
- **Reciprocal Rank Fusion (RRF)**: Combines BM25 and vector results with k=60
- **NOR-LIFT ranking**: Attempted but currently falls back to ChromaDB (not fully implemented)
- Separate indices for episodic and semantic memories
- Incremental updates: `add_episode()` and `add_semantic_memory()` update both indices

### Relevance to AnimaOS
- **Predict-calibrate pattern**: AnimaOS's consolidation could predict what it expects, then extract only surprises
- **Batch segmentation**: AnimaOS could batch-segment conversations by topic rather than fixed-size chunks
- **RRF hybrid search**: AnimaOS's semantic retrieval could combine BM25 + vector for better recall
- **User-level locking**: Important for multi-user scenarios (AnimaOS is single-user but good pattern)
- **Knowledge quality filtering**: The 4-test filter (persistence, specificity, utility, independence) is excellent

---

## 4. MemOS (MemCube Abstraction)

### Architecture
- **MOSCore**: Memory Operating System Core -- manages multiple MemCubes
- **MemCube** (`GeneralMemCube`): Container for 4 memory types:
  1. `text_mem` (TextualMemory): Factual/conversational memory stored as text
  2. `act_mem` (ActivationMemory): KV cache for fast inference (vLLM/HuggingFace)
  3. `para_mem` (ParametricMemory): LoRA adapters for fine-tuned knowledge
  4. `pref_mem` (PreferenceMemory): User preference storage
- **MemScheduler**: Background task scheduler for memory management operations

### MemCube System (Key Innovation)
- Each MemCube has a `cube_id` and `user_id`
- **Serialization**: `load(dir)` / `dump(dir)` -- entire memory state portable via directory
- **Schema validation**: Config schema checked on load to prevent version mismatches
- **Remote loading**: `init_from_remote_repo()` loads MemCubes from HuggingFace datasets
- **Selective loading**: Can load/dump specific memory types: `load(dir, memory_types=["text_mem", "pref_mem"])`

### Activation Memory Manager (Key Innovation)
- Converts textual memories into KV cache entries for LLM inference acceleration
- `update_activation_memory()`: Takes text memories -> extracts KV cache items -> dumps to disk
- **Deduplication**: Skips update if new composed text matches existing cache
- **Periodic updates**: `update_activation_memory_periodically()` with configurable interval
- Supports both HuggingFace and vLLM KV cache formats

### Tree Text Memory
- Hierarchical memory organization with `tree_text_memory/organize/`
- `reorganizer.py`: Periodic reorganization of memory tree structure
- `relation_reason_detector.py`: Detects relationships and reasons between memories
- `advanced_searcher.py`: Multi-strategy search through the memory tree

### Memory Scheduler
- Task-based architecture: ADD, ANSWER, QUERY, MEM_READ, PREF_ADD task types
- `dispatcher.py` -> `orchestrator.py` -> individual `handlers/`
- Redis or local queue backends
- Monitor system: `general_monitor.py`, `task_schedule_monitor.py`, `dispatcher_monitor.py`
- `enhancement_pipeline.py`, `filter_pipeline.py`, `rerank_pipeline.py` for retrieval quality

### Relevance to AnimaOS
- **MemCube portability aligns perfectly with AnimaOS's .anima/ Core**: Both aim for portable, self-contained AI memory
- **4-type memory taxonomy**: AnimaOS has text+episodic but lacks activation (KV cache) and parametric (LoRA) memory
- **Activation memory**: If AnimaOS uses local LLMs (Ollama/vLLM), pre-computing KV caches from memories would dramatically speed up inference
- **Selective memory loading**: AnimaOS could load only needed memory domains when starting up
- **Memory scheduler pattern**: Background task queue for memory management operations

---

## 5. MemoryOS (Hierarchical Short/Mid/Long-Term)

### Architecture
- Clean 3-tier memory hierarchy:
  1. `ShortTermMemory`: FIFO deque (default capacity: 10 QA pairs)
  2. `MidTermMemory`: Session-based with heat scoring and LFU eviction
  3. `LongTermMemory`: User profiles + knowledge base with vector search
- `Updater`: Orchestrates short-term -> mid-term promotion
- `Retriever`: Parallel retrieval across all tiers
- ChromaDB for vector storage

### Heat-Based Memory Management (Key Innovation)
- **Segment heat formula**: `H = alpha * N_visit + beta * L_interaction + gamma * R_recency`
  - `N_visit`: Number of times session was accessed
  - `L_interaction`: Number of interactions (pages) in session
  - `R_recency`: Time decay factor `compute_time_decay(last_visit, now, tau_hours=24)`
- **Max-heap** (negated for Python's min-heap): Hottest sessions surface first
- **LFU eviction**: When mid-term exceeds capacity, least-frequently-used sessions are evicted
- **Heat-triggered promotion**: When session heat exceeds threshold, triggers profile + knowledge extraction

### Session-Based Mid-Term Memory
- Messages are grouped into "sessions" with summaries, keywords, and embeddings
- **Topic merging**: New pages find the best matching session via semantic + keyword (Jaccard) similarity
- **Dialogue chain tracking**: Pages linked via `pre_page`/`next_page` with `meta_info` propagation
- **Conversation continuity detection**: LLM-based check if consecutive pages are part of same conversation

### Parallel Retrieval (Key Innovation)
- `Retriever.retrieve_context()` runs 3 searches in parallel via `ThreadPoolExecutor(max_workers=3)`:
  1. Mid-term session/page search
  2. User long-term knowledge search
  3. Assistant long-term knowledge search
- Results combined with heap-based top-K selection across sessions

### Knowledge Extraction Pipeline
- **Parallel LLM processing**: Profile update + knowledge extraction run concurrently
- **Dual knowledge stores**: User knowledge (private) + assistant knowledge (shared)
- After extraction, pages marked as `analyzed=True` and session heat reset

### Relevance to AnimaOS
- **Heat scoring is directly implementable**: AnimaOS could add heat scores to memory items for importance-weighted retrieval
- **Session-based grouping**: Instead of flat memory items, group related memories into sessions with summaries
- **Heat-triggered consolidation**: Run expensive operations (profile extraction, knowledge distillation) only when memory "heat" exceeds threshold -- more efficient than time-based scheduling
- **Parallel retrieval**: AnimaOS's semantic retrieval should parallelize across memory types
- **Dialogue chain tracking**: Link related conversations for context continuity

---

## Cross-Cutting Patterns Summary

### Pattern 1: Background Processing Architecture
| Framework | Mechanism | Trigger |
|-----------|-----------|---------|
| Letta | Async sleep-time agents | Every N turns (configurable) |
| MemOS | Task scheduler with queue | Task dispatch + periodic timer |
| MemoryOS | Heat-triggered analysis | Session heat > threshold |
| Nemori | Buffer threshold | Buffer size >= min threshold |

**Recommendation for AnimaOS**: Combine heat-triggering (MemoryOS) with async background agents (Letta). Run consolidation when accumulated memory "heat" exceeds threshold, not on fixed timer.

### Pattern 2: Multi-Tier Memory with Promotion
| Framework | Tiers | Promotion Mechanism |
|-----------|-------|---------------------|
| MemoryOS | Short -> Mid -> Long | FIFO overflow -> topic merge -> heat threshold |
| MemOS | Text + Activation + Parametric + Preference | Scheduler-driven |
| Nemori | Buffer -> Episodic -> Semantic | Buffer threshold -> predict-calibrate |

**Recommendation for AnimaOS**: Formalize the promotion pipeline: working memory -> episodic (conversation chunks) -> semantic (extracted facts) -> self-model (identity-level abstractions).

### Pattern 3: Hybrid Search
| Framework | Strategy |
|-----------|----------|
| Nemori | BM25 + ChromaDB vector + RRF fusion |
| Mem0 | Graph traversal + BM25 reranking |
| MemoryOS | Semantic + keyword (Jaccard) similarity |
| MemOS | Tree search + advanced searcher |

**Recommendation for AnimaOS**: Add BM25 lexical search alongside existing vector search, fuse with RRF (k=60). This would catch keyword-relevant memories that embedding similarity misses.

### Pattern 4: Prediction-Based Learning
| Framework | Approach |
|-----------|----------|
| Nemori | Predict episode content from existing knowledge, extract delta |
| MemoryOS | Compare conversation to existing sessions for topic merging |

**Recommendation for AnimaOS**: Implement predict-calibrate in consolidation: before extracting facts from a conversation, predict what you'd expect based on existing knowledge, then focus extraction on surprises/contradictions.

### Pattern 5: Memory Portability
| Framework | Export Format |
|-----------|-------------|
| MemOS | Directory with config.json + serialized memories |
| AnimaOS | .anima/ SQLite + SQLCipher |

**Validation**: AnimaOS's approach is more secure (encrypted at rest) and more compact (single DB file vs directory of files). MemOS validates the concept but AnimaOS's implementation is superior for personal AI.

---

## Priority Implementation Recommendations for AnimaOS

### Critical (Phase 9.5 - Knowledge Graph)
1. **SQLite-backed knowledge graph**: Entities table + relations table, extracted via structured LLM tool calls (Mem0 pattern)
2. **Graph + vector hybrid retrieval**: Traverse graph for related entities, then vector search for semantic relevance

### Critical (New - Hybrid Search)
3. **Add BM25 search**: Complement existing vector search with lexical matching
4. **RRF fusion**: Combine vector + BM25 results using Reciprocal Rank Fusion

### High Priority (Phase 10.4 - Heat-Based Memory Scoring)
5. **Heat-based memory scoring**: `H = alpha * access_count + beta * interaction_depth + gamma * recency_decay`
6. **Heat-triggered consolidation**: Replace fixed-timer consolidation with heat-threshold triggers

### High Priority (New - Predict-Calibrate)
7. **Prediction-correction in consolidation**: Predict expected facts, extract only delta/surprises
8. **Knowledge quality gates**: Apply persistence, specificity, utility, independence tests before storing

### Medium Priority (Sleep-Time Enhancement)
9. **Async background agents**: Run reflection/consolidation asynchronously after conversation turns
10. **Frequency gating**: Not every turn needs background processing; use counter + heat threshold

### Future (Activation Memory)
11. **KV cache pre-computation**: If using vLLM/local models, pre-compute KV caches from core memories for faster inference
12. **Batch segmentation**: Use LLM to intelligently group conversation messages by topic (non-continuous grouping)

---

## References

These 12 recommendations have been translated into a concrete engineering plan with exact file paths, function signatures, and Alembic migration specs:

- See `docs/architecture/memory/memory-implementation-plan.md` for the phase-by-phase implementation plan (Phases 1-6)
- See `docs/thesis/roadmap.md` for the product-level roadmap phases (9.5, 9.7, 10.3, 10.4, 10.6, 10.7)
- See `docs/thesis/research-report-2026-03-18.md` for the research context behind these findings
