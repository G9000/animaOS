# AnimaOS Memory Architecture Implementation Plan

**Date**: 2026-03-18
**Author**: Memory Implementation Architect
**Base**: repo-analysis-2026-03-18.md
**Current test suite**: 602 tests, all passing
**Existing Alembic migrations**: 15

---

## Dependency Graph

```
                    Phase 1: Hybrid Search (BM25 + Vector + RRF)
                        |
              +---------+---------+
              |                   |
    Phase 2: Heat Scoring    Phase 4: Knowledge Graph
              |                   |
              v                   |
    Phase 3: Predict-Calibrate    |
              |                   |
              v                   |
    Phase 5: Async Sleep Agents <-+
              |
              v
    Phase 6: Batch Episode Segmentation
```

**Key dependency rules**:
- Phase 1 is foundational: improved search benefits all subsequent phases
- Phase 2 (heat) has no hard dependencies but benefits from Phase 1 for heat-triggered retrieval
- Phase 3 (predict-calibrate) requires Phase 1 for relevant-statement retrieval
- Phase 4 (knowledge graph) is independent but integrates with Phase 1 for graph+vector hybrid
- Phase 5 (async agents) orchestrates Phases 2-4 as background tasks
- Phase 6 (batch segmentation) is independent but integrates with Phase 3 and Phase 5

---

## Recommended Execution Order

| Order | Phase | Rationale |
|-------|-------|-----------|
| 1st   | Phase 1: Hybrid Search | Foundation -- every other phase retrieves memories; better retrieval = better everything. AnimaOS already has RRF scaffolding in `embeddings.py` but uses naive Jaccard for the keyword leg. Adding real BM25 is the single highest-impact change. |
| 2nd   | Phase 2: Heat Scoring | Low complexity, high value. Adds `heat` column to `MemoryItem`, replaces the current fixed-weight `_retrieval_score()`. Once heat exists, it gates all expensive operations. |
| 3rd   | Phase 4: Knowledge Graph | Independent of Phases 2-3 and highest novelty. AnimaOS has zero relational structure today. SQLite graph tables are simple to add. |
| 4th   | Phase 3: Predict-Calibrate | Depends on Phase 1 for retrieval. Modifies the consolidation pipeline which is the riskiest integration point. Doing it 4th means the search infrastructure is stable. |
| 5th   | Phase 5: Async Sleep Agents | Orchestration layer. Must come after the things it orchestrates (heat, predict-calibrate, graph extraction). |
| 6th   | Phase 6: Batch Segmentation | Lowest priority. Enhances episodes but the current fixed-size chunking works. Best saved for last. |

---

## New pip Dependencies

| Package | Phase | Purpose | Size |
|---------|-------|---------|------|
| `rank-bm25` | 1 | BM25Okapi implementation | ~15 KB, pure Python |

That is the only new dependency. All other work uses SQLite, existing SQLAlchemy, and LLM calls via the existing provider abstraction. We explicitly avoid `spacy`, `neo4j`, `chromadb`, and `numpy` (using stdlib `math` instead).

**Total new migration count across all phases**: 4

---

## Phase 1: Hybrid Search (BM25 + Vector + RRF)

### Overview

Replace the naive Jaccard-based `search_by_text()` / `_text_similarity()` in the vector store with proper BM25 lexical ranking. AnimaOS already has RRF fusion in `embeddings.py` (`_reciprocal_rank_fusion()`, `hybrid_search()`), but the keyword leg is weak -- it uses word-overlap Jaccard (`_text_similarity()` in `vector_store.py`) rather than BM25's term-frequency / inverse-document-frequency scoring. Swapping in BM25 gives dramatically better lexical recall, especially for proper nouns, technical terms, and exact phrases that embedding similarity misses.

### Source Reference

- `nemori/src/search/unified_search.py` -- parallel BM25 + vector with RRF fusion (k=60)
- `nemori/src/search/bm25_search.py` -- `rank_bm25.BM25Okapi` with per-user indices
- `mem0/mem0/memory/graph_memory.py` lines 119-127 -- BM25 reranking of graph results

### Current State

AnimaOS already has:
- **Vector search**: `vector_store.py` `OrmVecStore.search_by_vector()` (cosine similarity over `MemoryVector` table)
- **Keyword search**: `vector_store.py` `_text_similarity()` (Jaccard word overlap -- primitive)
- **RRF fusion**: `embeddings.py` `_reciprocal_rank_fusion()` and `hybrid_search()` -- already wired into the retrieval pipeline
- **Adaptive filtering**: `embeddings.py` `adaptive_filter()` -- score gap detection

The gap: the keyword leg (`search_by_text()`) uses Jaccard similarity which misses term frequency weighting, IDF, and document length normalization.

### Data Model Changes

**No schema changes required.** BM25 indices are built in-memory from existing `MemoryVector.content` data.

Migration count: **0**

### New Files

```
apps/server/src/anima_server/services/agent/bm25_index.py
```

Key function signatures:

```python
class BM25Index:
    """Per-user BM25 index built lazily from MemoryVector content."""

    def __init__(self) -> None: ...

    def build(self, documents: list[tuple[int, str]]) -> None:
        """Build index from (item_id, content) pairs."""
        ...

    def search(self, query: str, *, limit: int = 20) -> list[tuple[int, float]]:
        """Return (item_id, bm25_score) ranked descending."""
        ...

    def add_document(self, item_id: int, content: str) -> None:
        """Incrementally add a document (rebuilds index)."""
        ...

    def remove_document(self, item_id: int) -> None:
        """Remove a document by ID."""
        ...

    @property
    def document_count(self) -> int: ...


# Module-level per-user cache
_user_indices: dict[int, BM25Index] = {}
_indices_lock: Lock

def get_or_build_index(user_id: int, *, db: Session) -> BM25Index:
    """Lazy-load the BM25 index for a user, building from MemoryVector rows."""
    ...

def invalidate_index(user_id: int) -> None:
    """Clear cached index when content changes (upsert/delete)."""
    ...

def bm25_search(
    user_id: int,
    *,
    query: str,
    limit: int = 20,
    db: Session,
) -> list[tuple[int, float]]:
    """Search using BM25. Returns (item_id, score) pairs."""
    ...
```

