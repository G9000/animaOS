"""Vector embedding support for semantic memory search.

Generates embeddings via LLM providers and stores them in both:
- MemoryVector table in per-user anima.db (for fast similarity search)
- MemoryItem.embedding_json (for portability / brute-force fallback)

All supported providers (ollama, openrouter, vllm) expose an
OpenAI-compatible /v1/embeddings endpoint, so we use a single
httpx-based implementation for all of them.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import time
from collections import OrderedDict
from dataclasses import dataclass
from threading import Lock
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.config import settings
from anima_server.models import MemoryItem
from anima_server.services.agent.llm import (
    LLMConfigError,
    build_provider_headers,
    resolve_base_url,
    validate_provider_configuration,
)
from anima_server.services.data_crypto import df

logger = logging.getLogger(__name__)

# Default embedding models per provider.  Users can override via
# ANIMA_AGENT_EXTRACTION_MODEL.
_DEFAULT_EMBEDDING_MODELS: dict[str, str] = {
    "ollama": "nomic-embed-text",
    "openrouter": "openai/text-embedding-3-small",
    "vllm": "text-embedding-3-small",
}


def _resolve_embedding_model() -> str:
    """Return the embedding model to use, preferring the user-configured one."""
    configured = settings.agent_extraction_model.strip()
    if configured:
        return configured
    return _DEFAULT_EMBEDDING_MODELS.get(settings.agent_provider, "nomic-embed-text")


def _resolve_embedding_base_url() -> str:
    """Resolve the base URL for embeddings.

    For openrouter we always use the canonical API URL regardless of any
    local ``agent_base_url`` override (which is typically pointed at a
    local Ollama instance for chat).  For other providers, delegate to
    the shared ``resolve_base_url`` helper.
    """
    provider = settings.agent_provider
    if provider == "openrouter":
        return "https://openrouter.ai/api/v1"
    return resolve_base_url(provider)


# ---------------------------------------------------------------------------
# 3.2 — Embedding cache (LRU with TTL)
# ---------------------------------------------------------------------------

_CACHE_MAX_SIZE = 2048
_CACHE_TTL_S = 3600  # 1 hour

_embedding_cache: OrderedDict[str, tuple[list[float], float]] = OrderedDict()
_cache_lock = Lock()
_cache_hits = 0
_cache_misses = 0


def _cache_key(text: str) -> str:
    provider = settings.agent_provider
    model = _resolve_embedding_model()
    raw = f"{provider}:{model}:{text}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _cache_get(key: str) -> list[float] | None:
    global _cache_hits, _cache_misses
    with _cache_lock:
        entry = _embedding_cache.get(key)
        if entry is None:
            _cache_misses += 1
            return None
        embedding, ts = entry
        if time.monotonic() - ts > _CACHE_TTL_S:
            _embedding_cache.pop(key, None)
            _cache_misses += 1
            return None
        _embedding_cache.move_to_end(key)
        _cache_hits += 1
        return embedding


def _cache_put(key: str, embedding: list[float]) -> None:
    with _cache_lock:
        _embedding_cache[key] = (embedding, time.monotonic())
        _embedding_cache.move_to_end(key)
        while len(_embedding_cache) > _CACHE_MAX_SIZE:
            _embedding_cache.popitem(last=False)


def clear_embedding_cache() -> None:
    """Clear the embedding cache. Called on model config change or in tests."""
    global _cache_hits, _cache_misses
    with _cache_lock:
        _embedding_cache.clear()
        _cache_hits = 0
        _cache_misses = 0


def get_embedding_cache_stats() -> dict[str, int]:
    """Return cache hit/miss counters for monitoring."""
    return {"hits": _cache_hits, "misses": _cache_misses, "size": len(_embedding_cache)}


async def generate_embedding(text: str) -> list[float] | None:
    """Generate an embedding vector for the given text using the configured provider."""
    provider = settings.agent_provider

    if provider == "scaffold":
        return None

    if provider == "openrouter":
        try:
            result = await _embed_ollama(text)
        except Exception:
            logger.debug(
                "OpenRouter has no embeddings endpoint and local Ollama "
                "fallback unavailable — skipping embedding generation"
            )
            return None
        if result is not None:
            key = _cache_key(text)
            _cache_put(key, result)
        return result

    # Check cache first
    key = _cache_key(text)
    cached = _cache_get(key)
    if cached is not None:
        return cached

    try:
        validate_provider_configuration(provider)
    except LLMConfigError as exc:
        logger.debug("Skipping embedding generation for provider %s: %s", provider, exc)
        return None

    try:
        if provider == "ollama":
            result = await _embed_ollama(text)
        else:
            # openrouter, vllm — all OpenAI-compatible
            result = await _embed_openai_compatible(text)
    except LLMConfigError as exc:
        logger.debug("Skipping embedding generation for provider %s: %s", provider, exc)
        return None
    except Exception:
        logger.exception("Embedding generation failed for provider %s", provider)
        return None

    if result is not None:
        _cache_put(key, result)
    return result


async def _embed_openai_compatible(text: str) -> list[float] | None:
    """Generate embeddings via any OpenAI-compatible /v1/embeddings endpoint."""
    import httpx

    base_url = _resolve_embedding_base_url()
    model = _resolve_embedding_model()
    headers = build_provider_headers(settings.agent_provider)
    headers["Content-Type"] = "application/json"

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{base_url}/embeddings",
            headers=headers,
            json={"model": model, "input": [text]},
        )
        resp.raise_for_status()
        data = resp.json()
        entries = data.get("data", [])
        if entries and isinstance(entries[0], dict):
            embedding = entries[0].get("embedding")
            if isinstance(embedding, list):
                return embedding
        return None


async def _embed_ollama(text: str) -> list[float] | None:
    """Generate embeddings via ollama's native /api/embed endpoint."""
    import httpx

    # For ollama, use the raw base URL (without /v1 suffix)
    configured = settings.agent_base_url.strip()
    base_url = configured if configured else "http://127.0.0.1:11434"
    # Strip /v1 suffix if present (resolve_base_url adds it for chat)
    base_url = base_url.removesuffix("/v1")
    model = _resolve_embedding_model()

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{base_url}/api/embed",
            json={"model": model, "input": text},
        )
        resp.raise_for_status()
        data = resp.json()
        embeddings = data.get("embeddings", [])
        if embeddings and isinstance(embeddings[0], list):
            return embeddings[0]
        return None


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


