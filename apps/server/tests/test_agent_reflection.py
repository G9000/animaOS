from __future__ import annotations

import asyncio
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from anima_server.db.base import Base
from anima_server.models import MemoryEpisode, User
from anima_server.services.agent import reflection as reflection_service
from anima_server.services.agent.reflection import run_reflection
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
async def test_run_reflection_generates_episode_when_turns_available() -> None:
    """Episode generation reads from RuntimeMessage (not MemoryDailyLog)."""
    from anima_server.db.runtime_base import RuntimeBase
    from anima_server.models.runtime import RuntimeMessage, RuntimeThread

    with _db_session() as session, runtime_db_session() as runtime_session:
        user = User(
            username="reflection-test",
            password_hash="not-used",
            display_name="Reflection Test",
        )
        session.add(user)
        session.commit()

        # Create a runtime thread and messages
        thread = RuntimeThread(user_id=user.id)
        runtime_session.add(thread)
        runtime_session.flush()

        now = datetime.now(UTC)
        for i in range(3):
            runtime_session.add(
                RuntimeMessage(
                    thread_id=thread.id,
                    user_id=user.id,
                    sequence_id=i * 2,
                    role="user",
                    content_text=f"Turn {i} message",
                    created_at=now,
                )
            )
            runtime_session.add(
                RuntimeMessage(
                    thread_id=thread.id,
                    user_id=user.id,
                    sequence_id=i * 2 + 1,
                    role="assistant",
                    content_text=f"Turn {i} response",
                    created_at=now,
                )
            )
        runtime_session.commit()

        # Call maybe_generate_episode directly with dual factories
        eng = session.get_bind()
        soul_factory = sessionmaker(
            bind=eng,
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
            class_=Session,
        )
        rt_eng = runtime_session.get_bind()
        rt_factory = sessionmaker(
            bind=rt_eng,
            autoflush=False,
            expire_on_commit=False,
        )

        from anima_server.services.agent.episodes import maybe_generate_episode

        episode = await maybe_generate_episode(
            user_id=user.id,
            db_factory=soul_factory,
            runtime_db_factory=rt_factory,
        )

        with soul_factory() as db2:
            episodes = db2.query(MemoryEpisode).filter_by(user_id=user.id).all()
            assert len(episodes) == 1
            assert episodes[0].turn_count == 3


@pytest.mark.asyncio
async def test_run_reflection_does_not_fail_with_no_turns() -> None:
    with _db_session() as session:
        user = User(
            username="reflection-empty",
            password_hash="not-used",
            display_name="Reflection Empty",
        )
        session.add(user)
        session.commit()

        eng = session.get_bind()
        test_factory = sessionmaker(
            bind=eng,
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
            class_=Session,
        )

        await run_reflection(
            user_id=user.id,
            db_factory=test_factory,
        )

        with test_factory() as db2:
            episodes = db2.query(MemoryEpisode).filter_by(user_id=user.id).all()
            assert len(episodes) == 0


@pytest.mark.asyncio
async def test_cancel_pending_reflection_clears_user_state() -> None:
    user_id = 999
    reflection_service._last_activities[user_id] = datetime.now(UTC)
    task = asyncio.create_task(asyncio.sleep(60))
    reflection_service._pending_reflections[user_id] = task

    await reflection_service.cancel_pending_reflection(user_id=user_id)

    assert user_id not in reflection_service._pending_reflections
    assert user_id not in reflection_service._last_activities
    assert task.cancelled() or task.done()