### Modified Files

| File | Function | Change |
|------|----------|--------|
| `vector_store.py` | `OrmVecStore.upsert()` | After upsert, call `bm25_index.invalidate_index(user_id)` |
| `vector_store.py` | `OrmVecStore.delete()` | After delete, call `bm25_index.invalidate_index(user_id)` |
| `vector_store.py` | `OrmVecStore.rebuild()` | After rebuild, call `bm25_index.invalidate_index(user_id)` |
| `embeddings.py` | `hybrid_search()` | Replace the keyword leg: instead of calling `search_by_text()` (Jaccard), call `bm25_search()` from `bm25_index.py`. The RRF merge logic stays the same. |

### Integration Points

- **Retrieval pipeline**: `hybrid_search()` in `embeddings.py` is already called by `companion.py` during prompt assembly. No changes needed above this layer.
- **Index lifecycle**: BM25 indices live in process memory (like the current `InMemoryVectorStore`). They rebuild lazily on first search after invalidation. For a single-user system, this is fast (typically < 1000 documents).

### Token Budget Impact

None. This changes ranking quality, not what goes into the prompt.

### Dependencies

None -- this is the foundation phase.

### Test Plan

1. **Unit: BM25Index build/search** -- build index from known documents, verify expected rankings
2. **Unit: BM25Index incremental add/remove** -- add document, verify it appears in results; remove, verify it disappears
3. **Unit: hybrid_search with BM25 leg** -- mock vector results + BM25 results, verify RRF produces blended ranking
4. **Integration: exact-match advantage** -- query for a proper noun (e.g., "PostgreSQL"), verify BM25 ranks it higher than Jaccard would
5. **Regression: existing hybrid_search tests** -- ensure all existing tests still pass when BM25 is used as the keyword backend

### Risk / Complexity Assessment

**Complexity: Low.** The `rank-bm25` library is well-tested and requires ~50 lines of wrapper code. The RRF infrastructure already exists.

**Risk: Index staleness.** The BM25 index must be invalidated on every content change. Mitigation: invalidation hooks in `upsert()`, `delete()`, and `rebuild()`. Worst case, a stale index returns slightly-off keyword results for one turn.

**Risk: Memory usage.** BM25Okapi stores tokenized corpus in memory. For 10,000 memories at ~50 tokens each, this is ~4 MB. Acceptable for a personal AI.

---

## Phase 2: Heat-Based Memory Scoring

### Overview

Replace the fixed-weight retrieval scoring formula (`_retrieval_score()` in `memory_store.py`) with a heat-based model inspired by MemoryOS. Heat combines access frequency, interaction depth, and time-decay into a single score that determines both retrieval priority and consolidation triggers. Hot memories surface first; cold memories are candidates for archival or eviction.

### Source Reference

- `MemoryOS/memoryos-chromadb/mid_term.py` `compute_segment_heat()` -- `H = alpha * N_visit + beta * L_interaction + gamma * R_recency`
- `MemoryOS/memoryos-chromadb/mid_term.py` `MidTermMemory.search_sessions()` -- heat updates on access
- `MemoryOS/memoryos-chromadb/retriever.py` `Retriever._retrieve_mid_term_context()` -- heap-based top-K selection

### Current State

AnimaOS has in `memory_store.py`:
- `_retrieval_score(item, now)` -- combines importance (0.4), recency (0.35), and access frequency (0.25)
- `touch_memory_items(db, items)` -- increments `reference_count` and updates `last_referenced_at`
- `get_memory_items_scored(db, ...)` -- fetches a pool, scores each item, optionally blends with query embedding similarity

The current approach works but has fixed weights and no heat-triggered behavior. Heat scoring adds:
1. A persistent `heat` column so we do not re-compute on every query
2. Configurable weights via constants (tunable without code changes)
3. Heat as a trigger for consolidation (Phase 5 integration)

### Data Model Changes

**Add column to `MemoryItem`:**

```python
heat: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
```

**Add index:**

```python
Index("ix_memory_items_user_heat", "user_id", "heat")
```

Migration count: **1** (`20260319_0001_add_heat_column_to_memory_items.py`)

### New Files

```
apps/server/src/anima_server/services/agent/heat_scoring.py
```

Key function signatures:

