"""Knowledge graph service — F4.

SQLite-backed entity-relationship graph extracted from conversations.
Entities (people, places, orgs, projects, concepts) and typed relations
are stored in kg_entities / kg_relations tables. Graph traversal uses
SQL JOINs (max depth 2) for relational memory retrieval.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime
from threading import Thread
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from anima_server.config import settings
from anima_server.models import KGEntity, KGRelation
from anima_server.services.agent.embedding_integrity import (
    check_embedding,
    compute_embedding_checksum,
    parse_embedding_payload,
)
from anima_server.services.agent.embeddings import (
    cosine_similarity,
    generate_embedding,
    generate_embeddings_batch,
)
from anima_server.services.agent.graph_triplets import extract_triplets as extract_rule_triplets
from anima_server.services.agent.text_processing import prepare_embedding_text

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


# ── Token-level entity similarity ────────────────────────────────────

_ABBREV_MAP: dict[str, str] = {}  # extensible later if needed
_SEMANTIC_ENTITY_LIMIT = 3
_SEMANTIC_ENTITY_THRESHOLD = 0.5

_TOKEN_SPLIT_RE = re.compile(r"[^a-z0-9]+")


def _tokenize_name(name: str) -> set[str]:
    """Split a name into lowercase alphanumeric tokens."""
    return {t for t in _TOKEN_SPLIT_RE.split(name.lower()) if t}


def _jaccard(a: set[str], b: set[str]) -> float:
    """Jaccard similarity between two token sets."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _substring_containment(short: str, long: str) -> bool:
    """Check if the shorter name is wholly contained in the longer one (case-insensitive).

    Only triggers when the shorter side has >= 3 characters (avoids matching
    trivial substrings like "AI" inside "Maine").
    """
    s = short.lower().strip()
    lng = long.lower().strip()
    if min(len(s), len(lng)) < 3:
        return False
    return s in lng or lng in s


def _find_similar_entity(
    db: Session,
    user_id: int,
    name: str,
    entity_type: str = "unknown",
    threshold: float = 0.7,
) -> KGEntity | None:
    """Find the best fuzzy match among existing entities for *name*.

    Uses token-level Jaccard similarity. If the best match scores above
    *threshold*, return that entity; otherwise return None.  Only
    considers entities with a compatible type (same type, or either
    side is ``unknown``).

    Also considers substring containment as a fallback signal — if one
    name is fully contained inside the other and the Jaccard score is
    above a lower threshold (0.5), the match is accepted.
    """
    entities = list(db.scalars(select(KGEntity).where(KGEntity.user_id == user_id)).all())
    if not entities:
        return None

    new_tokens = _tokenize_name(name)
    if not new_tokens:
        return None

    best_entity: KGEntity | None = None
    best_score: float = 0.0

    for entity in entities:
        # Only match compatible types (same type or either is unknown)
        if (
            entity_type != "unknown"
            and entity.entity_type != "unknown"
            and entity.entity_type != entity_type
        ):
            continue
        existing_tokens = _tokenize_name(entity.name)
        score = _jaccard(new_tokens, existing_tokens)

        # Boost for substring containment (catches "New York" vs "New York City"
        # and other partial-overlap cases).
        if score >= 0.5 and _substring_containment(name, entity.name):
            score = max(score, threshold)  # promote to threshold

        if score > best_score:
            best_score = score
            best_entity = entity

    if best_score >= threshold and best_entity is not None:
        logger.debug(
            "Fuzzy entity match: '%s' -> '%s' (score=%.2f)",
            name,
            best_entity.name,
            best_score,
        )
        return best_entity

    return None


def _entity_embedding_text(
    *,
    name: str,
    entity_type: str,
    description: str,
) -> str:
    parts = [name.strip()]
    normalized_type = _normalize_graph_entity_type(entity_type)
    if normalized_type != "unknown":
        parts.append(normalized_type)
    if description.strip():
        parts.append(description.strip())
    return prepare_embedding_text(". ".join(part for part in parts if part), limit=512)


async def _attach_entity_embeddings(entities: list[dict[str, Any]]) -> None:
    texts: list[str] = []
    entity_indexes: list[int] = []

    for index, entity in enumerate(entities):
        text = _entity_embedding_text(
            name=str(entity.get("name", "")),
            entity_type=str(entity.get("type", "unknown")),
            description=str(entity.get("description", "")),
        )
        if not text:
            continue
        texts.append(text)
        entity_indexes.append(index)

    if not texts:
        return

    try:
        embeddings = await generate_embeddings_batch(texts)
    except Exception:
        logger.debug("Failed to generate entity embeddings for graph ingestion", exc_info=True)
        return

    for index, embedding in zip(entity_indexes, embeddings, strict=False):
        if embedding is not None:
            entities[index]["embedding"] = embedding


