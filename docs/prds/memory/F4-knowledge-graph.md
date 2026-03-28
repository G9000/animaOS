---
title: "PRD: F4 — Knowledge Graph (SQLite-backed)"
description: SQLite-backed knowledge graph for relational memory
category: prd
version: "1.0"
---

# PRD: F4 — Knowledge Graph (SQLite-backed)

**Version**: 1.0
**Date**: 2026-03-18
**Status**: Shipped (~93%)
**Roadmap Phase**: 9.5
**Priority**: P1
**Depends on**: None (independent); benefits from F1 for entity-name BM25 matching
**Blocks**: F5 (orchestrates `graph_ingestion` as one of the parallel background tasks)

---

## 1. Overview

Add a lightweight knowledge graph layer on top of existing semantic memory. Entities (people, places, organizations, concepts) and typed relationships between them are extracted from conversations and stored in two new SQLite tables. This gives AnimaOS relational structure between its flat facts — enabling graph traversal for context that embedding similarity alone would miss.

When the user asks "what do you know about my family?", AnimaOS can traverse the graph from the user entity through `sister_of`, `parent_of`, `married_to` relations and surface connected memories, rather than relying on embedding similarity between "family" and individual fact strings.

---

## 2. Problem Statement

### Current State

AnimaOS stores flat semantic facts in `MemoryItem`:

```
"User works at Google" (category: fact)
"Alice is User's sister" (category: relationship)
"User lives in Berlin" (category: fact)
"Alice's birthday is March 15" (category: fact)
"User is working on Project Aurora at Google" (category: fact)
```

These are independent strings with no relational structure. There is no way to:

- **Link entities**: "Alice" in fact #2 and "Alice" in fact #4 are not connected
- **Traverse relationships**: "Who does the user know?" requires scanning all facts for relationship patterns
- **Deduplicate entities**: "NYC" and "New York City" are stored as different strings
- **Contextual retrieval**: Asking about "my work" should surface Google + Project Aurora + colleagues — but embedding similarity may miss the structural connection

`MemoryItemTag` provides rudimentary grouping but not entity-relation modeling.

### Evidence

| Source | Finding |
|--------|---------|
| Mem0 `graph_memory.py` | `MemoryGraph` with entity extraction via LLM tool calling, 26% accuracy improvement with graph-augmented retrieval |
| Mem0 entity dedup | `_search_source_node()` / `_search_destination_node()` using embedding similarity threshold |
| Research Report C2 | Knowledge graph + vector hybrid retrieval rated "Critical" |
| Repo Analysis #1, #2 | SQLite-backed knowledge graph and graph+vector hybrid retrieval both rated "Critical" |

### User Impact

- "Tell me about my family" → fails to connect Alice (sister), Mom (parent), Bob (brother-in-law)
- "What's happening at work?" → surfaces "User works at Google" but misses "Project Aurora" and "colleague Sarah mentioned in last week's conversation"
- User mentions "New York City" and later "NYC" → stored as unrelated facts, no entity linking

---

## 3. Goals and Non-Goals

### Goals

1. Two new SQLite tables (`kg_entities`, `kg_relations`) for entity-relationship storage
2. LLM-based entity and relation extraction via structured tool calling during `graph_ingestion`
3. Entity deduplication via normalized names and embedding similarity
4. Graph traversal via SQL JOINs (max depth 2) for contextual retrieval
5. `knowledge_graph` memory block injected into the system prompt with relevant entity relationships
6. Graph ingestion runs as its own structured background task in the F5 orchestrator, invisible to the user
7. Keep the graph SQLite-backed inside the Core, preserving portability, encryption, and single-directory ownership

### Non-Goals

- **Neo4j, Kuzu, Memgraph, and similar graph backends/platform abstractions** — competitor implementations inform lifecycle patterns only; AnimaOS remains SQLite-backed inside the Core
- **Deep graph traversal** (depth > 2) — unnecessary for personal-scale graphs
- **Automatic entity resolution against external knowledge bases** — no Wikipedia, Wikidata, etc.
- **Graph visualization UI** — backend only in this PRD
- **Replacing vector search** — the graph augments it, does not replace it
- **Importing graph-platform abstractions** — no Cypher, graph services, graph daemons, or backend-specific admin workflows
- **World model synthesis** — the knowledge graph provides raw entities and relations; a higher-level "world model" narrative synthesis is a separate feature

---

## 4. Detailed Design

### 4.1 Data Model

