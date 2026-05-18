from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture
def log_dir(tmp_path: Path) -> Path:
    d = tmp_path / "logs"
    d.mkdir()
    return d


# -- db_integrity --------------------------------------------------------


@pytest.mark.asyncio
async def test_db_integrity_healthy():
    """Both SQLite and runtime connections work."""
    from anima_server.services.health.checks import check_db_integrity

    # Use a simple in-memory SQLite for both to avoid needing real PG
    engine = create_engine("sqlite://", poolclass=StaticPool)
    factory = sessionmaker(bind=engine)
    result = await check_db_integrity(
        user_id=1,
        soul_db_factory=factory,
        runtime_db_factory=factory,
    )
    assert result.status == "healthy"
    assert "ok" in result.details.get("sqlite_integrity", "").lower()
    assert result.details.get("pg_connected") is True


@pytest.mark.asyncio
async def test_db_integrity_pg_unreachable():
    """Runtime DB factory raises -- should be unhealthy."""
    from anima_server.services.health.checks import check_db_integrity

    ok_engine = create_engine("sqlite://", poolclass=StaticPool)
    ok_factory = sessionmaker(bind=ok_engine)

    def bad_factory():
        raise RuntimeError("PG down")

    result = await check_db_integrity(
        user_id=1,
        soul_db_factory=ok_factory,
        runtime_db_factory=bad_factory,
    )
    assert result.status == "unhealthy"
    assert "runtime" in result.message.lower(
    ) or "unreachable" in result.message.lower()


# -- llm_connectivity ----------------------------------------------------


@pytest.mark.asyncio
async def test_llm_connectivity_healthy(log_dir: Path):
    from anima_server.services.health.checks import check_llm_connectivity
    from anima_server.services.health.event_logger import EventLogger

    el = EventLogger(log_dir=log_dir, min_level="trace")
    for _ in range(10):
        el.emit("llm", "invoke", "trace")
    el.flush()

    result = await check_llm_connectivity(user_id=1, event_logger=el)
    assert result.status == "healthy"


@pytest.mark.asyncio
async def test_llm_connectivity_degraded(log_dir: Path):
    from anima_server.services.health.checks import check_llm_connectivity
    from anima_server.services.health.event_logger import EventLogger

    el = EventLogger(log_dir=log_dir, min_level="trace")
    for _ in range(8):
        el.emit("llm", "invoke", "trace")
    for _ in range(2):
        el.emit("llm", "failure", "error")
    el.flush()

    result = await check_llm_connectivity(user_id=1, event_logger=el)
    assert result.status == "degraded"


@pytest.mark.asyncio
async def test_llm_connectivity_unhealthy(log_dir: Path):
    from anima_server.services.health.checks import check_llm_connectivity
    from anima_server.services.health.event_logger import EventLogger

    el = EventLogger(log_dir=log_dir, min_level="trace")
    for _ in range(3):
        el.emit("llm", "invoke", "trace")
    for _ in range(7):
        el.emit("llm", "failure", "error")
    el.flush()

    result = await check_llm_connectivity(user_id=1, event_logger=el)
    assert result.status == "unhealthy"


@pytest.mark.asyncio
async def test_llm_connectivity_no_data(log_dir: Path):
    from anima_server.services.health.checks import check_llm_connectivity
    from anima_server.services.health.event_logger import EventLogger

    el = EventLogger(log_dir=log_dir, min_level="trace")
    result = await check_llm_connectivity(user_id=1, event_logger=el)
    assert result.status == "healthy"
    assert "no" in result.message.lower()


# -- background_tasks ----------------------------------------------------


@pytest.mark.asyncio
async def test_background_tasks_healthy():
    """No failed or stuck tasks, recent consolidation."""
    from anima_server.db.runtime import get_runtime_session_factory
    from anima_server.models.runtime import RuntimeBackgroundTaskRun
    from anima_server.services.health.checks import check_background_tasks

    factory = get_runtime_session_factory()
    with factory() as db:
        db.add(RuntimeBackgroundTaskRun(
            user_id=1,
            task_type="consolidation",
            status="completed",
            started_at=datetime.now(UTC) - timedelta(minutes=10),
            completed_at=datetime.now(UTC) - timedelta(minutes=5),
        ))
        db.commit()

    result = await check_background_tasks(user_id=1, runtime_db_factory=factory)
    assert result.status == "healthy"