async def semantic_search(
    db: Session,
    *,
    user_id: int,
    query: str,
    limit: int = 10,
    similarity_threshold: float = 0.3,
) -> list[tuple[MemoryItem, float]]:
    """Search memory items by semantic similarity.

    Uses the per-user vector store first, falls back to brute-force cosine
    over embedding_json if the vector store has no data.
    """
    query_embedding = await generate_embedding(query)
    if query_embedding is None:
        return []

    # Try vector store first (per-user anima.db)
    try:
        from anima_server.services.agent.vector_store import search_similar

        vs_results = search_similar(
            user_id,
            query_embedding=query_embedding,
            limit=limit,
            db=db,
        )
        if vs_results:
            item_ids = [r["id"] for r in vs_results if r["similarity"] >= similarity_threshold]
            if item_ids:
                items_by_id = {
                    item.id: item
                    for item in db.scalars(
                        select(MemoryItem).where(MemoryItem.id.in_(item_ids))
                    ).all()
                }
                from anima_server.services.agent.forgetting import HEAT_VISIBILITY_FLOOR as _HVF

                results: list[tuple[MemoryItem, float]] = []
                for r in vs_results:
                    if r["similarity"] >= similarity_threshold and r["id"] in items_by_id:
                        item = items_by_id[r["id"]]
                        if item.heat not in (None, 0.0) and item.heat < _HVF:
                            continue
                        results.append((item, r["similarity"]))
                return results[:limit]
    except Exception:
        logger.debug("Vector store search failed, falling back to brute-force")

    # Fallback: brute-force over embedding_json column
    items = list(
        db.scalars(
            select(MemoryItem).where(
                MemoryItem.user_id == user_id,
                MemoryItem.superseded_by.is_(None),
                MemoryItem.embedding_json.isnot(None),
            )
        ).all()
    )

    from anima_server.services.agent.forgetting import HEAT_VISIBILITY_FLOOR as _HVF2

    scored: list[tuple[MemoryItem, float]] = []
    for item in items:
        if item.heat not in (None, 0.0) and item.heat < _HVF2:
            continue
        item_embedding = _parse_embedding(item.embedding_json)
        if item_embedding is None:
            continue
        sim = cosine_similarity(query_embedding, item_embedding)
        if sim >= similarity_threshold:
            scored.append((item, sim))

    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored[:limit]