```python
# Configurable weights (match MemoryOS defaults)
HEAT_ALPHA: float = 1.0   # access count weight
HEAT_BETA: float = 1.0    # interaction depth weight
HEAT_GAMMA: float = 1.0   # recency weight
RECENCY_TAU_HOURS: float = 24.0  # time-decay half-life

def compute_heat(
    *,
    access_count: int,
    interaction_depth: int,
    last_accessed_at: datetime | None,
    now: datetime | None = None,
) -> float:
    """Compute heat score: H = alpha * access + beta * depth + gamma * recency_decay."""
    ...

def compute_time_decay(
    last_accessed: datetime,
    now: datetime,
    *,
    tau_hours: float = RECENCY_TAU_HOURS,
) -> float:
    """Exponential time decay: exp(-hours_since / tau)."""
    ...

def update_heat_on_access(
    db: Session,
    items: list[MemoryItem],
    *,
    now: datetime | None = None,
) -> None:
    """Increment access_count, update last_referenced_at, recompute heat."""
    ...

def decay_all_heat(
    db: Session,
    *,
    user_id: int,
    now: datetime | None = None,
) -> int:
    """Batch-update heat for all active items (called during sleep tasks). Returns count updated."""
    ...

def get_hottest_items(
    db: Session,
    *,
    user_id: int,
    limit: int = 20,
    category: str | None = None,
) -> list[MemoryItem]:
    """Return items sorted by heat descending."""
    ...

def get_coldest_items(
    db: Session,
    *,
    user_id: int,
    limit: int = 20,
    heat_threshold: float = 0.1,
) -> list[MemoryItem]:
    """Return items below heat threshold (candidates for archival)."""
    ...
```

### Modified Files

| File | Function | Change |
|------|----------|--------|
| `memory_store.py` | `_retrieval_score()` | Replace body with call to `compute_heat()` for the base score, then blend with query embedding similarity as before. |
| `memory_store.py` | `touch_memory_items()` | After updating `reference_count` and `last_referenced_at`, call `update_heat_on_access()` to recompute heat. |
| `memory_store.py` | `get_memory_items_scored()` | Use `ORDER BY heat DESC` from the database instead of fetching a large pool and sorting in Python. |
| `sleep_tasks.py` | `run_sleep_tasks()` | Add step 0: `decay_all_heat(db, user_id=user_id)` to refresh heat scores before other operations. |
| `models/agent_runtime.py` | `MemoryItem` | Add `heat` column and index. |

### Integration Points

- **Retrieval**: `get_memory_items_scored()` uses heat as the primary sort. Query-embedding blending is a secondary adjustment.
- **Prompt assembly**: `build_facts_memory_block()` etc. in `memory_blocks.py` call `get_memory_items_scored()` -- no changes needed.
- **Sleep tasks**: Heat decay runs during `run_sleep_tasks()` so items cool over time.
- **Phase 5**: Heat threshold will gate whether consolidation agents fire (e.g., only consolidate when accumulated heat > threshold).

### Token Budget Impact

None. Heat changes ranking, not content volume.

### Dependencies

None (standalone), but Phase 5 will use heat thresholds.

### Test Plan

1. **Unit: `compute_heat()`** -- verify formula with known inputs
2. **Unit: `compute_time_decay()`** -- verify exponential decay at known time intervals
3. **Unit: `update_heat_on_access()`** -- access an item 5 times, verify heat increases monotonically
4. **Unit: `decay_all_heat()`** -- set items with known last_accessed, run decay, verify heat decreases
5. **Unit: `get_hottest_items()` / `get_coldest_items()`** -- create items with varied heat, verify ordering
6. **Integration: prompt relevance** -- high-heat items should appear first in memory blocks
7. **Regression: `_retrieval_score` callers** -- verify `get_memory_items_scored()` still returns sensible results

### Risk / Complexity Assessment

**Complexity: Low-Medium.** One new column, one new service file, straightforward formula.

**Risk: Heat staleness.** If decay only runs during sleep tasks and the user has not been idle, heat may not reflect true recency. Mitigation: `touch_memory_items()` recomputes heat on every access, so active items stay hot. Decay only affects untouched items.

**Risk: Migration on existing data.** All existing items get `heat=0.0`. Mitigation: the first sleep-tasks run calls `decay_all_heat()` which recomputes heat for all items based on existing `reference_count` and `last_referenced_at`.

---

## Phase 3: Predict-Calibrate Consolidation

### Overview

Augment the existing LLM memory extraction pipeline (`consolidation.py` `consolidate_turn_memory_with_llm()`) with a predict-then-extract cycle inspired by Nemori's Free Energy Principle approach. Instead of extracting facts from a conversation cold, first predict what facts the conversation likely contains (based on existing knowledge), then extract only the delta -- the surprises, corrections, and genuinely new information. This reduces redundant extraction and focuses LLM effort on novel knowledge.

### Source Reference

- `nemori/src/generation/prediction_correction_engine.py` -- `learn_from_episode_simplified()`, `_predict_episode()`, `_extract_knowledge_from_comparison()`
- Cold start mode: `_cold_start_extraction()` -- direct extraction when no prior knowledge exists

### Current State

AnimaOS's consolidation in `consolidation.py`:
1. `consolidate_turn_memory()` -- regex extraction (patterns for age, birthday, occupation, etc.)
2. `consolidate_turn_memory_with_llm()` -- calls `extract_memories_via_llm()` which prompts the LLM with EXTRACTION_PROMPT, then deduplicates against regex results
3. `resolve_conflict()` -- LLM-based UPDATE/DIFFERENT classification for similar items
4. All extracted items go through `store_memory_item()` which has built-in duplicate/update/similar detection

The gap: the LLM extraction prompt has no awareness of what AnimaOS already knows. It extracts everything from scratch, producing many duplicates that `store_memory_item()` then filters. This wastes LLM tokens and misses opportunities to detect contradictions.

### Data Model Changes

None. The predict-calibrate logic is a change in the consolidation pipeline, not the data model.

Migration count: **0**

### New Files

```
apps/server/src/anima_server/services/agent/predict_calibrate.py
```

Key function signatures:

