from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from anima_server.services.health.models import CheckResult


async def check_db_integrity(
    user_id: int,
    *,
    soul_db_factory: Callable[..., Any] | None = None,
    runtime_db_factory: Callable[..., Any] | None = None,
) -> CheckResult:
    """Check SQLite integrity and PostgreSQL connectivity."""
    start = time.monotonic()
    details: dict[str, Any] = {}
    issues: list[str] = []

    # 1. SQLite integrity check
    try:
        if soul_db_factory is None:
            from anima_server.db.session import ensure_user_database

            soul_db_factory = ensure_user_database(user_id)

        with soul_db_factory() as db:
            from sqlalchemy import text

            result = db.execute(text("PRAGMA integrity_check")).scalar()
            details["sqlite_integrity"] = result or "ok"
            if result and result.lower() != "ok":
                issues.append(f"SQLite integrity: {result}")
    except Exception as exc:
        details["sqlite_integrity"] = str(exc)
        issues.append(f"SQLite check failed: {exc}")

    # 2. PostgreSQL connectivity
    try:
        if runtime_db_factory is None:
            from anima_server.db.runtime import get_runtime_session_factory

            runtime_db_factory = get_runtime_session_factory()

        with runtime_db_factory() as db:
            from sqlalchemy import text

            db.execute(text("SELECT 1"))
            details["pg_connected"] = True
    except Exception as exc:
        details["pg_connected"] = False
        issues.append(f"Runtime DB unreachable: {exc}")

    elapsed = (time.monotonic() - start) * 1000

    if any("unreachable" in i.lower() or "failed" in i.lower() for i in issues) or any("integrity" in i.lower() for i in issues):
        status = "unhealthy"
    else:
        status = "healthy"

    message = "; ".join(issues) if issues else "SQLite OK, runtime DB connected"
    return CheckResult(
        name="db_integrity",
        status=status,
        message=message,
        details=details,
        duration_ms=elapsed,
    )


async def check_llm_connectivity(
    user_id: int,
    *,
    event_logger: Any | None = None,
    window_minutes: int = 10,
) -> CheckResult:
    """Check LLM error rate from recent event logs."""
    start = time.monotonic()

    if event_logger is None:
        from anima_server.services.health.event_logger import get_event_logger

        event_logger = get_event_logger()

    since = datetime.now(UTC) - timedelta(minutes=window_minutes)

    invocations = event_logger.query_events(
        category="llm", event="invoke", since=since, limit=10000
    )
    failures = event_logger.query_events(
        category="llm", event="failure", since=since, limit=10000
    )

    total = len(invocations) + len(failures)
    error_count = len(failures)

    elapsed = (time.monotonic() - start) * 1000
    details: dict[str, Any] = {
        "total_invocations": total,
        "error_count": error_count,
        "window_minutes": window_minutes,
    }

    if total == 0:
        return CheckResult(
            name="llm_connectivity",
            status="healthy",
            message="No recent LLM activity (no data)",
            details=details,
            duration_ms=elapsed,
        )

    error_rate = error_count / total
    details["error_rate"] = round(error_rate, 3)

    if error_rate > 0.5:
        status = "unhealthy"
        message = f"{error_count} errors in last {window_minutes} min ({error_rate:.0%} error rate)"
    elif error_rate > 0.1:
        status = "degraded"
        message = f"{error_count} errors in last {window_minutes} min ({error_rate:.0%} error rate)"
    else:
        status = "healthy"
        message = f"{total} calls, {error_count} errors in last {window_minutes} min"

    return CheckResult(
        name="llm_connectivity",
        status=status,
        message=message,
        details=details,
        duration_ms=elapsed,
    )