**Table: `kg_entities`**

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | Integer | PK, autoincrement | |
| `user_id` | Integer | FK → users.id, CASCADE | |
| `name` | String(200) | NOT NULL | Display name ("Alice", "Google", "New York City") |
| `name_normalized` | String(200) | NOT NULL, UNIQUE(user_id, name_normalized) | Lowered, underscore-joined ("alice", "google", "new_york_city") |
| `entity_type` | String(50) | NOT NULL, default "unknown" | person, place, organization, project, concept |
| `description` | Text | NOT NULL, default "" | Brief description ("User's sister, lives in Munich") |
| `mentions` | Integer | NOT NULL, default 1 | Conversation mention count |
| `embedding_json` | JSON | nullable | Embedding for alias dedup |
| `created_at` | DateTime(tz) | NOT NULL, server_default now() | |
| `updated_at` | DateTime(tz) | NOT NULL, server_default now() | |

**Indices**: `ix_kg_entities_user_type` on `(user_id, entity_type)`

**Table: `kg_relations`**

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | Integer | PK, autoincrement | |
| `user_id` | Integer | FK → users.id, CASCADE | |
| `source_id` | Integer | FK → kg_entities.id, CASCADE | Source entity |
| `destination_id` | Integer | FK → kg_entities.id, CASCADE | Destination entity |
| `relation_type` | String(100) | NOT NULL | works_at, sister_of, lives_in, related_to_project, etc. |
| `mentions` | Integer | NOT NULL, default 1 | How many times this relation was extracted |
| `source_memory_id` | Integer | FK → memory_items.id, SET NULL, nullable | Which memory item this relation was derived from |
| `created_at` | DateTime(tz) | NOT NULL, server_default now() | |
| `updated_at` | DateTime(tz) | NOT NULL, server_default now() | |

**Indices**: `ix_kg_relations_source` on `(source_id)`, `ix_kg_relations_dest` on `(destination_id)`

### 4.2 Entity Extraction via LLM Tool Calling

Following Mem0's pattern, entity extraction uses structured tool calls:

```python
EXTRACT_ENTITIES_TOOL = {
    "type": "function",
    "function": {
        "name": "extract_entities",
        "description": "Extract entities mentioned in the conversation",
        "parameters": {
            "type": "object",
            "properties": {
                "entities": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "type": {"type": "string", "enum": ["person", "place", "organization", "project", "concept"]},
                            "description": {"type": "string"}
                        },
                        "required": ["name", "type"]
                    },
                    "maxItems": 5
                }
            }
        }
    }
}

EXTRACT_RELATIONS_TOOL = {
    "type": "function",
    "function": {
        "name": "extract_relations",
        "description": "Extract relationships between entities",
        "parameters": {
            "type": "object",
            "properties": {
                "relations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "source": {"type": "string"},
                            "relation": {"type": "string"},
                            "destination": {"type": "string"}
                        },
                        "required": ["source", "relation", "destination"]
                    }
                }
            }
        }
    }
}
```

### 4.3 New Files

```
apps/server/src/anima_server/services/agent/knowledge_graph.py
apps/server/src/anima_server/api/routes/knowledge_graph.py  (optional REST API)
```

### 4.4 Core Functions