async def embed_memory_item(
    db: Session,
    item: MemoryItem,
) -> bool:
    """Generate and store an embedding for a single memory item.

    Stores in both the embedding_json column (for portability/fallback)
    and the MemoryVector table (for fast search).
    Returns True if successful.
    """
    plaintext = df(item.user_id, item.content, table="memory_items", field="content")
    embedding = await generate_embedding(plaintext)
    if embedding is None:
        return False

    item.embedding_json = embedding
    db.flush()

    try:
        from anima_server.services.agent.vector_store import upsert_memory

        upsert_memory(
            item.user_id,
            item_id=item.id,
            content=plaintext,
            embedding=embedding,
            category=item.category,
            importance=item.importance,
            db=db,
        )
    except Exception:
        logger.debug("Failed to upsert item %d into vector store", item.id)

    return True


async def backfill_embeddings(
    db: Session,
    *,
    user_id: int,
    batch_size: int = 50,
) -> int:
    """Generate embeddings for all items that don't have one yet. Returns count of items embedded."""
    items = list(
        db.scalars(
            select(MemoryItem)
            .where(
                MemoryItem.user_id == user_id,
                MemoryItem.superseded_by.is_(None),
                MemoryItem.embedding_json.is_(None),
            )
            .limit(batch_size)
        ).all()
    )

    if not items:
        return 0

    plaintexts = [
        df(user_id, item.content, table="memory_items", field="content") for item in items
    ]
    embeddings = await generate_embeddings_batch(plaintexts)

    count = 0
    for item, plaintext, embedding in zip(items, plaintexts, embeddings, strict=False):
        if embedding is None:
            continue
        item.embedding_json = embedding
        try:
            from anima_server.services.agent.vector_store import upsert_memory

            upsert_memory(
                item.user_id,
                item_id=item.id,
                content=plaintext,
                embedding=embedding,
                category=item.category,
                importance=item.importance,
                db=db,
            )
        except Exception:
            logger.debug("Failed to upsert item %d into vector store", item.id)
        count += 1

    if count > 0:
        db.flush()
    return count


def sync_to_vector_store(
    db: Session,
    *,
    user_id: int,
) -> int:
    """Sync all items with existing embeddings into the vector store. Used after vault import."""
    items = list(
        db.scalars(
            select(MemoryItem).where(
                MemoryItem.user_id == user_id,
                MemoryItem.superseded_by.is_(None),
                MemoryItem.embedding_json.isnot(None),
            )
        ).all()
    )

    if not items:
        return 0

    try:
        from anima_server.services.agent.vector_store import rebuild_user_index

        index_data = [
            (
                item.id,
                df(user_id, item.content, table="memory_items", field="content"),
                item.embedding_json,
                item.category,
                item.importance,
            )
            for item in items
            if isinstance(item.embedding_json, list) and item.embedding_json
        ]
        return rebuild_user_index(user_id, index_data, db=db)
    except Exception:
        logger.exception("Failed to sync embeddings to vector store for user %d", user_id)
        return 0


def sync_embeddings_to_runtime(
    soul_db: Session,
    *,
    user_id: int,
) -> int:
    """Sync cached soul embeddings into the runtime pgvector store."""
    items = list(
        soul_db.scalars(
            select(MemoryItem).where(
                MemoryItem.user_id == user_id,
                MemoryItem.superseded_by.is_(None),
                MemoryItem.embedding_json.isnot(None),
            )
        ).all()
    )

    if not items:
        return 0

    try:
        from anima_server.db.runtime import get_runtime_session_factory

        runtime_db = get_runtime_session_factory()()
    except RuntimeError:
        logger.debug("Runtime PG unavailable for embedding sync for user %d", user_id)
        return 0
    except Exception:
        logger.debug("Failed to open runtime PG session for user %d", user_id, exc_info=True)
        return 0

    try:
        from anima_server.services.agent.pgvec_store import PgVecStore

        store = PgVecStore(runtime_db)
        count = 0

        for item in items:
            embedding = _parse_embedding(item.embedding_json)
            if embedding is None:
                continue

            plaintext = df(user_id, item.content, table="memory_items", field="content")
            store.upsert(
                user_id,
                item_id=item.id,
                content=plaintext,
                embedding=embedding,
                category=item.category,
                importance=item.importance,
            )
            count += 1

        if count > 0:
            runtime_db.commit()
        return count
    except Exception:
        runtime_db.rollback()
        logger.exception("Failed to sync embeddings to runtime PG for user %d", user_id)
        return 0
    finally:
        runtime_db.close()


