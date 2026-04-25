"""Soul Writer — single serialized promoter from PG runtime to SQLCipher soul vault.

Triggered by: pre-turn check, inactivity, compaction, shutdown, threshold.
Guarantees: per-user asyncio lock, per-item transactions, idempotent via content hash.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Per-user locks — prevents concurrent Soul Writer runs for the same user
_user_locks: dict[int, asyncio.Lock] = {}
MAX_RETRY_COUNT = 3
MAX_ITEMS_PER_RUN = 50


def _get_user_lock(user_id: int) -> asyncio.Lock:
    return _user_locks.setdefault(user_id, asyncio.Lock())


@dataclass(slots=True)
class PromotionDecision:
    action: str  # "promote" | "supersede" | "rejected"
    reason: str = ""
    old_item: object | None = None  # MemoryItem when action == "supersede"


@dataclass(slots=True)
class SoulWriterResult:
    ops_processed: int = 0
    ops_skipped: int = 0
    ops_failed: int = 0
    candidates_promoted: int = 0
    candidates_rejected: int = 0
    candidates_superseded: int = 0
    candidates_failed: int = 0
    access_sync: dict = field(default_factory=dict)
    retrieval_feedback_sync: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


async def _embed_and_index_item(
    user_id: int,
    item_id: int,
    content: str,
    category: str,
    importance: int,
    soul_db: Session,
) -> None:
    """Generate embedding for a newly promoted item and upsert into indexes."""
    try:
        from anima_server.models import MemoryItem
        from anima_server.services.agent.bm25_index import invalidate_index
        from anima_server.services.agent.embedding_integrity import compute_embedding_checksum
        from anima_server.services.agent.embeddings import generate_embedding
        from anima_server.services.agent.memory_store import sync_memory_item_to_retrieval_index
        from anima_server.services.agent.vector_store import upsert_memory

        embedding = await generate_embedding(content)
        if embedding is None:
            return

        item = soul_db.get(MemoryItem, item_id)
        if item is not None:
            item.embedding_json = embedding
            item.embedding_checksum = compute_embedding_checksum(embedding)
            soul_db.flush()
            sync_memory_item_to_retrieval_index(item)

            upsert_memory(
                user_id,
                item_id=item_id,
                content=content,
                embedding=embedding,
                category=category,
                importance=importance,
                db=soul_db,
            )

        invalidate_index(user_id)
        logger.debug(
            "Embedded and indexed promoted item %d for user %s", item_id, user_id)
    except Exception:
        logger.debug("Failed to embed promoted item %d",
                     item_id, exc_info=True)


async def run_soul_writer(
    user_id: int,
    *,
    soul_db_factory: Callable[..., object] | None = None,
    runtime_db_factory: Callable[..., object] | None = None,
) -> SoulWriterResult:
    """Main entry point. Acquires per-user lock and runs the promotion pipeline."""
    lock = _get_user_lock(user_id)
    result = SoulWriterResult()

    # Non-blocking acquire — if another run is in progress, skip
    if lock.locked():
        logger.debug(
            "Soul Writer already running for user %s, skipping", user_id)
        return result

    async with lock:
        try:
            # Capture the event loop so worker threads can schedule coroutines back
            loop = asyncio.get_running_loop()
            # Run sync DB work in a thread to avoid blocking the event loop
            await asyncio.to_thread(
                _run_soul_writer_inner,
                user_id,
                result=result,
                soul_db_factory=soul_db_factory,
                runtime_db_factory=runtime_db_factory,
                event_loop=loop,
            )
        except Exception as e:
            logger.exception("Soul Writer failed for user %s", user_id)
            result.errors.append(str(e))

    total_work = (
        result.ops_processed
        + result.ops_skipped
        + result.ops_failed
        + result.candidates_promoted
        + result.candidates_rejected
        + result.candidates_superseded
        + result.candidates_failed
    )
    if total_work > 0 or result.errors:
        logger.info(
            "Soul Writer user=%s: ops=%d/%d/%d cands=%d/%d/%d/%d access=%s retrieval=%s errors=%d",
            user_id,
            result.ops_processed,
            result.ops_skipped,
            result.ops_failed,
            result.candidates_promoted,
            result.candidates_rejected,
            result.candidates_superseded,
            result.candidates_failed,
            result.access_sync.get("items_synced", 0),
            result.retrieval_feedback_sync.get("items_synced", 0),
            len(result.errors),
        )

    return result


def _run_soul_writer_inner(
    user_id: int,
    *,
    result: SoulWriterResult,
    soul_db_factory: Callable[..., object] | None = None,
    runtime_db_factory: Callable[..., object] | None = None,
    event_loop: asyncio.AbstractEventLoop | None = None,
) -> None:
    """Inner pipeline — called under lock via asyncio.to_thread."""
    from anima_server.db.runtime import get_runtime_session_factory
    from anima_server.db.session import SessionLocal

    rt_factory = runtime_db_factory or get_runtime_session_factory()
    soul_factory = soul_db_factory or SessionLocal

    # Phase 1: Process PendingMemoryOps
    with rt_factory() as runtime_db:
        from anima_server.services.agent.pending_ops import get_pending_ops

        pending_ops = get_pending_ops(runtime_db, user_id=user_id)

        # Also retry previously-failed ops (transient errors like SQLCipher busy)
        from anima_server.models.pending_memory_op import PendingMemoryOp as _PendingOp

        failed_ops = list(
            runtime_db.scalars(
                select(_PendingOp)
                .where(
                    _PendingOp.user_id == user_id,
                    _PendingOp.consolidated.is_(False),
                    _PendingOp.failed.is_(True),
                )
                .order_by(_PendingOp.id.asc())
                .limit(MAX_ITEMS_PER_RUN)
            ).all()
        )
        for op in failed_ops:
            op.failed = False
            op.failure_reason = None
        if failed_ops:
            runtime_db.flush()
            pending_ops.extend(failed_ops)

        for op in pending_ops:
            try:
                _process_pending_op(
                    op,
                    user_id=user_id,
                    runtime_db=runtime_db,
                    soul_db_factory=soul_factory,
                    result=result,
                )
            except Exception as e:
                logger.exception("Soul Writer op %s failed", op.id)
                op.failed = True
                op.failure_reason = str(e)[:500]
                result.ops_failed += 1
                result.errors.append(f"op {op.id}: {e}")

        runtime_db.commit()

    # Phase 2: Process MemoryCandidates
    with rt_factory() as runtime_db:
        from anima_server.models.runtime_memory import MemoryCandidate

        candidates = list(
            runtime_db.scalars(
                select(MemoryCandidate)
                .where(
                    MemoryCandidate.user_id == user_id,
                    MemoryCandidate.status.in_(["extracted", "queued"]),
                )
                .order_by(MemoryCandidate.created_at)
                .limit(MAX_ITEMS_PER_RUN)
            ).all()
        )

        # Also retry failed candidates below the max retry threshold,
        # but exclude those whose content_hash already appears in an active row
        # to avoid violating the partial unique index uq_memory_candidates_active_hash.
        remaining = MAX_ITEMS_PER_RUN - len(candidates)
        if remaining > 0:
            active_hashes = {c.content_hash for c in candidates}
            failed_retryable = list(
                runtime_db.scalars(
                    select(MemoryCandidate)
                    .where(
                        MemoryCandidate.user_id == user_id,
                        MemoryCandidate.status == "failed",
                        MemoryCandidate.retry_count < MAX_RETRY_COUNT,
                    )
                    .order_by(MemoryCandidate.created_at)
                    .limit(remaining)
                ).all()
            )
            candidates.extend(
                c for c in failed_retryable if c.content_hash not in active_hashes
            )

        for candidate in candidates:
            candidate.status = "queued"

        try:
            runtime_db.flush()
        except Exception:
            # A concurrent writer may have created an active duplicate since
            # our read above.  Fall back to per-row updates, skipping any
            # that violate the unique constraint.
            runtime_db.rollback()
            from sqlalchemy.exc import IntegrityError as _IE

            for candidate in candidates:
                candidate.status = "queued"
                try:
                    runtime_db.flush()
                except _IE:
                    runtime_db.rollback()
                    logger.debug(
                        "Skipping candidate %s: active duplicate hash %s",
                        candidate.id, candidate.content_hash,
                    )
                    candidate.status = "failed"
                    candidates = [
                        c for c in candidates if c.status == "queued"]
                    break

        for candidate in candidates:
            try:
                _process_candidate(
                    candidate,
                    user_id=user_id,
                    runtime_db=runtime_db,
                    soul_db_factory=soul_factory,
                    result=result,
                    event_loop=event_loop,
                )
            except Exception as e:
                logger.exception(
                    "Soul Writer candidate %s failed", candidate.id)
                candidate.status = "failed"
                candidate.last_error = str(e)[:500]
                candidate.retry_count = (candidate.retry_count or 0) + 1
                result.candidates_failed += 1
                result.errors.append(f"candidate {candidate.id}: {e}")

        runtime_db.commit()

    # Phase 3: Access sync (always runs)
    with rt_factory() as runtime_db, soul_factory() as soul_db:
        from anima_server.services.agent.access_sync import sync_access_metadata
        from anima_server.services.agent.retrieval_feedback import sync_retrieval_feedback

        result.access_sync = sync_access_metadata(
            user_id=user_id,
            runtime_db=runtime_db,
            soul_db=soul_db,
        )
        result.retrieval_feedback_sync = sync_retrieval_feedback(
            user_id=user_id,
            runtime_db=runtime_db,
            soul_db=soul_db,
        )

    # Phase 4: Promote emotional patterns (if due)
    try:
        from anima_server.services.agent.emotional_patterns import promote_emotional_patterns

        with rt_factory() as runtime_db, soul_factory() as soul_db:
            promoted = promote_emotional_patterns(
                soul_db=soul_db,
                pg_db=runtime_db,
                user_id=user_id,
            )
            if promoted > 0:
                soul_db.commit()
                runtime_db.commit()
                logger.info(
                    "Soul Writer promoted %d emotional patterns for user %s",
                    promoted,
                    user_id,
                )
    except Exception:
        logger.debug("Emotional pattern promotion failed for user %s",
                     user_id, exc_info=True)


def _process_pending_op(
    op,
    *,
    user_id: int,
    runtime_db: Session,
    soul_db_factory: Callable,
    result: SoulWriterResult,
) -> None:
    """Process a single PendingMemoryOp with idempotency checks."""
    from anima_server.models.runtime_memory import PromotionJournal
    from anima_server.services.agent.soul_blocks import (
        _get_soul_block,
        append_to_soul_block,
        full_replace_soul_block,
        replace_in_soul_block,
    )
    from anima_server.services.data_crypto import df

    now = datetime.now(UTC)

    # Write tentative journal entry
    journal = PromotionJournal(
        user_id=user_id,
        pending_op_id=op.id,
        decision="promoted",
        reason=f"pending op: {op.op_type} on {op.target_block}",
        target_table="self_model_blocks",
        content_hash=op.content_hash,
        journal_status="tentative",
    )
    runtime_db.add(journal)
    runtime_db.flush()

    # Idempotency check 1: content_hash already confirmed in journal
    if op.content_hash:
        existing = runtime_db.scalar(
            select(PromotionJournal.id).where(
                PromotionJournal.user_id == user_id,
                PromotionJournal.content_hash == op.content_hash,
                PromotionJournal.journal_status == "confirmed",
                PromotionJournal.id != journal.id,
            )
        )
        if existing:
            op.consolidated = True
            op.consolidated_at = now
            journal.journal_status = "confirmed"
            journal.reason = "idempotent skip — hash in journal"
            result.ops_skipped += 1
            return

    # Idempotency check 2: content-based check against current block state
    with soul_db_factory() as soul_db:
        block = _get_soul_block(soul_db, user_id=user_id,
                                section=op.target_block)
        if block is not None:
            current_content = df(
                user_id,
                block.content,
                table="self_model_blocks",
                field="content",
            )
            if op.op_type == "append" and op.content.strip() in current_content:
                op.consolidated = True
                op.consolidated_at = now
                journal.journal_status = "confirmed"
                journal.reason = "idempotent skip — content already in block"
                result.ops_skipped += 1
                return
            if op.op_type == "full_replace" and current_content.strip() == op.content.strip():
                op.consolidated = True
                op.consolidated_at = now
                journal.journal_status = "confirmed"
                journal.reason = "idempotent skip — block already has target content"
                result.ops_skipped += 1
                return
            if (
                op.op_type == "replace"
                and op.old_content
                and op.old_content.strip() not in current_content
            ):
                # Old content no longer present — replace already applied or block changed
                op.consolidated = True
                op.consolidated_at = now
                journal.journal_status = "confirmed"
                journal.reason = "idempotent skip — old content not in block (already replaced)"
                result.ops_skipped += 1
                return

        # Apply the op
        if op.op_type == "append":
            append_to_soul_block(
                soul_db,
                user_id=user_id,
                section=op.target_block,
                content=op.content,
            )
        elif op.op_type == "replace":
            replace_in_soul_block(
                soul_db,
                user_id=user_id,
                section=op.target_block,
                old_content=op.old_content or "",
                new_content=op.content,
            )
        elif op.op_type == "full_replace":
            full_replace_soul_block(
                soul_db,
                user_id=user_id,
                section=op.target_block,
                content=op.content,
            )
        else:
            raise ValueError(f"Unknown op_type: {op.op_type}")

        soul_db.commit()

    # Mark as consolidated + confirm journal
    op.consolidated = True
    op.consolidated_at = now
    journal.journal_status = "confirmed"
    result.ops_processed += 1


def _process_candidate(
    candidate,
    *,
    user_id: int,
    runtime_db: Session,
    soul_db_factory: Callable,
    result: SoulWriterResult,
    event_loop: asyncio.AbstractEventLoop | None = None,
) -> None:
    """Process a single MemoryCandidate."""
    from anima_server.models.runtime_memory import PromotionJournal

    now = datetime.now(UTC)

    with soul_db_factory() as soul_db:
        decision = plan_candidate_promotion(soul_db, candidate, user_id)

        # Write journal entry
        journal = PromotionJournal(
            user_id=user_id,
            candidate_id=candidate.id,
            decision=decision.action,
            reason=decision.reason,
            content_hash=candidate.content_hash,
            extraction_model=candidate.extraction_model,
            journal_status="tentative",
        )
        runtime_db.add(journal)
        runtime_db.flush()

        if decision.action == "rejected":
            candidate.status = "rejected"
            candidate.processed_at = now
            journal.journal_status = "confirmed"
            result.candidates_rejected += 1
            return

        if decision.action == "supersede":
            from anima_server.services.agent.memory_store import supersede_memory_item

            old_item = decision.old_item
            new_item = supersede_memory_item(
                soul_db,
                old_item_id=old_item.id,
                new_content=candidate.content,
                importance=candidate.importance,
            )

            # Suppress old item
            try:
                from anima_server.services.agent.forgetting import suppress_memory

                suppress_memory(
                    soul_db,
                    memory_id=old_item.id,
                    superseded_by=new_item.id,
                    user_id=user_id,
                )
            except Exception:
                logger.debug("suppress_memory failed for item %s", old_item.id)

            # Upsert claim
            try:
                from anima_server.services.agent.claims import upsert_claim

                upsert_claim(
                    soul_db,
                    user_id=user_id,
                    content=candidate.content,
                    category=candidate.category,
                    importance=candidate.importance,
                    source_kind="extraction",
                    extractor=candidate.source,
                    memory_item_id=new_item.id,
                    evidence_text=candidate.content,
                )
            except Exception:
                logger.debug(
                    "upsert_claim failed for candidate %s", candidate.id)

            soul_db.commit()

            # Embed immediately so the item is searchable right away
            if event_loop is not None:
                try:
                    import asyncio as _aio

                    _aio.run_coroutine_threadsafe(
                        _embed_and_index_item(
                            user_id,
                            new_item.id,
                            candidate.content,
                            candidate.category,
                            candidate.importance,
                            soul_db,
                        ),
                        event_loop,
                    ).result(timeout=15)
                except Exception:
                    logger.debug(
                        "Inline embedding failed for superseded item %d, will backfill later",
                        new_item.id,
                    )

            candidate.status = "promoted"
            candidate.processed_at = now
            journal.target_table = "memory_items"
            journal.target_record_id = str(new_item.id)
            journal.journal_status = "confirmed"
            result.candidates_superseded += 1
            return

        # action == "promote"
        from anima_server.services.agent.memory_store import store_memory_item

        write_result = store_memory_item(
            soul_db,
            user_id=user_id,
            content=candidate.content,
            category=candidate.category,
            importance=candidate.importance,
            source="extraction",
            allow_update=True,
            defer_on_similar=False,
            tags=candidate.tags_json,
        )

        if write_result.action in ("duplicate", "conflict", "rejected"):
            candidate.status = "rejected"
            candidate.processed_at = now
            journal.decision = "rejected"
            journal.reason = f"store rejected: {write_result.reason}"
            journal.journal_status = "confirmed"
            result.candidates_rejected += 1
            return

        new_item = write_result.item
        if new_item is not None:
            # Upsert claim
            try:
                from anima_server.services.agent.claims import upsert_claim

                upsert_claim(
                    soul_db,
                    user_id=user_id,
                    content=candidate.content,
                    category=candidate.category,
                    importance=candidate.importance,
                    source_kind="extraction",
                    extractor=candidate.source,
                    memory_item_id=new_item.id,
                    evidence_text=candidate.content,
                )
            except Exception:
                logger.debug(
                    "upsert_claim failed for candidate %s", candidate.id)

            # If store_memory_item did a supersession, suppress old item
            if write_result.action == "superseded" and write_result.matched_item:
                try:
                    from anima_server.services.agent.forgetting import suppress_memory

                    suppress_memory(
                        soul_db,
                        memory_id=write_result.matched_item.id,
                        superseded_by=new_item.id,
                        user_id=user_id,
                    )
                except Exception:
                    logger.warning(
                        "suppress_memory failed for item %s (superseded by %s)",
                        write_result.matched_item.id,
                        new_item.id,
                        exc_info=True,
                    )

        soul_db.commit()

        # Embed immediately so the item is searchable right away
        if new_item is not None and event_loop is not None:
            try:
                import asyncio as _aio

                _aio.run_coroutine_threadsafe(
                    _embed_and_index_item(
                        user_id,
                        new_item.id,
                        candidate.content,
                        candidate.category,
                        candidate.importance,
                        soul_db,
                    ),
                    event_loop,
                ).result(timeout=15)
            except Exception:
                logger.debug(
                    "Inline embedding failed for promoted item %d, will backfill later", new_item.id
                )

        candidate.status = "promoted"
        candidate.processed_at = now
        journal.target_table = "memory_items"
        if new_item:
            journal.target_record_id = str(new_item.id)
        journal.journal_status = "confirmed"
        result.candidates_promoted += 1


def plan_candidate_promotion(
    soul_db: Session,
    candidate,
    user_id: int,
) -> PromotionDecision:
    """Decide what to do with a candidate by deduping against canonical SQLCipher state."""
    from anima_server.models import MemoryItem

    # High-authority fast paths
    if candidate.importance_source == "user_explicit":
        return PromotionDecision(
            action="promote", reason="user_explicit authority — always promote"
        )

    if candidate.importance_source == "correction" and candidate.supersedes_item_id:
        target = soul_db.get(MemoryItem, candidate.supersedes_item_id)
        if target is not None and target.superseded_by is None:
            return PromotionDecision(
                action="supersede",
                old_item=target,
                reason=f"correction supersedes item {target.id}",
            )
        return PromotionDecision(
            action="promote",
            reason="correction target missing — promoting as new memory",
        )

    # Normal dedup via store_memory_item dry_run
    from anima_server.services.agent.memory_store import store_memory_item

    write_analysis = store_memory_item(
        soul_db,
        user_id=user_id,
        content=candidate.content,
        category=candidate.category,
        importance=candidate.importance,
        source="extraction",
        allow_update=True,
        defer_on_similar=True,
        dry_run=True,
    )

    if write_analysis.action == "duplicate":
        return PromotionDecision(action="rejected", reason="duplicate in canonical state")

    if write_analysis.action == "superseded":
        return PromotionDecision(
            action="supersede",
            old_item=write_analysis.matched_item,
            reason=(
                f"supersedes item {write_analysis.matched_item.id}"
                if write_analysis.matched_item
                else "supersede"
            ),
        )

    if write_analysis.action == "similar":
        from anima_server.services.agent.memory_store import _extract_fact_slot

        if _extract_fact_slot(candidate.content) is not None:
            # similar action populates similar_items (not matched_item)
            old = (
                write_analysis.similar_items[0]
                if write_analysis.similar_items
                else write_analysis.matched_item
            )
            if old is not None:
                return PromotionDecision(
                    action="supersede",
                    old_item=old,
                    reason=f"slot match supersedes item {old.id}",
                )
            return PromotionDecision(action="promote", reason="slot match but no target item found")
        return PromotionDecision(action="promote", reason="similar but no structured slot — append")

    return PromotionDecision(action="promote", reason="new memory")