@pytest.mark.asyncio
async def test_background_tasks_healthy_multiple_consolidations():
    """Multiple completed consolidation tasks should not crash .scalar()."""
    from anima_server.db.runtime import get_runtime_session_factory
    from anima_server.models.runtime import RuntimeBackgroundTaskRun
    from anima_server.services.health.checks import check_background_tasks

    factory = get_runtime_session_factory()
    with factory() as db:
        for i in range(5):
            db.add(RuntimeBackgroundTaskRun(
                user_id=1,
                task_type="consolidation",
                status="completed",
                started_at=datetime.now(UTC) - timedelta(minutes=60 - i * 10),
                completed_at=datetime.now(
                    UTC) - timedelta(minutes=55 - i * 10),
            ))
        db.commit()

    result = await check_background_tasks(user_id=1, runtime_db_factory=factory)
    assert result.status == "healthy"
    assert result.details["last_consolidation"] is not None
    # Most recent completed_at is now - 15 minutes; verify ORDER BY DESC works.
    assert 14 < result.details["consolidation_age_minutes"] < 16


@pytest.mark.asyncio
async def test_background_tasks_degraded_failures():
    """Failed tasks in the last hour -> degraded."""
    from anima_server.db.runtime import get_runtime_session_factory
    from anima_server.models.runtime import RuntimeBackgroundTaskRun
    from anima_server.services.health.checks import check_background_tasks

    factory = get_runtime_session_factory()
    with factory() as db:
        for _i in range(3):
            db.add(RuntimeBackgroundTaskRun(
                user_id=1,
                task_type="consolidation",
                status="failed",
                started_at=datetime.now(UTC) - timedelta(minutes=30),
                completed_at=datetime.now(UTC) - timedelta(minutes=20),
                error_message="test failure",
            ))
        db.commit()

    result = await check_background_tasks(user_id=1, runtime_db_factory=factory)
    assert result.status == "degraded"


@pytest.mark.asyncio
async def test_background_tasks_unhealthy_stuck():
    """Tasks stuck in 'running' state -> unhealthy."""
    from anima_server.db.runtime import get_runtime_session_factory
    from anima_server.models.runtime import RuntimeBackgroundTaskRun
    from anima_server.services.health.checks import check_background_tasks

    factory = get_runtime_session_factory()
    with factory() as db:
        db.add(RuntimeBackgroundTaskRun(
            user_id=1,
            task_type="consolidation",
            status="running",
            started_at=datetime.now(UTC) - timedelta(hours=1),
        ))
        db.commit()

    result = await check_background_tasks(user_id=1, runtime_db_factory=factory)
    assert result.status == "unhealthy"


@pytest.mark.asyncio
async def test_background_tasks_filtered_by_user_id():
    """Tasks from another user should not affect the queried user's report."""
    from anima_server.db.runtime import get_runtime_session_factory
    from anima_server.models.runtime import RuntimeBackgroundTaskRun
    from anima_server.services.health.checks import check_background_tasks

    factory = get_runtime_session_factory()
    with factory() as db:
        # User 2 has a stuck task and failed tasks
        db.add(RuntimeBackgroundTaskRun(
            user_id=2,
            task_type="consolidation",
            status="running",
            started_at=datetime.now(UTC) - timedelta(hours=2),
        ))
        for _ in range(3):
            db.add(RuntimeBackgroundTaskRun(
                user_id=2,
                task_type="consolidation",
                status="failed",
                started_at=datetime.now(UTC) - timedelta(minutes=30),
                completed_at=datetime.now(UTC) - timedelta(minutes=20),
                error_message="test failure",
            ))
        # User 1 has a healthy completed task
        db.add(RuntimeBackgroundTaskRun(
            user_id=1,
            task_type="consolidation",
            status="completed",
            started_at=datetime.now(UTC) - timedelta(minutes=10),
            completed_at=datetime.now(UTC) - timedelta(minutes=5),
        ))
        db.commit()

    # User 1 should be healthy despite user 2's issues
    result = await check_background_tasks(user_id=1, runtime_db_factory=factory)
    assert result.status == "healthy"
    assert result.details["failed_last_hour"] == 0
    assert result.details["stuck_tasks"] == 0

    # User 2 should be unhealthy (stuck task)
    result2 = await check_background_tasks(user_id=2, runtime_db_factory=factory)
    assert result2.status == "unhealthy"
    assert result2.details["stuck_tasks"] >= 1


# -- memory_pipeline -----------------------------------------------------


def _make_soul_factory_with_memory_items(*, missing_embedding: bool = False):
    from anima_server.db.base import Base
    from anima_server.models import MemoryItem, User
    from anima_server.services.agent.embedding_integrity import compute_embedding_checksum

    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)

    embedding = [0.1, 0.2, 0.3]
    with factory() as db:
        user = User(username="health_memory", display_name="Health", password_hash="x")
        db.add(user)
        db.flush()
        db.add(
            MemoryItem(
                user_id=1,
                content="user likes reliable recall",
                category="preference",
                importance=4,
                source="test",
                embedding_json=embedding,
                embedding_checksum=compute_embedding_checksum(embedding),
            )
        )
        if missing_embedding:
            db.add(
                MemoryItem(
                    user_id=1,
                    content="user wants memory health surfaced",
                    category="goal",
                    importance=4,
                    source="test",
                )
            )
        db.commit()

    return factory