```python
async def extract_entities_and_relations(
    *, text: str, user_id: int,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Extract entities and relations from text using LLM tool calling.
    Returns (entities, relations).
    Entities: [{name, type, description}, ...]
    Relations: [{source, relation, destination}, ...]
    Cap: max 5 entities per call.
    """
    ...

def normalize_entity_name(name: str) -> str:
    """'New York City' → 'new_york_city'. For dedup key."""
    ...

def upsert_entity(
    db: Session, *, user_id: int, name: str, entity_type: str, description: str = "",
) -> KGEntity:
    """Create or update entity. Increments mentions on match.
    Uses normalized name for dedup.
    """
    ...

def upsert_relation(
    db: Session, *, user_id: int, source_name: str, destination_name: str,
    relation_type: str, source_memory_id: int | None = None,
) -> KGRelation:
    """Create or update relation. Increments mentions on match."""
    ...

async def deduplicate_entity(
    db: Session, *, user_id: int, new_entity_name: str,
    similarity_threshold: float = 0.85,
) -> KGEntity | None:
    """Check if a new entity matches an existing one via embedding similarity.
    'NYC' vs 'New York City' → returns existing entity.
    Returns None if no match found.
    """
    ...

def search_graph(
    db: Session, *, user_id: int, entity_names: list[str],
    max_depth: int = 2, limit: int = 20,
) -> list[dict[str, str]]:
    """Traverse graph from given entities via SQL JOINs.
    Returns [{"source": ..., "relation": ..., "destination": ...}, ...]
    """
    ...

def rerank_graph_results(
    results: list[dict[str, str]], query: str, top_n: int = 10,
) -> list[dict[str, str]]:
    """BM25-rerank graph traversal results for query relevance.
    Tokenizes each triple as 'source relation destination', scores against query.
    Adopted from Mem0's graph_memory.py BM25 reranking pattern.
    """
    ...

def graph_context_for_query(
    db: Session, *, user_id: int, query: str, limit: int = 10,
) -> list[str]:
    """Extract entity names from query, traverse graph, BM25-rerank results,
    return context strings.
    Output: ["Alice (person, User's sister) → lives_in → Munich", ...]
    Suitable for inclusion in a memory block.
    """
    ...

async def prune_stale_relations(
    db: Session, *, user_id: int, new_facts: list[str],
    existing_relations: list[dict[str, str]],
) -> list[int]:
    """LLM-driven relation pruning. Given new facts from the current conversation
    and existing relations touching the same entities, ask the LLM which relations
    are now outdated or contradicted.
    Returns list of kg_relations.id to delete.
    Adopted from Mem0's DELETE_RELATIONS_SYSTEM_PROMPT pattern.
    """
    ...

async def ingest_conversation_graph(
    db: Session, *, user_id: int, user_message: str, assistant_response: str,
) -> tuple[int, int, int]:
    """Full pipeline for the `graph_ingestion` background task:
    extract → dedup → upsert entities + relations → prune stale relations.
    Returns (entities_upserted, relations_upserted, relations_pruned).
    """
    ...
```

### 4.5 Graph Traversal Implementation

Since we're using SQLite (not Neo4j), graph traversal uses SQL JOINs:

**Depth 1** (direct relations):
```sql
SELECT e2.name, r.relation_type, e2.entity_type
FROM kg_relations r
JOIN kg_entities e2 ON r.destination_id = e2.id
WHERE r.source_id = :entity_id AND r.user_id = :user_id
UNION
SELECT e2.name, r.relation_type, e2.entity_type
FROM kg_relations r
JOIN kg_entities e2 ON r.source_id = e2.id
WHERE r.destination_id = :entity_id AND r.user_id = :user_id
```

**Depth 2**: Run depth-1 query, collect result entity IDs, run depth-1 again from those IDs. Two queries, not a recursive CTE — simpler and predictable performance.

For < 1,000 entities (typical for a personal AI), this completes in < 50ms.

### 4.6 Graph Lifecycle

The graph is **not append-only**. F4 adopts a bounded lifecycle that keeps the graph local and SQLite-native while preventing monotonic accumulation of stale relations.

`graph_ingestion` is a distinct structured background task that may run in parallel with consolidation under F5, but its scope remains bounded to the current turn and explicit graph deltas. It is not a transcript-wide graph sweep and it is not nested inside consolidation.

**Lifecycle policy**

1. During each `graph_ingestion` run, `ingest_conversation_graph()` extracts entities and relations from the current turn's user message and assistant response.
2. Upsert entities first, then load only existing relations that touch the entities mentioned in the new turn.
3. Run `prune_stale_relations()` against that bounded candidate set before finalizing the turn's graph state.
4. Delete only relations the pruning step classifies as outdated or contradicted by evidence in the new turn.

This means stale-relation pruning happens **during ingestion only**, against relations touching the current turn's entities. F4 does not include a sleep-time pruning pass, a whole-graph cleanup sweep, or any separate graph-maintenance subsystem. Competitor systems using Neo4j, Kuzu, or Memgraph are relevant here only as evidence that append-only graphs drift; they do not change AnimaOS's storage architecture.

### 4.7 Graph Retrieval and Reranking Policy

F4 explicitly **adopts lightweight reranking** for graph results. After `search_graph()` returns a small traversal set (max 20 triples), `rerank_graph_results()` BM25-reranks those triples against the user query before `graph_context_for_query()` builds the `knowledge_graph` block.

The rationale is straightforward:

- SQL traversal is good at finding connected triples, not ordering them by query phrasing.
- The candidate set is already small, so in-process BM25 reranking is cheap and predictable.
- AnimaOS does **not** add an external reranker service or graph-specific search backend for this; lightweight local reranking is enough.