def _parse_embedding(raw: Any) -> list[float] | None:
    """Parse an embedding from the JSON column value."""
    if raw is None:
        return None
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
    return None


# ---------------------------------------------------------------------------
# 1.1 — Hybrid search with Reciprocal Rank Fusion (RRF)
# ---------------------------------------------------------------------------

_RRF_K = 60  # Standard RRF constant (Cormack et al. 2009)


@dataclass(frozen=True, slots=True)
class HybridSearchResult:
    """Return type for hybrid_search — carries items + the query embedding for reuse."""

    items: list[tuple[MemoryItem, float]]
    query_embedding: list[float] | None


def _tokenize(text: str) -> list[str]:
    """Simple whitespace tokenizer with lowercasing and minimum length filter."""
    return [w for w in text.lower().split() if len(w) > 1]


def _bm25_rerank(
    results: list[tuple[MemoryItem, float]],
    query: str,
    user_id: int,
    *,
    rrf_weight: float = 0.7,
    bm25_weight: float = 0.3,
    k1: float = 1.5,
    b: float = 0.75,
) -> list[tuple[MemoryItem, float]]:
    """Re-rank search results by combining existing RRF scores with BM25 relevance.

    Computes BM25 scores for each result's content against the query, normalises
    both score distributions to [0, 1], then returns results sorted by the
    weighted combination: ``rrf_weight * norm_rrf + bm25_weight * norm_bm25``.

    This adds a lexical-precision boost on top of the hybrid (semantic + keyword)
    RRF merge, similar to the reranker stage in Mem0's retrieval pipeline but
    without requiring any external model — pure BM25 in ~30 lines.
    """
    if len(results) <= 1:
        return results

    query_tokens = _tokenize(query)
    if not query_tokens:
        return results

    # Decrypt and tokenize each document
    doc_tokens: list[list[str]] = []
    for item, _score in results:
        plaintext = df(item.user_id, item.content, table="memory_items", field="content")
        doc_tokens.append(_tokenize(plaintext))

    n = len(doc_tokens)
    # Average document length
    total_len = sum(len(dt) for dt in doc_tokens)
    avgdl = total_len / n if total_len > 0 else 1.0

    # Document frequency for each query term
    doc_freq: dict[str, int] = {}
    for qt in query_tokens:
        doc_freq[qt] = sum(1 for dt in doc_tokens if qt in dt)

    # Compute BM25 score for each document
    bm25_scores: list[float] = []
    for dt in doc_tokens:
        dl = len(dt) if dt else 1
        score = 0.0
        for qt in query_tokens:
            tf = dt.count(qt)
            if tf == 0:
                continue
            df_val = doc_freq.get(qt, 0)
            # IDF: log((N - df + 0.5) / (df + 0.5) + 1)
            idf = math.log((n - df_val + 0.5) / (df_val + 0.5) + 1.0)
            # BM25 term score
            numerator = tf * (k1 + 1.0)
            denominator = tf + k1 * (1.0 - b + b * dl / avgdl)
            score += idf * numerator / denominator
        bm25_scores.append(score)

    # Normalise BM25 scores to [0, 1]
    max_bm25 = max(bm25_scores) if bm25_scores else 0.0
    norm_bm25 = [s / max_bm25 for s in bm25_scores] if max_bm25 > 0.0 else [0.0] * n

    # Normalise RRF scores to [0, 1]
    rrf_scores = [score for _, score in results]
    max_rrf = max(rrf_scores) if rrf_scores else 0.0
    norm_rrf = [s / max_rrf for s in rrf_scores] if max_rrf > 0.0 else [0.0] * n

    # Combine and re-sort
    combined: list[tuple[MemoryItem, float]] = []
    for i, (item, _original_score) in enumerate(results):
        final = rrf_weight * norm_rrf[i] + bm25_weight * norm_bm25[i]
        combined.append((item, final))

    combined.sort(key=lambda pair: pair[1], reverse=True)
    return combined


def _reciprocal_rank_fusion(
    semantic_ranked: list[tuple[int, float]],
    keyword_ranked: list[tuple[int, float]],
    *,
    semantic_weight: float = 0.5,
    keyword_weight: float = 0.5,
) -> list[tuple[int, float]]:
    """Merge two ranked lists using RRF. Returns (item_id, rrf_score) sorted descending."""
    scores: dict[int, float] = {}

    for rank, (item_id, _sim) in enumerate(semantic_ranked):
        scores[item_id] = scores.get(item_id, 0.0) + semantic_weight / (_RRF_K + rank + 1)

    for rank, (item_id, _sim) in enumerate(keyword_ranked):
        scores[item_id] = scores.get(item_id, 0.0) + keyword_weight / (_RRF_K + rank + 1)

    merged = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return merged


