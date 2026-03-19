"""Knowledge graph service — F4.

SQLite-backed entity-relationship graph extracted from conversations.
Entities (people, places, orgs, projects, concepts) and typed relations
are stored in kg_entities / kg_relations tables. Graph traversal uses
SQL JOINs (max depth 2) for relational memory retrieval.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, or_, select, func as sa_func
from sqlalchemy.orm import Session

from anima_server.config import settings
from anima_server.models import KGEntity, KGRelation

logger = logging.getLogger(__name__)

# ── Entity name normalization ────────────────────────────────────────

_NORMALIZE_RE = re.compile(r"[^a-z0-9]+")


def normalize_entity_name(name: str) -> str:
    """Normalize entity name for dedup key.

    'New York City' -> 'new_york_city'
    'Dr. Alice Smith' -> 'dr._alice_smith'
    """
    # Lowercase, replace whitespace with underscore, keep periods
    lowered = name.lower().strip()
    # Replace spaces with underscores
    result = lowered.replace(" ", "_")
    # Collapse multiple underscores
    result = re.sub(r"_+", "_", result)
    return result.strip("_")


# ── Entity / Relation CRUD ───────────────────────────────────────────

def upsert_entity(
    db: Session,
    *,
    user_id: int,
    name: str,
    entity_type: str = "unknown",
    description: str = "",
) -> KGEntity:
    """Create or update an entity. Increments mentions on existing match."""
    normalized = normalize_entity_name(name)
    existing = db.scalar(
        select(KGEntity).where(
            KGEntity.user_id == user_id,
            KGEntity.name_normalized == normalized,
        )
    )
    if existing is not None:
        existing.mentions = (existing.mentions or 1) + 1
        existing.updated_at = datetime.now(UTC)
        if description and (not existing.description or len(description) > len(existing.description)):
            existing.description = description
        if entity_type != "unknown" and existing.entity_type == "unknown":
            existing.entity_type = entity_type
        db.flush()
        return existing

    entity = KGEntity(
        user_id=user_id,
        name=name.strip(),
        name_normalized=normalized,
        entity_type=entity_type,
        description=description,
        mentions=1,
    )
    db.add(entity)
    db.flush()
    return entity


def upsert_relation(
    db: Session,
    *,
    user_id: int,
    source_name: str,
    destination_name: str,
    relation_type: str,
    source_memory_id: int | None = None,
) -> KGRelation | None:
    """Create or update a relation between two entities.

    Entities must already exist (looked up by normalized name).
    Increments mentions on existing match.
    """
    src_norm = normalize_entity_name(source_name)
    dst_norm = normalize_entity_name(destination_name)

    source = db.scalar(
        select(KGEntity).where(
            KGEntity.user_id == user_id,
            KGEntity.name_normalized == src_norm,
        )
    )
    dest = db.scalar(
        select(KGEntity).where(
            KGEntity.user_id == user_id,
            KGEntity.name_normalized == dst_norm,
        )
    )
    if source is None or dest is None:
        logger.debug(
            "Cannot create relation: source=%s(%s) dest=%s(%s)",
            source_name, source is not None, destination_name, dest is not None,
        )
        return None

    # Check for existing relation
    existing = db.scalar(
        select(KGRelation).where(
            KGRelation.user_id == user_id,
            KGRelation.source_id == source.id,
            KGRelation.destination_id == dest.id,
            KGRelation.relation_type == relation_type,
        )
    )
    if existing is not None:
        existing.mentions = (existing.mentions or 1) + 1
        existing.updated_at = datetime.now(UTC)
        if source_memory_id is not None:
            existing.source_memory_id = source_memory_id
        db.flush()
        return existing

    relation = KGRelation(
        user_id=user_id,
        source_id=source.id,
        destination_id=dest.id,
        relation_type=relation_type,
        mentions=1,
        source_memory_id=source_memory_id,
    )
    db.add(relation)
    db.flush()
    return relation


# ── Graph traversal ──────────────────────────────────────────────────

def search_graph(
    db: Session,
    *,
    user_id: int,
    entity_names: list[str],
    max_depth: int = 2,
    limit: int = 20,
) -> list[dict[str, str]]:
    """Traverse graph from given entities via SQL JOINs.

    Bidirectional traversal at each depth level.
    Returns [{"source": ..., "relation": ..., "destination": ...,
              "source_type": ..., "destination_type": ...}, ...]
    """
    # Resolve starting entity IDs
    normalized_names = [normalize_entity_name(n) for n in entity_names]
    start_entities = list(db.scalars(
        select(KGEntity).where(
            KGEntity.user_id == user_id,
            KGEntity.name_normalized.in_(normalized_names),
        )
    ).all())

    if not start_entities:
        return []

    entity_ids = {e.id for e in start_entities}
    # Cache entity info by ID
    entity_cache: dict[int, KGEntity] = {e.id: e for e in start_entities}
    results: list[dict[str, str]] = []
    seen_triples: set[tuple[int, str, int]] = set()

    for _depth in range(max_depth):
        if not entity_ids:
            break

        # Fetch all relations touching current entity IDs (bidirectional)
        relations = list(db.scalars(
            select(KGRelation).where(
                KGRelation.user_id == user_id,
                or_(
                    KGRelation.source_id.in_(entity_ids),
                    KGRelation.destination_id.in_(entity_ids),
                ),
            )
        ).all())

        next_ids: set[int] = set()
        new_entity_ids: set[int] = set()

        for rel in relations:
            triple_key = (rel.source_id, rel.relation_type, rel.destination_id)
            if triple_key in seen_triples:
                continue
            seen_triples.add(triple_key)

            # Cache entities we haven't seen yet
            for eid in (rel.source_id, rel.destination_id):
                if eid not in entity_cache:
                    new_entity_ids.add(eid)

            next_ids.add(rel.source_id)
            next_ids.add(rel.destination_id)

        # Bulk-fetch new entities
        if new_entity_ids:
            new_entities = list(db.scalars(
                select(KGEntity).where(KGEntity.id.in_(new_entity_ids))
            ).all())
            for e in new_entities:
                entity_cache[e.id] = e

        # Build result triples
        for rel in relations:
            triple_key = (rel.source_id, rel.relation_type, rel.destination_id)
            src = entity_cache.get(rel.source_id)
            dst = entity_cache.get(rel.destination_id)
            if src is None or dst is None:
                continue
            result_entry = {
                "source": src.name,
                "relation": rel.relation_type,
                "destination": dst.name,
                "source_type": src.entity_type,
                "destination_type": dst.entity_type,
            }
            if result_entry not in results:
                results.append(result_entry)

        # Expand frontier for next depth
        entity_ids = next_ids - entity_ids  # only new IDs

        if len(results) >= limit:
            break

    return results[:limit]


# ── BM25 reranking ───────────────────────────────────────────────────

def rerank_graph_results(
    results: list[dict[str, str]],
    query: str,
    top_n: int = 10,
) -> list[dict[str, str]]:
    """BM25-rerank graph traversal results for query relevance.

    Tokenizes each triple as 'source relation destination', scores against query.
    """
    if not results or not query.strip():
        return results[:top_n]

    try:
        from rank_bm25 import BM25Okapi
    except ImportError:
        logger.debug("rank_bm25 not available, returning unranked results")
        return results[:top_n]

    # Tokenize each triple as a document
    documents: list[list[str]] = []
    for r in results:
        doc_text = f"{r['source']} {r['relation']} {r['destination']}"
        desc_parts = []
        if r.get("source_type"):
            desc_parts.append(r["source_type"])
        if r.get("destination_type"):
            desc_parts.append(r["destination_type"])
        if desc_parts:
            doc_text += " " + " ".join(desc_parts)
        documents.append(doc_text.lower().split())

    query_tokens = query.lower().split()
    if not query_tokens or not documents:
        return results[:top_n]

    bm25 = BM25Okapi(documents)
    scores = bm25.get_scores(query_tokens)

    scored = sorted(
        zip(results, scores),
        key=lambda x: x[1],
        reverse=True,
    )
    return [r for r, _ in scored[:top_n]]


# ── Query-to-graph context ───────────────────────────────────────────

def graph_context_for_query(
    db: Session,
    *,
    user_id: int,
    query: str,
    limit: int = 10,
) -> list[str]:
    """Extract entity names from query, traverse graph, BM25-rerank,
    return formatted context strings for the knowledge_graph memory block.

    Output: ["Alice (person, User's sister) -> lives_in -> Munich", ...]
    """
    entity_names = _extract_entity_names_from_query(db, user_id=user_id, query=query)
    if not entity_names:
        return []

    raw_results = search_graph(
        db, user_id=user_id, entity_names=entity_names, max_depth=2, limit=20,
    )
    if not raw_results:
        return []

    ranked = rerank_graph_results(raw_results, query, top_n=limit)

    lines: list[str] = []
    for r in ranked:
        src_desc = f" ({r['source_type']})" if r.get("source_type") and r["source_type"] != "unknown" else ""
        dst_desc = f" ({r['destination_type']})" if r.get("destination_type") and r["destination_type"] != "unknown" else ""
        lines.append(
            f"{r['source']}{src_desc} -> {r['relation']} -> {r['destination']}{dst_desc}"
        )

    return lines


def _extract_entity_names_from_query(
    db: Session,
    *,
    user_id: int,
    query: str,
) -> list[str]:
    """Find entity names from the query by matching against known entities.

    Simple approach: tokenize the query, check if any known entity names
    appear as substrings. More sophisticated approaches (NER) can be added later.
    """
    query_lower = query.lower()

    # Fetch all entity names for this user (personal-scale, <1000 entities)
    entities = list(db.scalars(
        select(KGEntity).where(KGEntity.user_id == user_id)
    ).all())

    matched: list[str] = []
    for entity in entities:
        if entity.name.lower() in query_lower:
            matched.append(entity.name)

    return matched


# ── LLM extraction ──────────────────────────────────────────────────

EXTRACT_ENTITIES_PROMPT = """You are a knowledge graph extraction system for a personal AI companion.
Given a conversation turn, extract entities (people, places, organizations, projects, concepts) and relationships between them.

