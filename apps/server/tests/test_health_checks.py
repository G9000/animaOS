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
    assert "runtime" in result.message.lower() or "unreachable" in result.message.lower()


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
async def test_background_tasks_degraded_failures():
    """Failed tasks in the last hour -> degraded."""
    from anima_server.db.runtime import get_runtime_session_factory
    from anima_server.models.runtime import RuntimeBackgroundTaskRun
    from anima_server.services.health.checks import check_background_tasks

    factory = get_runtime_session_factory()
    with factory() as db:
        for i in range(3):
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