def _run_async_blocking(coro: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result: dict[str, Any] = {}
    error: dict[str, BaseException] = {}

    def _runner() -> None:
        try:
            result["value"] = asyncio.run(coro)
        except BaseException as exc:  # pragma: no cover - propagated to caller
            error["value"] = exc

    thread = Thread(target=_runner, daemon=True)
    thread.start()
    thread.join()

    if "value" in error:
        raise error["value"]
    return result.get("value")


def _generate_query_embedding_sync(query: str) -> list[float] | None:
    prepared_query = prepare_embedding_text(query, limit=512)
    if not prepared_query:
        return None

    try:
        return _run_async_blocking(generate_embedding(prepared_query))
    except Exception:
        logger.debug("Semantic graph query embedding generation failed", exc_info=True)
        return None


def _validated_entity_embedding(entity: KGEntity) -> tuple[list[float] | None, bool]:
    checked = check_embedding(entity.embedding_json, entity.embedding_checksum)

    if checked.status == "missing_checksum" and checked.actual_checksum is not None:
        entity.embedding_checksum = checked.actual_checksum
        return checked.embedding, True

    if checked.status == "checksum_mismatch":
        logger.warning("Skipping KG entity %s due to embedding checksum mismatch", entity.id)
        return None, False

    if checked.status == "invalid":
        return None, False

    return checked.embedding, False


def _semantic_entity_names_from_query(
    db: Session,
    *,
    user_id: int,
    query: str,
    limit: int = _SEMANTIC_ENTITY_LIMIT,
    similarity_threshold: float = _SEMANTIC_ENTITY_THRESHOLD,
) -> list[str]:
    query_embedding = _generate_query_embedding_sync(query)
    if query_embedding is None:
        return []

    entities = list(db.scalars(select(KGEntity).where(KGEntity.user_id == user_id)).all())
    if not entities:
        return []

    repaired_any = False
    scored: list[tuple[float, int, str]] = []
    for entity in entities:
        entity_embedding, repaired = _validated_entity_embedding(entity)
        repaired_any = repaired_any or repaired
        if entity_embedding is None:
            continue

        similarity = cosine_similarity(query_embedding, entity_embedding)
        if similarity < similarity_threshold:
            continue
        scored.append((similarity, entity.mentions or 1, entity.name))

    if repaired_any:
        db.flush()

    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    matches: list[str] = []
    seen: set[str] = set()
    for _similarity, _mentions, name in scored:
        if name in seen:
            continue
        seen.add(name)
        matches.append(name)
        if len(matches) >= limit:
            break
    return matches


# ── Entity / Relation CRUD ───────────────────────────────────────────


def upsert_entity(
    db: Session,
    *,
    user_id: int,
    name: str,
    entity_type: str = "unknown",
    description: str = "",
    embedding: list[float] | None = None,
) -> KGEntity:
    """Create or update an entity. Increments mentions on existing match."""
    normalized = normalize_entity_name(name)
    parsed_embedding = None
    if embedding is not None:
        parsed_embedding = parse_embedding_payload(embedding)
        if parsed_embedding is None:
            raise ValueError("KG entity embedding must be a non-empty sequence of finite numbers.")

    existing = db.scalar(
        select(KGEntity).where(
            KGEntity.user_id == user_id,
            KGEntity.name_normalized == normalized,
        )
    )
    # Fuzzy match: if no exact normalized match, look for token-similar names
    if existing is None:
        existing = _find_similar_entity(db, user_id, name, entity_type=entity_type)

    if existing is not None:
        existing.mentions = (existing.mentions or 1) + 1
        existing.updated_at = datetime.now(UTC)
        if description and (
            not existing.description or len(description) > len(existing.description)
        ):
            existing.description = description
        if entity_type != "unknown" and existing.entity_type == "unknown":
            existing.entity_type = entity_type
        if parsed_embedding is not None:
            existing.embedding_json = parsed_embedding
            existing.embedding_checksum = compute_embedding_checksum(parsed_embedding)
        db.flush()
        return existing

    entity = KGEntity(
        user_id=user_id,
        name=name.strip(),
        name_normalized=normalized,
        entity_type=entity_type,
        description=description,
        mentions=1,
        embedding_json=parsed_embedding,
        embedding_checksum=(
            compute_embedding_checksum(parsed_embedding) if parsed_embedding is not None else None
        ),
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
            source_name,
            source is not None,
            destination_name,
            dest is not None,
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
    start_entities = list(
        db.scalars(
            select(KGEntity).where(
                KGEntity.user_id == user_id,
                KGEntity.name_normalized.in_(normalized_names),
            )
        ).all()
    )

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
        relations = list(
            db.scalars(
                select(KGRelation).where(
                    KGRelation.user_id == user_id,
                    or_(
                        KGRelation.source_id.in_(entity_ids),
                        KGRelation.destination_id.in_(entity_ids),
                    ),
                )
            ).all()
        )

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
            new_entities = list(
                db.scalars(select(KGEntity).where(KGEntity.id.in_(new_entity_ids))).all()
            )
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
                "source_mentions": src.mentions or 1,
                "destination_mentions": dst.mentions or 1,
                "relation_mentions": rel.mentions or 1,
            }
            if result_entry not in results:
                results.append(result_entry)

        # Expand frontier for next depth
        entity_ids = next_ids - entity_ids  # only new IDs

        if len(results) >= limit:
            break

    return results[:limit]


# ── BM25 reranking ───────────────────────────────────────────────────


def _mention_boost(result: dict[str, Any]) -> float:
    """Compute a logarithmic mention boost for a graph result triple.

    The boost is based on the combined mention counts of the source entity,
    destination entity, and the relation itself.  Uses log2 scaling so
    that mention counts provide a gentle signal rather than dominating
    the BM25 text-relevance score.

    A triple where every component has mentions=1 gets boost=1.0 (neutral).
    A triple with 4 combined mentions beyond the baseline gets boost ~1.15.
    """
    import math

    src_m = result.get("source_mentions", 1) or 1
    dst_m = result.get("destination_mentions", 1) or 1
    rel_m = result.get("relation_mentions", 1) or 1
    # Sum of extra mentions beyond baseline (3 = one per component)
    extra = (src_m - 1) + (dst_m - 1) + (rel_m - 1)
    # log2(extra + 1): 0.0 for extra=1, ~1.58 for extra=2, ~2.32 for extra=4
    # Scaled to a modest multiplier range: 1.0 .. ~1.3
    return 1.0 + 0.1 * math.log2(extra + 1) if extra > 0 else 1.0


def rerank_graph_results(
    results: list[dict[str, Any]],
    query: str,
    top_n: int = 10,
) -> list[dict[str, Any]]:
    """BM25-rerank graph traversal results for query relevance.

    Tokenizes each triple as 'source relation destination', scores against query.
    Applies a logarithmic mention-count boost so frequently-referenced
    entities and relations are ranked slightly higher.
    """
    if not results or not query.strip():
        return results[:top_n]

    try:
        from rank_bm25 import BM25Okapi
    except ImportError:
        logger.debug("rank_bm25 not available, sorting by mention boost only")
        # Fall back to mention-only ordering
        scored = sorted(
            results,
            key=lambda r: _mention_boost(r),
            reverse=True,
        )
        return scored[:top_n]

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

    # Apply mention boost to BM25 scores
    boosted_scores = [score * _mention_boost(r) for r, score in zip(results, scores, strict=False)]

    scored = sorted(
        zip(results, boosted_scores, strict=False),
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
        db,
        user_id=user_id,
        entity_names=entity_names,
        max_depth=2,
        limit=20,
    )
    if not raw_results:
        return []

    ranked = rerank_graph_results(raw_results, query, top_n=limit)

    lines: list[str] = []
    for r in ranked:
        src_desc = (
            f" ({r['source_type']})"
            if r.get("source_type") and r["source_type"] != "unknown"
            else ""
        )
        dst_desc = (
            f" ({r['destination_type']})"
            if r.get("destination_type") and r["destination_type"] != "unknown"
            else ""
        )
        line = f"{r['source']}{src_desc} -> {r['relation']} -> {r['destination']}{dst_desc}"
        # Annotate frequently-mentioned triples so the LLM knows they are well-established
        rel_m = r.get("relation_mentions", 1) or 1
        if rel_m >= 3:
            line += f" [mentioned {rel_m}x]"
        lines.append(line)

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
    entities = list(db.scalars(select(KGEntity).where(KGEntity.user_id == user_id)).all())

    matched: list[str] = []
    for entity in entities:
        if entity.name.lower() in query_lower:
            matched.append(entity.name)

    if matched:
        return matched

    return _semantic_entity_names_from_query(db, user_id=user_id, query=query)


def _normalize_graph_entity_type(entity_type: str) -> str:
    normalized = entity_type.strip().lower()
    if normalized in {"", "unknown"}:
        return "unknown"
    if normalized in {"location", "loc", "gpe"}:
        return "place"
    if normalized in {"organization", "org", "company"}:
        return "organization"
    if normalized in {"person", "place", "project", "concept"}:
        return normalized
    if normalized == "other":
        return "concept"
    return normalized


def _select_rule_extraction_text(
    *,
    text: str,
    user_message: str,
    assistant_response: str,
) -> str:
    # Deterministic extraction is intentionally user-first so assistant
    # phrasing does not create graph edges on its own in scaffold mode.
    for candidate in (user_message, text, assistant_response):
        if isinstance(candidate, str) and candidate.strip():
            return candidate
    return ""


def _triplets_to_entities_and_relations(
    triplets: list[dict[str, Any]],
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    entities_by_key: dict[str, dict[str, str]] = {}
    relations_by_key: dict[tuple[str, str, str], dict[str, str]] = {}

    for triplet in triplets:
        source = str(triplet.get("subject", "")).strip()
        destination = str(triplet.get("object", "")).strip()
        relation = str(triplet.get("predicate", "")).strip().lower()
        if not source or not destination or not relation:
            continue

        source_type = _normalize_graph_entity_type(str(triplet.get("subject_type", "unknown")))
        destination_type = _normalize_graph_entity_type(
            str(triplet.get("object_type", "unknown"))
        )

        for name, entity_type in ((source, source_type), (destination, destination_type)):
            key = normalize_entity_name(name)
            existing = entities_by_key.get(key)
            if existing is None:
                entities_by_key[key] = {
                    "name": name,
                    "type": entity_type,
                    "description": "",
                }
            elif existing["type"] == "unknown" and entity_type != "unknown":
                existing["type"] = entity_type

        relation_key = (
            normalize_entity_name(source),
            relation,
            normalize_entity_name(destination),
        )
        relations_by_key.setdefault(
            relation_key,
            {
                "source": source,
                "relation": relation,
                "destination": destination,
            },
        )

    return list(entities_by_key.values()), list(relations_by_key.values())


def _merge_graph_extractions(
    *groups: tuple[list[dict[str, str]], list[dict[str, str]]],
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    entities_by_key: dict[str, dict[str, str]] = {}
    relations_by_key: dict[tuple[str, str, str], dict[str, str]] = {}

    for entities, relations in groups:
        for entity in entities:
            name = str(entity.get("name", "")).strip()
            if not name:
                continue
            key = normalize_entity_name(name)
            entity_type = _normalize_graph_entity_type(str(entity.get("type", "unknown")))
            description = str(entity.get("description", "")).strip()
            existing = entities_by_key.get(key)
            if existing is None:
                entities_by_key[key] = {
                    "name": name,
                    "type": entity_type,
                    "description": description,
                }
                continue

            if existing["type"] == "unknown" and entity_type != "unknown":
                existing["type"] = entity_type
            if description and len(description) > len(existing.get("description", "")):
                existing["description"] = description

        for relation in relations:
            source = str(relation.get("source", "")).strip()
            destination = str(relation.get("destination", "")).strip()
            predicate = str(relation.get("relation", "")).strip().lower()
            if not source or not destination or not predicate:
                continue
            key = (
                normalize_entity_name(source),
                predicate,
                normalize_entity_name(destination),
            )
            relations_by_key.setdefault(
                key,
                {
                    "source": source,
                    "relation": predicate,
                    "destination": destination,
                },
            )

    for relation in relations_by_key.values():
        for name in (relation["source"], relation["destination"]):
            key = normalize_entity_name(name)
            entities_by_key.setdefault(
                key,
                {
                    "name": name,
                    "type": "unknown",
                    "description": "",
                },
            )

    return list(entities_by_key.values()), list(relations_by_key.values())


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
    """Extract entities and relations from text using rules plus LLM.

    Rules-based triplets provide a deterministic fast path and scaffold-mode
    fallback. The LLM path supplements those results when configured.

    Returns (entities, relations).
    """
    # Use user_message/assistant_response if provided, else use text
    msg = user_message or text
    resp = assistant_response or ""
    rule_text = _select_rule_extraction_text(
        text=text,
        user_message=user_message,
        assistant_response=assistant_response,
    )
    rule_entities, rule_relations = _triplets_to_entities_and_relations(
        extract_rule_triplets(rule_text)
    )

    if settings.agent_provider == "scaffold":
        return rule_entities, rule_relations

    try:
        from anima_server.services.agent.llm import create_llm
        from anima_server.services.agent.messages import HumanMessage, SystemMessage

        llm = create_llm()
        from anima_server.services.agent.prompt_loader import PromptLoader

        prompt_loader = PromptLoader(agent_name="Anima")
        prompt = prompt_loader.extract_entities(
            user_message=msg,
            assistant_response=resp,
        )
        response = await llm.ainvoke(
            [
                SystemMessage(
                    content="You extract entities and relationships. Respond only with JSON.",
                ),
                HumanMessage(content=prompt),
            ]
        )
        content = getattr(response, "content", "")
        if not isinstance(content, str):
            content = str(content)

        parsed = _parse_json_object(content)
        if parsed is None:
            return rule_entities, rule_relations

        entities = parsed.get("entities", [])
        relations = parsed.get("relations", [])

        if not isinstance(entities, list):
            entities = []
        if not isinstance(relations, list):
            relations = []

        # Validate and cap LLM entities at 5 before merging with rule results.
        valid_entities: list[dict[str, str]] = []
        for e in entities[:5]:
            if isinstance(e, dict) and e.get("name") and e.get("type"):
                valid_entities.append(
                    {
                        "name": str(e["name"]),
                        "type": _normalize_graph_entity_type(str(e["type"])),
                        "description": str(e.get("description", "")),
                    }
                )

        # Validate relations
        {e["name"].lower() for e in valid_entities}
        valid_relations: list[dict[str, str]] = []
        for r in relations:
            if (
                isinstance(r, dict)
                and r.get("source")
                and r.get("relation")
                and r.get("destination")
            ):
                valid_relations.append(
                    {
                        "source": str(r["source"]),
                        "relation": str(r["relation"]).lower(),
                        "destination": str(r["destination"]),
                    }
                )

        return _merge_graph_extractions(
            (rule_entities, rule_relations),
            (valid_entities, valid_relations),
        )

    except Exception:
        logger.exception("LLM entity extraction failed")
        return rule_entities, rule_relations


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
        rel_lines.append(f"  ID {r['id']}: {r['source']} -> {r['relation']} -> {r['destination']}")
    rel_text = "\n".join(rel_lines)
    facts_text = "\n".join(f"- {f}" for f in new_facts)

    try:
        from anima_server.services.agent.llm import create_llm
        from anima_server.services.agent.messages import HumanMessage, SystemMessage

        llm = create_llm()
        from anima_server.services.agent.prompt_loader import PromptLoader

        prompt_loader = PromptLoader(agent_name="Anima")
        prompt = prompt_loader.prune_relations(
            existing_relations=rel_text,
            new_facts=facts_text,
        )
        response = await llm.ainvoke(
            [
                SystemMessage(
                    content="You evaluate knowledge graph relations. Respond only with JSON."
                ),
                HumanMessage(content=prompt),
            ]
        )
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

    except Exception:
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

    await _attach_entity_embeddings(entities)

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
                embedding=entity_data.get("embedding"),
            )
            entities_upserted += 1
        except Exception:
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
        except Exception:
            logger.debug("Failed to upsert relation: %s", rel_data)

    # 4. Prune stale relations touching this turn's entities
    entity_names = [e["name"] for e in entities]
    normalized_names = [normalize_entity_name(n) for n in entity_names]

    # Find entity IDs for this turn
    turn_entities = list(
        db.scalars(
            select(KGEntity).where(
                KGEntity.user_id == user_id,
                KGEntity.name_normalized.in_(normalized_names),
            )
        ).all()
    )
    turn_entity_ids = {e.id for e in turn_entities}

    if turn_entity_ids:
        # Load existing relations touching these entities
        existing_rels = list(
            db.scalars(
                select(KGRelation).where(
                    KGRelation.user_id == user_id,
                    or_(
                        KGRelation.source_id.in_(turn_entity_ids),
                        KGRelation.destination_id.in_(turn_entity_ids),
                    ),
                )
            ).all()
        )

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
                extra = list(db.scalars(select(KGEntity).where(KGEntity.id.in_(missing_ids))).all())
                for e in extra:
                    entity_map[e.id] = e

            rel_dicts = []
            for r in existing_rels:
                src = entity_map.get(r.source_id)
                dst = entity_map.get(r.destination_id)
                if src and dst:
                    rel_dicts.append(
                        {
                            "id": r.id,
                            "source": src.name,
                            "relation": r.relation_type,
                            "destination": dst.name,
                        }
                    )

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

from anima_server.services.agent.json_utils import (
    parse_json_object as _parse_json_object,
)
