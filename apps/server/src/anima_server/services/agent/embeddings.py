"""Vector embedding support for semantic memory search.

Generates embeddings via LLM providers and stores them in both:
- RuntimeEmbedding table in PostgreSQL via pgvector (for fast ANN search)
- MemoryItem.embedding_json (portable cache for .anima/ transfers)

OpenAI-compatible providers use /v1/embeddings. Ollama uses its native
/api/embed endpoint.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import math
import time
from collections import OrderedDict
from dataclasses import dataclass
from threading import Lock
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.config import settings
from anima_server.models import MemoryItem
from anima_server.services import anima_core_retrieval
from anima_server.services.agent.adaptive_retrieval import (
    AdaptiveFilterResult,
    AdaptiveRetrievalConfig,
    apply_adaptive_filter,
)
from anima_server.services.agent.embedding_integrity import (
    check_embedding,
    compute_embedding_checksum,
    parse_embedding_payload,
)
from anima_server.services.agent.llm import (
    LLMConfigError,
    validate_provider,
)
from anima_server.services.agent.text_processing import prepare_embedding_text
from anima_server.services.data_crypto import df

logger = logging.getLogger(__name__)

# Default embedding models per provider. Users can override via the
# dedicated embedding settings, with extraction_model kept as a
# backwards-compatible fallback.
_DEFAULT_EMBEDDING_MODELS: dict[str, str] = {
    "ollama": "nomic-embed-text",
    "openrouter": "openai/text-embedding-3-small",
    "openai": "text-embedding-3-small",
    "vllm": "text-embedding-3-small",
}

_DEFAULT_EMBEDDING_BASE_URLS: dict[str, str] = {
    "ollama": "http://127.0.0.1:11434",
    "openrouter": "https://openrouter.ai/api/v1",
    "moonshot": "https://api.moonshot.cn/v1",
    "vllm": "http://127.0.0.1:8000/v1",
    "openai": "https://api.openai.com/v1",
}


def _setting_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _resolve_embedding_provider() -> str:
    configured = _setting_text(getattr(settings, "agent_embedding_provider", ""))
    if configured:
        return configured
    return _setting_text(getattr(settings, "agent_provider", "")) or "ollama"


def _resolve_embedding_api_key() -> str:
    configured = _setting_text(getattr(settings, "agent_embedding_api_key", ""))
    if configured:
        return configured
    return _setting_text(getattr(settings, "agent_api_key", ""))


def _resolve_embedding_model() -> str:
    """Return the embedding model to use, preferring the user-configured one."""
    configured = _setting_text(getattr(settings, "agent_embedding_model", ""))
    if configured:
        return configured
    configured = _setting_text(getattr(settings, "agent_extraction_model", ""))
    if configured:
        return configured
    return _DEFAULT_EMBEDDING_MODELS.get(_resolve_embedding_provider(), "nomic-embed-text")


def _resolve_embedding_base_url() -> str:
    """Resolve the base URL for the active embedding provider."""
    provider = _resolve_embedding_provider()
    configured = _setting_text(getattr(settings, "agent_embedding_base_url", ""))
    if configured:
        return configured.removesuffix("/v1") if provider == "ollama" else configured

    configured_agent = _setting_text(getattr(settings, "agent_base_url", ""))
    if configured_agent and not _setting_text(getattr(settings, "agent_embedding_provider", "")):
        if provider == "openrouter":
            return _DEFAULT_EMBEDDING_BASE_URLS[provider]
        return configured_agent.removesuffix("/v1") if provider == "ollama" else configured_agent

    return _DEFAULT_EMBEDDING_BASE_URLS[provider]


def _validate_embedding_provider_configuration(provider: str) -> None:
    validate_provider(provider)
    if provider in ("openrouter", "moonshot", "openai") and not _resolve_embedding_api_key():
        raise LLMConfigError(
            f"ANIMA_AGENT_EMBEDDING_API_KEY (or ANIMA_AGENT_API_KEY) is required "
            f"when embedding_provider='{provider}'"
        )


def validate_provider_configuration(provider: str) -> None:
    _validate_embedding_provider_configuration(provider)


def _build_embedding_headers(provider: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    api_key = _resolve_embedding_api_key()

    if provider == "openrouter":
        headers["Authorization"] = f"Bearer {api_key}"
        headers["HTTP-Referer"] = "https://anima.local"
        headers["X-Title"] = "ANIMA"
        return headers

    if provider == "moonshot":
        headers["Authorization"] = f"Bearer {api_key}"
        headers["Content-Type"] = "application/json"
        return headers

    if provider == "openai":
        headers["Authorization"] = f"Bearer {api_key}"
        return headers

    if provider == "vllm" and api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    return headers


def build_provider_headers(provider: str) -> dict[str, str]:
    return _build_embedding_headers(provider)


def resolve_base_url() -> str:
    return _resolve_embedding_base_url()


def _embedding_skip_reason(provider: str) -> str | None:
    if provider == "openrouter":
        return "provider has no supported embeddings endpoint; configure an explicit embedding provider"
    return None


# ---------------------------------------------------------------------------
# 3.2 — Embedding cache (LRU with TTL)
# ---------------------------------------------------------------------------

_CACHE_MAX_SIZE = 2048
_CACHE_TTL_S = 3600  # 1 hour
_PROVIDER_FAILURE_COOLDOWN_S = 30.0

_embedding_cache: OrderedDict[str, tuple[list[float], float]] = OrderedDict()
_cache_lock = Lock()
_cache_hits = 0
_cache_misses = 0
_provider_unavailable_until: dict[str, float] = {}
_provider_unavailable_lock = Lock()


def _cache_key(text: str) -> str:
    provider = _resolve_embedding_provider()
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


def _provider_failure_key(
    provider: str,
    *,
    base_url: str | None = None,
    model: str | None = None,
) -> str:
    resolved_base_url = (base_url or _resolve_embedding_base_url()).rstrip("/")
    resolved_model = model or _resolve_embedding_model()
    return f"{provider}:{resolved_base_url}:{resolved_model}"


def _provider_in_cooldown(key: str) -> bool:
    with _provider_unavailable_lock:
        unavailable_until = _provider_unavailable_until.get(key)
        if unavailable_until is None:
            return False
        if unavailable_until <= time.monotonic():
            _provider_unavailable_until.pop(key, None)
            return False
        return True


def _mark_provider_unavailable(
    key: str,
    *,
    provider: str,
    base_url: str,
    exc: Exception,
) -> None:
    now = time.monotonic()
    with _provider_unavailable_lock:
        unavailable_until = _provider_unavailable_until.get(key)
        if unavailable_until is not None and unavailable_until > now:
            return
        _provider_unavailable_until[key] = now + _PROVIDER_FAILURE_COOLDOWN_S
    logger.warning(
        "Embedding provider %s unavailable at %s: %s. Cooling down for %.0fs",
        provider,
        base_url,
        exc,
        _PROVIDER_FAILURE_COOLDOWN_S,
    )


def _clear_provider_unavailable(key: str) -> None:
    with _provider_unavailable_lock:
        _provider_unavailable_until.pop(key, None)


def clear_embedding_cache() -> None:
    """Clear the embedding cache. Called on model config change or in tests."""
    global _cache_hits, _cache_misses
    with _cache_lock:
        _embedding_cache.clear()
        _cache_hits = 0
        _cache_misses = 0
    with _provider_unavailable_lock:
        _provider_unavailable_until.clear()
    from anima_server.config import clear_detected_embedding_dim

    clear_detected_embedding_dim()


def get_embedding_cache_stats() -> dict[str, int]:
    """Return cache hit/miss counters for monitoring."""
    with _provider_unavailable_lock:
        cooling_down = sum(
            1 for unavailable_until in _provider_unavailable_until.values()
            if unavailable_until > time.monotonic()
        )
    return {
        "hits": _cache_hits,
        "misses": _cache_misses,
        "size": len(_embedding_cache),
        "cooling_down": cooling_down,
    }


async def generate_embedding(text: str) -> list[float] | None:
    """Generate an embedding vector for the given text using the configured provider."""
    prepared_text = prepare_embedding_text(text)
    if not prepared_text:
        return None

    provider = _resolve_embedding_provider()
    base_url = resolve_base_url()
    model = _resolve_embedding_model()
    provider_key = _provider_failure_key(
        provider,
        base_url=base_url,
        model=model,
    )

    if provider == "scaffold":
        return None

    skip_reason = _embedding_skip_reason(provider)
    if skip_reason is not None:
        logger.debug(
            "Skipping embedding generation for provider %s: %s", provider, skip_reason)
        return None

    # Check cache first
    key = _cache_key(prepared_text)
    cached = _cache_get(key)
    if cached is not None:
        return cached

    if _provider_in_cooldown(provider_key):
        return None

    try:
        validate_provider_configuration(provider)
    except LLMConfigError as exc:
        logger.debug(
            "Skipping embedding generation for provider %s: %s", provider, exc)
        return None

    try:
        if provider == "ollama":
            result = await _embed_ollama(prepared_text)
        else:
            # openrouter, vllm — all OpenAI-compatible
            result = await _embed_openai_compatible(prepared_text)
    except LLMConfigError as exc:
        logger.debug(
            "Skipping embedding generation for provider %s: %s", provider, exc)
        return None
    except httpx.HTTPError as exc:
        _mark_provider_unavailable(
            provider_key,
            provider=provider,
            base_url=base_url,
            exc=exc,
        )
        return None
    except Exception:
        logger.exception(
            "Embedding generation failed for provider %s", provider)
        return None

    if result is not None:
        _clear_provider_unavailable(provider_key)
        _cache_put(key, result)
        from anima_server.config import _detected_embedding_dim, set_detected_embedding_dim

        if _detected_embedding_dim is None:
            set_detected_embedding_dim(len(result))
            logger.info(
                "Auto-detected embedding dimension: %d (model=%s)",
                len(result),
                _resolve_embedding_model(),
            )
    return result


async def _embed_openai_compatible(text: str) -> list[float] | None:
    """Generate embeddings via any OpenAI-compatible /v1/embeddings endpoint."""
    provider = _resolve_embedding_provider()
    base_url = resolve_base_url()
    model = _resolve_embedding_model()
    headers = build_provider_headers(provider)
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
    """Generate embeddings via Ollama's native /api/embed endpoint."""
    base_url = resolve_base_url()
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


