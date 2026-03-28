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

    if any("unreachable" in i.lower() or "failed" in i.lower() for i in issues):
        status = "unhealthy"
    elif any("integrity" in i.lower() for i in issues):
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

        from anima_server.models.runtime import RuntimeBackgroundTaskRun

        one_hour_ago = datetime.now(UTC) - timedelta(hours=1)
        stuck_cutoff = datetime.now(UTC) - timedelta(minutes=stuck_threshold_minutes)

        with runtime_db_factory() as db:
            failed_count = (
                db.query(RuntimeBackgroundTaskRun)
                .filter(
                    RuntimeBackgroundTaskRun.status == "failed",
                    RuntimeBackgroundTaskRun.completed_at >= one_hour_ago,
                )
                .count()
            )
            details["failed_last_hour"] = failed_count

            stuck_count = (
                db.query(RuntimeBackgroundTaskRun)
                .filter(
                    RuntimeBackgroundTaskRun.status == "running",
                    RuntimeBackgroundTaskRun.started_at < stuck_cutoff,
                )
                .count()
            )
            details["stuck_tasks"] = stuck_count

            last_completed = (
                db.query(RuntimeBackgroundTaskRun.completed_at)
                .filter(
                    RuntimeBackgroundTaskRun.status == "completed",
                    RuntimeBackgroundTaskRun.task_type == "consolidation",
                )
                .order_by(RuntimeBackgroundTaskRun.completed_at.desc())
                .scalar()
            )
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