async def hybrid_search(
    db: Session,
    *,
    user_id: int,
    query: str,
    limit: int = 15,
    similarity_threshold: float = 0.25,
    semantic_weight: float = 0.5,
    keyword_weight: float = 0.5,
    tags: list[str] | None = None,
    tag_match_mode: str = "any",
) -> HybridSearchResult:
    """Combined semantic + keyword search over memory items using RRF merge.

    When *tags* is provided, post-filters results to only include items
    that match the given tags (using "any" or "all" match mode).

    Returns a HybridSearchResult containing:
    - items: list of (MemoryItem, rrf_score) sorted by relevance
    - query_embedding: the embedding vector for reuse in query-aware blocks
    """
    # If tags are given, pre-fetch the allowed item IDs
    allowed_ids: set[int] | None = None
    if tags:
        from anima_server.services.agent.memory_store import get_items_by_tags

        tag_items = get_items_by_tags(
            db,
            user_id=user_id,
            tags=tags,
            match_mode=tag_match_mode,
            limit=500,
        )
        allowed_ids = {item.id for item in tag_items}
        if not allowed_ids:
            return HybridSearchResult(items=[], query_embedding=None)

    query_embedding = await generate_embedding(query)

    from anima_server.services.agent.vector_store import search_similar

    # --- Semantic leg ---
    semantic_ranked: list[tuple[int, float]] = []
    if query_embedding is not None:
        try:
            sem_results = search_similar(
                user_id,
                query_embedding=query_embedding,
                limit=limit,
                db=db,
            )
            semantic_ranked = [
                (r["id"], r["similarity"])
                for r in sem_results
                if r["similarity"] >= similarity_threshold
            ]
        except Exception:
            logger.debug("Semantic search failed in hybrid_search")

        # Brute-force fallback if vector store is empty
        if not semantic_ranked:
            items_with_emb = list(
                db.scalars(
                    select(MemoryItem).where(
                        MemoryItem.user_id == user_id,
                        MemoryItem.superseded_by.is_(None),
                        MemoryItem.embedding_json.isnot(None),
                    )
                ).all()
            )
            bruteforce: list[tuple[int, float]] = []
            for item in items_with_emb:
                emb = _parse_embedding(item.embedding_json)
                if emb is None:
                    continue
                sim = cosine_similarity(query_embedding, emb)
                if sim >= similarity_threshold:
                    bruteforce.append((item.id, sim))
            bruteforce.sort(key=lambda x: x[1], reverse=True)
            semantic_ranked = bruteforce[:limit]

    # --- Keyword leg (BM25) ---
    keyword_ranked: list[tuple[int, float]] = []
    try:
        from anima_server.services.agent.bm25_index import bm25_search

        keyword_ranked = bm25_search(user_id, query=query, limit=limit, db=db)
    except Exception:
        logger.debug("BM25 keyword search failed in hybrid_search")

    # --- RRF merge ---
    if not semantic_ranked and not keyword_ranked:
        return HybridSearchResult(items=[], query_embedding=query_embedding)

    merged = _reciprocal_rank_fusion(
        semantic_ranked,
        keyword_ranked,
        semantic_weight=semantic_weight,
        keyword_weight=keyword_weight,
    )

    # Resolve item_ids to MemoryItem objects
    merged_ids = [item_id for item_id, _ in merged[:limit]]
    items_by_id = (
        {
            item.id: item
            for item in db.scalars(select(MemoryItem).where(MemoryItem.id.in_(merged_ids))).all()
        }
        if merged_ids
        else {}
    )

    from anima_server.services.agent.forgetting import HEAT_VISIBILITY_FLOOR

    results: list[tuple[MemoryItem, float]] = []
    for item_id, rrf_score in merged[:limit]:
        if item_id in items_by_id:
            if allowed_ids is not None and item_id not in allowed_ids:
                continue
            item = items_by_id[item_id]
            # Respect passive forgetting: skip items that have been scored
            # (heat > 0) but decayed below the visibility floor.
            if item.heat not in (None, 0.0) and item.heat < HEAT_VISIBILITY_FLOOR:
                continue
            results.append((item, rrf_score))

    # --- BM25 rerank stage ---
    if results:
        results = _bm25_rerank(results, query, user_id)

    return HybridSearchResult(items=results, query_embedding=query_embedding)