async def check_background_tasks(
    user_id: int,
    *,
    runtime_db_factory: Callable[..., Any] | None = None,
    stuck_threshold_minutes: int = 30,
) -> CheckResult:
    """Check for failed and stuck background tasks."""
    start = time.monotonic()
    details: dict[str, Any] = {}
    issues: list[str] = []

    try:
        if runtime_db_factory is None:
            from anima_server.db.runtime import get_runtime_session_factory

            runtime_db_factory = get_runtime_session_factory()

        from sqlalchemy import func, select

        from anima_server.models.runtime import RuntimeBackgroundTaskRun

        one_hour_ago = datetime.now(UTC) - timedelta(hours=1)
        stuck_cutoff = datetime.now(UTC) - timedelta(minutes=stuck_threshold_minutes)

        with runtime_db_factory() as db:
            failed_count = db.execute(
                select(func.count())
                .select_from(RuntimeBackgroundTaskRun)
                .where(
                    RuntimeBackgroundTaskRun.user_id == user_id,
                    RuntimeBackgroundTaskRun.status == "failed",
                    RuntimeBackgroundTaskRun.completed_at >= one_hour_ago,
                )
            ).scalar_one()
            details["failed_last_hour"] = failed_count

            stuck_count = db.execute(
                select(func.count())
                .select_from(RuntimeBackgroundTaskRun)
                .where(
                    RuntimeBackgroundTaskRun.user_id == user_id,
                    RuntimeBackgroundTaskRun.status == "running",
                    RuntimeBackgroundTaskRun.started_at < stuck_cutoff,
                )
            ).scalar_one()
            details["stuck_tasks"] = stuck_count

            last_completed = db.execute(
                select(RuntimeBackgroundTaskRun.completed_at)
                .where(
                    RuntimeBackgroundTaskRun.user_id == user_id,
                    RuntimeBackgroundTaskRun.status == "completed",
                    RuntimeBackgroundTaskRun.task_type == "consolidation",
                )
                .order_by(RuntimeBackgroundTaskRun.completed_at.desc())
                .limit(1)
            ).scalar()
            if last_completed is not None:
                # SQLite returns tz-naive datetimes; ensure tz-aware for arithmetic
                if last_completed.tzinfo is None:
                    last_completed = last_completed.replace(tzinfo=UTC)
                details["last_consolidation"] = last_completed.isoformat()
                age_min = (datetime.now(UTC) - last_completed).total_seconds() / 60
                details["consolidation_age_minutes"] = round(age_min, 1)
            else:
                details["last_consolidation"] = None

    except Exception as exc:
        elapsed = (time.monotonic() - start) * 1000
        return CheckResult(
            name="background_tasks",
            status="unhealthy",
            message=f"Failed to query background tasks: {exc}",
            details={"error": str(exc)},
            duration_ms=elapsed,
        )

    elapsed = (time.monotonic() - start) * 1000

    if stuck_count > 0:
        status = "unhealthy"
        issues.append(f"{stuck_count} stuck task(s)")
    elif failed_count > 0:
        status = "degraded"
        issues.append(f"{failed_count} failed task(s) in last hour")
    else:
        status = "healthy"

    if last_completed is None and status == "healthy":
        status = "degraded"
        issues.append("No consolidation history")
    elif last_completed is not None:
        age_min = details.get("consolidation_age_minutes", 0)
        if age_min > stuck_threshold_minutes and status == "healthy":
            status = "degraded"
            issues.append(f"Last consolidation {age_min:.0f}m ago")

    message = (
        "; ".join(issues)
        if issues
        else f"0 failed, 0 stuck, last consolidation {details.get('consolidation_age_minutes', '?')}m ago"
    )

    return CheckResult(
        name="background_tasks",
        status=status,
        message=message,
        details=details,
        duration_ms=elapsed,
    )