### 4.8 Modified Files

| File | Function | Change |
|------|----------|--------|
| `sleep_agent.py` | `run_sleeptime_agents()` | Issue `graph_ingestion` as its own structured background task, calling `ingest_conversation_graph()` with current-turn inputs |
| `memory_blocks.py` | `build_runtime_memory_blocks()` | Add `knowledge_graph` block between `relationships` and `current_focus`. Call `graph_context_for_query()`. |
| `models/agent_runtime.py` | (module level) | Add `KGEntity` and `KGRelation` model classes |
| `models/__init__.py` | exports | Export `KGEntity`, `KGRelation` |

### 4.9 Memory Block Format

The `knowledge_graph` block in the system prompt:

```
## Knowledge Graph (relationships between entities in User's life)

- Alice (person, User's sister) → lives_in → Munich
- Alice (person) → birthday → March 15
- Google (organization) → employer_of → User
- Project Aurora (project) → related_to → Google
- Sarah (person, colleague) → works_at → Google
```

Estimated size: 200-400 tokens for 10-15 triples. Omitted when no relevant graph context is found for the current query.

### 4.10 Entity Type Taxonomy

| Type | Examples | When extracted |
|------|----------|----------------|
| `person` | Alice, Sarah, Dr. Smith | Names of people mentioned in conversation |
| `place` | Berlin, Munich, "the coffee shop on Main St" | Locations mentioned |
| `organization` | Google, MIT, "Alice's school" | Companies, institutions |
| `project` | Project Aurora, "my thesis", "the kitchen renovation" | Named efforts or initiatives |
| `concept` | Python, machine learning, stoicism | Technical terms, interests, philosophies |

### 4.11 Relation Type Conventions

Freeform strings, but the LLM prompt encourages consistent naming:

```
works_at, lives_in, sister_of, brother_of, parent_of, married_to,
friend_of, colleague_of, related_to_project, interested_in,
member_of, located_in, part_of, created_by
```

---

## 5. Requirements

| ID | Requirement | Priority |
|----|-------------|----------|
| F4.1 | `KGEntity` model with all columns from Section 4.1 | Must |
| F4.2 | `KGRelation` model with all columns from Section 4.1 | Must |
| F4.3 | `extract_entities_and_relations()` using LLM structured tool calls | Must |
| F4.4 | `normalize_entity_name()` for consistent dedup keys | Must |
| F4.5 | `upsert_entity()` with normalized-name dedup and `mentions` increment | Must |
| F4.6 | `upsert_relation()` with source/destination entity lookup and `mentions` increment | Must |
| F4.7 | `deduplicate_entity()` via embedding similarity (threshold 0.85) for alias resolution | Should |
| F4.8 | `search_graph()` traversing with max_depth=2, limit=20 | Must |
| F4.9 | `graph_context_for_query()` extracting entity names from query and returning context strings | Must |
| F4.10 | `ingest_conversation_graph()` full pipeline called as its own structured `graph_ingestion` background task | Must |
| F4.11 | `knowledge_graph` memory block added to `build_runtime_memory_blocks()` | Must |
| F4.12 | Cap entity extraction at 5 per conversation turn (`maxItems: 5` in tool schema) | Must |
| F4.13 | Bidirectional graph traversal (both source→dest and dest→source) | Must |
| F4.14 | REST API endpoints for viewing entities and relations | Could |
| F4.15 | Full vault export/import support for `kg_entities` and `kg_relations` | Must |
| F4.16 | `rerank_graph_results()` — BM25 reranking of graph traversal results before returning context; lightweight local reranking is required for the max-20 triple candidate set | Must |
| F4.17 | `prune_stale_relations()` — LLM-driven deletion of outdated/contradicted relations during ingestion (adopted from Mem0) | Must |
| F4.18 | UUID/ID hallucination protection — map real entity/relation IDs to sequential integers before sending to LLM prompts, map back after; use ID indirection only inside LLM-facing pruning flows, not as a new storage layer (adopted from Mem0) | Must |
| F4.19 | Tool-calling fallback — when the LLM does not support `function_call` / tool use, fall back to a JSON-formatted prompt with response parsing (consistent with existing codebase patterns in consolidation.py, episodes.py) | Must |
| F4.20 | Storage architecture constraint — the knowledge graph remains SQLite-backed inside the Core; Neo4j, Kuzu, Memgraph, and similar systems are reference material only, not implementation targets | Must |

