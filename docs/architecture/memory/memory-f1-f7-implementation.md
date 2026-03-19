---
title: "F1-F7 Memory System Implementation"
description: "Implementation details for BM25 hybrid search, heat scoring, predict-calibrate, knowledge graph, async sleep-time orchestrator, batch segmentation, and intentional forgetting"
category: architecture
date: 2026-03-19
---

# F1-F7 Memory System Implementation

[Back to Memory System](memory-system.md) | [Back to Index](../README.md)

Seven subsystems that upgrade AnimaOS's memory from simple storage to an adaptive, self-maintaining cognitive architecture. Each feature addresses a gap identified in competitor analysis (Letta, mem0, MemoryOS, Nemori).

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [F1 — BM25 Hybrid Search](#f1--bm25-hybrid-search)
3. [F2 — Heat-Based Memory Scoring](#f2--heat-based-memory-scoring)
4. [F3 — Predict-Calibrate Consolidation](#f3--predict-calibrate-consolidation)
5. [F4 — SQLite Knowledge Graph](#f4--sqlite-knowledge-graph)
6. [F5 — Async Sleep-Time Orchestrator](#f5--async-sleep-time-orchestrator)
7. [F6 — Batch Episode Segmentation](#f6--batch-episode-segmentation)
8. [F7 — Intentional Forgetting](#f7--intentional-forgetting)
9. [Cross-Cutting Concerns](#cross-cutting-concerns)
10. [Database Migrations](#database-migrations)
11. [File Reference](#file-reference)
12. [Test Coverage](#test-coverage)

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
    +------------------+                  +--------v----------+
    | Hybrid Search    |                  | Sleep-Time Agent  |
    | (F1: BM25 + RRF) |                 | Orchestrator (F5) |
    | - embed query    |                  |                   |
    | - semantic leg   |                  | Per-turn:         |
    | - BM25 lexical   |                  | - consolidation   |
    | - RRF merge      |                  |                   |
    | - heat floor (F2)|                  | Every Nth turn:   |
    +--------+---------+                  | +-- parallel --+  |
             |                            | | consolidate  |  |
             v                            | | embed backfill|  |
    +------------------+                  | | KG ingest(F4)|  |
    | Memory Blocks    |                  | | heat decay   |  |
    | + KG context (F4)|                  | | episode gen  |  |
    | -> system prompt |                  | +-- sequential-+  |
    +------------------+                  | | contradict   |  |
                                          | | profile synth|  |
                                          | +-- time-gated-+  |
                                          | | deep monologue|  |
                                          +---------+---------+
                                                    |
                                          +--------v----------+
                                          | Forgetting (F7)   |
                                          | - passive decay   |
                                          | - active suppress |
                                          | - user-initiated  |
                                          +-------------------+
```

### How the pieces connect

| Feature | Writes to | Reads from | Triggered by |
|---------|-----------|------------|-------------|
| F1 BM25 | BM25 in-memory index | `memory_items` | `hybrid_search()` call |
| F2 Heat | `memory_items.heat` | access patterns, time | Every retrieval, sleep decay |
| F3 Predict-Calibrate | `memory_items`, `memory_claims` | User + assistant text | Every turn (consolidation) |
| F4 Knowledge Graph | `kg_entities`, `kg_relations` | User + assistant text | Every Nth turn (orchestrator) |
| F5 Orchestrator | `background_task_runs` | Turn counter, heat threshold | Every turn (gated) |
| F6 Batch Segmentation | `memory_episodes` | `memory_daily_logs` | Episode generation |
| F7 Forgetting | Deletes from all tables | Supersession chains | User request, heat decay, contradictions |

---

## F1 — BM25 Hybrid Search

**File:** `services/agent/bm25_index.py`
**PRD:** `docs/prds/memory/F1-hybrid-search.md`

### Problem

Semantic-only search misses lexical matches. A query for "Python" might not find "I work with Python daily" if the embeddings don't overlap strongly enough.

### Solution

Add a BM25Okapi lexical search leg alongside semantic search, fused via Reciprocal Rank Fusion (RRF).

### Architecture

```
hybrid_search(query)
  |
  +-- Semantic Leg (cosine similarity)
  |   - embed query via generate_embedding()
  |   - search_similar() from vector store
  |   - fallback: brute-force over embedding_json
  |
  +-- BM25 Lexical Leg (NEW)
  |   - bm25_search(user_id, query, limit, db)
  |   - In-memory BM25Okapi index per user
  |   - Built lazily, invalidated on mutations
  |
  +-- RRF Fusion
      - score[item] = Σ weight / (k + rank + 1)
      - k=60, semantic_weight=0.5, keyword_weight=0.5
      - Heat visibility floor applied to results
```

### BM25 Index Lifecycle

```python
# Build (lazy, on first search or after invalidation)
def _build_index(user_id: int, db: Session) -> BM25Okapi:
    items = get all active MemoryItems for user
    tokenize each item's decrypted content
    return BM25Okapi(corpus)

# Search
def bm25_search(user_id, query, limit, db) -> list[tuple[int, float]]:
    index = _get_or_build(user_id, db)
    scores = index.get_scores(tokenize(query))
    return top-k (item_id, score) pairs

# Invalidate (called on add/delete/supersede)
def invalidate_index(user_id: int) -> None:
    remove cached index for user
```

### Tokenization

```python
def _tokenize(text: str) -> list[str]:
    lowercase, split on non-alphanumeric
    remove stopwords (the, is, a, an, etc.)
    filter tokens < 2 chars
```

### Integration Points

- `hybrid_search()` in `embeddings.py` calls `bm25_search()` for the keyword leg
- `invalidate_index()` called from:
  - `store_memory_item()` (new item added)
  - `supersede_memory_item()` (item superseded)
  - `forget_memory()` (item deleted)
  - `_cleanup_superseded_indexes()` (contradiction resolution)

---

## F2 — Heat-Based Memory Scoring

**File:** `services/agent/heat_scoring.py`
**PRD:** `docs/prds/memory/F2-heat-scoring.md`

### Problem

All memories are equally "present" regardless of recency, relevance, or access patterns. There's no natural way for unimportant or stale memories to fade from retrieval.

### Solution

A composite heat score that captures how "alive" a memory is. Hot memories surface easily; cold ones fall below a visibility floor and effectively disappear from retrieval without being deleted.

### Heat Formula

```
H = (α·access_count + β·interaction_depth + δ·importance) × recency + γ·recency

where:
  α = 1.0  (HEAT_ALPHA — access frequency weight)
  β = 1.0  (HEAT_BETA — interaction depth weight)
  γ = 1.0  (HEAT_GAMMA — base recency weight)
  δ = 0.5  (HEAT_DELTA — importance weight)
  recency = exp(-hours_since_access / τ),  τ = 24 hours
```

**Critical invariant:** Every term multiplies by `recency`. When `recency → 0` (no access for days), the entire heat score decays to zero. This makes the visibility floor reachable for all memories.

### Visibility Floor

```python
HEAT_VISIBILITY_FLOOR = 0.01
```

Memories with `heat < 0.01` (and `heat` is not NULL or 0.0) are excluded from:
- `get_memory_items()` in `memory_store.py`
- `hybrid_search()` in `embeddings.py`
- `semantic_search()` in `embeddings.py`

NULL heat (new items not yet scored) is treated as visible.

### Heat Decay

```python
def decay_all_heat(db, *, user_id) -> int:
    """Recompute heat for all active items. Called during sleep-time."""
    for each active MemoryItem:
        new_heat = compute_heat(
            access_count=item.access_count,
            interaction_depth=item.interaction_depth,
            last_accessed_at=item.last_accessed_at,
            importance=item.importance,
        )
        item.heat = new_heat
    return count of items updated
```

### Database

- **Column:** `memory_items.heat` (Float, nullable, default NULL)
- **Migration:** `20260319_0001_add_heat_column.py`

---

## F3 — Predict-Calibrate Consolidation

**File:** `services/agent/predict_calibrate.py`
**PRD:** `docs/prds/memory/F3-predict-calibrate.md`

### Problem

Memory extraction was a single pass (regex OR LLM). No quality gate, no emotion extraction separate from memory extraction, no feedback loop.

### Solution

Two-stage pipeline inspired by the Free Energy Principle: predict (fast regex), then calibrate (LLM refinement with quality filtering).

### Pipeline

```
User message + Assistant response
        |
        v
  [Stage 1: Predict — Regex Extraction]
  Fast, deterministic patterns:
  - "I am N years old" -> fact
  - "I work at X" -> fact
  - "I like/love/prefer X" -> preference
  - "I'm focused on X" -> focus
        |
        v
  [Stage 2: Calibrate — LLM Extraction]
  Rich extraction with quality gate:
  - Extract memories + emotion in single call
  - Quality gate filters:
    - Content < 3 words -> rejected
    - Category not in valid set -> rejected
    - Importance not 1-5 -> rejected
        |
        v
  [Output: (extractions, emotion_data)]
  Returns tuple so emotion data is preserved
  even if all extractions fail quality gate
```

### Quality Gate

```python
_MIN_WORD_COUNT = 3
_VALID_CATEGORIES = {"fact", "preference", "goal", "relationship"}

def _quality_filter(item: dict) -> bool:
    content = item.get("content", "")
    if len(content.split()) < _MIN_WORD_COUNT:
        return False
    if item.get("category") not in _VALID_CATEGORIES:
        return False
    importance = item.get("importance", 0)
    if not (1 <= importance <= 5):
        return False
    return True
```

### Return Type

```python
async def predict_calibrate(...) -> tuple[list[dict], dict | None]:
    # Returns (filtered_extractions, emotion_data)
    # emotion_data is preserved even when extractions are empty
```

### Suppression on Supersession

When regex extraction detects an update (e.g., new age value superseding old), `suppress_memory()` is called on the superseded item to flag derived references for regeneration.

---

## F4 — SQLite Knowledge Graph

**File:** `services/agent/knowledge_graph.py` (787 lines)
**PRD:** `docs/prds/memory/F4-knowledge-graph.md`

### Problem

Flat fact storage can't represent relationships between entities. "Alice works at Google" and "Bob works at Google" are stored as independent facts with no link between Alice, Bob, and Google.

### Solution

Entity-relationship graph stored in SQLite. Entities (people, places, orgs, projects, concepts) and typed relations extracted from conversations via LLM.

### Schema

```sql
-- kg_entities
CREATE TABLE kg_entities (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    name_normalized TEXT NOT NULL,  -- dedup key
    entity_type TEXT NOT NULL,      -- person, place, organization, project, concept
    description TEXT,
    mention_count INTEGER DEFAULT 1,
    first_seen_at TIMESTAMP,
    last_seen_at TIMESTAMP
);

-- kg_relations
CREATE TABLE kg_relations (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    source_id INTEGER REFERENCES kg_entities(id),
    destination_id INTEGER REFERENCES kg_entities(id),
    relation_type TEXT NOT NULL,   -- works_at, sister_of, lives_in, etc.
    confidence FLOAT DEFAULT 1.0,
    first_seen_at TIMESTAMP,
    last_seen_at TIMESTAMP
);
```

### Ingestion Pipeline

```
ingest_conversation_graph(db, user_id, user_message, assistant_response)
  |
  1. extract_entities_and_relations() via LLM
     - Max 5 entities per turn
     - Validates entity types and relation structure
     - Inner reasoning prefix stripped before extraction
  |
  2. upsert_entity() for each entity
     - Dedup via name_normalized
     - Increment mention_count on re-encounter
  |
  3. upsert_relation() for each relation
     - Requires both source and destination entities to exist
     - Updates last_seen_at on re-encounter
  |
  4. prune_relations() against current turn's entities
     - Ask LLM which existing relations are now outdated
     - Delete contradicted relations
```

### Entity Name Normalization

```python
"Dr. Alice Smith" -> "dr._alice_smith"
"New York City"   -> "new_york_city"
```

### Graph Traversal

```python
def get_related_entities(db, user_id, entity_name, max_depth=2):
    """BFS traversal up to depth 2 via SQL JOINs."""
    # Returns entities connected to the given entity
    # Used for building KG context block in system prompt
```

### System Prompt Integration

```python
def build_kg_context_block(db, user_id, query_entities):
    """Build a memory block showing relevant graph context."""
    # Finds entities mentioned in user message
    # Traverses graph to show related entities/relations
    # Injected as 'knowledge_graph' memory block
```

---

## F5 — Async Sleep-Time Orchestrator

**File:** `services/agent/sleep_agent.py`
**PRD:** `docs/prds/memory/F5-async-sleep-agents.md`

### Problem

Background tasks (consolidation, reflection, episodes) ran as independent fire-and-forget tasks with no coordination, no frequency gating, and no tracking.

### Solution

Unified orchestrator with turn-counting, heat-gating, and time-gating. Tasks are tracked in a `background_task_runs` table with status lifecycle.

### Orchestration Model

```
Every turn:
  bump_turn_counter(user_id)
  |
  if turn_count % SLEEPTIME_FREQUENCY == 0:  (every 3rd turn)
  |   run_sleeptime_agents()   <-- full orchestrator
  else:
  |   run_background_memory_consolidation()  <-- per-turn only
```

### Task Groups

```
Parallel Group (always run):
  1. consolidation        — predict-calibrate extraction
  2. embedding_backfill   — embed items without vectors
  3. graph_ingestion      — F4 KG extraction
  4. heat_decay           — F2 recompute all heat scores
  5. episode_gen          — check and generate episodes

Sequential Group (heat-gated, skip if max heat < 5.0):
  6. contradiction_scan   — find and resolve conflicts
  7. profile_synthesis    — merge related facts

Time-Gated (once per 24h, only on success):
  8. deep_monologue       — full self-model reflection
```

### Heat Gating

```python
HEAT_THRESHOLD_CONSOLIDATION = 5.0

def _should_run_expensive(db, user_id) -> bool:
    hottest = get_hottest_items(db, user_id, limit=1)
    return hottest[0].heat >= HEAT_THRESHOLD_CONSOLIDATION
```

When `force=True` (inactivity timer): bypass heat gates.

### Task Tracking

```python
class BackgroundTaskRun(Base):
    user_id: int
    task_type: str       # "consolidation", "heat_decay", etc.
    status: str          # "pending" -> "running" -> "completed" | "failed"
    started_at: datetime
    completed_at: datetime
    result_json: dict    # task-specific output
    error_message: str   # on failure
```

### Restart Cursor

The consolidation task stores a restart cursor in `result_json`:

```python
{
    "thread_id": 42,
    "last_processed_message_id": 1337,
    "messages_processed": 1
}
```

Uses Python-side filtering (not PostgreSQL JSONB) since AnimaOS runs on SQLite:

```python
for candidate in runs:
    rj = candidate.result_json
    if isinstance(rj, dict) and rj.get("thread_id") == thread_id:
        run = candidate
        break
```

### Deep Monologue Gating

```python
_DEEP_MONOLOGUE_INTERVAL_HOURS = 24
_last_deep_monologue: dict[int, datetime] = {}  # in-memory

def _should_run_deep_monologue(user_id) -> bool:
    last = _last_deep_monologue.get(user_id)
    if last and hours_since(last) < 24:
        return False
    return True

def mark_deep_monologue_done(user_id) -> None:
    _last_deep_monologue[user_id] = datetime.now(UTC)
```

`mark_deep_monologue_done` is only called when the monologue completes without errors — a failed monologue does not advance the 24h gate.

### Inner Reasoning Stripping

The consolidation pipeline prepends `[Agent's inner reasoning]` to the assistant response for memory extraction. The KG ingestion path strips this prefix before extraction to prevent spurious entity creation:

```python
def _strip_inner_reasoning(text: str) -> str:
    if "[Agent's response to user]" in text:
        return text after that marker
    if text starts with "[Agent's inner reasoning]":
        return text after first double newline
    return text
```

---

## F6 — Batch Episode Segmentation

**File:** `services/agent/batch_segmenter.py`
**PRD:** `docs/prds/memory/F6-batch-segmentation.md`

### Problem

Episode generation used fixed-size sequential chunking. A conversation about Python (turns 1,3,5) interleaved with cooking (turns 2,4) would produce incoherent episodes mixing both topics.

### Solution

LLM-driven topic-coherent grouping that can produce non-contiguous segments.

### When It Triggers

```python
BATCH_THRESHOLD = 8  # minimum messages for batch segmentation

def should_batch_segment(buffer_size: int) -> bool:
    return buffer_size >= BATCH_THRESHOLD
```

Below threshold: standard sequential episode generation.

### Segmentation Flow

```
1. Format messages for LLM prompt
   [1] User: ... / Assistant: ...
   [2] User: ... / Assistant: ...

2. LLM groups by topic coherence
   Returns: [[1, 3, 5], [2, 4]]  (1-based indices)

3. Validate indices
   - All indices 1..N present exactly once
   - No out-of-range values
   - No duplicates across groups

4. Convert to 0-based indices
   [[0, 2, 4], [1, 3]]

5. Sort each group chronologically
   (already sorted in this example)

6. Generate one episode per group
   - LLM summarizes each topic segment
   - Records segmentation_method='batch_llm'
   - Stores message_indices_json (1-based)
```

### Fallback

On any LLM failure or validation error: single-group fallback `[[1, 2, ..., N]]`.

### Database Changes

- **Column:** `memory_episodes.message_indices_json` (JSON, nullable)
- **Column:** `memory_episodes.segmentation_method` (String, nullable)
- **Migration:** `20260319_0005_add_episode_segmentation.py`

---

## F7 — Intentional Forgetting

**File:** `services/agent/forgetting.py`
**PRD:** `docs/prds/memory/F7-intentional-forgetting.md`

### Problem

Memories could only be superseded, never truly forgotten. Users couldn't request deletion. Stale memories had no natural decay mechanism. Derived references (episodes, self-model) citing forgotten memories would persist.

### Solution

Three forgetting mechanisms with full cleanup of derived artifacts.

### Mechanism 1: Passive Decay

Heat scores decay exponentially over time. When heat drops below `HEAT_VISIBILITY_FLOOR` (0.01), the memory is excluded from all retrieval paths but remains in the database.

```
No access for ~7 days (τ=24h) → heat ≈ 0.0 → invisible
```

### Mechanism 2: Active Suppression

When a memory is superseded (by contradiction resolution, profile synthesis, or consolidation), `suppress_memory()` is called:

```python
def suppress_memory(db, memory_id, superseded_by, user_id):
    1. Get the memory's decrypted content
    2. find_derived_references(db, memory_content, user_id)
       - Search memory_episodes.summary for substring match
       - Search self_model_blocks.content (growth_log, intentions)
    3. redact_derived_references(db, refs, strategy="flag_for_regeneration")
       - Set needs_regeneration=True on matching records
    4. Record in forget_audit_log (trigger="suppression")
```

### Mechanism 3: User-Initiated Forgetting

Hard delete with full chain cleanup:

```python
def forget_memory(db, memory_id, user_id, trigger="user_request"):
    # 1. Walk the full supersession chain
    #    If user forgets C in A→B→C, we must also delete A and B
    #    because ON DELETE SET NULL on superseded_by FK would
    #    resurrect them otherwise
    chain = walk_predecessors(memory_id)  # [C, B, A]

    # 2. Flag derived references for ALL chain items
    for item in chain:
        refs = find_derived_references(item.content)
        redact_derived_references(refs, "flag_for_regeneration")

    # 3. Delete claims + evidence for ALL chain items
    claims = MemoryClaim.where(memory_item_id IN chain_ids)
    for claim: delete evidence, delete claim

    # 4. Hard-delete all items in chain
    for item in chain: db.delete(item)

    # 5. Remove from vector store + invalidate BM25
    for item_id in chain_ids: delete_memory(user_id, item_id)
    invalidate_index(user_id)

    # 6. Audit log (no content stored)
    ForgetAuditLog(
        items_forgotten=len(chain),  # actual count, not hardcoded
        derived_refs_affected=count,
        trigger=trigger,
    )
```

### Topic-Based Forgetting

```python
def forget_by_topic(db, topic, user_id) -> list[MemoryItem]:
    """Find candidates for confirmation — does NOT auto-delete."""
    1. Substring search across all active items (decrypted)
    2. BM25 search for additional lexical matches
    3. Return candidates for user to confirm
```

### Derived Reference Detection

```python
def find_derived_references(db, memory_content, user_id):
    # Decrypts before matching (field-level encryption)
    for episode in memory_episodes:
        summary = df(user_id, episode.summary, ...)
        if content.lower() in summary.lower():
            refs.episodes.append(episode)

    for block in self_model_blocks(section in [growth_log, intentions]):
        block_content = df(user_id, block.content, ...)
        if content.lower() in block_content.lower():
            refs.self_model_blocks.append(block)
```

### API

```
DELETE /api/memories/{user_id}/{memory_id}/forget
  -> forget_memory(db, memory_id, user_id)
  -> Returns { items_forgotten, derived_refs_affected, audit_log_id }

POST /api/memories/{user_id}/forget-by-topic
  Body: { "topic": "python" }
  -> forget_by_topic(db, topic, user_id)
  -> Returns candidates for confirmation (does not delete)

DELETE /api/memories/{user_id}/forget-confirmed
  Body: { "memory_ids": [1, 2, 3] }
  -> forget_memory() for each confirmed ID
```

### Database

```sql
CREATE TABLE forget_audit_log (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    forgotten_at TIMESTAMP NOT NULL,
    trigger TEXT NOT NULL,         -- "user_request", "suppression"
    scope TEXT NOT NULL,           -- "single", "topic"
    items_forgotten INTEGER,
    derived_refs_affected INTEGER
    -- NOTE: no content stored — the audit log must not
    -- retain the information the user asked to forget
);
```

---

## Cross-Cutting Concerns

### Encryption

All content that flows through forgetting and derived reference detection is decrypted before matching:

```python
from anima_server.services.data_crypto import df

# Decrypt before comparison
content = df(user_id, item.content, table="memory_items", field="content")
summary = df(user_id, episode.summary, table="memory_episodes", field="summary")
```

Without this, substring matching would fail on encrypted content.

### Supersession Chain Invariant

The `superseded_by` FK uses `ON DELETE SET NULL`. This means deleting any item in a chain resurrects its predecessors:

```
A.superseded_by = B.id
B.superseded_by = C.id

Delete C → B.superseded_by becomes NULL → B is "active" again
Delete B → A.superseded_by becomes NULL → A is "active" again
```

**Solution:** `forget_memory()` walks the full chain and deletes ALL predecessors. The chain walker uses BFS:

```python
frontier = [memory_id]
while frontier:
    preds = MemoryItem.where(superseded_by IN frontier)
    frontier = [p.id for p in preds]
    chain.extend(preds)
```

### Contradiction Resolution Guards

When scanning contradictions, an item that was already superseded in the current scan must not be processed again:

```python
resolved_ids: set[int] = set()
for item_a, item_b in pairs:
    if item_a.id in resolved_ids or item_b.id in resolved_ids:
        continue  # skip — already resolved
    # ... process contradiction ...
    resolved_ids.add(loser.id)
```

Without this, 3+ conflicting items could produce cyclic or duplicate supersession chains.

### NULL Heat Handling

New items have `heat = NULL` (not yet scored). The visibility floor filter must treat NULL as visible:

```python
or_(
    MemoryItem.heat.is_(None),    # new items → visible
    MemoryItem.heat == 0.0,       # explicitly zero → visible (edge case)
    MemoryItem.heat >= HEAT_VISIBILITY_FLOOR,
)
```

---

## Database Migrations

| Migration | Feature | Changes |
|-----------|---------|---------|
| `20260319_0001` | F2 Heat | Add `heat` FLOAT column to `memory_items` |
| `20260319_0002` | F4 KG | Create `kg_entities`, `kg_relations` tables |
| `20260319_0003` | F7 Forgetting | Create `forget_audit_log` table; add `needs_regeneration` to `memory_episodes` and `self_model_blocks` |
| `20260319_0004` | F5 Orchestrator | Create `background_task_runs` table |
| `20260319_0005` | F6 Segmentation | Add `message_indices_json`, `segmentation_method` to `memory_episodes` |

All migrations use `batch_alter_table` for SQLite compatibility.

---

## File Reference

| File | Feature | Lines | Role |
|------|---------|-------|------|
| `bm25_index.py` | F1 | ~123 | BM25Okapi lexical search index |
| `heat_scoring.py` | F2 | ~190 | Heat formula, decay, hottest items |
| `predict_calibrate.py` | F3 | ~180 | Two-stage extraction with quality gate |
| `knowledge_graph.py` | F4 | ~787 | Entity-relation extraction, dedup, traversal, pruning |
| `sleep_agent.py` | F5 | ~523 | Orchestrator, task wrappers, tracking |
| `batch_segmenter.py` | F6 | ~249 | LLM-driven topic segmentation |
| `forgetting.py` | F7 | ~368 | Passive decay, active suppression, user-initiated delete |
| `consolidation.py` | F3/F5 | ~(+124) | Per-turn vs orchestrator routing, suppress on supersession |
| `embeddings.py` | F1/F2 | ~(+26) | BM25 leg in hybrid_search, heat floor in semantic_search |
| `memory_store.py` | F2 | ~(+3) | NULL-safe heat visibility floor |
| `memory_blocks.py` | F4 | ~(+41) | KG context block for system prompt |
| `sleep_tasks.py` | F5/F7 | ~(+74) | Contradiction guards, MERGE suppression, monologue gating |
| `api/routes/forgetting.py` | F7 | ~76 | REST API for forget operations |

---

## Test Coverage

748 tests total (all passing). Key test files for F1-F7:

| Test File | Tests | Coverage |
|-----------|-------|----------|
| `test_heat_scoring.py` | 9 | Heat formula, decay, edge cases (NULL, no access) |
| `test_predict_calibrate.py` | 6 | Quality gate, emotion extraction, return type |
| `test_sleep_agent.py` | 8 | Orchestrator, task count, frequency gating |
| `test_agent_consolidation.py` | 12 | Per-turn vs full orchestrator, suppress calls |
| `test_hybrid_retrieval.py` | 11 | BM25 + semantic fusion, adaptive filter |
| `test_knowledge_graph.py` | 13 | Entity dedup, relation upsert, traversal |
| `test_batch_segmenter.py` | 8 | Index validation, chronological sort, fallback |
| `test_forgetting.py` | 14 | Chain deletion, derived refs, audit log, topic search |
| `test_consciousness.py` | 49 | Self-model, emotions, needs_regeneration |

### Bug Review History

7 review rounds (alternating Claude and Codex) found and fixed ~35 bugs before shipping. Key categories:

- **Supersession chain management** (most persistent): ON DELETE SET NULL behavior required walking and deleting all predecessors
- **Heat formula evolution** (3 iterations): Ensuring all terms decay to zero required multiplying everything by recency
- **Derived reference cleanup**: Every path that supersedes or deletes a memory must flag episodes and self-model blocks
- **Cross-module consistency**: Heat floor applied in all retrieval paths, not just one