async def check_memory_pipeline(
    user_id: int,
    *,
    soul_db_factory: Callable[..., Any] | None = None,
    runtime_db_factory: Callable[..., Any] | None = None,
    retrieval_index_dirty_checker: Callable[[], bool | None] | None = None,
    max_retry: int = 3,
    candidate_backlog_threshold: int = 50,
    pending_op_backlog_threshold: int = 25,
    sync_backlog_threshold: int = 1000,
    stale_work_minutes: int = 30,
    stale_sync_minutes: int = 60,
    min_embedding_coverage: float = 0.8,
) -> CheckResult:
    """Check memory promotion, embedding, sync, and retrieval-index health."""
    start = time.monotonic()
    now = datetime.now(UTC)
    stale_work_cutoff = now - timedelta(minutes=stale_work_minutes)
    stale_sync_cutoff = now - timedelta(minutes=stale_sync_minutes)
    details: dict[str, Any] = {}
    issues: list[str] = []
    unhealthy = False

    try:
        if runtime_db_factory is None:
            from anima_server.db.runtime import get_runtime_session_factory

            runtime_db_factory = get_runtime_session_factory()

        from sqlalchemy import and_, func, or_, select

        from anima_server.models.pending_memory_op import PendingMemoryOp
        from anima_server.models.runtime_memory import (
            MemoryAccessLog,
            MemoryCandidate,
            MemoryExtractionFailure,
            MemoryRetrievalFeedback,
        )

        eligible_candidate_filter = and_(
            MemoryCandidate.user_id == user_id,
            MemoryCandidate.status.in_(["extracted", "queued"]),
        )
        retryable_failed_candidate_filter = and_(
            MemoryCandidate.user_id == user_id,
            MemoryCandidate.status == "failed",
            MemoryCandidate.retry_count < max_retry,
        )
        failed_candidate_filter = and_(
            MemoryCandidate.user_id == user_id,
            MemoryCandidate.status == "failed",
        )
        retry_exhausted_candidate_filter = and_(
            MemoryCandidate.user_id == user_id,
            MemoryCandidate.status == "failed",
            MemoryCandidate.retry_count >= max_retry,
        )
        failed_extraction_filter = and_(
            MemoryExtractionFailure.user_id == user_id,
            MemoryExtractionFailure.status == "failed",
        )
        retryable_extraction_filter = and_(
            MemoryExtractionFailure.user_id == user_id,
            MemoryExtractionFailure.status == "failed",
            MemoryExtractionFailure.retry_count < max_retry,
        )
        retry_exhausted_extraction_filter = and_(
            MemoryExtractionFailure.user_id == user_id,
            MemoryExtractionFailure.status == "failed",
            MemoryExtractionFailure.retry_count >= max_retry,
        )
        pending_op_filter = and_(
            PendingMemoryOp.user_id == user_id,
            PendingMemoryOp.consolidated.is_(False),
            PendingMemoryOp.failed.is_(False),
        )
        failed_pending_op_filter = and_(
            PendingMemoryOp.user_id == user_id,
            PendingMemoryOp.failed.is_(True),
        )
        unsynced_access_filter = and_(
            MemoryAccessLog.user_id == user_id,
            MemoryAccessLog.synced.is_(False),
        )
        unsynced_feedback_filter = and_(
            MemoryRetrievalFeedback.user_id == user_id,
            MemoryRetrievalFeedback.synced.is_(False),
        )

        with runtime_db_factory() as runtime_db:
            candidate_backlog = _count(
                runtime_db,
                select(func.count(MemoryCandidate.id)).where(eligible_candidate_filter),
            )
            retryable_failed_candidates = _count(
                runtime_db,
                select(func.count(MemoryCandidate.id)).where(
                    retryable_failed_candidate_filter
                ),
            )
            failed_candidates = _count(
                runtime_db,
                select(func.count(MemoryCandidate.id)).where(failed_candidate_filter),
            )
            retry_exhausted_candidates = _count(
                runtime_db,
                select(func.count(MemoryCandidate.id)).where(
                    retry_exhausted_candidate_filter
                ),
            )
            stale_candidates = _count(
                runtime_db,
                select(func.count(MemoryCandidate.id)).where(
                    eligible_candidate_filter,
                    MemoryCandidate.created_at < stale_work_cutoff,
                ),
            )
            oldest_candidate = runtime_db.scalar(
                select(func.min(MemoryCandidate.created_at)).where(
                    or_(eligible_candidate_filter, retryable_failed_candidate_filter)
                )
            )
            extraction_failures = _count(
                runtime_db,
                select(func.count(MemoryExtractionFailure.id)).where(
                    failed_extraction_filter
                ),
            )
            retryable_extraction_failures = _count(
                runtime_db,
                select(func.count(MemoryExtractionFailure.id)).where(
                    retryable_extraction_filter
                ),
            )
            retry_exhausted_extraction_failures = _count(
                runtime_db,
                select(func.count(MemoryExtractionFailure.id)).where(
                    retry_exhausted_extraction_filter
                ),
            )
            stale_extraction_failures = _count(
                runtime_db,
                select(func.count(MemoryExtractionFailure.id)).where(
                    retryable_extraction_filter,
                    MemoryExtractionFailure.created_at < stale_work_cutoff,
                ),
            )
            oldest_extraction_failure = runtime_db.scalar(
                select(func.min(MemoryExtractionFailure.created_at)).where(
                    retryable_extraction_filter
                )
            )

            pending_ops_backlog = _count(
                runtime_db,
                select(func.count(PendingMemoryOp.id)).where(pending_op_filter),
            )
            pending_ops_failed = _count(
                runtime_db,
                select(func.count(PendingMemoryOp.id)).where(failed_pending_op_filter),
            )
            stale_pending_ops = _count(
                runtime_db,
                select(func.count(PendingMemoryOp.id)).where(
                    pending_op_filter,
                    PendingMemoryOp.created_at < stale_work_cutoff,
                ),
            )
            oldest_pending_op = runtime_db.scalar(
                select(func.min(PendingMemoryOp.created_at)).where(pending_op_filter)
            )

            access_log_unsynced = _count(
                runtime_db,
                select(func.count(MemoryAccessLog.id)).where(unsynced_access_filter),
            )
            stale_access_logs = _count(
                runtime_db,
                select(func.count(MemoryAccessLog.id)).where(
                    unsynced_access_filter,
                    MemoryAccessLog.accessed_at < stale_sync_cutoff,
                ),
            )
            oldest_access_log = runtime_db.scalar(
                select(func.min(MemoryAccessLog.accessed_at)).where(unsynced_access_filter)
            )

            retrieval_feedback_unsynced = _count(
                runtime_db,
                select(func.count(MemoryRetrievalFeedback.id)).where(
                    unsynced_feedback_filter
                ),
            )
            stale_retrieval_feedback = _count(
                runtime_db,
                select(func.count(MemoryRetrievalFeedback.id)).where(
                    unsynced_feedback_filter,
                    MemoryRetrievalFeedback.created_at < stale_sync_cutoff,
                ),
            )
            oldest_retrieval_feedback = runtime_db.scalar(
                select(func.min(MemoryRetrievalFeedback.created_at)).where(
                    unsynced_feedback_filter
                )
            )
    except Exception as exc:
        elapsed = (time.monotonic() - start) * 1000
        return CheckResult(
            name="memory_pipeline",
            status="unhealthy",
            message=f"Failed to query runtime memory pipeline state: {exc}",
            details={"runtime_error": str(exc)},
            duration_ms=elapsed,
        )

    details.update(
        {
            "candidate_backlog": candidate_backlog,
            "failed_candidates": failed_candidates,
            "retryable_failed_candidates": retryable_failed_candidates,
            "retry_exhausted_candidates": retry_exhausted_candidates,
            "stale_candidates": stale_candidates,
            "oldest_candidate_age_minutes": _age_minutes(now, oldest_candidate),
            "extraction_failures": extraction_failures,
            "retryable_extraction_failures": retryable_extraction_failures,
            "retry_exhausted_extraction_failures": retry_exhausted_extraction_failures,
            "stale_extraction_failures": stale_extraction_failures,
            "oldest_extraction_failure_age_minutes": _age_minutes(
                now, oldest_extraction_failure
            ),
            "pending_ops_backlog": pending_ops_backlog,
            "pending_ops_failed": pending_ops_failed,
            "stale_pending_ops": stale_pending_ops,
            "oldest_pending_op_age_minutes": _age_minutes(now, oldest_pending_op),
            "access_log_unsynced": access_log_unsynced,
            "stale_access_logs": stale_access_logs,
            "oldest_access_log_age_minutes": _age_minutes(now, oldest_access_log),
            "retrieval_feedback_unsynced": retrieval_feedback_unsynced,
            "stale_retrieval_feedback": stale_retrieval_feedback,
            "oldest_retrieval_feedback_age_minutes": _age_minutes(
                now, oldest_retrieval_feedback
            ),
        }
    )

    try:
        if soul_db_factory is None:
            from anima_server.db.session import ensure_user_database

            soul_db_factory = ensure_user_database(user_id)

        from sqlalchemy import select

        from anima_server.models import MemoryItem
        from anima_server.services.agent.embedding_integrity import check_embedding

        with soul_db_factory() as soul_db:
            active_items = list(
                soul_db.scalars(
                    select(MemoryItem).where(
                        MemoryItem.user_id == user_id,
                        MemoryItem.superseded_by.is_(None),
                    )
                ).all()
            )

        embedding_missing_count = 0
        embedding_invalid_count = 0
        for item in active_items:
            checked = check_embedding(item.embedding_json, item.embedding_checksum)
            if checked.status in {"valid", "missing_checksum"}:
                continue
            if item.embedding_json is None:
                embedding_missing_count += 1
            else:
                embedding_invalid_count += 1

        active_memory_items = len(active_items)
        embedded_count = active_memory_items - embedding_missing_count - embedding_invalid_count
        embedding_coverage = (
            round(embedded_count / active_memory_items, 3)
            if active_memory_items
            else 1.0
        )
    except Exception as exc:
        elapsed = (time.monotonic() - start) * 1000
        return CheckResult(
            name="memory_pipeline",
            status="unhealthy",
            message=f"Failed to query durable memory embedding state: {exc}",
            details={**details, "soul_error": str(exc)},
            duration_ms=elapsed,
        )

    details.update(
        {
            "active_memory_items": active_memory_items,
            "embedding_missing_count": embedding_missing_count,
            "embedding_invalid_count": embedding_invalid_count,
            "embedding_coverage": embedding_coverage,
        }
    )

    retrieval_index_dirty = _check_retrieval_index_dirty(
        active_memory_items=active_memory_items,
        retrieval_index_dirty_checker=retrieval_index_dirty_checker,
    )
    details["retrieval_index_dirty"] = retrieval_index_dirty

    if retry_exhausted_candidates > 0:
        unhealthy = True
        issues.append(f"{retry_exhausted_candidates} retry-exhausted candidate(s)")
    elif retryable_failed_candidates > 0:
        issues.append(f"{retryable_failed_candidates} retryable failed candidate(s)")

    if retry_exhausted_extraction_failures > 0:
        unhealthy = True
        issues.append(
            f"{retry_exhausted_extraction_failures} retry-exhausted extraction failure(s)"
        )
    elif retryable_extraction_failures > 0:
        issues.append(f"{retryable_extraction_failures} retryable extraction failure(s)")
    if stale_extraction_failures > 0:
        issues.append(f"{stale_extraction_failures} stale extraction failure(s)")

    if pending_ops_failed > 0:
        unhealthy = True
        issues.append(f"{pending_ops_failed} failed pending memory op(s)")

    if candidate_backlog > candidate_backlog_threshold:
        issues.append(f"{candidate_backlog} queued candidate(s)")
    if stale_candidates > 0:
        issues.append(f"{stale_candidates} stale candidate(s)")

    if pending_ops_backlog > pending_op_backlog_threshold:
        issues.append(f"{pending_ops_backlog} pending memory op(s)")
    if stale_pending_ops > 0:
        issues.append(f"{stale_pending_ops} stale pending memory op(s)")

    if embedding_coverage < min_embedding_coverage:
        issues.append(f"embedding coverage {embedding_coverage:.0%}")
    if embedding_invalid_count > 0:
        issues.append(f"{embedding_invalid_count} invalid embedding(s)")

    if access_log_unsynced > sync_backlog_threshold:
        issues.append(f"{access_log_unsynced} unsynced access log(s)")
    if stale_access_logs > 0:
        issues.append(f"{stale_access_logs} stale access log(s)")

    if retrieval_feedback_unsynced > sync_backlog_threshold:
        issues.append(f"{retrieval_feedback_unsynced} unsynced retrieval feedback row(s)")
    if stale_retrieval_feedback > 0:
        issues.append(f"{stale_retrieval_feedback} stale retrieval feedback row(s)")

    if retrieval_index_dirty is True:
        issues.append("retrieval index dirty")

    elapsed = (time.monotonic() - start) * 1000
    status = "unhealthy" if unhealthy else "degraded" if issues else "healthy"
    message = "; ".join(issues) if issues else "Memory pipeline healthy"
    return CheckResult(
        name="memory_pipeline",
        status=status,
        message=message,
        details=details,
        duration_ms=elapsed,
    )


def _count(db: Any, statement: Any) -> int:
    return int(db.execute(statement).scalar_one() or 0)


def _age_minutes(now: datetime, value: datetime | None) -> float | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return round((now - value).total_seconds() / 60, 1)


def _check_retrieval_index_dirty(
    *,
    active_memory_items: int,
    retrieval_index_dirty_checker: Callable[[], bool | None] | None,
) -> bool | None:
    if retrieval_index_dirty_checker is not None:
        return retrieval_index_dirty_checker()
    if active_memory_items <= 0:
        return False

    try:
        from anima_server.services import anima_core_retrieval
        from anima_server.services.agent.memory_store import memory_retrieval_index_needs_rebuild

        return memory_retrieval_index_needs_rebuild(
            root=anima_core_retrieval.get_retrieval_root()
        )
    except RuntimeError:
        return None
    except Exception:
        return None