```python
PREDICTION_PROMPT: str = """..."""  # Given these existing facts + conversation topic, predict what new facts this conversation contains
DELTA_EXTRACTION_PROMPT: str = """..."""  # Given the prediction vs actual conversation, extract ONLY surprising/new/contradictory facts
KNOWLEDGE_QUALITY_PROMPT: str = """..."""  # Apply persistence, specificity, utility, independence tests


async def predict_episode_knowledge(
    *,
    existing_facts: list[str],
    conversation_summary: str,
) -> str:
    """Predict what knowledge a conversation likely contains, given existing facts."""
    ...

async def extract_knowledge_delta(
    *,
    user_message: str,
    assistant_response: str,
    prediction: str,
) -> list[dict[str, Any]]:
    """Extract only the delta between prediction and actual conversation."""
    ...

async def apply_quality_gates(
    *,
    statements: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Filter statements through persistence, specificity, utility, independence tests."""
    ...

async def predict_calibrate_extraction(
    *,
    user_id: int,
    user_message: str,
    assistant_response: str,
    db: Session,
) -> list[dict[str, Any]]:
    """Full predict-calibrate pipeline:
    1. Retrieve relevant existing facts (via hybrid_search)
    2. Predict expected knowledge
    3. Extract delta
    4. Apply quality gates
    Returns list of high-quality memory items to store.
    """
    ...
```

### Modified Files

