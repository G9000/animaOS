---
title: Memory System Deep Dive
description: Full memory lifecycle — write paths, retrieval scoring, consolidation, embeddings, claims, episodic memory, self-model
category: architecture
---

# Memory System Deep Dive

[Back to Index](README.md)

This document traces every path through AnimaOS's memory system: how memories are written, stored, retrieved, scored, consolidated, and maintained. It covers the full lifecycle from user utterance to long-term knowledge.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Memory Taxonomy](#memory-taxonomy)
3. [Write Path: How Memories Are Created](#write-path-how-memories-are-created)
4. [Read Path: How Memories Are Retrieved](#read-path-how-memories-are-retrieved)
5. [Memory Blocks: The System Prompt Interface](#memory-blocks-the-system-prompt-interface)
6. [Embedding & Vector Search](#embedding--vector-search)
7. [Retrieval Scoring](#retrieval-scoring)
8. [Consolidation Pipeline](#consolidation-pipeline)
9. [Conflict Resolution & Deduplication](#conflict-resolution--deduplication)
10. [Structured Claims](#structured-claims)
11. [Session Memory (Working Notes)](#session-memory-working-notes)
12. [Episodic Memory](#episodic-memory)
13. [Self-Model (Agent Identity)](#self-model-agent-identity)
14. [Emotional Intelligence](#emotional-intelligence)
15. [Sleep Tasks (Background Maintenance)](#sleep-tasks-background-maintenance)
16. [Reflection & Inner Monologue](#reflection--inner-monologue)
17. [Context Window Integration](#context-window-integration)
18. [Encryption & Portability](#encryption--portability)
19. [File Reference](#file-reference)

> **See also:** [F1-F7 Memory System Implementation](memory-f1-f7-implementation.md) — BM25 hybrid search, heat scoring, predict-calibrate, knowledge graph, async orchestrator, batch segmentation, intentional forgetting

---

## Architecture Overview

```
                          USER MESSAGE
                               |
            +------------------+------------------+
            |                                     |
     [Real-time Path]                    [Background Path]
            |                              (after turn completes)
            v                                     |
   +------------------+                  +--------v-----------+
   | Hybrid Search    |                  | Sleep-Time Agent   |
   | (embeddings.py)  |                  | Orchestrator (F5)  |
   | - embed query    |                  |                    |
   | - semantic leg   |                  | Per-turn:          |
   | - BM25 leg (F1)  |                  |  - consolidation   |
   | - RRF merge      |                  |    (predict-cal F3)|
   | - heat floor (F2)|                  |                    |
   +--------+---------+                  | Every Nth turn:    |
            |                            | +-- parallel ---+  |
            v                            | | consolidate   |  |
   +------------------+                  | | embed backfill|  |
   | Memory Blocks    |                  | | KG ingest (F4)|  |
   | (memory_blocks)  |                  | | heat decay(F2)|  |
   | - 15+ blocks     |                  | | episode gen   |  |
   | - KG context (F4)|                  | |  (batch F6)   |  |
   | - scored ranked  |                  | +-- heat-gated -+  |
   | -> system prompt |                  | | contradict    |  |
   +------------------+                  | | profile synth |  |
                                         | +-- time-gated -+  |
                                         | | deep monologue|  |
                                         | +-- forgetting -+  |
                                         | | passive decay |  |
                                         | | suppress (F7) |  |
                                         +---------+----------+
```

---

## Memory Taxonomy

All persistent memory lives in SQLite tables. The system uses **supersession** (never deletes, creates new rows and links old ones via `superseded_by`).

### Storage Tables

| Table | Purpose | Key Fields |
|-------|---------|-----------|
| `memory_items` | Core long-term memories (facts, preferences, goals, relationships, focus) | `content`, `category`, `importance`, `embedding_json`, `superseded_by`, `heat`, `reference_count`, `last_referenced_at` |
| `memory_item_tags` | Tag junction table for memory items | `item_id`, `tag`, `user_id` |
| `memory_claims` | Structured slot-based claims (deterministic dedup) | `canonical_key`, `namespace`, `slot`, `value_text`, `polarity`, `confidence`, `status` |
| `memory_claim_evidence` | Evidence backing each claim | `claim_id`, `source_text`, `source_kind` |
| `memory_daily_logs` | Raw conversation turn logs | `user_message`, `assistant_response`, `date` |
| `memory_episodes` | Summarized conversation sessions | `summary`, `topics_json`, `emotional_arc`, `significance_score`, `turn_count`, `message_indices_json`, `segmentation_method`, `needs_regeneration` |
| `memory_vectors` | Packed float32 embeddings for fast search | `item_id`, `content`, `embedding` (binary), `category`, `importance` |
| `self_model_blocks` | Agent's self-model sections (identity, persona, soul, etc.) | `section`, `content`, `version`, `needs_regeneration` |
| `emotional_signals` | Detected user emotional states | `emotion`, `confidence`, `trajectory`, `evidence_type`, `evidence` |
| `session_notes` | Working notes scoped to conversation thread | `key`, `value`, `note_type`, `is_active` |
| `kg_entities` | Knowledge graph entities (F4) | `name`, `name_normalized`, `entity_type`, `description`, `mention_count` |
| `kg_relations` | Knowledge graph relations (F4) | `source_id`, `destination_id`, `relation_type`, `confidence` |
| `forget_audit_log` | Audit trail for forgetting operations (F7) | `trigger`, `scope`, `items_forgotten`, `derived_refs_affected` |
| `background_task_runs` | Sleep-time task tracking (F5) | `task_type`, `status`, `result_json`, `error_message` |

### Memory Categories

| Category | What it captures | Example | Importance range |
|----------|-----------------|---------|-----------------|
| `fact` | Biographical and factual info | "Works as a software engineer" | 1-5 (5 = identity-defining) |
| `preference` | Likes, dislikes, preferences | "Prefers dark mode" | 1-5 |
| `goal` | Aspirations and objectives | "Wants to learn Rust" | 1-5 |
| `relationship` | People and connections | "Has a sister named Maya" | 1-5 |
| `focus` | Current primary focus (singleton) | "Preparing for job interview" | 4 (fixed) |

---

## Write Path: How Memories Are Created

Memories enter the system through four channels:

### Channel 1: Agent Tools (Explicit)

The agent can directly write memories during conversation via tools:

```
save_to_memory(content, category, importance)
  -> memory_store.store_memory_item()
    -> analyze_memory_item()    # check for duplicates/conflicts
    -> if "add": create MemoryItem
    -> if "update": supersede_memory_item()
    -> if "duplicate": skip
    -> if "similar" + defer: return for LLM conflict resolution
```

**`update_human_memory(content)`** updates the agent's understanding of the user (stored in `self_model_blocks` section="human").

### Channel 2: Background Consolidation (Automatic)

After every conversation turn, `schedule_background_memory_consolidation()` runs as a fire-and-forget async task:

```
consolidation pipeline:
  1. add_daily_log()              -> raw turn log
  2. extract_turn_memory()        -> regex patterns (fast, deterministic)
  3. extract_memories_via_llm()   -> LLM extraction (rich, async)
  4. store_memory_item()          -> for each extracted item
  5. upsert_claim()               -> structured claim dual-write
  6. record_emotional_signal()    -> emotional detection from LLM
  7. backfill_embeddings()        -> embed items without vectors
  8. companion.invalidate_memory() -> bust cache for next turn
```

### Channel 3: Sleep Tasks (Maintenance)

During user inactivity, `run_sleep_tasks()` can create merged memories:

- **Contradiction scan**: finds conflicting items, resolves via LLM (KEEP_FIRST/KEEP_SECOND/MERGE)
- **Profile synthesis**: combines related facts into single statements

### Channel 4: Reflection (Self-Model)

The agent updates its own self-model sections during reflection:
- **Quick reflection**: updates inner_state, records emotional observations
- **Deep monologue**: full self-model rewrite (identity, working memory, growth log, intentions)

### The `store_memory_item()` Flow

This is the core write function. It never blindly inserts:

```python
def store_memory_item(db, *, user_id, content, category, importance,
                      allow_update=False, defer_on_similar=False):
    1. clean_memory_text(content)
    2. analyze_memory_item() ->
       a. For each existing item in category:
          - casefold match? -> "duplicate"
          - same fact slot, different value? -> "update"
          - same preference subject, different polarity? -> "update"
          - Jaccard similarity > 0.4? -> "similar"
       b. No match? -> "add"
    3. Based on analysis:
       - "duplicate" -> skip, return matched item
       - "update" + allow_update -> supersede_memory_item()
       - "update" + !allow_update -> return "conflict"
       - "similar" + defer_on_similar -> return for LLM resolution
       - "add" -> create new MemoryItem
```

### Supersession (Never Delete)

```python
def supersede_memory_item(db, *, old_item_id, new_content, importance):
    1. Create new MemoryItem with new_content
    2. Set old_item.superseded_by = new_item.id
    3. Remove old item from vector store
    # Old item stays in DB forever -- queryable for audit/history
```

---

## Read Path: How Memories Are Retrieved

Memories are loaded into the agent's context through two mechanisms:

### 1. Static Memory Blocks (Cached)

Loaded by `AnimaCompanion.ensure_memory_loaded()` and cached between turns:

```
build_runtime_memory_blocks(db, user_id, thread_id)
  -> build_soul_biography_block()       # immutable origin
  -> build_persona_block()              # living personality
  -> build_human_core_block()           # user understanding
  -> build_user_directive_memory_block() # user's instructions
  -> build_self_model_memory_blocks()   # 5 sections
  -> build_emotional_context_block()    # emotional read
  -> build_facts_memory_block()         # scored facts (up to 30)
  -> build_preferences_memory_block()   # scored prefs (up to 20)
  -> build_goals_memory_block()         # scored goals (up to 15)
  -> build_tasks_memory_block()         # open tasks (up to 15)
  -> build_relationships_memory_block() # scored relationships (up to 15)
  -> build_current_focus_memory_block() # singleton focus
  -> build_thread_summary_block()       # compaction summary
  -> build_episodes_memory_block()      # last 5 episodes
  -> build_session_memory_block()       # active session notes
```

### 2. Semantic Retrieval (Per-Turn)

Query-dependent, runs every turn:

```
service.py _prepare_turn_context():
  1. hybrid_search(query=user_message)
     -> embed user message
     -> semantic leg (cosine similarity)
     -> keyword leg (Jaccard text match)
     -> RRF merge
  2. adaptive_filter(results)
     -> precision mode if top-N all > 0.7
     -> gap detection for natural cutoff
  3. _build_semantic_block(filtered_results)
     -> "relevant_memories" block in system prompt
  4. Rebuild scored blocks with query_embedding
     -> facts, prefs, goals, relationships re-ranked
```

---

## Memory Blocks: The System Prompt Interface

Memory blocks are the bridge between the storage layer and the LLM. Each block is a `MemoryBlock(label, value, description, read_only)` injected into the system prompt via Jinja2 templates.

### Block Priority Hierarchy

| Priority | Blocks | Truncation |
|----------|--------|-----------|
| 0 (highest) | soul, persona, human, user_directive | Never truncated |
| 1 | self_identity, self_inner_state, self_working_memory, self_growth_log, self_intentions | Never truncated |
| 2 | emotional_context | Rarely truncated |
| 3 | relevant_memories, facts, preferences, goals, relationships | May be trimmed by prompt budget |
| 4 | user_tasks, current_focus, thread_summary, recent_episodes, session_memory | May be trimmed |

### Block Size Limits

Each category block has a character cap to prevent context explosion:
- Facts: 2000 chars
- Preferences: 2000 chars
- Goals: 1500 chars
- Tasks: 1500 chars
- Relationships: 1500 chars
- Session memory: configurable via `agent_session_memory_budget_chars`

### Memory Refresh Between Steps

When a tool modifies memory mid-turn (sets `ToolContext.memory_modified = True`), the runtime calls the `memory_refresher` callback to rebuild blocks. The system message is replaced in-place so the next LLM step sees fresh data.

---

## Embedding & Vector Search

**File**: `embeddings.py`, `vector_store.py`

### Embedding Generation

```
generate_embedding(text) -> list[float] | None
  - Provider routing:
    - ollama: POST /api/embed (native endpoint)
    - openrouter: tries local Ollama first, skips if unavailable
    - vllm: POST /v1/embeddings (OpenAI-compatible)
  - Default models:
    - ollama: nomic-embed-text
    - openrouter: openai/text-embedding-3-small
    - vllm: text-embedding-3-small
  - LRU cache: 2048 entries, 1-hour TTL, keyed by provider:model:text hash
```

### Dual Storage

Embeddings are stored in two places for resilience:
1. **`MemoryItem.embedding_json`** (JSON column) -- portable, survives export/import
2. **`MemoryVector` table** (packed float32 binary) -- fast search via `OrmVecStore`

### Vector Store Architecture

```python
class OrmVecStore(VectorStore):
    """Per-user SQLAlchemy-backed store. Vectors live in anima.db."""
    - upsert(): serialize float32 -> blob, store in MemoryVector
    - search_by_vector(): load all vectors, compute cosine sim in Python
    - search_by_text(): Jaccard word-overlap similarity
    - rebuild(): bulk replace for vault import

class InMemoryVectorStore(VectorStore):
    """Dict-based fallback for tests. No persistence."""
```

The search is currently brute-force (load all vectors, compute similarity in Python). This works because memory counts are typically in the hundreds, not millions.

### Hybrid Search with RRF (F1 Enhanced)

```python
async def hybrid_search(db, *, user_id, query, limit=15,
                        similarity_threshold=0.25):
    1. Embed query -> query_embedding
    2. Semantic leg:
       - search_similar() via vector store
       - Fallback: brute-force over embedding_json if vector store empty
       - Filter by similarity_threshold (0.25)
    3. BM25 Lexical leg (F1):
       - bm25_search() via in-memory BM25Okapi index
       - Per-user index, built lazily, invalidated on mutations
       - Tokenized with stopword removal
    4. RRF merge (Reciprocal Rank Fusion, k=60):
       score[item] = Σ weight / (k + rank + 1)
       - semantic_weight=0.5, keyword_weight=0.5
    5. Resolve item IDs -> MemoryItem objects
    6. Heat visibility floor (F2):
       - Exclude items with heat < 0.01 (NULL heat = visible)
    7. Optional tag filtering (post-filter)
    -> HybridSearchResult(items, query_embedding)
```

### Adaptive Filtering

After hybrid search, `adaptive_filter()` trims results intelligently:

```
1. Cap at max_results (12)
2. Precision mode: if top-3 all score > 0.7, keep only items > 0.7
3. Gap detection: scan for score drop > 0.15 between consecutive items
4. Fallback: return all up to max_results
```

---

## Retrieval Scoring

**File**: `memory_store.py`

When building memory blocks (facts, preferences, goals, relationships), items are ranked by a multi-factor retrieval score:

### Score Formula

```python
score = 0.4 * importance + 0.35 * recency + 0.25 * access

where:
  importance = (item.importance - 1) / 4.0           # normalize 1-5 to 0-1
  recency = exp(-0.693 * age_days / 14.0)             # half-life: 14 days
  access = min(1.0, log1p(reference_count) / log1p(10))  # log-scaled
  + 0.3 boost if referenced within last 3 days
```

### Query-Aware Blending

When a `query_embedding` is available (from hybrid search), the retrieval score is blended with cosine similarity using per-category weights:

```python
CATEGORY_QUERY_WEIGHTS = {
    "fact":         (0.5 retrieval, 0.5 query),
    "preference":   (0.4 retrieval, 0.6 query),  # prefs benefit more from relevance
    "goal":         (0.7 retrieval, 0.3 query),   # goals are more stable
    "relationship": (0.3 retrieval, 0.7 query),   # relationships need context
}

final_score = w_retrieval * retrieval_score + w_query * normalized_similarity
```

### Touch-on-Read

Every time memory items are loaded into blocks, `touch_memory_items()` increments `reference_count` and updates `last_referenced_at`. This creates a natural "spaced repetition" effect where frequently-accessed memories score higher.

---

## Consolidation Pipeline

**File**: `consolidation.py`

Runs as a background task after every conversation turn.

### Stage 1: Regex Extraction (Fast, Deterministic)

Pattern-matched extraction from the user's message:

**Fact extractors:**
- "I am 25 years old" -> `Age: 25`
- "my birthday is March 5" -> `Birthday: March 5`
- "I work as a designer" -> `Works as designer`
- "I work at Google" -> `Works at Google`
- "I live in Berlin" -> `Lives in Berlin`

**Preference extractors:**
- "I like/love/enjoy X" -> `Likes X`
- "I prefer X" -> `Prefers X`
- "I don't like/dislike/hate X" -> `Dislikes X`

**Focus extractors:**
- "I'm focused on X" -> set_current_focus(X)
- "my main priority is X" -> set_current_focus(X)

### Stage 2: LLM Extraction (Rich, Async)

Sends the full turn (user message + assistant response including inner thoughts) to the LLM with `EXTRACTION_PROMPT`:

```
Returns JSON:
{
  "memories": [
    {"content": "...", "category": "fact|preference|goal|relationship", "importance": 1-5}
  ],
  "emotion": {
    "emotion": "frustrated|excited|anxious|calm|...",
    "confidence": 0.0-1.0,
    "trajectory": "escalating|de-escalating|stable|shifted",
    "evidence_type": "explicit|linguistic|behavioral|contextual",
    "evidence": "what indicated this"
  }
}
```

The agent's inner thoughts (from `inner_thought` tool calls) are included in the consolidation input, so the LLM can extract observations the agent made about the user during reasoning.

### Stage 3: Dedup & Store

For each LLM-extracted item:
1. Skip if already extracted by regex (content match)
2. `store_memory_item()` with `allow_update=True, defer_on_similar=True`
3. On "similar" result: ask LLM via `resolve_conflict()` -> "UPDATE" or "DIFFERENT"
4. Dual-write: `upsert_claim()` for structured claim storage

### Stage 4: Emotional Signal Recording

If the LLM detected an emotion with confidence > threshold:
```python
record_emotional_signal(db, user_id, emotion, confidence, evidence_type, evidence, trajectory)
```

### Stage 5: Embedding Backfill

After consolidation, `_backfill_user_embeddings()` finds items without embeddings and generates them in batches.

### Stage 6: Cache Invalidation

```python
companion.invalidate_memory()  # bumps version counter
# Next turn reloads fresh memory blocks from DB
```

---

## Conflict Resolution & Deduplication

The system has three layers of dedup:

### Layer 1: Text-Based Classification (`memory_store.py`)

```python
def _classify_memory_relation(existing, new, category):
    - casefold exact match -> "duplicate"
    - Facts: same slot (age, birthday, occupation, etc.), different value -> "update"
    - Preferences: same subject, different polarity -> "update"
    - Focus: always "update" (singleton by design)
    - Otherwise -> "different"
```

**Fact slot detection:**
```
"Age: 25" and "Age: 30" -> same slot "age" -> "update"
"Works at Google" and "Works at Meta" -> same slot "employer" -> "update"
```

**Preference signal detection:**
```
"Likes Python" and "Likes Python" -> same subject, same polarity -> "duplicate"
"Likes Python" and "Dislikes Python" -> same subject, different polarity -> "update"
```

### Layer 2: Jaccard Similarity (`memory_store.py`)

```python
def _similarity(a, b) -> float:
    # Word-overlap (Jaccard) after tokenization and stopword removal
    # Threshold: 0.4 for "similar" detection in analyze_memory_item
    # Threshold: 0.3-0.95 for contradiction scanning in sleep tasks
```

### Layer 3: LLM Conflict Resolution (`consolidation.py`)

When text similarity is moderate (similar but not duplicate), the LLM is asked:

```
CONFLICT_CHECK_PROMPT:
  "Given EXISTING and NEW memory, is the new one UPDATE or DIFFERENT?"
  -> "UPDATE" -> supersede old with new
  -> "DIFFERENT" -> keep both
```

---

## Structured Claims

**File**: `claims.py`

Claims are a parallel structured storage layer that mirrors freeform `MemoryItem` writes with deterministic deduplication via `canonical_key`.

### Canonical Key Format

```
user:{namespace}:{slot}

Examples:
  user:fact:age          -> "Age: 25"
  user:fact:occupation   -> "Works as designer"
  user:preference:likes  -> "Likes Python"
  user:preference:dislikes -> "Dislikes Java"
```

For items without a recognized slot pattern, a content-based slug is used:
```
user:fact:works_as_software_engineer_at_google
```

### Supersession Chain

```python
def upsert_claim(db, *, user_id, content, category, ...):
    1. Derive canonical_key from content
    2. Find existing active claim on this key
    3. If same value: just add evidence (new source)
    4. If different value: mark old "superseded", create new, link via superseded_by_id
```

### Evidence Tracking

Each claim accumulates evidence from multiple sources:
```python
MemoryClaimEvidence(
    claim_id=claim.id,
    source_text=user_message,  # the conversation that produced this claim
    source_kind="extraction",  # extraction, user_tool, or sleep_task
)
```

---

## Session Memory (Working Notes)

**File**: `session_memory.py`

Ephemeral notes scoped to the current conversation thread. Distinct from long-term memory -- these capture in-session context.

### Operations

| Tool | Function | Effect |
|------|----------|--------|
| `note_to_self(key, value, type)` | `write_session_note()` | Create/update by key |
| `remove_note(key)` | `remove_session_note()` | Deactivate by key |
| `promote_note(key, category)` | `promote_session_note()` | Convert to long-term MemoryItem |

### Note Types

- `observation` -- "user seems tired today"
- `plan` -- "help user plan weekend trip"
- `context` -- "working on React app with TypeScript"
- `emotion` -- "stressed about work deadline"

### Constraints

- Max active notes per thread: `agent_session_memory_max_notes`
- When limit reached: oldest note deactivated automatically
- Character budget for system prompt: `agent_session_memory_budget_chars`
- Key length: max 128 chars
- Value length: max 2000 chars

### Lifecycle

```
Session start -> notes empty
During conversation -> agent writes notes via note_to_self tool
Thread reset -> all notes deactivated
Promotion -> note becomes MemoryItem, note deactivated
```

---

## Episodic Memory

**File**: `episodes.py`

Episodic memory captures summarized "experiences" -- compressed records of what happened in a conversation session.

### Generation Trigger

Generated when there are enough un-episoded turns for the day:
- Minimum turns: `EPISODE_MIN_TURNS = 3`
- Max turns per episode: `EPISODE_MIN_TURNS * 2 = 6`
- Tracks consumed turns via `SUM(turn_count)` to avoid overlap

### LLM-Generated Episode

```python
EPISODE_GENERATION_PROMPT -> JSON:
{
    "summary": "1-2 sentence summary",
    "topics": ["work", "python", "debugging"],  # 1-5 labels
    "emotional_arc": "curious -> satisfied",
    "significance": 3  # 1-5
}
```

Falls back to text-based summary if LLM fails.

### System Prompt Integration

Last 5 episodes appear in the `recent_episodes` memory block:
```
- 2026-03-15: Discussed debugging strategy for Python app (Topics: python, debugging)
- 2026-03-14: Helped plan weekend trip to mountains (Topics: travel, planning)
```

---

## Self-Model (Agent Identity)

**File**: `self_model.py`

The agent's understanding of itself, stored as 5 mutable sections in `self_model_blocks`:

### Sections

| Section | Label | Purpose | Update Pattern |
|---------|-------|---------|---------------|
| `identity` | `self_identity` | Who the agent is to THIS user | Full rewrite via reflection |
| `inner_state` | `self_inner_state` | Current cognitive state, active threads | Updated per-reflection |
| `working_memory` | `self_working_memory` | Cross-session buffer with expiring items | Append with TTL, auto-expire |
| `growth_log` | `self_growth_log` | Changelog of evolution | Append-only |
| `intentions` | `self_intentions` | Active goals and learned behavioral rules | Updated via reflection |

### Additional Identity Blocks

| Section | Label | Purpose |
|---------|-------|---------|
| `soul` | `soul` | Immutable biographical origin (never changes) |
| `persona` | `persona` | Living personality and voice (evolves slowly) |
| `human` | `human` | Agent's understanding of the user (profile + agent-authored) |
| `user_directive` | `user_directive` | User's customization instructions |

### Seeding

On first interaction, sections are seeded with starter content:
```python
_SEED_IDENTITY = """# Who I Am
<!-- certainty: low -->
I'm still getting to know this person..."""
```

### Working Memory Expiry

Items in working_memory have TTL markers (`<!-- expires: YYYY-MM-DD -->`). The `expire_working_memory_items()` function removes expired items during reflection.

---

## Emotional Intelligence

**File**: `emotional_intelligence.py`

Tracks the user's emotional state across turns.

### Emotion Taxonomy

**Primary (8):** frustrated, excited, anxious, calm, stressed, relieved, curious, disappointed

**Secondary (4):** vulnerable, proud, overwhelmed, playful

### Signal Recording

```python
record_emotional_signal(db, *, user_id, emotion, confidence,
                        evidence_type, trajectory):
    - Confidence threshold: agent_emotional_confidence_threshold
    - Auto-detects trajectory by comparing with previous signal
    - Trims signal buffer to keep only recent history
```

### Trajectory Tracking

| Trajectory | Meaning |
|-----------|---------|
| `stable` | Same emotion continuing |
| `escalating` | Emotion intensifying |
| `de-escalating` | Emotion calming |
| `shifted` | Different emotion from previous |

### System Prompt Integration

`synthesize_emotional_context()` builds a text summary of the user's emotional state for the `emotional_context` memory block. This guides the agent's tone without explicitly analyzing emotions to the user.

---

## Sleep Tasks (Background Maintenance)

**Files**: `sleep_agent.py` (F5 orchestrator), `sleep_tasks.py` (task implementations)

Background maintenance is managed by the **Sleep-Time Agent Orchestrator** (F5), which replaces the previous independent fire-and-forget model with coordinated, frequency-gated execution.

### Orchestration Model

```
Every turn:
  bump_turn_counter(user_id)
  |
  if turn_count % 3 == 0:   (SLEEPTIME_FREQUENCY)
  |   run_sleeptime_agents()     <- full orchestrator
  else:
  |   consolidation only         <- per-turn extraction
```

### Parallel Group (always run on orchestrator turns)

| Task | What it does |
|------|-------------|
| Consolidation | Predict-calibrate extraction (F3) |
| Embedding backfill | Embed items without vectors (batch of 50) |
| KG ingestion | Extract entities/relations for knowledge graph (F4) |
| Heat decay | Recompute all heat scores (F2) |
| Episode generation | Create episodic summary if enough turns (F6 batch if ≥8 messages) |

### Sequential Group (heat-gated, skip if max heat < 5.0)

| Task | What it does |
|------|-------------|
| Contradiction scan | Find conflicting items via Jaccard similarity, resolve via LLM (KEEP_FIRST/KEEP_SECOND/MERGE). Guards against double-processing with resolved_ids set. Calls `suppress_memory()` on losers. |
| Profile synthesis | Merge related facts into single statements. Cleanup vector/BM25/derived refs for all merged-away items. |

### Time-Gated (once per 24h)

| Task | What it does |
|------|-------------|
| Deep monologue | Full self-model reflection. `mark_deep_monologue_done()` only called on success (no errors). Failed monologue does not advance the 24h gate. |

### Task Tracking

All tasks are tracked in `background_task_runs` with status lifecycle: pending → running → completed/failed. Each run records `result_json` for restart cursors and `error_message` on failure.

See [F1-F7 Implementation](memory-f1-f7-implementation.md#f5--async-sleep-time-orchestrator) for full orchestrator details.

---

## Reflection & Inner Monologue

**File**: `reflection.py`, `inner_monologue.py`

### Scheduling

After every turn, `schedule_reflection()` sets a 5-minute timer. Each new turn resets the timer. Only when 5 minutes of inactivity pass does reflection actually run.

```python
schedule_reflection():
    - Cancel existing timer for this user
    - Create new asyncio task with 5-min delay
    - On timer fire: check if any activity occurred since scheduling
    - If quiet: run_reflection()
```

### Reflection Pipeline

```
run_reflection():
  1. expire_working_memory_items()   # remove TTL'd items
  2. run_quick_reflection()          # post-conversation inner monologue
     -> updates inner_state section
     -> may record emotional signal
  3. run_sleep_tasks()               # full maintenance suite
     -> contradiction scan
     -> profile synthesis
     -> episode generation
     -> deep monologue (if 24h cooldown passed)
     -> embedding backfill
  4. companion.invalidate_memory()   # bust cache
```

---

## Context Window Integration

### How Memory Enters the LLM

```
System Prompt (Jinja2 template):
  ├── System Rules (behavior guidelines)
  ├── Guardrails (safety constraints)
  ├── Persona (personality template or living persona)
  ├── Dynamic Identity (self-model identity section)
  ├── Tool Summaries
  └── Memory Blocks:
      ├── soul (immutable origin)
      ├── persona (living personality)
      ├── human (user understanding)
      ├── user_directive (user instructions)
      ├── self_identity (relationship identity)
      ├── self_inner_state (cognitive state)
      ├── self_working_memory (cross-session buffer)
      ├── self_growth_log (evolution changelog)
      ├── self_intentions (goals and rules)
      ├── emotional_context (emotional read)
      ├── relevant_memories (semantic search results)
      ├── facts (scored, up to 30)
      ├── preferences (scored, up to 20)
      ├── goals (scored, up to 15)
      ├── user_tasks (open tasks)
      ├── relationships (scored, up to 15)
      ├── current_focus (singleton)
      ├── thread_summary (compaction summary)
      ├── recent_episodes (last 5)
      ├── session_memory (working notes)
      └── memory_pressure_warning (if > 80% context)
```

### Prompt Budget Planning

`plan_prompt_budget()` allocates token budget across blocks, ensuring high-priority blocks are never truncated while lower-priority blocks may be trimmed if context is tight.

---

## Encryption & Portability

All memory content is encrypted at the field level using domain-specific DEKs (Data Encryption Keys):

```python
ef(user_id, content, table="memory_items", field="content")  # encrypt
df(user_id, content, table="memory_items", field="content")  # decrypt
```

- **Encrypted fields**: `MemoryItem.content`, `EmotionalSignal.evidence`, `SessionNote.value`, `MemoryEpisode.summary`, `SelfModelBlock.content`, etc.
- **Vector content**: stored in plaintext in `MemoryVector.content` for search (not the actual memory content, just the searchable representation)
- **Embeddings**: stored as float32 blobs -- numerical, not sensitive

Portability guarantee: copy `.anima/` directory, enter passphrase on new machine, all memory decrypts and the AI wakes up intact.

---

## File Reference

| File | Role |
|------|------|
| `memory_blocks.py` | Builds 15+ MemoryBlock objects for system prompt (incl. KG context) |
| `memory_store.py` | Core CRUD, scored retrieval, dedup, supersession, heat visibility floor |
| `consolidation.py` | Post-turn extraction (predict-calibrate F3 + emotional signals), orchestrator routing (F5) |
| `embeddings.py` | Embedding generation, hybrid search (BM25 F1 + semantic), heat floor, adaptive filter |
| `vector_store.py` | ORM-backed vector storage and cosine similarity search |
| `bm25_index.py` | **F1** — BM25Okapi lexical search index (per-user, in-memory) |
| `heat_scoring.py` | **F2** — Heat formula, exponential decay, visibility floor |
| `predict_calibrate.py` | **F3** — Two-stage extraction with quality gate |
| `knowledge_graph.py` | **F4** — Entity-relation extraction, dedup, traversal, pruning |
| `sleep_agent.py` | **F5** — Async orchestrator, task wrappers, tracking, inner reasoning stripping |
| `batch_segmenter.py` | **F6** — LLM-driven topic-coherent episode segmentation |
| `forgetting.py` | **F7** — Passive decay, active suppression, user-initiated delete, chain cleanup |
| `claims.py` | Structured slot-based claims with deterministic dedup |
| `session_memory.py` | Thread-scoped working notes |
| `episodes.py` | Episodic memory generation (LLM-summarized sessions, batch segmentation F6) |
| `self_model.py` | Agent identity sections (5 mutable + 4 fixed) |
| `emotional_intelligence.py` | Emotion detection, signal buffer, trajectory tracking |
| `sleep_tasks.py` | Task implementations (contradictions, synthesis, monologue gating) |
| `reflection.py` | Delayed reflection scheduling, working memory expiry |
| `inner_monologue.py` | Quick reflection + deep self-model reflection |
| `conversation_search.py` | Full-text + semantic search across conversation history |
| `compaction.py` | Context window compaction (text-based + LLM-powered) |
| `feedback_signals.py` | Re-ask/correction detection from user turns |
| `api/routes/forgetting.py` | **F7** — REST API for forget operations |