---

## 6. Data Model Changes

**Migration**: `20260320_0001_create_knowledge_graph_tables.py`

```python
def upgrade():
    op.create_table(
        "kg_entities",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("name_normalized", sa.String(200), nullable=False),
        sa.Column("entity_type", sa.String(50), nullable=False, server_default="unknown"),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("mentions", sa.Integer, nullable=False, server_default="1"),
        sa.Column("embedding_json", sa.JSON, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "name_normalized", name="uq_kg_entities_user_name"),
    )
    op.create_index("ix_kg_entities_user_type", "kg_entities", ["user_id", "entity_type"])

    op.create_table(
        "kg_relations",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_id", sa.Integer, sa.ForeignKey("kg_entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("destination_id", sa.Integer, sa.ForeignKey("kg_entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("relation_type", sa.String(100), nullable=False),
        sa.Column("mentions", sa.Integer, nullable=False, server_default="1"),
        sa.Column("source_memory_id", sa.Integer, sa.ForeignKey("memory_items.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_kg_relations_source", "kg_relations", ["source_id"])
    op.create_index("ix_kg_relations_dest", "kg_relations", ["destination_id"])
```

- New tables: **2** (`kg_entities`, `kg_relations`)
- New indices: **3**

---

## 7. Acceptance Criteria

| # | Criterion | Verification |
|---|-----------|--------------|
| AC1 | After conversations mentioning "Alice (sister)" and "Alice's birthday is March 15", the entity "Alice" exists with type "person" and mentions >= 2 | Integration test |
| AC2 | Relations `User → sister_of → Alice` and `Alice → birthday → March 15` exist | Integration test |
| AC3 | Querying "what do you know about my family?" traverses graph and surfaces Alice | Integration test: verify `graph_context_for_query()` returns Alice-related triples |
| AC4 | "NYC" and "New York City" are deduplicated into one entity | Unit test for `deduplicate_entity()` with embedding mock |
| AC5 | Entity extraction capped at 5 per conversation turn | Unit test: conversation mentioning 10 entities → only 5 extracted |
| AC6 | Graph traversal completes in < 50ms for 1,000 entities with depth=2 | Performance test |
| AC7 | `knowledge_graph` memory block appears in system prompt when relevant entities exist | Integration test |
| AC8 | `knowledge_graph` block is omitted when no relevant entities found | Integration test |
| AC9 | Vault export includes `kg_entities` and `kg_relations` | Integration test |
| AC10 | All 602 existing tests pass | CI |
| AC11 | When user says "I left Google", the `works_at` relation between User and Google is pruned by `prune_stale_relations()` | Integration test |
| AC12 | `graph_context_for_query()` returns BM25-reranked results (most relevant triples first) | Unit test with mock graph results |
| AC13 | Entity/relation IDs sent to LLM prompts are mapped to sequential integers, not real IDs | Unit test for ID mapping |

---

## 8. Test Plan

| # | Type | Test | Details |
|---|------|------|---------|
| T1 | Unit | `normalize_entity_name()` | "New York City" → "new_york_city", "Dr. Alice Smith" → "dr_alice_smith" |
| T2 | Unit | `upsert_entity()` | Create entity, upsert same name, verify `mentions` increments |
| T3 | Unit | `upsert_relation()` | Create relation, verify lookup by source/destination |
| T4 | Unit | `search_graph()` depth=1 | Create A→B, search from A, verify B found |
| T5 | Unit | `search_graph()` depth=2 | Create A→B→C, search from A with depth=2, verify C reachable |
| T6 | Unit | `search_graph()` bidirectional | Create A→B, search from B, verify A found |
| T7 | Unit | `deduplicate_entity()` | Create "New York City", attempt "NYC", verify dedup (mock embeddings) |
| T8 | Integration | `extract_entities_and_relations()` | Mock LLM tool call response, verify parsing |
| T9 | Integration | `ingest_conversation_graph()` | Feed one turn mentioning entities, verify they appear in graph and pruning only inspects relations touching that turn's entities |
| T10 | Integration | Prompt assembly | Verify `knowledge_graph` block appears when relevant entities exist |
| T11 | Integration | Vault export/import | Export with entities, import on fresh DB, verify entities preserved |
| T12 | Regression | Existing memory blocks | Adding graph block does not break existing block assembly |
| T13 | Performance | Graph traversal | 1,000 entities, depth=2 traversal < 50ms |
| T14 | Unit | `rerank_graph_results()` | 20 triples, BM25 rerank for query, verify top results are most relevant |
| T15 | Unit | `prune_stale_relations()` | Mock LLM returns deletion list, verify stale relations removed from DB |
| T16 | Integration | Stale relation pruning | Store "User works_at Google", conversation says "I left Google", verify relation pruned |
| T17 | Unit | ID hallucination protection | Map 3 real IDs to [1,2,3], send to LLM, map response back, verify correct round-trip |