def _set_embedding_checksum(target: Any, checksum: str) -> bool:
    try:
        setattr(target, "embedding_checksum", checksum)
        return True
    except Exception:
        return False


def _validated_cached_embedding(item: Any) -> tuple[list[float] | None, bool]:
    checked = check_embedding(
        getattr(item, "embedding_json", None),
        getattr(item, "embedding_checksum", None),
    )
    item_id = getattr(item, "id", "unknown")

    if checked.status == "missing_checksum" and checked.embedding is not None:
        repaired = checked.actual_checksum is not None and _set_embedding_checksum(
            item, checked.actual_checksum
        )
        if repaired:
            logger.info("Backfilled missing embedding checksum for memory item %s", item_id)
        return checked.embedding, repaired

    if checked.status == "checksum_mismatch":
        logger.warning("Skipping memory item %s due to embedding checksum mismatch", item_id)
        return None, False

    if checked.status == "invalid":
        logger.warning("Skipping memory item %s due to malformed embedding payload", item_id)
        return None, False

    return checked.embedding, False


def _semantic_ranked_ids(
    db: Session,
    *,
    user_id: int,
    query_embedding: list[float],
    limit: int,
    similarity_threshold: float,
) -> list[tuple[int, float]]:
    rust_ranked = _semantic_ranked_ids_via_rust(
        db=db,
        user_id=user_id,
        query_embedding=query_embedding,
        limit=limit,
    )
    if rust_ranked:
        return [
            (item_id, similarity)
            for item_id, similarity in rust_ranked
            if similarity >= similarity_threshold
        ]

    from anima_server.services.agent.vector_store import search_similar

    try:
        vs_results = search_similar(
            user_id,
            query_embedding=query_embedding,
            limit=limit,
            db=db,
        )
    except Exception:
        logger.debug("Semantic search failed in hybrid_search")
        return []

    return [
        (int(result["id"]), float(result["similarity"]))
        for result in vs_results
        if float(result["similarity"]) >= similarity_threshold
    ]


