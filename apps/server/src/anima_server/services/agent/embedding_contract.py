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

from sqlalchemy import delete, null, select, text

logger = logging.getLogger(__name__)
degraded_logger = logging.getLogger("anima.runtime.degraded")

# Process-local mirror of the persisted reembed flag so the per-turn
# search path doesn't pay a DB read; refreshed on every contract check
# and on re-embed completion.
_reembed_required: bool | None = None

# Process-local mirrors of per-user re-embed progress (``None`` = not loaded).
# Re-embed is per-user, so the reset and the search gate are both per-user:
# the global flag says "a cycle is open"; ``_reset_users`` are users whose
# derived stores have already been reset this cycle (so the reset runs once,
# not every pass); ``_completed_users`` are users whose backfill has finished
# (so semantic search comes back for them without re-enabling it for others).
_completed_users: set[int] | None = None
_reset_users: set[int] | None = None


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
    global _reembed_required, _completed_users, _reset_users
    _reembed_required = None
    _completed_users = None
    _reset_users = None


def _load_progress(
    runtime_db_factory: Callable[..., object] | None,
) -> tuple[set[int], set[int]]:
    """Return ``(reset_users, completed_users)`` for the active cycle."""
    global _completed_users, _reset_users
    if _reset_users is not None and _completed_users is not None:
        return _reset_users, _completed_users
    from anima_server.models.runtime_memory import ReembedCompletion

    factory = _runtime_factory(runtime_db_factory)
    if factory is None:
        _reset_users, _completed_users = set(), set()
        return _reset_users, _completed_users
    try:
        with factory() as rt_db:
            rows = rt_db.execute(
                select(ReembedCompletion.user_id, ReembedCompletion.completed)
            ).all()
        _reset_users = {int(uid) for uid, _done in rows}
        _completed_users = {int(uid) for uid, done in rows if done}
    except Exception:
        _reset_users, _completed_users = set(), set()
    return _reset_users, _completed_users


def has_reset_done(
    user_id: int,
    runtime_db_factory: Callable[..., object] | None = None,
) -> bool:
    """Whether ``user_id``'s derived stores were already reset this cycle."""
    reset_users, _ = _load_progress(runtime_db_factory)
    return int(user_id) in reset_users


def mark_reset_done(
    user_id: int,
    runtime_db_factory: Callable[..., object] | None = None,
) -> None:
    """Record that ``user_id``'s derived stores have been reset this cycle so
    the (expensive, destructive) reset runs exactly once."""
    global _reset_users
    from anima_server.models.runtime_memory import ReembedCompletion

    factory = _runtime_factory(runtime_db_factory)
    if factory is not None:
        try:
            with factory() as rt_db:
                if rt_db.get(ReembedCompletion, int(user_id)) is None:
                    rt_db.add(ReembedCompletion(user_id=int(user_id), completed=False))
                    rt_db.commit()
        except Exception:
            logger.exception("Failed to record re-embed reset for user %s", user_id)
    if _reset_users is not None:
        _reset_users.add(int(user_id))


def mark_user_reembed_complete(
    user_id: int,
    runtime_db_factory: Callable[..., object] | None = None,
) -> None:
    """Record that ``user_id`` has finished re-embedding for the current cycle.

    This does NOT clear the global ``reembed_required`` flag — clearing it
    would re-enable semantic search for every user, including those whose
    vectors are still stale (the multi-user bug this gate fixes).
    """
    global _completed_users, _reset_users
    from anima_server.models.runtime_memory import ReembedCompletion

    factory = _runtime_factory(runtime_db_factory)
    if factory is not None:
        try:
            with factory() as rt_db:
                row = rt_db.get(ReembedCompletion, int(user_id))
                if row is None:
                    rt_db.add(
                        ReembedCompletion(user_id=int(user_id), completed=True)
                    )
                else:
                    row.completed = True
                    row.updated_at = datetime.now(UTC)
                rt_db.commit()
        except Exception:
            logger.exception(
                "Failed to record re-embed completion for user %s", user_id
            )
    if _completed_users is not None:
        _completed_users.add(int(user_id))
    if _reset_users is not None:
        _reset_users.add(int(user_id))


def _clear_reembed_completions(
    runtime_db_factory: Callable[..., object] | None = None,
) -> None:
    """Start a fresh re-embed cycle: every user must reset + re-embed again."""
    global _completed_users, _reset_users
    from anima_server.models.runtime_memory import ReembedCompletion

    factory = _runtime_factory(runtime_db_factory)
    if factory is not None:
        try:
            with factory() as rt_db:
                rt_db.execute(delete(ReembedCompletion))
                rt_db.commit()
        except Exception:
            logger.debug("Failed to clear re-embed completions", exc_info=True)
    _completed_users = set()
    _reset_users = set()


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

            # Mismatch: the active (model, dim) differs from the recorded
            # re-embed target.  This fires on the first switch AND on any later
            # switch while a cycle is still open — the completion markers
            # recorded against the *previous* target are invalid in both cases.
            # Clearing must therefore be unconditional on a target change, not
            # gated on a false->true transition: a second switch would otherwise
            # leave stale "complete" rows that ungate users still holding
            # old-model vectors.  Record the new active pair as the target so
            # routine re-checks with the same model match (and don't re-clear).
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
            row.embedding_model = model[:128]
            row.embedding_dim = dim
            row.reembed_required = True
            row.updated_at = datetime.now(UTC)
            rt_db.commit()
            _clear_reembed_completions(runtime_db_factory)
            _reembed_required = True
            return False
    except Exception:
        logger.debug("Embedding contract check failed", exc_info=True)
        return True