Return a JSON object with two fields:

"entities": array of objects with:
- "name": string (the entity name as mentioned)
- "type": one of "person", "place", "organization", "project", "concept"
- "description": string (brief description, optional)

"relations": array of objects with:
- "source": string (source entity name, must match an entity name above)
- "relation": string (relationship type, e.g. "works_at", "sister_of", "lives_in")
- "destination": string (destination entity name, must match an entity name above)

Rules:
- Extract at most 5 entities
- Only extract entities and relations explicitly stated or clearly implied
- Use consistent relation type naming: works_at, lives_in, sister_of, brother_of, parent_of, married_to, friend_of, colleague_of, related_to_project, interested_in, member_of, located_in, part_of, created_by
- Do not fabricate relationships not supported by the text
- Return empty arrays if nothing to extract

User message:
{user_message}

Assistant response:
{assistant_response}"""

PRUNE_RELATIONS_PROMPT = """You are evaluating whether existing knowledge graph relations are still valid given new information from a conversation.

Existing relations (use the ID numbers to refer to them):
{existing_relations}

New information from conversation:
{new_facts}

Which of the existing relations are now outdated, contradicted, or no longer valid based on the new information?

Return a JSON object with:
"delete_ids": array of integer IDs of relations to delete

If all relations are still valid, return: {{"delete_ids": []}}"""

EXTRACT_ENTITIES_TOOL = {
    "type": "function",
    "function": {
        "name": "extract_entities_and_relations",
        "description": "Extract entities and relationships from conversation text",
        "parameters": {
            "type": "object",
            "properties": {
                "entities": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "type": {
                                "type": "string",
                                "enum": ["person", "place", "organization", "project", "concept"],
                            },
                            "description": {"type": "string"},
                        },
                        "required": ["name", "type"],
                    },
                    "maxItems": 5,
                },
                "relations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "source": {"type": "string"},
                            "relation": {"type": "string"},
                            "destination": {"type": "string"},
                        },
                        "required": ["source", "relation", "destination"],
                    },
                },
            },
            "required": ["entities", "relations"],
        },
    },
}


async def extract_entities_and_relations(
    *,
    text: str,
    user_id: int,
    user_message: str = "",
    assistant_response: str = "",
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Extract entities and relations from text using LLM.

    Uses JSON prompt with response parsing (consistent with consolidation.py pattern).
    Cap: max 5 entities per call.

    Returns (entities, relations).
    """
    if settings.agent_provider == "scaffold":
        return [], []

    # Use user_message/assistant_response if provided, else use text
    msg = user_message or text
    resp = assistant_response or ""

    try:
        from anima_server.services.agent.messages import HumanMessage, SystemMessage
        from anima_server.services.agent.llm import create_llm

        llm = create_llm()
        prompt = EXTRACT_ENTITIES_PROMPT.format(
            user_message=msg,
            assistant_response=resp,
        )
        response = await llm.ainvoke([
            SystemMessage(
                content="You extract entities and relationships. Respond only with JSON.",
            ),
            HumanMessage(content=prompt),
        ])
        content = getattr(response, "content", "")
        if not isinstance(content, str):
            content = str(content)

        parsed = _parse_json_object(content)
        if parsed is None:
            return [], []

        entities = parsed.get("entities", [])
        relations = parsed.get("relations", [])

        if not isinstance(entities, list):
            entities = []
        if not isinstance(relations, list):
            relations = []

        # Validate and cap entities at 5
        valid_entities: list[dict[str, str]] = []
        for e in entities[:5]:
            if isinstance(e, dict) and e.get("name") and e.get("type"):
                valid_entities.append({
                    "name": str(e["name"]),
                    "type": str(e["type"]),
                    "description": str(e.get("description", "")),
                })

        # Validate relations
        entity_names = {e["name"].lower() for e in valid_entities}
        valid_relations: list[dict[str, str]] = []
        for r in relations:
            if (
                isinstance(r, dict)
                and r.get("source")
                and r.get("relation")
                and r.get("destination")
            ):
                valid_relations.append({
                    "source": str(r["source"]),
                    "relation": str(r["relation"]),
                    "destination": str(r["destination"]),
                })

        return valid_entities, valid_relations

    except Exception:  # noqa: BLE001
        logger.exception("LLM entity extraction failed")
        return [], []