def _semantic_ranked_ids_via_rust(
    *,
    db: Session,
    user_id: int,
    query_embedding: list[float],
    limit: int,
) -> list[tuple[int, float]] | None:
    try:
        root = anima_core_retrieval.get_retrieval_root()
        from anima_server.services.agent.memory_store import ensure_memory_retrieval_index_ready

        if not ensure_memory_retrieval_index_ready(db, user_id=user_id, root=root):
            return None
        hits = anima_core_retrieval.memory_index_vector_search(
            root=root,
            user_id=user_id,
            query_embedding=query_embedding,
            limit=limit,
        )
    except RuntimeError:
        logger.debug("Rust semantic memory index is unavailable")
        return None
    except Exception:
        logger.debug("Rust semantic memory index search failed", exc_info=True)
        return None

    ranked: list[tuple[int, float]] = []
    for hit in hits:
        record_id = hit.get("record_id")
        score = hit.get("score")
        if record_id is None or score is None:
            continue
        try:
            ranked.append((int(record_id), float(score)))
        except (TypeError, ValueError):
            continue
    return ranked


async def semantic_search(
    db: Session,
    *,
    user_id: int,
    query: str,
    limit: int = 10,
    similarity_threshold: float = 0.3,
) -> list[tuple[MemoryItem, float]]:
    """Search memory items by semantic similarity via pgvector."""
    prepared_query = prepare_embedding_text(query, limit=4096)
    if not prepared_query:
        return []

    query_embedding = await generate_embedding(prepared_query)
    if query_embedding is None:
        return []

    ranked = _semantic_ranked_ids(
        db,
        user_id=user_id,
        query_embedding=query_embedding,
        limit=limit,
        similarity_threshold=similarity_threshold,
    )
    if not ranked:
        return []

    item_ids = [item_id for item_id, _similarity in ranked]
    if not item_ids:
        return []

    items_by_id = {
        item.id: item
        for item in db.scalars(
            select(MemoryItem).where(MemoryItem.id.in_(item_ids))
        ).all()
    }

    from anima_server.services.agent.forgetting import HEAT_VISIBILITY_FLOOR

    results: list[tuple[MemoryItem, float]] = []
    for item_id, similarity in ranked:
        if item_id in items_by_id:
            item = items_by_id[item_id]
            if item.heat not in (None, 0.0) and item.heat < HEAT_VISIBILITY_FLOOR:
                continue
            results.append((item, similarity))
    return results[:limit]