---

## 9. Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| **Entity extraction quality** | Medium | Structured tool calling (function_call) with explicit schema. 5-entity cap prevents garbage floods. Entity dedup catches duplicates. |
| **Graph explosion** | Medium | 5-entity cap per turn. `mentions` increment on existing entities rather than creating new ones. Aggressive normalization + embedding dedup. |
| **SQLite graph traversal performance** | Low | Depth capped at 2, result limit 20. For < 1,000 entities, this is fast. No recursive CTEs — just two sequential queries. |
| **Architecture drift from competitor patterns** | Medium | Mem0 and similar systems inform pruning and ID-indirection safeguards only. F4 explicitly rejects Neo4j, Kuzu, Memgraph, and related platform abstractions to preserve the portable Core. |
| **LLM tool calling compatibility** | Medium | Not all models via Ollama/OpenRouter support structured tool calling. Fallback: use a regular prompt with JSON output format + parsing. |
| **Entity type drift** | Low | Fixed enum in tool schema. LLM may occasionally choose wrong type, but this is cosmetic — graph traversal doesn't filter by type. |
| **Relation type inconsistency** | Low | LLM may use "works_at" vs "employed_at" vs "works_for" for the same relation. Normalize common variations in `upsert_relation()`. |
| **Stale relation accumulation** | Medium | Without pruning, the graph grows monotonically and outdated relations compete with current ones. Mitigated by ingestion-time pruning scoped to relations touching the current turn's entities via `prune_stale_relations()` (F4.17), adopted from Mem0's pattern but kept bounded for SQLite/Core constraints. |
| **LLM ID hallucination** | Medium | LLMs may hallucinate or corrupt entity/relation IDs when they appear in prompts. Mitigated by mapping real IDs to sequential integers before LLM calls (F4.18), adopted from Mem0's pattern. |

---

## 10. Rollout

1. Create `KGEntity` and `KGRelation` models in `models/agent_runtime.py`
2. Create Alembic migration for both tables
3. Create `knowledge_graph.py` with all functions including `rerank_graph_results()` and `prune_stale_relations()`
4. Implement ID hallucination protection helper (int mapping for LLM prompts)
5. Write unit tests for entity normalization, upsert, graph traversal, BM25 reranking, relation pruning, ID mapping
6. Integrate the F5 orchestrator so it issues `graph_ingestion` as a separate task calling `ingest_conversation_graph()`
7. Modify `memory_blocks.py` to add `knowledge_graph` block
8. Write integration tests for full pipeline, prompt assembly, and stale relation pruning
9. Add vault export/import support for new tables
10. Run full test suite (602+ tests)
11. Ship as single PR

---

## 11. Knowledge Graph vs World Model

The inner-life thesis (Section 2.6) describes a "world model" as a structured section of user memory: key people, places, recurring situations, active projects. The knowledge graph provides the **storage and traversal layer** for this concept:

- `kg_entities` stores the key people, places, and projects
- `kg_relations` stores how they connect
- `graph_context_for_query()` is the retrieval interface

A higher-level "world model narrative" — a synthesized prose summary of the user's life context generated from graph data — is a separate future feature that would consume the knowledge graph as input. This PRD covers the graph infrastructure, not the narrative synthesis.

---

## 13. Implementation Audit (2026-03-28)

### Requirements Checklist