# ── ID hallucination protection ──────────────────────────────────────

def _map_ids_to_sequential(
    items: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[int, int]]:
    """Map real entity/relation IDs to sequential integers for LLM prompts.

    Returns (mapped_items, reverse_map) where reverse_map[sequential] = real_id.
    """
    reverse_map: dict[int, int] = {}
    mapped: list[dict[str, Any]] = []
    for idx, item in enumerate(items, start=1):
        real_id = item.get("id", idx)
        reverse_map[idx] = real_id
        mapped_item = dict(item)
        mapped_item["id"] = idx
        mapped.append(mapped_item)
    return mapped, reverse_map


def _map_ids_back(
    sequential_ids: list[int],
    reverse_map: dict[int, int],
) -> list[int]:
    """Map sequential IDs back to real IDs."""
    return [reverse_map[sid] for sid in sequential_ids if sid in reverse_map]


# ── Stale relation pruning ───────────────────────────────────────────

async def prune_stale_relations(
    db: Session,
    *,
    user_id: int,
    new_facts: list[str],
    existing_relations: list[dict[str, Any]],
) -> list[int]:
    """LLM-driven relation pruning.

    Given new facts from the current conversation and existing relations
    touching the same entities, ask the LLM which relations are now
    outdated or contradicted.

    Uses ID hallucination protection: maps real IDs to sequential integers.

    Returns list of kg_relations.id that were deleted.
    """
    if not existing_relations or not new_facts:
        return []

    if settings.agent_provider == "scaffold":
        return []

    # Map IDs for LLM safety
    mapped_relations, reverse_map = _map_ids_to_sequential(existing_relations)

    # Format relations for the prompt
    rel_lines = []
    for r in mapped_relations:
        rel_lines.append(
            f"  ID {r['id']}: {r['source']} -> {r['relation']} -> {r['destination']}"
        )
    rel_text = "\n".join(rel_lines)
    facts_text = "\n".join(f"- {f}" for f in new_facts)

    try:
        from anima_server.services.agent.messages import HumanMessage, SystemMessage
        from anima_server.services.agent.llm import create_llm

        llm = create_llm()
        prompt = PRUNE_RELATIONS_PROMPT.format(
            existing_relations=rel_text,
            new_facts=facts_text,
        )
        response = await llm.ainvoke([
            SystemMessage(content="You evaluate knowledge graph relations. Respond only with JSON."),
            HumanMessage(content=prompt),
        ])
        content = getattr(response, "content", "")
        if not isinstance(content, str):
            content = str(content)

        parsed = _parse_json_object(content)
        if parsed is None:
            return []

        delete_seq_ids = parsed.get("delete_ids", [])
        if not isinstance(delete_seq_ids, list):
            return []

        # Map back to real IDs
        real_ids = _map_ids_back(
            [int(x) for x in delete_seq_ids if isinstance(x, (int, float))],
            reverse_map,
        )

        # Delete the relations
        if real_ids:
            for rel_id in real_ids:
                rel = db.get(KGRelation, rel_id)
                if rel is not None and rel.user_id == user_id:
                    db.delete(rel)
            db.flush()

        return real_ids

    except Exception:  # noqa: BLE001
        logger.exception("LLM relation pruning failed")
        return []