async def embed_memory_item(
    db: Session,
    item: MemoryItem,
) -> bool:
    """Generate and store an embedding for a single memory item.

    Stores in both the embedding_json column (for portability/fallback)
    and the RuntimeEmbedding table in PG (for fast search via pgvector).
    Returns True if successful.
    """
    plaintext = df(item.user_id, item.content,
                   table="memory_items", field="content")
    prepared_text = prepare_embedding_text(plaintext)
    embedding = await generate_embedding(prepared_text)
    if embedding is None:
        return False

    item.embedding_json = embedding
    item.embedding_checksum = compute_embedding_checksum(embedding)
    db.flush()

    with contextlib.suppress(Exception):
        from anima_server.services.agent.memory_store import sync_memory_item_to_retrieval_index

        sync_memory_item_to_retrieval_index(item)

    try:
        from anima_server.services.agent.vector_store import upsert_memory

        upsert_memory(
            item.user_id,
            item_id=item.id,
            content=prepared_text or plaintext,
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
        prepared_text = prepare_embedding_text(plaintext)
        item.embedding_json = embedding
        item.embedding_checksum = compute_embedding_checksum(embedding)
        with contextlib.suppress(Exception):
            from anima_server.services.agent.memory_store import sync_memory_item_to_retrieval_index

            sync_memory_item_to_retrieval_index(item)
        try:
            from anima_server.services.agent.vector_store import upsert_memory

            upsert_memory(
                item.user_id,
                item_id=item.id,
                content=prepared_text or plaintext,
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

    repaired_any = False
    index_data: list[tuple[int, str, list[float], str, int]] = []
    for item in items:
        embedding, repaired = _validated_cached_embedding(item)
        if embedding is None:
            continue
        plaintext = df(user_id, item.content, table="memory_items", field="content")
        with contextlib.suppress(Exception):
            from anima_server.services.agent.memory_store import sync_memory_item_to_retrieval_index

            sync_memory_item_to_retrieval_index(item)
        index_data.append(
            (
                item.id,
                prepare_embedding_text(plaintext) or plaintext,
                embedding,
                item.category,
                item.importance,
            )
        )
        repaired_any = repaired_any or repaired

    if repaired_any:
        db.flush()
    if not index_data:
        return 0

    try:
        from anima_server.services.agent.vector_store import rebuild_user_index

        return rebuild_user_index(user_id, index_data, db=db)
    except Exception:
        logger.exception(
            "Failed to sync embeddings to vector store for user %d", user_id)
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
        logger.debug(
            "Runtime PG unavailable for embedding sync for user %d", user_id)
        return -1
    except Exception:
        logger.debug("Failed to open runtime PG session for user %d",
                     user_id, exc_info=True)
        return -1

    try:
        from anima_server.services.agent.pgvec_store import PgVecStore

        store = PgVecStore(runtime_db)
        count = 0
        repaired_any = False

        for item in items:
            embedding, repaired = _validated_cached_embedding(item)
            if embedding is None:
                continue

            plaintext = df(user_id, item.content,
                           table="memory_items", field="content")
            with contextlib.suppress(Exception):
                from anima_server.services.agent.memory_store import sync_memory_item_to_retrieval_index

                sync_memory_item_to_retrieval_index(item)
            store.upsert(
                user_id,
                item_id=item.id,
                content=plaintext,
                embedding=embedding,
                category=item.category,
                importance=item.importance,
            )
            count += 1
            repaired_any = repaired_any or repaired

        if repaired_any:
            soul_db.flush()
        if count > 0:
            runtime_db.commit()
        return count
    except Exception:
        runtime_db.rollback()
        logger.exception(
            "Failed to sync embeddings to runtime PG for user %d", user_id)
        return -1
    finally:
        runtime_db.close()


def _parse_embedding(raw: Any) -> list[float] | None:
    """Parse an embedding from the JSON column value."""
    return parse_embedding_payload(raw)


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
    normalized = prepare_embedding_text(text, limit=4096)
    return [w for w in normalized.lower().split() if len(w) > 1]


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
        plaintext = df(item.user_id, item.content,
                       table="memory_items", field="content")
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
    norm_bm25 = [
        s / max_bm25 for s in bm25_scores] if max_bm25 > 0.0 else [0.0] * n

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
        scores[item_id] = scores.get(
            item_id, 0.0) + semantic_weight / (_RRF_K + rank + 1)

    for rank, (item_id, _sim) in enumerate(keyword_ranked):
        scores[item_id] = scores.get(
            item_id, 0.0) + keyword_weight / (_RRF_K + rank + 1)

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

    prepared_query = prepare_embedding_text(query, limit=4096)
    if not prepared_query:
        return HybridSearchResult(items=[], query_embedding=None)

    query_embedding = await generate_embedding(prepared_query)

    from anima_server.services.agent.vector_store import search_similar

    # --- Semantic leg ---
    semantic_ranked: list[tuple[int, float]] = []
    if query_embedding is not None:
        semantic_ranked = _semantic_ranked_ids(
            db,
            user_id=user_id,
            query_embedding=query_embedding,
            limit=limit,
            similarity_threshold=similarity_threshold,
        )

    # --- Keyword leg (BM25) ---
    keyword_ranked: list[tuple[int, float]] = []
    try:
        from anima_server.services.agent.bm25_index import bm25_search

        keyword_ranked = bm25_search(user_id, query=prepared_query, limit=limit, db=db)
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
        results = _bm25_rerank(results, prepared_query, user_id)

    return HybridSearchResult(items=results, query_embedding=query_embedding)


# ---------------------------------------------------------------------------
# 1.2 — Adaptive result filtering with configurable cutoff strategies
# ---------------------------------------------------------------------------


def adaptive_filter_with_stats(
    results: list[tuple[MemoryItem, float]],
    *,
    config: AdaptiveRetrievalConfig | None = None,
) -> AdaptiveFilterResult[MemoryItem]:
    """Apply adaptive retrieval cutoffs and return results plus cutoff stats.

    The default config uses a memvid-style combined strategy. For existing call
    sites that still expect the older precision/gap heuristic, use
    ``adaptive_filter`` below.
    """
    return apply_adaptive_filter(
        results,
        config=config or AdaptiveRetrievalConfig.combined(),
    )


def adaptive_filter(
    results: list[tuple[MemoryItem, float]],
    *,
    max_results: int = 12,
    high_confidence_threshold: float = 0.7,
    min_results: int = 3,
    gap_threshold: float = 0.15,
) -> list[tuple[MemoryItem, float]]:
    """Backward-compatible legacy adaptive filter wrapper.

    This preserves the original precision-mode plus gap-detection behavior for
    existing callers and tests while ``adaptive_filter_with_stats`` drives the
    upgraded combined strategy on the live retrieval path.
    """
    return apply_adaptive_filter(
        results,
        config=AdaptiveRetrievalConfig.legacy(
            max_results=max_results,
            min_results=min_results,
            high_confidence_threshold=high_confidence_threshold,
            gap_threshold=gap_threshold,
        ),
    ).results


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

    provider = _resolve_embedding_provider()
    if provider == "scaffold":
        return [None] * len(texts)

    skip_reason = _embedding_skip_reason(provider)
    if skip_reason is not None:
        logger.debug(
            "Skipping batch embedding generation for provider %s: %s", provider, skip_reason)
        return [None] * len(texts)

    try:
        validate_provider_configuration(provider)
    except LLMConfigError:
        return [None] * len(texts)

    if provider == "ollama":
        return await _batch_embed_ollama(texts)

    prepared_items = [
        (index, prepare_embedding_text(text)) for index, text in enumerate(texts)
    ]
    non_empty_items = [item for item in prepared_items if item[1]]
    if not non_empty_items:
        return [None] * len(texts)

    prepared_results = await _batch_embed_openai_compatible(
        [text for _, text in non_empty_items],
        max_batch_size=max_batch_size,
    )

    results: list[list[float] | None] = [None] * len(texts)
    for (index, _text), embedding in zip(non_empty_items, prepared_results, strict=False):
        results[index] = embedding

    return results


async def _batch_embed_openai_compatible(
    texts: list[str],
    *,
    max_batch_size: int = 32,
) -> list[list[float] | None]:
    """Batch embedding via OpenAI-compatible /v1/embeddings with adaptive retry."""
    provider = _resolve_embedding_provider()
    base_url = resolve_base_url()
    model = _resolve_embedding_model()
    headers = build_provider_headers(provider)
    headers["Content-Type"] = "application/json"

    results: list[list[float] | None] = [None] * len(texts)
    batch_size = min(max_batch_size, len(texts))

    for start in range(0, len(texts), batch_size):
        chunk = texts[start: start + batch_size]
        current_batch = len(chunk)

        while current_batch >= 1:
            try:
                async with httpx.AsyncClient(timeout=60.0) as client:
                    # Process sub-chunks if we had to halve
                    for sub_start in range(0, len(chunk), current_batch):
                        sub_chunk = chunk[sub_start: sub_start + current_batch]
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
    if not texts:
        return []

    first_result = await generate_embedding(texts[0])
    if len(texts) == 1:
        return [first_result]

    provider_key = _provider_failure_key("ollama")
    if first_result is None and _provider_in_cooldown(provider_key):
        return [None] * len(texts)

    tasks = [generate_embedding(text) for text in texts[1:]]
    remainder = list(await asyncio.gather(*tasks))
    return [first_result, *remainder]
