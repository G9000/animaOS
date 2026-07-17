"""Soul Writer — single serialized promoter from PG runtime to SQLCipher soul vault.

Triggered by: pre-turn check, inactivity, compaction, shutdown, threshold.
Guarantees: per-user asyncio lock, per-item transactions, idempotent via content hash.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Ops/candidates dropped at their retry cap log here so poison-pill rows
# stay greppable instead of silently starving the queue.
degraded_logger = logging.getLogger("anima.runtime.degraded")

# Per-user locks — prevents concurrent Soul Writer runs for the same user
_user_locks: dict[int, asyncio.Lock] = {}
MAX_RETRY_COUNT = 3
MAX_ITEMS_PER_RUN = 50
# A "pending" extraction guard newer than this is treated as a live in-flight
# call and left alone; older than this it is assumed to be from a crashed
# process and becomes eligible for crash-recovery retry.
STALE_PENDING_EXTRACTION = timedelta(minutes=15)
# Headroom added to the configured LLM retry budget when waiting on a
# cross-loop extraction retry, so the inner provider timeout/retry policy fires
# first and returns a clean result instead of the outer wait killing a
# legitimate call.
EXTRACTION_RETRY_TIMEOUT_BUFFER = 30.0


def _extraction_retry_wait_seconds() -> float:
    """Outer wall-clock budget for a cross-loop extraction retry.

    ``extract_memories_via_llm`` runs through ``invoke_with_retry``, which makes
    ``retry_limit + 1`` attempts — each up to ``agent_llm_timeout`` — with up to
    ``retry_max_delay`` of backoff between them.  The outer ``future.result``
    wait must cover that entire budget (plus headroom); a shorter wait would
    cancel a legitimately slow/retrying call partway through the configured
    policy, burning ``retry_count`` and eventually abandoning the extraction.
    """
    from anima_server.config import settings

    attempts = settings.agent_llm_retry_limit + 1
    max_backoff_total = (
        settings.agent_llm_retry_limit * settings.agent_llm_retry_max_delay
    )
    return (
        attempts * settings.agent_llm_timeout
        + max_backoff_total
        + EXTRACTION_RETRY_TIMEOUT_BUFFER
    )


def _get_user_lock(user_id: int) -> asyncio.Lock:
    return _user_locks.setdefault(user_id, asyncio.Lock())


@dataclass(slots=True)
class PromotionDecision:
    action: str  # "promote" | "supersede" | "evolve" | "reinforce" | "rejected" | "fold_to_trace"
    reason: str = ""
    old_item: object | None = None  # MemoryItem when action targets an existing item
    topic_key: str | None = None  # set when action == "fold_to_trace"
    score: float | None = None  # IL4 latent score, set whenever it was computed


@dataclass(slots=True)
class SoulWriterResult:
    ops_processed: int = 0
    ops_skipped: int = 0
    ops_failed: int = 0
    extraction_failures_retried: int = 0
    extraction_failures_resolved: int = 0
    extraction_failures_failed: int = 0
    candidates_promoted: int = 0
    candidates_rejected: int = 0
    candidates_reinforced: int = 0
    candidates_superseded: int = 0
    candidates_folded: int = 0
    candidates_failed: int = 0
    profile_updates_promoted: int = 0
    profile_updates_failed: int = 0
    access_sync: dict = field(default_factory=dict)
    retrieval_feedback_sync: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


async def _embed_and_index_item(
    user_id: int,
    item_id: int,
    content: str,
    category: str,
    importance: int,
    soul_db_factory: Callable[..., Session],
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

        with soul_db_factory() as soul_db:
            item = soul_db.get(MemoryItem, item_id)
            if item is None:
                return

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
            soul_db.commit()

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
    ops_only: bool = False,
) -> SoulWriterResult:
    """Main entry point. Acquires per-user lock and runs the promotion pipeline.

    With ``ops_only=True`` only Phase 1 (pending core-memory ops, no LLM
    calls) runs — used pre-turn where candidate promotion would block
    time-to-first-token on per-candidate LLM extraction.
    """
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
                ops_only=ops_only,
            )
        except Exception as e:
            logger.exception("Soul Writer failed for user %s", user_id)
            result.errors.append(str(e))

    # Soul Writer mutates identity blocks (human/persona/soul) outside the
    # turn pipeline, so the companion's static-block cache must be told.
    if (
        result.ops_processed > 0
        or result.candidates_promoted > 0
        or result.profile_updates_promoted > 0
        or result.candidates_reinforced > 0
    ):
        try:
            from anima_server.services.agent.companion import get_companion

            companion = get_companion(user_id)
            if companion is not None:
                companion.invalidate_memory()
        except Exception:
            logger.debug(
                "Companion cache invalidation failed after Soul Writer run",
                exc_info=True,
            )

    total_work = (
        result.ops_processed
        + result.ops_skipped
        + result.ops_failed
        + result.extraction_failures_retried
        + result.candidates_promoted
        + result.candidates_rejected
        + result.candidates_reinforced
        + result.candidates_superseded
        + result.candidates_failed
        + result.profile_updates_promoted
        + result.profile_updates_failed
    )
    if total_work > 0 or result.errors:
        logger.info(
            (
                "Soul Writer user=%s: ops=%d/%d/%d extraction_retries=%d/%d/%d "
                "cands=%d/%d/%d/%d/%d profile=%d/%d access=%s retrieval=%s errors=%d"
            ),
            user_id,
            result.ops_processed,
            result.ops_skipped,
            result.ops_failed,
            result.extraction_failures_retried,
            result.extraction_failures_resolved,
            result.extraction_failures_failed,
            result.candidates_promoted,
            result.candidates_rejected,
            result.candidates_reinforced,
            result.candidates_superseded,
            result.candidates_failed,
            result.profile_updates_promoted,
            result.profile_updates_failed,
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
    ops_only: bool = False,
) -> None:
    """Inner pipeline — called under lock via asyncio.to_thread."""
    from anima_server.db.helpers import dual_session_scope
    from anima_server.db.runtime import get_runtime_session_factory
    from anima_server.db.session import SessionLocal, get_user_session_factory, is_sqlite_mode

    rt_factory = runtime_db_factory or get_runtime_session_factory()
    if soul_db_factory is not None:
        soul_factory = soul_db_factory
    elif is_sqlite_mode():
        soul_factory = get_user_session_factory(user_id)
    else:
        soul_factory = SessionLocal

    # Phase 1: Process PendingMemoryOps
    with rt_factory() as runtime_db:
        from anima_server.services.agent.pending_ops import get_pending_ops

        pending_ops = get_pending_ops(runtime_db, user_id=user_id)

        # Also retry previously-failed ops (transient errors like SQLCipher
        # busy) — but only below the retry cap: a deterministically-failing
        # op must not churn on every run and starve the 50-row queue.
        from anima_server.models.pending_memory_op import PendingMemoryOp as _PendingOp

        failed_ops = list(
            runtime_db.scalars(
                select(_PendingOp)
                .where(
                    _PendingOp.user_id == user_id,
                    _PendingOp.consolidated.is_(False),
                    _PendingOp.failed.is_(True),
                    _PendingOp.retry_count < MAX_RETRY_COUNT,
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

        dead_op_count = runtime_db.scalar(
            select(func.count())
            .select_from(_PendingOp)
            .where(
                _PendingOp.user_id == user_id,
                _PendingOp.consolidated.is_(False),
                _PendingOp.failed.is_(True),
                _PendingOp.retry_count >= MAX_RETRY_COUNT,
            )
        )
        if dead_op_count:
            degraded_logger.warning(
                "Soul Writer skipping %d pending op(s) at the retry cap "
                "for user %s (permanently failed identity writes)",
                dead_op_count,
                user_id,
            )

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
                op.retry_count = (op.retry_count or 0) + 1
                result.ops_failed += 1
                result.errors.append(f"op {op.id}: {e}")

        runtime_db.commit()

    if ops_only:
        return

    # Phase 1.5: Retry preserved turn-level extraction failures.
    with rt_factory() as runtime_db:
        _retry_memory_extraction_failures(
            runtime_db,
            user_id=user_id,
            result=result,
            event_loop=event_loop,
        )
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
            # our read above.  Fall back to per-row savepoints so a single
            # collision fails only that candidate (with its retry_count
            # incremented) instead of aborting the whole batch mid-state.
            runtime_db.rollback()
            from sqlalchemy.exc import IntegrityError as _IE

            queued: list = []
            for candidate in candidates:
                try:
                    with runtime_db.begin_nested():
                        candidate.status = "queued"
                        runtime_db.flush()
                    queued.append(candidate)
                except _IE:
                    logger.debug(
                        "Skipping candidate %s: active duplicate hash %s",
                        candidate.id, candidate.content_hash,
                    )
                    candidate.status = "failed"
                    candidate.last_error = "active duplicate content_hash"
                    candidate.retry_count = (candidate.retry_count or 0) + 1
            candidates = queued

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

    # Phase 2.5: Promote structured profile updates.
    with rt_factory() as runtime_db:
        from anima_server.services.agent.user_profile import (
            get_profile_update_candidates_for_promotion,
        )

        profile_candidates = get_profile_update_candidates_for_promotion(
            runtime_db,
            user_id=user_id,
            limit=MAX_ITEMS_PER_RUN,
            max_retry=MAX_RETRY_COUNT,
        )

        for candidate in profile_candidates:
            candidate.status = "queued"
        runtime_db.flush()

        for candidate in profile_candidates:
            try:
                _process_profile_update_candidate(
                    candidate,
                    soul_db_factory=soul_factory,
                    result=result,
                )
            except Exception as e:
                logger.exception(
                    "Soul Writer profile update candidate %s failed", candidate.id
                )
                candidate.status = "failed"
                candidate.last_error = str(e)[:500]
                candidate.retry_count = (candidate.retry_count or 0) + 1
                result.profile_updates_failed += 1
                result.errors.append(f"profile update {candidate.id}: {e}")

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

    # Phase 4: Promote emotional patterns — gated so the 50-signal scan
    # and SQLCipher writes don't run on every turn (the stated "if due"
    # previously had no gate at all).
    try:
        from anima_server.services.agent.emotional_patterns import (
            promote_emotional_patterns,
            should_promote_emotional_patterns,
        )

        with dual_session_scope(soul_factory, rt_factory) as (soul_db, runtime_db):
            if should_promote_emotional_patterns(
                soul_db=soul_db,
                pg_db=runtime_db,
                user_id=user_id,
            ):
                promoted = promote_emotional_patterns(
                    soul_db=soul_db,
                    pg_db=runtime_db,
                    user_id=user_id,
                )
                if promoted > 0:
                    logger.info(
                        "Soul Writer promoted %d emotional patterns for user %s",
                        promoted,
                        user_id,
                    )
    except Exception:
        logger.debug(
            "Emotional pattern promotion failed for user %s",
            user_id,
            exc_info=True,
        )


def _retry_memory_extraction_failures(
    runtime_db: Session,
    *,
    user_id: int,
    result: SoulWriterResult,
    event_loop: asyncio.AbstractEventLoop | None,
) -> None:
    if event_loop is None:
        return

    from anima_server.config import settings

    if settings.agent_provider == "scaffold":
        return

    from anima_server.models.runtime_memory import MemoryExtractionFailure
    from anima_server.services.agent.candidate_ops import create_memory_candidate
    from anima_server.services.agent.consolidation import extract_memories_via_llm
    from anima_server.services.agent.user_profile import (
        create_profile_update_candidates_from_payload,
    )

    # Retry genuine failures immediately, plus "pending" guards that have gone
    # stale — a still-pending row past the staleness window means the process
    # died mid-extraction (Phase C never ran).  Fresh "pending" rows are a live
    # in-flight call and are skipped so we don't double-extract them.
    stale_before = datetime.now(UTC) - STALE_PENDING_EXTRACTION
    failures = list(
        runtime_db.scalars(
            select(MemoryExtractionFailure)
            .where(
                MemoryExtractionFailure.user_id == user_id,
                or_(
                    MemoryExtractionFailure.status == "failed",
                    and_(
                        MemoryExtractionFailure.status == "pending",
                        MemoryExtractionFailure.last_attempt_at < stale_before,
                    ),
                ),
                MemoryExtractionFailure.retry_count < MAX_RETRY_COUNT,
            )
            .order_by(MemoryExtractionFailure.created_at)
            .limit(MAX_ITEMS_PER_RUN)
        ).all()
    )
    if not failures:
        return

    for failure in failures:
        now = datetime.now(UTC)
        failure.retry_count = (failure.retry_count or 0) + 1
        failure.last_attempt_at = now
        failure.updated_at = now
        result.extraction_failures_retried += 1

        user_message, assistant_response = _memory_extraction_failure_retry_texts(
            runtime_db,
            user_id=user_id,
            failure=failure,
        )

        future = asyncio.run_coroutine_threadsafe(
            extract_memories_via_llm(
                user_message=user_message,
                assistant_response=assistant_response,
            ),
            event_loop,
        )
        try:
            # Wait for the provider's full timeout+retry budget so a slow (e.g.
            # local) model or a transient-error retry isn't killed prematurely —
            # the old hard-coded 30s could abort legitimate calls, burn
            # retry_count, and leave the coroutine running to issue duplicate
            # billable requests.
            llm_result = future.result(timeout=_extraction_retry_wait_seconds())
        except Exception as exc:
            # Cancel the scheduled coroutine so a hung/slow call doesn't keep
            # running on the loop after we've given up on it.
            future.cancel()
            # Normalize a recovered stale "pending" guard to "failed" so it is
            # a plain retryable row from here on.
            failure.status = "failed"
            failure.failure_reason = str(exc)[:2000]
            result.extraction_failures_failed += 1
            result.errors.append(f"extraction failure {failure.id}: {exc}")
            continue

        if llm_result.failed:
            failure.status = "failed"
            failure.failure_reason = (llm_result.error or "LLM memory extraction failed")[:2000]
            result.extraction_failures_failed += 1
            result.errors.append(f"extraction failure {failure.id}: {failure.failure_reason}")
            continue

        for item in llm_result.memories:
            content = item.get("content", "")
            if not content or not isinstance(content, str):
                continue
            create_memory_candidate(
                runtime_db,
                user_id=user_id,
                content=content,
                category=item.get("category", "fact"),
                importance=item.get("importance", 3),
                importance_source="llm",
                source="llm",
                source_message_ids=list(failure.source_message_ids or []),
                extraction_model=failure.extraction_model,
                salience=item.get("salience")
                if isinstance(item.get("salience"), dict)
                else None,
            )

        create_profile_update_candidates_from_payload(
            runtime_db,
            user_id=user_id,
            profile_updates=llm_result.profile_updates,
            source_message_ids=list(failure.source_message_ids or []),
            extraction_model=failure.extraction_model,
        )

        failure.status = "resolved"
        failure.resolved_at = now
        failure.updated_at = now
        result.extraction_failures_resolved += 1


def _memory_extraction_failure_retry_texts(
    runtime_db: Session,
    *,
    user_id: int,
    failure,
) -> tuple[str, str]:
    source_message_ids = [int(message_id) for message_id in failure.source_message_ids or []]
    if source_message_ids:
        from anima_server.models.runtime import RuntimeMessage

        message_position = {
            message_id: index for index, message_id in enumerate(source_message_ids)
        }
        messages = list(
            runtime_db.scalars(
                select(RuntimeMessage).where(
                    RuntimeMessage.user_id == user_id,
                    RuntimeMessage.id.in_(source_message_ids),
                )
            ).all()
        )
        messages.sort(key=lambda message: message_position.get(int(message.id), len(messages)))
        if messages:
            user_parts: list[str] = []
            assistant_parts: list[str] = []
            for message in messages:
                content = _runtime_message_text(message)
                if not content:
                    continue
                if message.role == "user":
                    user_parts.append(content)
                elif message.role == "assistant":
                    assistant_parts.append(content)

            if user_parts or assistant_parts:
                return (
                    "\n\n".join(user_parts) or failure.user_message_preview or "",
                    "\n\n".join(assistant_parts) or failure.assistant_response_preview or "",
                )

    return (
        failure.user_message_preview or "",
        failure.assistant_response_preview or "",
    )


def _runtime_message_text(message) -> str:
    if message.content_text:
        return str(message.content_text)
    if message.content_json is not None:
        return str(message.content_json)
    return ""


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


def _process_profile_update_candidate(
    candidate,
    *,
    soul_db_factory: Callable,
    result: SoulWriterResult,
) -> None:
    """Promote one structured profile candidate into the durable soul DB."""
    from anima_server.services.agent.user_profile import promote_profile_update_candidate

    with soul_db_factory() as soul_db:
        promote_profile_update_candidate(soul_db, candidate=candidate)
        soul_db.commit()

    candidate.status = "promoted"
    candidate.processed_at = datetime.now(UTC)
    candidate.last_error = None
    result.profile_updates_promoted += 1


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

        if decision.action == "fold_to_trace":
            from anima_server.services.agent.latent_traces import (
                fold_candidate_into_trace,
            )

            trace = fold_candidate_into_trace(
                soul_db,
                user_id=user_id,
                candidate=candidate,
                topic_key=decision.topic_key,
                score=decision.score or 0.0,
            )
            soul_db.commit()

            candidate.status = "folded"
            candidate.processed_at = now
            journal.target_table = "latent_traces"
            journal.target_record_id = str(trace.id)
            journal.journal_status = "confirmed"
            result.candidates_folded += 1
            return

        if decision.action == "reinforce":
            old_item = decision.old_item
            if old_item is not None:
                from anima_server.services.agent.memory_salience import (
                    merge_salience_into_item,
                )
                from anima_server.services.agent.memory_store import (
                    invalidate_memory_retrieval_indexes,
                )
                from anima_server.services.agent.provenance import (
                    add_candidate_memory_item_evidence,
                )

                merge_salience_into_item(old_item, candidate.salience_json)
                add_candidate_memory_item_evidence(
                    soul_db,
                    runtime_db=runtime_db,
                    candidate=candidate,
                    memory_item=old_item,
                )
                soul_db.commit()
                invalidate_memory_retrieval_indexes(user_id, mark_dirty=False)

            candidate.status = "reinforced"
            candidate.processed_at = now
            journal.target_table = "memory_items"
            if old_item is not None:
                journal.target_record_id = str(old_item.id)
            journal.journal_status = "confirmed"
            result.candidates_reinforced += 1
            return

        if decision.action == "supersede":
            from anima_server.services.agent.memory_store import supersede_memory_item

            old_item = decision.old_item
            new_item = supersede_memory_item(
                soul_db,
                old_item_id=old_item.id,
                new_content=candidate.content,
                importance=candidate.importance,
                salience=candidate.salience_json,
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

            from anima_server.services.agent.provenance import (
                add_candidate_memory_item_evidence,
            )

            add_candidate_memory_item_evidence(
                soul_db,
                runtime_db=runtime_db,
                candidate=candidate,
                memory_item=new_item,
            )

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
                            soul_db_factory,
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

        if decision.action == "evolve":
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
                salience=candidate.salience_json,
            )
            new_item = write_result.item
            if new_item is None:
                candidate.status = "rejected"
                candidate.processed_at = now
                journal.decision = "rejected"
                journal.reason = f"evolution rejected: {write_result.reason}"
                journal.journal_status = "confirmed"
                result.candidates_rejected += 1
                return

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
                logger.debug("upsert_claim failed for candidate %s", candidate.id)

            from anima_server.services.agent.provenance import (
                add_candidate_memory_item_evidence,
            )

            add_candidate_memory_item_evidence(
                soul_db,
                runtime_db=runtime_db,
                candidate=candidate,
                memory_item=new_item,
            )
            soul_db.commit()

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
                            soul_db_factory,
                        ),
                        event_loop,
                    ).result(timeout=15)
                except Exception:
                    logger.debug(
                        "Inline embedding failed for evolved item %d, will backfill later",
                        new_item.id,
                    )

            candidate.status = "promoted"
            candidate.processed_at = now
            journal.target_table = "memory_items"
            journal.target_record_id = str(new_item.id)
            journal.journal_status = "confirmed"
            result.candidates_promoted += 1
            return

        # action == "promote"
        from anima_server.services.agent.memory_store import store_memory_item

        # Downstream consumers (overview counts, contradiction scan, memory
        # tools) enumerate only the four canonical categories, so a promoted
        # minor_observation must be remapped — mirroring the crystallization
        # path's category normalization.
        promote_category = (
            "fact" if candidate.category == "minor_observation" else candidate.category
        )
        write_result = store_memory_item(
            soul_db,
            user_id=user_id,
            content=candidate.content,
            category=promote_category,
            importance=candidate.importance,
            source="extraction",
            allow_update=True,
            defer_on_similar=False,
            tags=candidate.tags_json,
            salience=candidate.salience_json,
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

            from anima_server.services.agent.provenance import (
                add_candidate_memory_item_evidence,
            )

            add_candidate_memory_item_evidence(
                soul_db,
                runtime_db=runtime_db,
                candidate=candidate,
                memory_item=new_item,
            )

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
                        soul_db_factory,
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
        salience=candidate.salience_json,
        dry_run=True,
    )

    if write_analysis.action == "duplicate":
        return PromotionDecision(
            action="reinforce",
            old_item=write_analysis.matched_item,
            reason="duplicate in canonical state - reinforced salience/evidence",
        )

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

    if write_analysis.action == "evolved":
        return PromotionDecision(
            action="evolve",
            old_item=write_analysis.matched_item,
            reason=write_analysis.reason or "soft evolution",
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
            return _gate_new_memory_decision(
                candidate, reason="slot match but no target item found"
            )
        return _gate_new_memory_decision(
            candidate, reason="similar but no structured slot — append"
        )

    return _gate_new_memory_decision(candidate, reason="new memory")


def _gate_new_memory_decision(candidate, reason: str) -> PromotionDecision:
    """IL4 scoring gate for a fresh "would-promote" decision.

    The gate applies ONLY to importance-1 candidates: importance >= 2
    promotes exactly as it did before IL4 existed — verbatim
    behavior-preservation, regardless of what emotional_salience or
    evidence_strength the extractor reported (an LLM can explicitly emit
    near-zero salience values on the normal extraction path, and those
    must never change an importance >= 2 outcome). Only genuinely weak
    signals — the ``minor_observation`` lane and other importance-1
    extractions — are scored and may fold into a latent trace.

    Only candidates with no existing-memory match reach here — every
    dedup/supersede/evolve branch above always wins over folding (a
    duplicate of a weak signal reinforcing something already promoted is
    not itself a weak signal), and the user_explicit/correction
    high-authority fast paths return even earlier.
    """
    if candidate.importance >= 2:
        return PromotionDecision(action="promote", reason=reason)

    from anima_server.services.agent.claims import derive_topic_key
    from anima_server.services.agent.inner_life.latent import classify_score, score_candidate
    from anima_server.services.agent.latent_traces import get_latent_config

    salience = getattr(candidate, "salience_json", None) or {}
    # Defaults apply only when the value is ABSENT — an explicit 0.0 is an
    # honest signal and must not be replaced (`or` would discard it).
    es_raw = salience.get("emotional_salience")
    evs_raw = salience.get("evidence_strength")
    score = score_candidate(
        importance=candidate.importance,
        emotional_salience=float(0.0 if es_raw is None else es_raw),
        evidence_strength=float(0.8 if evs_raw is None else evs_raw),
    )
    config = get_latent_config()
    classification = classify_score(score, config)

    if classification == "reject":
        return PromotionDecision(
            action="rejected",
            reason=f"latent score {score:.3f} below floor {config.floor:.3f}",
            score=score,
        )
    if classification == "fold":
        topic_key = derive_topic_key(candidate.content, candidate.category)
        return PromotionDecision(
            action="fold_to_trace",
            reason=(
                f"latent score {score:.3f} below promotion threshold "
                f"{config.promotion_threshold:.3f} — folding into trace"
            ),
            topic_key=topic_key,
            score=score,
        )
    return PromotionDecision(action="promote", reason=reason, score=score)