# ---------------------------------------------------------------------------
# 1.2 — Adaptive result filtering with score gap detection
# ---------------------------------------------------------------------------


def adaptive_filter(
    results: list[tuple[MemoryItem, float]],
    *,
    max_results: int = 12,
    high_confidence_threshold: float = 0.7,
    min_results: int = 3,
    gap_threshold: float = 0.15,
) -> list[tuple[MemoryItem, float]]:
    """Trim results based on score density and gap detection.

    - If the top min_results all score above high_confidence_threshold,
      return only results above that threshold (precision mode).
    - Otherwise, scan for a score gap > gap_threshold between consecutive
      results and cut there (but never below min_results).
    - Falls back to returning up to max_results (recall mode).
    """
    if not results:
        return []

    capped = results[:max_results]

    if len(capped) <= min_results:
        return capped

    # Precision mode: if top-N are all very strong, trim to high-confidence only
    top_scores = [score for _, score in capped[:min_results]]
    if all(s >= high_confidence_threshold for s in top_scores):
        return [(item, score) for item, score in capped if score >= high_confidence_threshold]

    # Gap detection: find the largest drop after min_results
    for i in range(min_results, len(capped)):
        prev_score = capped[i - 1][1]
        curr_score = capped[i][1]
        if prev_score - curr_score > gap_threshold:
            return capped[:i]

    return capped


# ---------------------------------------------------------------------------
# 1.4 — Batch embedding generation with adaptive retry
# ---------------------------------------------------------------------------


async def generate_embeddings_batch(
    texts: list[str],
    *,
    max_batch_size: int = 32,
) -> list[list[float] | None]:
    """Generate embeddings for multiple texts in batched API calls.

    For OpenAI-compatible providers: sends texts in batches.
    For ollama: uses asyncio.gather() over individual calls.
    On failure: halves batch size and retries (adaptive strategy).
    Returns a list parallel to input — None for failed items.
    """
    if not texts:
        return []

    provider = settings.agent_provider
    if provider == "scaffold":
        return [None] * len(texts)

    try:
        validate_provider_configuration(provider)
    except LLMConfigError:
        return [None] * len(texts)

    if provider == "ollama":
        return await _batch_embed_ollama(texts)

    return await _batch_embed_openai_compatible(texts, max_batch_size=max_batch_size)


async def _batch_embed_openai_compatible(
    texts: list[str],
    *,
    max_batch_size: int = 32,
) -> list[list[float] | None]:
    """Batch embedding via OpenAI-compatible /v1/embeddings with adaptive retry."""
    import httpx

    base_url = _resolve_embedding_base_url()
    model = _resolve_embedding_model()
    headers = build_provider_headers(settings.agent_provider)
    headers["Content-Type"] = "application/json"

    results: list[list[float] | None] = [None] * len(texts)
    batch_size = min(max_batch_size, len(texts))

    for start in range(0, len(texts), batch_size):
        chunk = texts[start : start + batch_size]
        current_batch = len(chunk)

        while current_batch >= 1:
            try:
                async with httpx.AsyncClient(timeout=60.0) as client:
                    # Process sub-chunks if we had to halve
                    for sub_start in range(0, len(chunk), current_batch):
                        sub_chunk = chunk[sub_start : sub_start + current_batch]
                        resp = await client.post(
                            f"{base_url}/embeddings",
                            headers=headers,
                            json={"model": model, "input": sub_chunk},
                        )
                        resp.raise_for_status()
                        data = resp.json()
                        entries = data.get("data", [])
                        for entry in entries:
                            idx = entry.get("index", 0)
                            embedding = entry.get("embedding")
                            abs_idx = start + sub_start + idx
                            if abs_idx < len(results) and isinstance(embedding, list):
                                results[abs_idx] = embedding
                break  # Success — move to next batch
            except Exception:
                current_batch = current_batch // 2
                if current_batch < 1:
                    logger.warning(
                        "Batch embedding failed for chunk at offset %d after retries",
                        start,
                    )
                    break
                logger.debug(
                    "Batch embedding failed, retrying with batch_size=%d",
                    current_batch,
                )

    return results


async def _batch_embed_ollama(texts: list[str]) -> list[list[float] | None]:
    """Batch embedding for ollama via asyncio.gather over individual calls."""
    tasks = [generate_embedding(text) for text in texts]
    return list(await asyncio.gather(*tasks))
