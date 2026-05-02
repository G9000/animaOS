from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from unittest.mock import AsyncMock, patch

import pytest
from anima_server.config import settings
from anima_server.db.base import Base
from anima_server.models import User
from anima_server.services.agent import invalidate_agent_runtime_cache, run_agent
from anima_server.services.agent.consolidation import (
    drain_background_memory_tasks,
    run_background_extraction,
)
from conftest_runtime import runtime_db_session
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


@contextmanager
def _db_session() -> Generator[Session, None, None]:
    engine: Engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        class_=Session,
    )
    Base.metadata.create_all(bind=engine)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


@pytest.mark.asyncio
async def test_run_background_extraction_creates_candidates() -> None:
    """run_background_extraction writes MemoryCandidates to the runtime DB (PG),
    not MemoryItems to the soul DB (SQLCipher)."""
    from anima_server.models.runtime_memory import MemoryCandidate

    original_provider = settings.agent_provider
    try:
        settings.agent_provider = "scaffold"

        with runtime_db_session() as runtime_session:
            rt_engine = runtime_session.get_bind()
            rt_factory = sessionmaker(
                bind=rt_engine,
                autoflush=False,
                autocommit=False,
                expire_on_commit=False,
                class_=Session,
            )

            await run_background_extraction(
                user_id=1,
                user_message="I prefer short walks. I work as a product designer.",
                assistant_response="That sounds nice!",
                runtime_db_factory=rt_factory,
            )

            with rt_factory() as rt_db:
                candidates = list(
                    rt_db.query(MemoryCandidate)
                    .filter(MemoryCandidate.user_id == 1)
                    .all()
                )

        contents = [c.content.lower() for c in candidates]
        assert any("short walks" in c for c in contents)
        assert any("product designer" in c for c in contents)
        # All should be regex-sourced in scaffold mode (no LLM)
        assert all(c.source == "regex" for c in candidates)
        assert all(c.importance_source == "regex" for c in candidates)
    finally:
        settings.agent_provider = original_provider


@pytest.mark.asyncio
async def test_run_background_extraction_normalizes_whitespace_before_regex() -> None:
    from anima_server.models.runtime_memory import MemoryCandidate

    original_provider = settings.agent_provider
    try:
        settings.agent_provider = "scaffold"

        with runtime_db_session() as runtime_session:
            rt_engine = runtime_session.get_bind()
            rt_factory = sessionmaker(
                bind=rt_engine,
                autoflush=False,
                autocommit=False,
                expire_on_commit=False,
                class_=Session,
            )

            await run_background_extraction(
                user_id=1,
                user_message="I work\tat Acme Corp.\r\nI prefer   green tea.",
                assistant_response="Noted.",
                runtime_db_factory=rt_factory,
            )

            with rt_factory() as rt_db:
                candidates = list(
                    rt_db.query(MemoryCandidate)
                    .filter(MemoryCandidate.user_id == 1)
                    .all()
                )

        contents = [c.content for c in candidates]
        assert "Works at Acme Corp" in contents
        assert "Prefers green tea" in contents
    finally:
        settings.agent_provider = original_provider


@pytest.mark.asyncio
async def test_run_agent_schedules_background_memory_consolidation() -> None:
    original_provider = settings.agent_provider
    invalidate_agent_runtime_cache()

    try:
        settings.agent_provider = "scaffold"
        invalidate_agent_runtime_cache()

        with patch(
            "anima_server.services.agent.sleep_agent.run_sleeptime_agents",
            new=AsyncMock(return_value=[]),
        ) as run_sleeptime_agents, patch(
            "anima_server.services.agent.consolidation.run_background_extraction",
            new=AsyncMock(return_value=None),
        ) as run_background_extraction, _db_session() as session, runtime_db_session() as runtime_session:
            user = User(
                username="background-memory",
                password_hash="not-used",
                display_name="Background Memory",
            )
            session.add(user)
            session.commit()

            result = await run_agent(
                "I prefer short walks. My current focus is finishing the memory pipeline.",
                user.id,
                session,
                runtime_session,
            )
            await drain_background_memory_tasks()
    finally:
        settings.agent_provider = original_provider
        invalidate_agent_runtime_cache()

    assert "turn 1" in result.response
    run_background_extraction.assert_awaited_once()
    run_sleeptime_agents.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_agent_routes_third_turn_into_sleeptime_orchestrator() -> None:
    original_provider = settings.agent_provider
    invalidate_agent_runtime_cache()

    try:
        settings.agent_provider = "scaffold"
        invalidate_agent_runtime_cache()

        with patch(
            "anima_server.services.agent.sleep_agent.run_sleeptime_agents",
            new=AsyncMock(return_value=[]),
        ) as run_sleeptime_agents, patch(
            "anima_server.services.agent.consolidation.run_background_extraction",
            new=AsyncMock(return_value=None),
        ) as run_background_extraction, _db_session() as session, runtime_db_session() as runtime_session:
            user = User(
                username="background-memory-third-turn",
                password_hash="not-used",
                display_name="Background Memory Third Turn",
            )
            session.add(user)
            session.commit()

            await run_agent("first turn", user.id, session, runtime_session)
            await drain_background_memory_tasks()

            await run_agent("second turn", user.id, session, runtime_session)
            await drain_background_memory_tasks()

            result = await run_agent("third turn", user.id, session, runtime_session)
            await drain_background_memory_tasks()
    finally:
        settings.agent_provider = original_provider
        invalidate_agent_runtime_cache()

    assert "turn 3" in result.response
    assert run_background_extraction.await_count == 3
    run_sleeptime_agents.assert_awaited_once()
    assert run_sleeptime_agents.await_args.kwargs["user_id"] == user.id
    assert run_background_extraction.await_args_list[-1].kwargs["trigger_soul_writer"] is False