@pytest.mark.asyncio
async def test_memory_pipeline_healthy_when_work_is_drained():
    from anima_server.db.runtime import get_runtime_session_factory
    from anima_server.services.health.checks import check_memory_pipeline

    factory = get_runtime_session_factory()
    soul_factory = _make_soul_factory_with_memory_items()

    result = await check_memory_pipeline(
        user_id=1,
        runtime_db_factory=factory,
        soul_db_factory=soul_factory,
        retrieval_index_dirty_checker=lambda: False,
    )

    assert result.status == "healthy"
    assert result.details["candidate_backlog"] == 0
    assert result.details["pending_ops_backlog"] == 0
    assert result.details["embedding_coverage"] == 1.0
    assert result.details["retrieval_index_dirty"] is False


@pytest.mark.asyncio
async def test_memory_pipeline_reports_degradation_causes():
    from anima_server.db.runtime import get_runtime_session_factory
    from anima_server.models.pending_memory_op import PendingMemoryOp
    from anima_server.models.runtime_memory import (
        MemoryAccessLog,
        MemoryCandidate,
        MemoryRetrievalFeedback,
    )
    from anima_server.services.health.checks import check_memory_pipeline

    factory = get_runtime_session_factory()
    old = datetime.now(UTC) - timedelta(hours=2)

    with factory() as db:
        db.add(
            MemoryCandidate(
                user_id=1,
                content="user likes recall observability",
                category="preference",
                importance=4,
                importance_source="llm",
                source="llm",
                content_hash="candidate-backlog",
                status="extracted",
                created_at=old,
            )
        )
        db.add(
            MemoryCandidate(
                user_id=1,
                content="failed extraction should be visible",
                category="fact",
                importance=3,
                importance_source="llm",
                source="llm",
                content_hash="candidate-failed",
                status="failed",
                retry_count=3,
                last_error="model timeout",
                created_at=old,
            )
        )
        db.add(
            PendingMemoryOp(
                user_id=1,
                op_type="replace",
                target_block="identity",
                content="new identity text",
                failed=True,
                failure_reason="write failed",
                created_at=old,
            )
        )
        db.add(
            MemoryAccessLog(
                user_id=1,
                memory_item_id=1,
                accessed_at=old,
                synced=False,
            )
        )
        db.add(
            MemoryRetrievalFeedback(
                user_id=1,
                run_id=10,
                memory_item_id=1,
                was_used=False,
                evidence_score=0.0,
                created_at=old,
                synced=False,
            )
        )
        db.commit()

    result = await check_memory_pipeline(
        user_id=1,
        runtime_db_factory=factory,
        soul_db_factory=_make_soul_factory_with_memory_items(missing_embedding=True),
        retrieval_index_dirty_checker=lambda: True,
    )

    assert result.status == "unhealthy"
    assert result.details["candidate_backlog"] == 1
    assert result.details["failed_candidates"] == 1
    assert result.details["retry_exhausted_candidates"] == 1
    assert result.details["pending_ops_failed"] == 1
    assert result.details["embedding_missing_count"] == 1
    assert result.details["access_log_unsynced"] == 1
    assert result.details["retrieval_feedback_unsynced"] == 1
    assert result.details["retrieval_index_dirty"] is True
    assert "retry-exhausted" in result.message


@pytest.mark.asyncio
async def test_memory_pipeline_reports_failed_extraction_work():
    from anima_server.db.runtime import get_runtime_session_factory
    from anima_server.models.runtime_memory import MemoryExtractionFailure
    from anima_server.services.health.checks import check_memory_pipeline

    factory = get_runtime_session_factory()
    with factory() as db:
        db.add(
            MemoryExtractionFailure(
                user_id=1,
                source_message_ids=[101, 102],
                failure_reason="LLM timed out",
                retry_count=3,
                status="failed",
            )
        )
        db.commit()

    result = await check_memory_pipeline(
        user_id=1,
        runtime_db_factory=factory,
        soul_db_factory=_make_soul_factory_with_memory_items(),
        retrieval_index_dirty_checker=lambda: False,
    )

    assert result.status == "unhealthy"
    assert result.details["extraction_failures"] == 1
    assert result.details["retry_exhausted_extraction_failures"] == 1
    assert "retry-exhausted extraction" in result.message
