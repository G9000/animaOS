"""Persisted (model, dimension) embedding contract.

The derived embedding stores — soul-side ``embedding_json``, the pgvector
``runtime_embeddings`` table, and the rust index — are only meaningful
for the model/dimension they were built with.  This module records the
active pair, detects mismatches loudly (instead of the old behaviour:
pgvector queries raising into a swallowed except, degrading retrieval to
keyword-only forever), and drives the re-embed recovery path.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import delete, null, select

logger = logging.getLogger(__name__)
degraded_logger = logging.getLogger("anima.runtime.degraded")

# Process-local mirror of the persisted reembed flag so the per-turn
# search path doesn't pay a DB read; refreshed on every contract check
# and on re-embed completion.
_reembed_required: bool | None = None


def _runtime_factory(
    runtime_db_factory: Callable[..., object] | None,
) -> Callable[..., object] | None:
    if runtime_db_factory is not None:
        return runtime_db_factory
    from anima_server.db.runtime import get_runtime_session_factory

    try:
        return get_runtime_session_factory()
    except RuntimeError:
        return None


def reset_contract_cache() -> None:
    global _reembed_required
    _reembed_required = None


def check_embedding_contract(
    *,
    model: str,
    dim: int,
    runtime_db_factory: Callable[..., object] | None = None,
) -> bool:
    """Record or verify the active (model, dim) pair.

    Returns True when the contract matches (or was adopted on first use);
    False when the active model/dimension differs from what the stores
    were built with — in which case ``reembed_required`` is persisted and
    semantic search reports itself degraded until the re-embed completes.
    """
    global _reembed_required
    from anima_server.models.runtime_memory import EmbeddingConfig

    factory = _runtime_factory(runtime_db_factory)
    if factory is None:
        return True  # no runtime DB (tests, degraded boot) — nothing to verify

    try:
        with factory() as rt_db:
            row = rt_db.scalar(select(EmbeddingConfig).limit(1))
            if row is None:
                rt_db.add(
                    EmbeddingConfig(
                        embedding_model=model[:128],
                        embedding_dim=dim,
                    )
                )
                rt_db.commit()
                _reembed_required = False
                return True

            if row.embedding_model == model[:128] and row.embedding_dim == dim:
                _reembed_required = bool(row.reembed_required)
                return not row.reembed_required

            if not row.reembed_required:
                row.reembed_required = True
                row.updated_at = datetime.now(UTC)
                rt_db.commit()
                degraded_logger.error(
                    "Embedding contract mismatch: stores were built with "
                    "%s (dim %d) but the active model is %s (dim %d). "
                    "Semantic search is disabled until the re-embed "
                    "backfill completes.",
                    row.embedding_model,
                    row.embedding_dim,
                    model,
                    dim,
                )
            _reembed_required = True
            return False
    except Exception:
        logger.debug("Embedding contract check failed", exc_info=True)
        return True


def is_reembed_required(
    runtime_db_factory: Callable[..., object] | None = None,
) -> bool:
    """Cheap hot-path check: is semantic search currently degraded?"""
    global _reembed_required
    if _reembed_required is not None:
        return _reembed_required

    from anima_server.models.runtime_memory import EmbeddingConfig

    factory = _runtime_factory(runtime_db_factory)
    if factory is None:
        _reembed_required = False
        return False
    try:
        with factory() as rt_db:
            row = rt_db.scalar(select(EmbeddingConfig).limit(1))
            _reembed_required = bool(row.reembed_required) if row is not None else False
    except Exception:
        _reembed_required = False
    return _reembed_required


def complete_reembed(
    *,
    model: str,
    dim: int,
    runtime_db_factory: Callable[..., object] | None = None,
) -> None:
    """Adopt the new (model, dim) pair after a successful full re-embed."""
    global _reembed_required
    from anima_server.models.runtime_memory import EmbeddingConfig

    factory = _runtime_factory(runtime_db_factory)
    if factory is None:
        return
    try:
        with factory() as rt_db:
            row = rt_db.scalar(select(EmbeddingConfig).limit(1))
            if row is None:
                row = EmbeddingConfig(embedding_model=model[:128], embedding_dim=dim)
                rt_db.add(row)
            row.embedding_model = model[:128]
            row.embedding_dim = dim
            row.reembed_required = False
            row.updated_at = datetime.now(UTC)
            rt_db.commit()
        _reembed_required = False
        logger.info(
            "Embedding contract updated: model=%s dim=%d (re-embed complete)",
            model,
            dim,
        )
    except Exception:
        logger.exception("Failed to update embedding contract after re-embed")


def reset_derived_embedding_stores(
    soul_db,
    *,
    user_id: int,
    runtime_db_factory: Callable[..., object] | None = None,
) -> int:
    """Clear derived embeddings so the backfill regenerates everything.

    Embeddings are derived data: soul-side ``embedding_json`` is nulled,
    the user's pgvector rows are deleted (the JSON-typed sqlite variant
    and empty pgvector column tolerate any dimension afterwards), and the
    retrieval indexes are invalidated.  Returns the number of soul items
    marked for re-embedding.
    """
    from anima_server.models import MemoryItem

    items = list(
        soul_db.scalars(
            select(MemoryItem).where(
                MemoryItem.user_id == user_id,
                MemoryItem.embedding_json.isnot(None),
            )
        ).all()
    )
    for item in items:
        # Assign a SQL NULL, not Python None: this JSON column is not
        # none_as_null, so `item.embedding_json = None` would persist the JSON
        # value 'null' — which `embedding_json IS NULL` (used by the backfill
        # selector and the remaining-count check) does NOT match, leaving reset
        # items un-re-embedded and the contract falsely "complete".
        item.embedding_json = null()
        item.embedding_checksum = None
    soul_db.flush()

    factory = _runtime_factory(runtime_db_factory)
    if factory is not None:
        try:
            from anima_server.models.runtime_embedding import RuntimeEmbedding

            with factory() as rt_db:
                rt_db.execute(
                    delete(RuntimeEmbedding).where(
                        RuntimeEmbedding.user_id == user_id,
                        RuntimeEmbedding.source_type == "memory_item",
                    )
                )
                rt_db.commit()
        except Exception:
            logger.debug(
                "Failed to clear runtime embeddings for user %d", user_id,
                exc_info=True,
            )

    try:
        from anima_server.services.agent.bm25_index import invalidate_index

        invalidate_index(user_id)
    except Exception:
        pass
    try:
        from anima_server.services.agent.memory_store import (
            invalidate_memory_retrieval_indexes,
        )

        invalidate_memory_retrieval_indexes(user_id, mark_dirty=True)
    except Exception:
        pass

    return len(items)


def sweep_orphaned_runtime_embeddings(
    soul_db,
    *,
    user_id: int,
    runtime_db_factory: Callable[..., object] | None = None,
) -> int:
    """Delete pgvector rows whose source memory item no longer exists.

    Cleanup previously ran only via the ``forget_memory`` after-commit
    hook, so any other deletion path left live rows serving stale vectors.
    """
    from anima_server.models import MemoryItem

    factory = _runtime_factory(runtime_db_factory)
    if factory is None:
        return 0

    live_ids = {
        int(item_id)
        for item_id in soul_db.scalars(
            select(MemoryItem.id).where(MemoryItem.user_id == user_id)
        ).all()
    }

    try:
        from anima_server.models.runtime_embedding import RuntimeEmbedding

        with factory() as rt_db:
            stored_ids = [
                int(source_id)
                for source_id in rt_db.scalars(
                    select(RuntimeEmbedding.source_id).where(
                        RuntimeEmbedding.user_id == user_id,
                        RuntimeEmbedding.source_type == "memory_item",
                    )
                ).all()
            ]
            orphaned = [sid for sid in stored_ids if sid not in live_ids]
            if not orphaned:
                return 0
            rt_db.execute(
                delete(RuntimeEmbedding).where(
                    RuntimeEmbedding.user_id == user_id,
                    RuntimeEmbedding.source_type == "memory_item",
                    RuntimeEmbedding.source_id.in_(orphaned),
                )
            )
            rt_db.commit()
            logger.info(
                "Swept %d orphaned runtime embeddings for user %d",
                len(orphaned),
                user_id,
            )
            return len(orphaned)
    except Exception:
        logger.debug(
            "Orphaned-embedding sweep failed for user %d", user_id, exc_info=True
        )
        return 0