| File | Function | Change |
|------|----------|--------|
| `consolidation.py` | `consolidate_turn_memory_with_llm()` | Replace `extract_memories_via_llm()` call with `predict_calibrate_extraction()` when existing facts > 0. Keep `extract_memories_via_llm()` as the cold-start path (matching Nemori's pattern). |
| `consolidation.py` | `EXTRACTION_PROMPT` | Keep as-is for cold-start mode. |

### Integration Points

- **Retrieval**: `predict_calibrate_extraction()` calls `hybrid_search()` (Phase 1) to find relevant existing facts for the prediction step.
- **Storage**: Output feeds into the same `store_memory_item()` pipeline, so dedup/conflict resolution still applies as a safety net.
- **Cold start**: When `get_memory_items(db, user_id=user_id)` returns < 5 items, falls back to direct extraction (current behavior).

### Token Budget Impact

**Net reduction.** Instead of one large extraction prompt, we make two smaller calls (predict + delta-extract). But the delta-extract call is more focused and produces fewer, higher-quality items, reducing the downstream storage and dedup overhead. In steady state, this should use fewer total tokens per conversation turn.

### Dependencies

- **Phase 1 (Hybrid Search)**: `predict_calibrate_extraction()` uses `hybrid_search()` to find relevant existing facts. Without Phase 1, it could fall back to `get_memory_items_scored()` but with worse relevance.

### Test Plan

1. **Unit: `predict_episode_knowledge()`** -- given known facts and a topic, verify the prediction is reasonable (mock LLM)
2. **Unit: `extract_knowledge_delta()`** -- given a prediction and actual conversation with surprises, verify delta contains only the new information
3. **Unit: `apply_quality_gates()`** -- feed in low-quality statements (vague, temporal), verify they are filtered out
4. **Unit: cold start path** -- when no existing facts, verify fallback to direct extraction
5. **Integration: full pipeline** -- run `consolidate_turn_memory_with_llm()` with predict-calibrate, verify extracted facts are novel
6. **Regression: existing consolidation tests** -- ensure all 602 tests still pass

### Risk / Complexity Assessment

**Complexity: Medium.** Two new LLM prompts, careful pipeline integration.

**Risk: LLM prompt sensitivity.** The prediction prompt must be calibrated to produce useful predictions without hallucinating. Too creative = bad predictions = bad deltas. Mitigation: low temperature (0.3), and the downstream `store_memory_item()` dedup still catches errors.

**Risk: Latency.** Two sequential LLM calls instead of one. Mitigation: this runs in the background (`run_background_memory_consolidation()`), so latency is invisible to the user. The predict call can also be skipped for short conversations (< 3 exchanges).

**Risk: Integration with existing consolidation.** The `consolidation.py` file has careful error handling and fallback paths. The predict-calibrate call must be wrapped in the same try/except patterns.

---

## Phase 4: Knowledge Graph (SQLite-backed)

### Overview

Add a lightweight knowledge graph layer on top of existing semantic memory. Entities and relations are extracted from conversations and stored in two new SQLite tables (`kg_entities`, `kg_relations`). This gives AnimaOS relational structure between its flat facts -- e.g., "User -> works_at -> Google" or "Alice -> sister_of -> User" -- enabling graph traversal for context that embedding similarity alone would miss.

### Source Reference

- `mem0/mem0/memory/graph_memory.py` -- `MemoryGraph.add()`, `_retrieve_nodes_from_data()` (entity extraction via tool calling), `_establish_nodes_relations_from_data()` (relation extraction), `search()` (graph traversal + BM25 reranking)
- Entity deduplication: `_search_source_node()` / `_search_destination_node()` using embedding similarity threshold

### Current State

AnimaOS has:
- Flat semantic facts in `MemoryItem` (category: fact, preference, goal, relationship)
- No relational structure between entities
- Entity names appear in fact content but are not normalized or linked
- `MemoryItemTag` provides rudimentary grouping but not entity-relation modeling

### Data Model Changes

**New table: `kg_entities`**

```python
class KGEntity(Base):
    __tablename__ = "kg_entities"
    __table_args__ = (
        UniqueConstraint("user_id", "name_normalized", name="uq_kg_entities_user_name"),
        Index("ix_kg_entities_user_type", "user_id", "entity_type"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)           # display name
    name_normalized: Mapped[str] = mapped_column(String(200), nullable=False) # lowered, underscore-joined
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False, default="unknown")  # person, place, org, concept, etc.
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    mentions: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    embedding_json: Mapped[list[float] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
```

**New table: `kg_relations`**

```python
class KGRelation(Base):
    __tablename__ = "kg_relations"
    __table_args__ = (
        Index("ix_kg_relations_source", "source_id"),
        Index("ix_kg_relations_dest", "destination_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    source_id: Mapped[int] = mapped_column(ForeignKey("kg_entities.id", ondelete="CASCADE"), nullable=False)
    destination_id: Mapped[int] = mapped_column(ForeignKey("kg_entities.id", ondelete="CASCADE"), nullable=False)
    relation_type: Mapped[str] = mapped_column(String(100), nullable=False)  # works_at, lives_in, knows, etc.
    mentions: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    source_memory_id: Mapped[int | None] = mapped_column(ForeignKey("memory_items.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
```

Migration count: **1** (`20260320_0001_create_knowledge_graph_tables.py`)

### New Files

```
apps/server/src/anima_server/services/agent/knowledge_graph.py
apps/server/src/anima_server/api/routes/knowledge_graph.py  (optional REST API)
```

Key function signatures for `knowledge_graph.py`:

```python
EXTRACT_ENTITIES_PROMPT: str = """..."""
EXTRACT_RELATIONS_PROMPT: str = """..."""

async def extract_entities_and_relations(
    *,
    text: str,
    user_id: int,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Extract entities and relations from text using LLM tool calling.
    Returns (entities, relations) where each entity is {name, type, description}
    and each relation is {source, relation, destination}.
    """
    ...

def upsert_entity(
    db: Session,
    *,
    user_id: int,
    name: str,
    entity_type: str,
    description: str = "",
) -> KGEntity:
    """Create or update an entity, incrementing mentions on match.
    Uses normalized name for dedup.
    """
    ...

def upsert_relation(
    db: Session,
    *,
    user_id: int,
    source_name: str,
    destination_name: str,
    relation_type: str,
    source_memory_id: int | None = None,
) -> KGRelation:
    """Create or update a relation between two entities."""
    ...

async def deduplicate_entity(
    db: Session,
    *,
    user_id: int,
    new_entity_name: str,
    similarity_threshold: float = 0.85,
) -> KGEntity | None:
    """Check if a new entity matches an existing one via embedding similarity.
    Returns the existing entity if a match is found.
    """
    ...

def search_graph(
    db: Session,
    *,
    user_id: int,
    entity_names: list[str],
    max_depth: int = 2,
    limit: int = 20,
) -> list[dict[str, str]]:
    """Traverse the graph from given entities, returning related triples.
    Returns [{"source": ..., "relation": ..., "destination": ...}, ...]
    """
    ...

def graph_context_for_query(
    db: Session,
    *,
    user_id: int,
    query: str,
    limit: int = 10,
) -> list[str]:
    """Extract entities from query, traverse graph, return context strings.
    Suitable for inclusion in a memory block.
    """
    ...

async def ingest_conversation_graph(
    db: Session,
    *,
    user_id: int,
    user_message: str,
    assistant_response: str,
) -> tuple[int, int]:
    """Full pipeline: extract entities+relations from conversation, upsert into graph.
    Returns (entities_added, relations_added).
    """
    ...
```

### Modified Files

| File | Function | Change |
|------|----------|--------|
| `consolidation.py` | `run_background_memory_consolidation()` | After memory consolidation, call `ingest_conversation_graph()` to populate the knowledge graph. |
| `memory_blocks.py` | `build_runtime_memory_blocks()` | Add a new `knowledge_graph` memory block between `relationships` and `current_focus` blocks. Call `graph_context_for_query()` to get graph triples relevant to the current query. |
| `models/agent_runtime.py` | (module level) | Add `KGEntity` and `KGRelation` model classes. |
| `models/__init__.py` | exports | Export `KGEntity`, `KGRelation`. |

### Integration Points

- **Consolidation**: Graph ingestion runs in the background task after `consolidate_turn_memory_with_llm()`, so it is invisible to the user.
- **Retrieval**: `graph_context_for_query()` extracts entity names from the user's query (simple NER via the LLM), traverses the graph with SQL JOINs, and returns context strings.
- **Prompt assembly**: A new `knowledge_graph` memory block in the system prompt shows relevant entity relationships.
- **Phase 1 synergy**: Graph search results can be re-ranked by BM25 (matching Mem0's pattern) using the Phase 1 infrastructure.

### Token Budget Impact

**Adds one new memory block**: `knowledge_graph` (label). Estimated 200-400 tokens showing relevant triples. This displaces nothing -- it fits within the existing token budget alongside other memory blocks. If budget is tight, the block can be omitted when no relevant graph context is found (which is the common case for casual conversation).

### Dependencies

None (independent). Benefits from Phase 1 for entity-name BM25 matching.

### Test Plan

1. **Unit: entity normalization** -- "New York City" and "NYC" should normalize to the same key
2. **Unit: `upsert_entity()`** -- create entity, upsert same name, verify `mentions` increments
3. **Unit: `upsert_relation()`** -- create relation, verify lookup by source/destination
4. **Unit: `search_graph()`** -- create A->B->C graph, search from A with depth=2, verify C is reachable
5. **Unit: `deduplicate_entity()`** -- create "New York City", attempt to add "NYC", verify dedup (requires embedding mock)
6. **Integration: `ingest_conversation_graph()`** -- feed a conversation mentioning entities, verify they appear in the graph
7. **Integration: prompt assembly** -- verify `knowledge_graph` block appears when relevant entities exist
8. **Regression: existing memory blocks** -- verify adding the graph block does not break existing block assembly

### Risk / Complexity Assessment

**Complexity: Medium-High.** Two new tables, LLM-based entity extraction, graph traversal.

**Risk: Entity extraction quality.** LLM may extract irrelevant entities or miss important ones. Mitigation: use structured tool calling (function_call) as Mem0 does, and set a minimum confidence threshold.

**Risk: Graph explosion.** Every conversation could add many entities. Mitigation: cap at 5 entities per conversation turn; deduplicate aggressively; increment `mentions` rather than creating new entities.

**Risk: SQLite graph traversal performance.** Without Neo4j's native graph engine, multi-hop traversal requires recursive CTEs or multiple JOINs. Mitigation: limit depth to 2 hops, limit result count. For a personal AI with < 1000 entities, this is fast.

---

## Phase 5: Async Sleep-Time Agents

### Overview

Refactor the existing reflection/sleep-task pipeline into an async multi-agent architecture inspired by Letta's `SleeptimeMultiAgentV4`. Instead of running all background tasks sequentially in `run_reflection()` and `run_sleep_tasks()`, introduce a turn counter with frequency gating and fire-and-forget background tasks. This enables: (1) configurable frequency (not every turn), (2) heat-triggered consolidation (not just timer-based), (3) parallel execution of independent background tasks.

### Source Reference

- `letta/letta/groups/sleeptime_multi_agent_v4.py` -- `SleeptimeMultiAgentV4.step()`, `run_sleeptime_agents()`, `_issue_background_task()`, `bump_turns_counter_async()`
- Turn counter: `sleeptime_agent_frequency` controls how often background agents fire
- Last-processed tracking: `get_last_processed_message_id_and_update_async()` prevents reprocessing

### Current State

AnimaOS has:
- `reflection.py` `schedule_reflection()` -- schedules a delayed task after 5 minutes of inactivity
- `reflection.py` `run_reflection()` -- sequentially: expire working memory, quick inner monologue, then `run_sleep_tasks()`
- `sleep_tasks.py` `run_sleep_tasks()` -- sequentially: contradiction scan, profile synthesis, episode generation, deep monologue, embedding backfill
- `consolidation.py` `schedule_background_memory_consolidation()` -- fires immediately after every turn via `asyncio.create_task()`

The gap: no frequency gating (consolidation runs on every turn), no heat-based triggering, no parallelism, no turn counting.

### Data Model Changes

**New table: `background_task_runs`** (for tracking and debugging)

```python
class BackgroundTaskRun(Base):
    __tablename__ = "background_task_runs"
    __table_args__ = (
        Index("ix_bg_task_runs_user_status", "user_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    task_type: Mapped[str] = mapped_column(String(50), nullable=False)  # consolidation, contradiction_scan, profile_synthesis, graph_ingestion, heat_decay, episode_gen, deep_monologue
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")  # pending, running, completed, failed
    result_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
```

**Add column to `users` or use in-memory:**

```python
# In-memory turn counter (no migration needed)
_turn_counters: dict[int, int] = {}  # user_id -> turn_count
```

Migration count: **1** (`20260321_0001_create_background_task_runs.py`)

### New Files

```
apps/server/src/anima_server/services/agent/sleep_agent.py
```

Key function signatures:

```python
# Configuration
SLEEPTIME_FREQUENCY: int = 3  # Run background agents every N turns
HEAT_THRESHOLD_CONSOLIDATION: float = 5.0  # Minimum accumulated heat to trigger consolidation

def bump_turn_counter(user_id: int) -> int:
    """Increment and return the turn counter for a user."""
    ...

def should_run_sleeptime(user_id: int) -> bool:
    """Check if sleeptime agents should fire based on turn counter and frequency."""
    ...

async def run_sleeptime_agents(
    *,
    user_id: int,
    user_message: str,
    assistant_response: str,
    thread_id: int | None = None,
    db_factory: Callable[..., object] | None = None,
) -> list[str]:
    """Orchestrate all background tasks, gated by turn frequency and heat.

    Fires these tasks in parallel:
    1. Memory consolidation (predict-calibrate from Phase 3)
    2. Knowledge graph ingestion (Phase 4)
    3. Heat decay (Phase 2)
    4. Episode generation check

    Fires these tasks sequentially (expensive, infrequent):
    5. Contradiction scan (only when heat > threshold)
    6. Profile synthesis (only when heat > threshold)
    7. Deep monologue (only once per 24 hours)

    Returns list of task run IDs for tracking.
    """
    ...

async def _issue_background_task(
    *,
    user_id: int,
    task_type: str,
    task_fn: Callable[..., Any],
    db_factory: Callable[..., object] | None = None,
    **kwargs: Any,
) -> str:
    """Fire a tracked background task. Records run in background_task_runs table."""
    ...

def get_last_processed_message_id(user_id: int) -> int | None:
    """Get the last message ID processed by sleeptime agents."""
    ...

def update_last_processed_message_id(user_id: int, message_id: int) -> None:
    """Update the last-processed message ID."""
    ...
```

### Modified Files

| File | Function | Change |
|------|----------|--------|
| `consolidation.py` | `schedule_background_memory_consolidation()` | Replace direct `asyncio.create_task()` with `run_sleeptime_agents()` call which includes frequency gating. |
| `reflection.py` | `schedule_reflection()` | Keep the 5-minute inactivity timer, but when it fires, call `run_sleeptime_agents()` with `force=True` (bypass frequency gate) to run the full suite. |
| `reflection.py` | `run_reflection()` | Delegate to `run_sleeptime_agents()` with `force=True`. |
| `sleep_tasks.py` | `run_sleep_tasks()` | Keep as-is but make it callable from `run_sleeptime_agents()`. Add heat-threshold gating for expensive operations. |
| `models/agent_runtime.py` | (module level) | Add `BackgroundTaskRun` model. |

### Integration Points

- **Post-turn hook**: The main chat endpoint calls `schedule_background_memory_consolidation()`. This now goes through the frequency-gated orchestrator.
- **Inactivity hook**: `schedule_reflection()` fires after 5 minutes idle. This bypasses frequency gating and runs the full suite.
- **Task tracking**: All background work is recorded in `background_task_runs` for debugging and monitoring.
- **Heat gating**: Expensive operations (contradiction scan, profile synthesis) only fire when accumulated item heat exceeds `HEAT_THRESHOLD_CONSOLIDATION`.

### Token Budget Impact

None directly. This phase changes when and how background tasks fire, not what goes into the prompt. Net effect: fewer LLM calls (due to frequency gating), same or better quality.

### Dependencies

- **Phase 2 (Heat Scoring)**: Heat thresholds gate expensive operations.
- **Phase 3 (Predict-Calibrate)**: Consolidation uses predict-calibrate extraction.
- **Phase 4 (Knowledge Graph)**: Graph ingestion is one of the parallel tasks.

### Test Plan

1. **Unit: `bump_turn_counter()`** -- verify counter increments
2. **Unit: `should_run_sleeptime()`** -- verify fires on frequency multiples (e.g., turn 3, 6, 9)
3. **Unit: frequency gating** -- fire 5 turns, verify sleeptime agents ran only on turns 3 and 6 (with frequency=3)
4. **Unit: heat gating** -- mock low heat, verify expensive tasks are skipped; mock high heat, verify they fire
5. **Integration: `_issue_background_task()`** -- verify task run is recorded in `background_task_runs`
6. **Integration: end-to-end** -- send 3 messages, verify consolidation fires after the 3rd
7. **Regression: inactivity reflection** -- verify 5-minute idle still triggers full suite

### Risk / Complexity Assessment

**Complexity: Medium.** Mostly reorganization of existing code with new gating logic.

**Risk: Lost work on crash.** If the process crashes during background tasks, in-progress work is lost. Mitigation: `background_task_runs` records track what started, and all operations are idempotent (re-running is safe).

**Risk: Race conditions.** Multiple background tasks accessing the same DB. Mitigation: each task opens its own session via `db_factory()` (current pattern). SQLite WAL mode handles concurrent reads.

---

## Phase 6: Batch Episode Segmentation

### Overview

Replace the current fixed-size episode chunking (every `EPISODE_MIN_TURNS * 2` logs = one episode) with LLM-based topic-coherent segmentation. Instead of splitting conversations at fixed boundaries, batch up conversation turns and ask the LLM to group them by topic coherence. This produces episodes with non-continuous message indices -- e.g., messages 1-3 (about work) and messages 8, 10, 11 (also about work) become one episode, while messages 4-7 (about cooking) become another.

### Source Reference

- `nemori/src/generation/batch_segmenter.py` -- `BatchSegmenter.segment_batch()` returns `List[List[int]]` (groups of message indices, non-continuous allowed)
- Low temperature (0.2) for consistent segmentation
- Fallback: if LLM fails, all messages become one episode

### Current State

AnimaOS's `episodes.py`:
- `maybe_generate_episode()` -- checks if `remaining_logs >= EPISODE_MIN_TURNS` (3), takes up to `EPISODE_MIN_TURNS * 2` (6) logs, generates a single episode
- `_generate_episode_via_llm()` -- sends conversation turns to LLM, gets back summary/topics/emotional_arc/significance
- `_create_fallback_episode()` -- creates a basic episode when LLM fails
- Episodes track `turn_count` and consume turns sequentially (offset-based)

The gap: episodes always contain contiguous turns. If a conversation switches topics and comes back, the episode boundaries cut across topics rather than following them.

### Data Model Changes

**Add columns to `MemoryEpisode`:**

```python
message_indices_json: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)  # 1-based indices of included logs
segmentation_method: Mapped[str] = mapped_column(String(20), nullable=False, default="sequential")  # sequential, batch_llm
```

Migration count: **1** (`20260322_0001_add_episode_segmentation_columns.py`)

### New Files

```
apps/server/src/anima_server/services/agent/batch_segmenter.py
```

Key function signatures:

```python
BATCH_SEGMENTATION_PROMPT: str = """..."""  # Group these numbered messages into topic-coherent episodes
BATCH_THRESHOLD: int = 8  # Minimum messages to trigger batch segmentation

async def segment_messages_batch(
    messages: list[tuple[str, str]],  # (user_message, assistant_response) pairs
    *,
    user_id: int = 0,
) -> list[list[int]]:
    """Use LLM to group messages into topic-coherent episodes.

    Returns list of episode groups, each a list of 1-based message indices.
    Non-continuous indices are allowed (e.g., [[1,2,3], [4,5,6,7], [8,10,11], [9,12]]).

    Falls back to single-group if LLM fails.
    """
    ...

def should_batch_segment(buffer_size: int) -> bool:
    """Check if enough messages have accumulated for batch segmentation."""
    ...

async def generate_episodes_from_segments(
    db: Session,
    *,
    user_id: int,
    thread_id: int | None,
    logs: list[MemoryDailyLog],
    segments: list[list[int]],
    today: str,
) -> list[MemoryEpisode]:
    """Generate one episode per segment group.
    Each episode records the message_indices_json and segmentation_method='batch_llm'.
    """
    ...
```

### Modified Files

| File | Function | Change |
|------|----------|--------|
| `episodes.py` | `maybe_generate_episode()` | When `remaining_logs >= BATCH_THRESHOLD` (8), call `segment_messages_batch()` to get topic groups, then `generate_episodes_from_segments()` to create multiple episodes. Fall back to current single-episode logic when < 8 logs or LLM fails. |
| `episodes.py` | `EPISODE_GENERATION_PROMPT` | Keep as-is (used for per-segment summary generation). |
| `models/agent_runtime.py` | `MemoryEpisode` | Add `message_indices_json` and `segmentation_method` columns. |

### Integration Points

- **Sleep tasks**: `run_sleep_tasks()` calls `maybe_generate_episode()` -- no changes needed.
- **Prompt assembly**: `build_episodes_memory_block()` in `memory_blocks.py` reads episodes by `created_at` -- no changes needed, as topic-segmented episodes have the same structure.
- **Phase 5**: Batch segmentation runs as part of the sleeptime agent suite.

### Token Budget Impact

**Same or slightly increased.** Instead of one episode per 6 turns, we may get 2-3 episodes per 8 turns, each with a shorter summary. Total token usage in the `recent_episodes` block stays similar since we still show the last 5 episodes.

### Dependencies

None (standalone). Benefits from Phase 5 for orchestration.

### Test Plan

1. **Unit: `should_batch_segment()`** -- verify threshold logic
2. **Unit: `segment_messages_batch()`** -- mock LLM response with known groups, verify parsing
3. **Unit: non-continuous indices** -- verify that `[[1,2,3], [4,5], [6,8], [7,9]]` is handled correctly
4. **Unit: LLM failure fallback** -- mock LLM failure, verify all messages become one episode
5. **Integration: `maybe_generate_episode()`** -- with 10 logs, verify multiple episodes are created with correct `message_indices_json`
6. **Integration: `segmentation_method` column** -- verify new episodes have `segmentation_method='batch_llm'`
7. **Regression: existing episode tests** -- verify episodes with < 8 logs still use sequential method

### Risk / Complexity Assessment

**Complexity: Medium.** New LLM prompt, index-mapping logic, integration with existing episode generation.

**Risk: LLM segmentation quality.** The LLM may produce poor topic groupings, especially with short or ambiguous conversations. Mitigation: low temperature (0.2), fallback to single episode on any parsing error, and validation that all indices are covered (no dropped messages).

**Risk: Index off-by-one.** Nemori uses 1-based indices in prompts. Must ensure correct mapping to 0-based Python lists. Mitigation: explicit conversion functions with validation.

**Risk: Backward compatibility.** Existing code assumes `turn_count` is the number of sequential turns consumed. With batch segmentation, `turn_count` becomes the number of messages in the segment group (which may not be contiguous). Mitigation: keep `turn_count` as the count of messages in the episode, and add `message_indices_json` for the actual indices. The offset calculation in `maybe_generate_episode()` should use `sum(turn_count)` as before.

---

## Roadmap Phase Mapping

This plan uses its own sequential phase numbering (1-6). The table below maps each engineering phase to its corresponding roadmap entry in `docs/thesis/roadmap.md`.

| Implementation Plan Phase | Roadmap Phase | Roadmap Status |
|---------------------------|---------------|----------------|
| Phase 1: Hybrid Search (BM25 + Vector + RRF) | Phase 9.7 | planned |
| Phase 2: Heat-Based Memory Scoring | Phase 10.4 | planned |
| Phase 3: Predict-Calibrate Consolidation | Phase 10.3 | planned |
| Phase 4: Knowledge Graph | Phase 9.5 | planned |
| Phase 5: Async Sleep Agents | Phase 10.6 | planned |
| Phase 6: Batch Episode Segmentation | Phase 10.7 | planned |

The roadmap's existing Phase 6 (Reflection and Sleep Tasks) describes the current synchronous sleep task system. Implementation Plan Phase 5 (Async Sleep Agents) extends that system with the Letta-derived asynchronous pattern — it is a separate roadmap entry (Phase 10.6), not a replacement of the existing Phase 6.

---

## Summary

| Phase | New Files | Modified Files | Migrations | New Tables | New Pip Deps | Complexity |
|-------|-----------|----------------|------------|------------|--------------|------------|
| 1. Hybrid Search | 1 | 2 | 0 | 0 | `rank-bm25` | Low |
| 2. Heat Scoring | 1 | 4 | 1 | 0 | -- | Low-Medium |
| 3. Predict-Calibrate | 1 | 1 | 0 | 0 | -- | Medium |
| 4. Knowledge Graph | 1-2 | 3 | 1 | 2 | -- | Medium-High |
| 5. Async Sleep Agents | 1 | 4 | 1 | 1 | -- | Medium |
| 6. Batch Segmentation | 1 | 2 | 1 | 0 | -- | Medium |
| **Totals** | **6-7** | **~14** | **4** | **3** | **1** | -- |

### Total Migration Count: 4

1. `20260319_0001_add_heat_column_to_memory_items.py` (Phase 2)
2. `20260320_0001_create_knowledge_graph_tables.py` (Phase 4)
3. `20260321_0001_create_background_task_runs.py` (Phase 5)
4. `20260322_0001_add_episode_segmentation_columns.py` (Phase 6)

### Constraints Compliance Checklist

- [x] SQLite + SQLCipher only (no PostgreSQL, no Redis, no Neo4j, no ChromaDB)
- [x] Ollama / OpenRouter / vLLM only (no OpenAI, no Anthropic, no Google)
- [x] All state in `.anima/` directory
- [x] Python/FastAPI backend at `apps/server/`
- [x] 602+ existing tests preserved (all changes are additive or behind feature flags)
- [x] All file paths and function names reference actual code