def is_reembed_required(
    user_id: int | None = None,
    runtime_db_factory: Callable[..., object] | None = None,
) -> bool:
    """Cheap hot-path check: is semantic search currently degraded?

    The global ``reembed_required`` flag opens a re-embed cycle, but the work
    is per-user.  When ``user_id`` is given, semantic search is degraded only
    until THAT user has re-embedded — one user finishing no longer re-enables
    stale-vector search for the others.  ``user_id=None`` returns the global
    flag (any cycle open).
    """
    global _reembed_required
    if _reembed_required is None:
        from anima_server.models.runtime_memory import EmbeddingConfig

        factory = _runtime_factory(runtime_db_factory)
        if factory is None:
            _reembed_required = False
        else:
            try:
                with factory() as rt_db:
                    row = rt_db.scalar(select(EmbeddingConfig).limit(1))
                    _reembed_required = (
                        bool(row.reembed_required) if row is not None else False
                    )
            except Exception:
                _reembed_required = False

    if not _reembed_required:
        return False
    if user_id is None:
        return True
    _reset_users, completed_users = _load_progress(runtime_db_factory)
    return int(user_id) not in completed_users


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


def ensure_pgvector_dimension(
    dim: int,
    runtime_db_factory: Callable[..., object] | None = None,
) -> bool:
    """Make the pgvector ``embeddings.embedding`` column match ``dim``.

    The column is created as ``vector(<dim>)`` at migration time; on a real
    PostgreSQL runtime a model switch to a different dimension can't be
    satisfied by deleting rows — the column type is unchanged, so every
    re-embed upsert of the new-dimension vectors would fail with a dimension
    mismatch.  When the stored column dimension differs, drop all rows (they
    are all stale under the new contract anyway) and ``ALTER`` the column type.

    No-op on non-PostgreSQL backends (the sqlite variant is dimension-agnostic)
    and when the dimension already matches, so it is safe to call on every
    re-embed pass.

    Returns ``True`` when the column is aligned (a no-op counts as aligned) and
    ``False`` when the ALTER could not be applied (PG unavailable/locked), so
    the caller can retry instead of marking the reset done over a still-stale
    column.
    """
    factory = _runtime_factory(runtime_db_factory)
    if factory is None:
        return True
    try:
        with factory() as rt_db:
            if rt_db.get_bind().dialect.name != "postgresql":
                return True
            current_type = rt_db.execute(
                text(
                    "SELECT format_type(a.atttypid, a.atttypmod) "
                    "FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
                    "WHERE c.relname = 'embeddings' AND a.attname = 'embedding'"
                )
            ).scalar()
            # e.g. "vector(768)"; extract the declared dimension.
            current_dim: int | None = None
            if isinstance(current_type, str) and "(" in current_type:
                try:
                    current_dim = int(current_type.split("(")[1].split(")")[0])
                except (ValueError, IndexError):
                    current_dim = None
            if current_dim == int(dim):
                return True
            # A dimension change forces dropping ALL rows — pgvector can't hold
            # old-dimension vectors in a vector(new) column, so non-memory
            # sources go too.  Make that loud rather than silent: memory items
            # are rebuilt by the backfill and documents self-heal via the RAG
            # after-reset repair, but image-annotation and knowledge-concept
            # vectors are only rebuilt on re-ingestion.
            dropped_non_memory = (
                rt_db.execute(
                    text(
                        "SELECT count(*) FROM embeddings "
                        "WHERE source_type <> 'memory_item'"
                    )
                ).scalar()
                or 0
            )
            rt_db.execute(text("DELETE FROM embeddings"))
            rt_db.execute(
                text(
                    f"ALTER TABLE embeddings "
                    f"ALTER COLUMN embedding TYPE vector({int(dim)})"
                )
            )
            rt_db.commit()
            logger.info(
                "Recreated embeddings.embedding as vector(%d) for re-embed "
                "(was %s)",
                dim,
                current_type,
            )
            if dropped_non_memory:
                degraded_logger.warning(
                    "Dropped %d non-memory embedding rows while realigning the "
                    "pgvector column to dim %d; document vectors self-heal on "
                    "the next RAG query, image/knowledge-concept vectors rebuild "
                    "on re-ingestion.",
                    dropped_non_memory,
                    dim,
                )
            return True
    except Exception:
        logger.exception(
            "Failed to align pgvector column to dimension %d", dim
        )
        return False


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
                # Only ``memory_item`` vectors are cleared here: the re-embed
                # backfill regenerates those from ``MemoryItem.content``.  The
                # other sources (``document_chunk``, ``image_annotation``,
                # ``knowledge_concept``) share the embedding model but have NO
                # backfill in this cycle, so deleting them would drop those
                # memories from recall with no way to rebuild them — a worse
                # regression than a stale vector on an operator-initiated model
                # change.  Re-embedding non-memory sources is a separate follow
                # up; dimension changes still wipe the whole column via
                # ``ensure_pgvector_dimension`` (unavoidable there).
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