| ID | Requirement | Status | Evidence |
|----|-------------|--------|----------|
| F4.1 | `KGEntity` model with all columns | DONE | `agent_runtime.py:597-621` — all columns: name, name_normalized, entity_type, description, mentions, embedding_json, UniqueConstraint, index |
| F4.2 | `KGRelation` model with all columns | DONE | `agent_runtime.py:624-654` — all columns: source_id, destination_id, relation_type, mentions, source_memory_id, FK constraints, indexes |
| F4.3 | `extract_entities_and_relations()` using LLM | DONE | `knowledge_graph.py:610-701` — JSON prompt with response parsing (not tool calling — uses F4.19 fallback approach). Jinja2 template `extract_entities.md.j2` |
| F4.4 | `normalize_entity_name()` for dedup keys | DONE | `knowledge_graph.py:29-41` — lowercase, spaces→underscores, collapse multiples |
| F4.5 | `upsert_entity()` with normalized-name dedup + mentions increment | DONE | `knowledge_graph.py:140-183` — exact normalized match + fuzzy `_find_similar_entity()` fallback, increments mentions |
| F4.6 | `upsert_relation()` with lookup + mentions increment | DONE | `knowledge_graph.py:185-255` — looks up source/dest entities, dedup by source+dest+relation_type, increments mentions |
| F4.7 | `deduplicate_entity()` via embedding similarity (threshold 0.85) | **NOT DONE** | No `deduplicate_entity()` function. Dedup uses `_find_similar_entity()` with token-level Jaccard (threshold 0.7) + substring containment — NOT embedding-based. `embedding_json` column exists on `KGEntity` but is never populated or queried for similarity. |
| F4.8 | `search_graph()` with max_depth=2, limit=20 | DONE | `knowledge_graph.py:257-363` — bidirectional SQL JOIN traversal, depth param, limit param |
| F4.9 | `graph_context_for_query()` extracting entity names + returning context strings | DONE | `knowledge_graph.py:448-496` — calls `_extract_entity_names_from_query()` → `search_graph()` → `rerank_graph_results()`, returns formatted strings |
| F4.10 | `ingest_conversation_graph()` as background task | DONE | `knowledge_graph.py:825+` — full pipeline; `sleep_agent.py:243-244` — runs as `graph_ingestion` task |
| F4.11 | `knowledge_graph` memory block in prompt | DONE | `memory_blocks.py:763-795` — `build_knowledge_graph_block()` calls `graph_context_for_query()` |
| F4.12 | Cap entity extraction at 5 per turn | DONE | `knowledge_graph.py:669` — `entities[:5]` cap |
| F4.13 | Bidirectional graph traversal | DONE | `search_graph()` queries both `source_id` and `destination_id` directions |
| F4.14 | REST API endpoints for entities and relations | DONE | `api/routes/graph.py` — overview, entity list, relation list endpoints |
| F4.15 | Vault export/import support for KG tables | **NOT DONE** | No references to `kg_entities`/`kg_relations` in any vault/export code. KG data would be lost on vault export/import. |
| F4.16 | `rerank_graph_results()` — BM25 reranking of graph results | DONE | `knowledge_graph.py:388-446` — BM25 tokenized reranking with `_mention_boost()` multiplier |
| F4.17 | `prune_stale_relations()` — LLM-driven pruning during ingestion | DONE | `knowledge_graph.py:736-822` — loads relations touching current entities, LLM decides which to delete, uses `prune_relations.md.j2` template |
| F4.18 | ID hallucination protection — sequential int mapping | DONE | `knowledge_graph.py:707-730` — `_map_ids_to_sequential()` + `_map_ids_back()`, used in `prune_stale_relations()` |
| F4.19 | Tool-calling fallback — JSON prompt when no tool support | DONE | `extract_entities_and_relations()` uses JSON prompt parsing directly (the default path, not a fallback) |
| F4.20 | Storage architecture constraint — SQLite only | DONE | All data in SQLite tables, no external graph DB |

### Acceptance Criteria Checklist

| AC | Criterion | Status |
|----|-----------|--------|
| AC1 | Entity "Alice" exists with mentions >= 2 after repeated conversations | COVERED — `upsert_entity()` increments mentions |
| AC2 | Relations correctly created | COVERED — `upsert_relation()` tested |
| AC3 | "Family" query traverses graph to find Alice | COVERED — `graph_context_for_query()` tested |
| AC4 | "NYC" / "New York City" deduplicated | **PARTIAL** — token Jaccard dedup catches some aliases, but no embedding-based dedup (F4.7 gap) |
| AC5 | 5-entity cap enforced | DONE — `entities[:5]` |
| AC6 | Graph traversal < 50ms for 1K entities | NOT MEASURED — no benchmark test |
| AC7 | `knowledge_graph` block appears when relevant | DONE — tested |
| AC8 | Block omitted when no relevant entities | DONE — returns `None` when no context |
| AC9 | Vault export includes KG tables | **NOT DONE** — F4.15 gap |
| AC10 | All existing tests pass | DONE — 846 tests passing |
| AC11 | "I left Google" prunes `works_at` relation | COVERED — `prune_stale_relations()` with LLM |
| AC12 | BM25-reranked graph results | DONE — `rerank_graph_results()` |
| AC13 | Sequential int IDs in LLM prompts | DONE — `_map_ids_to_sequential()` |