# ── Full ingestion pipeline ──────────────────────────────────────────

async def ingest_conversation_graph(
    db: Session,
    *,
    user_id: int,
    user_message: str,
    assistant_response: str,
) -> tuple[int, int, int]:
    """Full pipeline for graph_ingestion background task.

    extract -> dedup -> upsert entities + relations -> prune stale relations.
    Returns (entities_upserted, relations_upserted, relations_pruned).
    """
    # 1. Extract entities and relations via LLM
    entities, relations = await extract_entities_and_relations(
        text=f"{user_message}\n{assistant_response}",
        user_id=user_id,
        user_message=user_message,
        assistant_response=assistant_response,
    )

    if not entities and not relations:
        return 0, 0, 0

    # 2. Upsert entities
    entities_upserted = 0
    for entity_data in entities:
        try:
            upsert_entity(
                db,
                user_id=user_id,
                name=entity_data["name"],
                entity_type=entity_data.get("type", "unknown"),
                description=entity_data.get("description", ""),
            )
            entities_upserted += 1
        except Exception:  # noqa: BLE001
            logger.debug("Failed to upsert entity: %s", entity_data.get("name"))

    # 3. Upsert relations
    relations_upserted = 0
    for rel_data in relations:
        try:
            result = upsert_relation(
                db,
                user_id=user_id,
                source_name=rel_data["source"],
                destination_name=rel_data["destination"],
                relation_type=rel_data["relation"],
            )
            if result is not None:
                relations_upserted += 1
        except Exception:  # noqa: BLE001
            logger.debug("Failed to upsert relation: %s", rel_data)

    # 4. Prune stale relations touching this turn's entities
    entity_names = [e["name"] for e in entities]
    normalized_names = [normalize_entity_name(n) for n in entity_names]

    # Find entity IDs for this turn
    turn_entities = list(db.scalars(
        select(KGEntity).where(
            KGEntity.user_id == user_id,
            KGEntity.name_normalized.in_(normalized_names),
        )
    ).all())
    turn_entity_ids = {e.id for e in turn_entities}

    if turn_entity_ids:
        # Load existing relations touching these entities
        existing_rels = list(db.scalars(
            select(KGRelation).where(
                KGRelation.user_id == user_id,
                or_(
                    KGRelation.source_id.in_(turn_entity_ids),
                    KGRelation.destination_id.in_(turn_entity_ids),
                ),
            )
        ).all())

        if existing_rels:
            # Build relation dicts for pruning with entity names
            entity_map = {e.id: e for e in turn_entities}
            # Also fetch any entities we don't have yet
            all_entity_ids = set()
            for r in existing_rels:
                all_entity_ids.add(r.source_id)
                all_entity_ids.add(r.destination_id)
            missing_ids = all_entity_ids - set(entity_map.keys())
            if missing_ids:
                extra = list(db.scalars(
                    select(KGEntity).where(KGEntity.id.in_(missing_ids))
                ).all())
                for e in extra:
                    entity_map[e.id] = e

            rel_dicts = []
            for r in existing_rels:
                src = entity_map.get(r.source_id)
                dst = entity_map.get(r.destination_id)
                if src and dst:
                    rel_dicts.append({
                        "id": r.id,
                        "source": src.name,
                        "relation": r.relation_type,
                        "destination": dst.name,
                    })

            new_facts = [user_message, assistant_response]
            pruned_ids = await prune_stale_relations(
                db,
                user_id=user_id,
                new_facts=new_facts,
                existing_relations=rel_dicts,
            )
            relations_pruned = len(pruned_ids)
        else:
            relations_pruned = 0
    else:
        relations_pruned = 0

    db.flush()
    return entities_upserted, relations_upserted, relations_pruned


# ── JSON parsing helpers ─────────────────────────────────────────────

def _parse_json_object(text: str) -> dict[str, Any] | None:
    """Extract a JSON object from LLM response text."""
    text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        parsed = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed
