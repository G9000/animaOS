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
import logging
import math
import os
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.config import settings
from anima_server.models import MemoryItem
from anima_server.services import anima_core_bindings
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
    "doubleword": "Qwen/Qwen3-Embedding-8B",
    "fastembed": "BAAI/bge-small-en-v1.5",
}

_DEFAULT_EMBEDDING_BASE_URLS: dict[str, str] = {
    "ollama": "http://127.0.0.1:11434",
    "openrouter": "https://openrouter.ai/api/v1",
    "moonshot": "https://api.moonshot.cn/v1",
    "vllm": "http://127.0.0.1:8000/v1",
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com/v1",
    "doubleword": "https://api.doubleword.ai/v1",
}

_EMBEDDING_API_KEY_ENV: dict[str, str] = {
    "doubleword": "DOUBLEWORD_API_KEY",
}


def _setting_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _resolve_embedding_provider() -> str:
    configured = _setting_text(getattr(settings, "agent_embedding_provider", ""))
    if configured:
        return configured
    return _setting_text(getattr(settings, "agent_provider", "")) or "ollama"


def _resolve_embedding_api_key(provider: str | None = None) -> str:
    configured = _setting_text(getattr(settings, "agent_embedding_api_key", ""))
    if configured:
        return configured
    resolved_provider = provider or _resolve_embedding_provider()
    env_name = _EMBEDDING_API_KEY_ENV.get(resolved_provider)
    if env_name is not None:
        configured = os.getenv(env_name, "").strip()
        if configured:
            return configured

    from anima_server.config import get_provider_api_key, has_provider_api_keys

    configured = get_provider_api_key(resolved_provider).strip()
    if configured:
        return configured

    if not has_provider_api_keys():
        configured = _setting_text(getattr(settings, "agent_api_key", ""))
        if configured:
            return configured
    return ""


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
    if provider == "fastembed":
        # In-process ONNX backend — no HTTP endpoint of any kind.
        return ""
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
    if provider in (
        "openrouter",
        "moonshot",
        "openai",
        "doubleword",
    ) and not _resolve_embedding_api_key(provider):
        key_hint = (
            "ANIMA_AGENT_EMBEDDING_API_KEY, saved provider API key, "
            "or ANIMA_AGENT_API_KEY"
        )
        env_name = _EMBEDDING_API_KEY_ENV.get(provider)
        if env_name is not None:
            key_hint = (
                "ANIMA_AGENT_EMBEDDING_API_KEY, saved provider API key, "
                f"ANIMA_AGENT_API_KEY, or {env_name}"
            )
        raise LLMConfigError(
            f"{key_hint} is required when embedding_provider='{provider}'"
        )


def validate_provider_configuration(provider: str) -> None:
    _validate_embedding_provider_configuration(provider)


def _build_embedding_headers(provider: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    api_key = _resolve_embedding_api_key(provider)

    if provider == "openrouter":
        headers["Authorization"] = f"Bearer {api_key}"
        headers["HTTP-Referer"] = "https://anima.local"
        headers["X-Title"] = "ANIMA"
        return headers

    if provider == "moonshot":
        headers["Authorization"] = f"Bearer {api_key}"
        headers["Content-Type"] = "application/json"
        return headers

    if provider in ("openai", "doubleword"):
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
    if provider == "anthropic":
        return "provider has no embeddings endpoint; configure an explicit embedding provider"
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
    # A config change must also re-arm the one-shot cold-start sync and
    # re-read the persisted contract — otherwise stale-model embeddings
    # keep serving searches until a process restart.
    from anima_server.services.agent.embedding_contract import reset_contract_cache
    from anima_server.services.agent.vector_store import clear_synced_users

    clear_synced_users()
    reset_contract_cache()


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
        if provider == "fastembed":
            from anima_server.services.agent.fastembed_backend import embed_texts

            vectors = await asyncio.to_thread(embed_texts, [prepared_text], model_name=model)
            result = vectors[0]
        elif provider == "ollama":
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
        _note_detected_embedding_dim(len(result))
    return result


def _note_detected_embedding_dim(dim: int) -> None:
    """Auto-detect the embedding dimension on the first successful embedding and
    verify it against the persisted contract.

    Shared by the single- and batch-embedding paths: the sleep backfill embeds
    via ``generate_embeddings_batch``, so gating the contract check on the
    single path alone let a model/dimension switch slip through undetected when
    the first embedding work of a process was a batch backfill — leaving
    ``reembed_required`` unset and mixing new-model vectors into stale stores.
    """
    from anima_server.config import _detected_embedding_dim, set_detected_embedding_dim

    if _detected_embedding_dim is not None:
        return
    set_detected_embedding_dim(dim)
    logger.info(
        "Auto-detected embedding dimension: %d (model=%s)",
        dim,
        _resolve_embedding_model(),
    )
    # A model switch used to surface only as swallowed pgvector errors,
    # silently degrading retrieval to keyword-only.
    from anima_server.services.agent.embedding_contract import check_embedding_contract

    check_embedding_contract(model=_resolve_embedding_model(), dim=dim)


def _note_first_embedding_dim(results: list[list[float] | None]) -> None:
    """Record the dimension from the first non-empty embedding in a batch."""
    for embedding in results:
        if embedding:
            _note_detected_embedding_dim(len(embedding))
            return


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
    if anima_core_bindings.rust_cosine_similarity is not None:
        try:
            return float(anima_core_bindings.rust_cosine_similarity(list(a), list(b)))
        except Exception:
            logger.debug("Rust cosine similarity failed; falling back to Python", exc_info=True)
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _set_embedding_checksum(target: Any, checksum: str) -> bool:
    try:
        target.embedding_checksum = checksum
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
    runtime_db: Session | None = None,
) -> list[tuple[int, float]]:
    from anima_server.services.agent.embedding_contract import is_reembed_required

    if is_reembed_required(user_id):
        # This user's derived stores were built with a different
        # model/dimension and haven't been re-embedded yet — comparing
        # against them would be wrong or raise.  Degrade loudly (once per
        # process the contract check logged ERROR).  The gate is per-user:
        # another user completing their re-embed doesn't re-enable this one.
        logger.debug(
            "Semantic leg skipped for user %d: re-embed required", user_id
        )
        return []

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
            runtime_db=runtime_db,
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
        from anima_server.services.agent.memory_store import (
            ensure_memory_retrieval_index_ready,
        )
        from anima_server.services.agent.retrieval_backends import (
            get_memory_retrieval_backend,
        )

        backend = get_memory_retrieval_backend()
        if not ensure_memory_retrieval_index_ready(db, user_id=user_id, backend=backend):
            return None
        hits = backend.search_memory_by_vector(
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
        ranked.append((hit.record_id, hit.score))
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
        _mark_vector_upsert_failed(item.user_id, item.id)

    return True


def _mark_vector_upsert_failed(user_id: int, item_id: int) -> None:
    """A failed pgvector write leaves the item searchable in one backend
    and invisible in another — flag the user so the next backfill task
    re-syncs instead of the failure being swallowed at debug level."""
    degraded_logger = logging.getLogger("anima.runtime.degraded")
    degraded_logger.warning(
        "Vector-store upsert failed for item %d (user %d); "
        "flagged for re-sync on the next embedding backfill",
        item_id,
        user_id,
    )
    try:
        from anima_server.services.agent.vector_store import mark_vector_store_dirty

        mark_vector_store_dirty(user_id)
    except Exception:
        pass


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
            _mark_vector_upsert_failed(item.user_id, item.id)
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
        # Preserve the retry: the caller consumed the dirty marker before this
        # pass, so if the vector store is still unavailable we must re-arm it or
        # a later maintenance pass would never retry and the user stays missing
        # runtime vectors until another write fails or the process restarts.
        from anima_server.services.agent.vector_store import mark_vector_store_dirty

        mark_vector_store_dirty(user_id)
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
                from anima_server.services.agent.memory_store import (
                    sync_memory_item_to_retrieval_index,
                )

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
        # Commit even when no embeddings were written. In tests the runtime
        # engine uses StaticPool, so closing this owned read/write session
        # without a commit can roll back an outer request transaction sharing
        # the same SQLite connection.
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
    # Decrypted content per item id: each surviving item is AEAD-decrypted
    # exactly once here so downstream consumers (fragments, blocks) stop
    # re-decrypting the same rows on the pre-first-token path.
    plaintexts: dict[int, str] | None = None
    # Top raw cosine similarity among the returned items (``None`` when the
    # semantic leg contributed nothing, e.g. keyword-only hits).  The per-item
    # ``float`` scores above are the fused RRF+BM25(+recency) ranking scale
    # (top renormalised to 1.0), which is meaningless for an absolute
    # confidence floor; this carries the same-scale cosine value so the
    # downstream ``absolute_min`` gate can judge whether the best match is
    # actually relevant.
    max_cosine: float | None = None


def _tokenize(text: str) -> list[str]:
    """Simple whitespace tokenizer with lowercasing and minimum length filter."""
    normalized = prepare_embedding_text(text, limit=4096)
    return [w for w in normalized.lower().split() if len(w) > 1]


def _blend_keyword_scores(
    results: list[tuple[MemoryItem, float]],
    keyword_ranked: list[tuple[int, float]],
    *,
    rrf_weight: float = 0.7,
    bm25_weight: float = 0.3,
) -> list[tuple[MemoryItem, float]]:
    """Lexical-precision boost reusing the keyword leg's BM25 scores.

    The old rerank stage recomputed an independent, differently-tokenized
    BM25 over freshly-decrypted content — ~15 extra AEAD decrypts plus an
    O(n·|q|) scoring pass per turn, before first token.  The keyword leg
    already produced BM25 scores for the same query; normalise both
    distributions to [0, 1] and sort by the weighted combination.

    The RRF side is normalised even when there is no lexical signal (no
    keyword hits, or a single result): raw RRF scores sit around
    ``1/(k+rank)`` (< 0.02), so leaving them un-normalised would let the
    downstream raw ``absolute_min`` gate drop a strong semantic-only match.
    Normalising keeps the top hit at 1.0 on the same scale as the blended
    case.
    """
    if not results:
        return results

    n = len(results)
    rrf_scores = [score for _, score in results]
    max_rrf = max(rrf_scores)
    norm_rrf = (
        [score / max_rrf for score in rrf_scores] if max_rrf > 0.0 else [0.0] * n
    )

    if not keyword_ranked:
        # No lexical signal to blend — return the RRF ranking (already sorted)
        # rescaled to [0, 1] so it is comparable to the blended path and
        # survives the downstream absolute-threshold gate.
        return [
            (item, norm_rrf[i]) for i, (item, _original_score) in enumerate(results)
        ]

    bm25_by_id = {item_id: score for item_id, score in keyword_ranked}
    bm25_scores = [bm25_by_id.get(item.id, 0.0) for item, _ in results]
    max_bm25 = max(bm25_scores)
    norm_bm25 = (
        [score / max_bm25 for score in bm25_scores] if max_bm25 > 0.0 else [0.0] * n
    )

    combined = [
        (item, rrf_weight * norm_rrf[i] + bm25_weight * norm_bm25[i])
        for i, (item, _original_score) in enumerate(results)
    ]
    combined.sort(key=lambda pair: pair[1], reverse=True)
    return combined


def recency_heat_rerank(
    results: list[tuple[MemoryItem, float]],
    *,
    now: datetime | None = None,
) -> list[tuple[MemoryItem, float]]:
    """Blend relevance scores with recency and heat.

    Pure RRF fusion has no time signal, so "what did I say I'd do this
    week?" can surface a semantically similar item from months ago over
    the relevant recent one.  Blends:

        final = w_rel * relevance + w_recency * exp(-days/tau) + w_heat * heat_norm

    Weights come from settings (``agent_retrieval_*_weight``); heat is
    normalised against the hottest item in the pool so the term is
    scale-free.  Input scores are assumed normalised to [0, 1] (the
    post-rerank hybrid_search scale).
    """
    if not results:
        return results

    w_rel = settings.agent_retrieval_relevance_weight
    w_recency = settings.agent_retrieval_recency_weight
    w_heat = settings.agent_retrieval_heat_weight
    tau_days = max(0.1, settings.agent_retrieval_recency_tau_days)

    ref_now = now or datetime.now(UTC)
    max_heat = max((item.heat or 0.0) for item, _score in results)

    blended: list[tuple[MemoryItem, float]] = []
    for item, score in results:
        recency_ref = item.updated_at or item.created_at
        recency = 0.0
        if recency_ref is not None:
            if recency_ref.tzinfo is None:
                recency_ref = recency_ref.replace(tzinfo=UTC)
            days = max(0.0, (ref_now - recency_ref).total_seconds() / 86400.0)
            recency = math.exp(-days / tau_days)
        heat_norm = (item.heat or 0.0) / max_heat if max_heat > 0.0 else 0.0
        blended.append(
            (item, w_rel * score + w_recency * recency + w_heat * heat_norm)
        )

    blended.sort(key=lambda pair: pair[1], reverse=True)
    return blended


def _reciprocal_rank_fusion(
    semantic_ranked: list[tuple[int, float]],
    keyword_ranked: list[tuple[int, float]],
    *,
    semantic_weight: float = 0.5,
    keyword_weight: float = 0.5,
) -> list[tuple[int, float]]:
    """Merge two ranked lists using RRF. Returns (item_id, rrf_score) sorted descending."""
    if (
        anima_core_bindings.rust_rrf_fuse is not None
        and abs(semantic_weight - keyword_weight) < 1e-12
    ):
        try:
            ranked_lists = [
                [(int(item_id), float(score)) for item_id, score in semantic_ranked],
                [(int(item_id), float(score)) for item_id, score in keyword_ranked],
            ]
            return [
                (int(item_id), float(score) * semantic_weight)
                for item_id, score in anima_core_bindings.rust_rrf_fuse(ranked_lists, _RRF_K)
            ]
        except Exception:
            logger.debug("Rust RRF fusion failed; falling back to Python", exc_info=True)

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
    runtime_db: Session | None = None,
    recency_heat_blend: bool = False,
) -> HybridSearchResult:
    """Combined semantic + keyword search over memory items using RRF merge.

    When *tags* is provided, post-filters results to only include items
    that match the given tags (using "any" or "all" match mode).

    With ``recency_heat_blend=True`` the final scores additionally factor
    in item recency and heat (used by the automatic per-turn retrieval,
    which otherwise has no time signal; explicit searches keep pure
    relevance ranking).

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

    # --- Semantic leg ---
    semantic_ranked: list[tuple[int, float]] = []
    if query_embedding is not None:
        semantic_ranked = _semantic_ranked_ids(
            db,
            user_id=user_id,
            query_embedding=query_embedding,
            limit=limit,
            similarity_threshold=similarity_threshold,
            runtime_db=runtime_db,
        )

    # --- Keyword leg (BM25) ---
    keyword_ranked: list[tuple[int, float]] = []
    try:
        from anima_server.services.agent.bm25_index import bm25_search

        keyword_ranked = bm25_search(
            user_id,
            query=prepared_query,
            limit=limit,
            db=db,
            runtime_db=runtime_db,
        )
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

    # Resolve the full merged candidate pool, then apply post-filters while
    # walking the ranking. Early candidates may be missing, tag-filtered, or
    # below the heat floor; later valid candidates should still backfill.
    merged_ids = [item_id for item_id, _ in merged]
    items_by_id = (
        {
            item.id: item
            for item in db.scalars(select(MemoryItem).where(MemoryItem.id.in_(merged_ids))).all()
        }
        if merged_ids
        else {}
    )

    from anima_server.services.agent.forgetting import HEAT_VISIBILITY_FLOOR

    # Raw cosine per id (before RRF/BM25 fusion) so the confidence floor can be
    # judged on the semantic scale rather than the renormalised ranking scale.
    cosine_by_id = {item_id: sim for item_id, sim in semantic_ranked}

    results: list[tuple[MemoryItem, float]] = []
    for item_id, rrf_score in merged:
        if item_id in items_by_id:
            if allowed_ids is not None and item_id not in allowed_ids:
                continue
            item = items_by_id[item_id]
            # Respect passive forgetting: skip items that have been scored
            # (heat > 0) but decayed below the visibility floor.
            if item.heat not in (None, 0.0) and item.heat < HEAT_VISIBILITY_FLOOR:
                continue
            results.append((item, rrf_score))
            if len(results) >= limit:
                break

    max_cosine = max(
        (cosine_by_id[item.id] for item, _ in results if item.id in cosine_by_id),
        default=None,
    )

    # --- Lexical rerank stage (reuses the keyword leg's BM25 scores) ---
    if results:
        results = _blend_keyword_scores(results, keyword_ranked)

    if results and recency_heat_blend:
        results = recency_heat_rerank(results)

    plaintexts = {
        item.id: df(user_id, item.content, table="memory_items", field="content")
        for item, _score in results
    }
    return HybridSearchResult(
        items=results,
        query_embedding=query_embedding,
        plaintexts=plaintexts,
        max_cosine=max_cosine,
    )


# ---------------------------------------------------------------------------
# 1.2 — Adaptive result filtering with configurable cutoff strategies
# ---------------------------------------------------------------------------


def adaptive_filter_with_stats(
    results: list[tuple[MemoryItem, float]],
    *,
    config: AdaptiveRetrievalConfig | None = None,
    confidence_score: float | None = None,
) -> AdaptiveFilterResult[MemoryItem]:
    """Apply adaptive retrieval cutoffs and return results plus cutoff stats.

    The default config uses a memvid-style combined strategy. For existing call
    sites that still expect the older precision/gap heuristic, use
    ``adaptive_filter`` below.

    ``confidence_score`` is the raw same-scale signal (cosine similarity) used
    by the ``absolute_min`` floor.  The per-item scores here are the fused
    ranking scale whose top is renormalised to 1.0, so without this the floor
    can never reject a low-confidence set.  ``None`` keeps the legacy behaviour
    of gating on the top ranking score.
    """
    return apply_adaptive_filter(
        results,
        config=config or AdaptiveRetrievalConfig.combined(),
        confidence_score=confidence_score,
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
        results = await _batch_embed_ollama(texts)
        _note_first_embedding_dim(results)
        return results

    non_empty_items = _prepare_non_empty_items(texts)
    if not non_empty_items:
        return [None] * len(texts)

    if provider == "fastembed":
        from anima_server.services.agent.fastembed_backend import embed_texts

        prepared_results = await asyncio.to_thread(
            embed_texts,
            [text for _, text in non_empty_items],
            model_name=_resolve_embedding_model(),
        )
    else:
        prepared_results = await _batch_embed_openai_compatible(
            [text for _, text in non_empty_items],
            max_batch_size=max_batch_size,
        )

    results = _scatter_batch_results(non_empty_items, prepared_results, total=len(texts))

    # Same-scale contract check as the single path, so a model/dimension switch
    # is caught even when the first embedding work is a batch backfill.
    _note_first_embedding_dim(results)
    return results


def _prepare_non_empty_items(texts: list[str]) -> list[tuple[int, str]]:
    """Return ``(original_index, prepared_text)`` pairs for non-empty texts."""
    prepared_items = [
        (index, prepare_embedding_text(text)) for index, text in enumerate(texts)
    ]
    return [item for item in prepared_items if item[1]]


def _scatter_batch_results(
    non_empty_items: list[tuple[int, str]],
    embeddings: list[list[float] | None],
    *,
    total: int,
) -> list[list[float] | None]:
    """Scatter per-prepared-text embeddings back to input positions."""
    results: list[list[float] | None] = [None] * total
    for (index, _text), embedding in zip(non_empty_items, embeddings, strict=False):
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