### Test Plan Checklist

| Test | Status | Evidence |
|------|--------|----------|
| T1: `normalize_entity_name()` | DONE | `test_knowledge_graph.py` (35 tests total) |
| T2: `upsert_entity()` mentions increment | DONE | tested |
| T3: `upsert_relation()` lookup | DONE | tested |
| T4: `search_graph()` depth=1 | DONE | tested |
| T5: `search_graph()` depth=2 | DONE | tested |
| T6: `search_graph()` bidirectional | DONE | tested |
| T7: `deduplicate_entity()` via embeddings | **NOT DONE** — no embedding dedup exists |
| T8: `extract_entities_and_relations()` | DONE | tested with mock LLM |
| T9: `ingest_conversation_graph()` | DONE | tested |
| T10: Prompt assembly with graph block | DONE | tested |
| T11: Vault export/import | **NOT DONE** — no vault support |
| T12: Existing blocks not broken | DONE | regression tests pass |
| T13: Performance benchmark | NOT DONE — no benchmark |
| T14: `rerank_graph_results()` | DONE | tested |
| T15: `prune_stale_relations()` | DONE | tested |
| T16: Stale relation pruning integration | DONE | tested |
| T17: ID mapping round-trip | DONE | tested |

### Beyond PRD (Implemented but not specified)

| Extra | Location | Notes |
|-------|----------|-------|
| `_find_similar_entity()` | `knowledge_graph.py:76` | Token-level Jaccard fuzzy matching + substring containment boost for entity dedup — used INSTEAD of embedding-based dedup. Simpler, no embedding cost. |
| `_mention_boost()` | `knowledge_graph.py:365` | Mention-count-based boost multiplier in BM25 reranking — higher-mention triples ranked higher |
| `_extract_entity_names_from_query()` | `knowledge_graph.py:498` | Substring matching of known entity names against query — simple NER alternative |
| REST API routes | `api/routes/graph.py` | Overview, entity list, relation list endpoints (PRD marked this as "Could") |

### Summary

**Status: SHIPPED (~93%)**

20 requirements, 18 implemented. 35 dedicated tests. Full pipeline integrated into sleep-time orchestrator with LLM extraction, dedup, upsert, traversal, reranking, pruning, and prompt injection.

**Remaining items:**
1. **F4.7**: Entity dedup via embedding similarity not implemented. Uses token-level Jaccard instead (`_find_similar_entity()` with threshold 0.7). This misses cases like "NYC" ↔ "New York City" where tokens don't overlap. `embedding_json` column exists on `KGEntity` but is never populated. Could be added as an enhancement to `_find_similar_entity()`.
2. **F4.15**: Vault export/import does not include `kg_entities` / `kg_relations`. KG data would be lost on vault transfer. Needs to be added to the vault export handler.
3. **AC6/T13**: No performance benchmark for graph traversal latency.

---

## 14. References

- Mem0 `graph_memory.py` — `MemoryGraph.add()`, `EXTRACT_ENTITIES_TOOL`, entity dedup via embedding similarity
- Mem0 `graph_memory.py` lines 117-130 — BM25 reranking of graph search results
- Mem0 `graph_memory.py` `_get_delete_entities_from_search_output()` — LLM-driven relation pruning on each add
- Mem0 `main.py` lines 496-499 — UUID-to-integer ID mapping to prevent LLM hallucination
- Mem0 — 26% accuracy improvement with graph-augmented vector search
- Research Report Section 1.5, Finding C2 — Knowledge Graph rated "Critical"
- [Implementation Plan](../../architecture/memory/memory-implementation-plan.md) — detailed SQLAlchemy model definitions and function signatures
- [Competitor Audit: Letta & Mem0](competitor-audit-letta-mem0.md) — source-code-level analysis informing F4.16-F4.18
- [Whitepaper](../../thesis/whitepaper.md) — Core portability, user ownership, and encrypted local-first constraints
- [Memory System Deep Dive](../../architecture/memory/memory-system.md) — SQLite-centered memory lifecycle and prompt-assembly context
