from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from anima_server.db.base import Base
from anima_server.models import MemoryDailyLog, MemoryItem, User
from anima_server.services.agent import invalidate_agent_runtime_cache, run_agent
from anima_server.services.agent.consolidation import (
    consolidate_turn_memory,
    drain_background_memory_tasks,
)
from anima_server.services.agent.memory_store import get_memory_items


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


def test_consolidate_turn_memory_writes_daily_log_and_user_memory() -> None:
    with _db_session() as session:
        user = User(
            username="consolidation-test",
            password_hash="not-used",
            display_name="Consolidation Test",
        )
        session.add(user)
        session.commit()

        factory = session.get_bind().engine.dispose  # unused, need the sessionmaker
        # Create a factory that returns the existing session for the test
        eng = session.get_bind()
        test_factory = sessionmaker(
            bind=eng,
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
            class_=Session,
        )

        result = consolidate_turn_memory(
            user_id=user.id,
            user_message=(
                "I love green tea. I work as a product designer. "
                "My current focus is finishing the runtime migration."
            ),
            assistant_response="I hear you. Let's keep the migration tight.",
            now=datetime(2026, 3, 14, 10, 30, tzinfo=UTC),
            db_factory=test_factory,
        )

        # Check daily log was written
        assert result.daily_log_id is not None

        # Check facts extracted
        assert "Works as a product designer" in result.facts_added

        # Check preferences extracted
        assert "Likes green tea" in result.preferences_added

        # Check current focus updated
        assert result.current_focus_updated == "finishing the runtime migration"

        # Verify data in DB
        with test_factory() as db2:
            facts = get_memory_items(db2, user_id=user.id, category="fact")
            assert any("product designer" in f.content for f in facts)

            prefs = get_memory_items(db2, user_id=user.id, category="preference")
            assert any("green tea" in p.content for p in prefs)

            focus = get_memory_items(db2, user_id=user.id, category="focus")
            assert any("runtime migration" in f.content for f in focus)


def test_consolidate_turn_memory_deduplicates_bullet_memory() -> None:
    with _db_session() as session:
        user = User(
            username="dedup-test",
            password_hash="not-used",
            display_name="Dedup Test",
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

        first = consolidate_turn_memory(
            user_id=user.id,
            user_message="I love green tea.",
            assistant_response="Noted.",
            now=datetime(2026, 3, 14, 10, 30, tzinfo=UTC),
            db_factory=test_factory,
        )
        second = consolidate_turn_memory(
            user_id=user.id,
            user_message="I love green tea.",
            assistant_response="Still noted.",
            now=datetime(2026, 3, 14, 10, 31, tzinfo=UTC),
            db_factory=test_factory,
        )

        assert first.preferences_added == ["Likes green tea"]
        assert second.preferences_added == []

        with test_factory() as db2:
            prefs = get_memory_items(db2, user_id=user.id, category="preference")
            matching = [p for p in prefs if "green tea" in p.content.lower()]
            assert len(matching) == 1


@pytest.mark.asyncio
async def test_run_agent_schedules_background_memory_consolidation() -> None:
    invalidate_agent_runtime_cache()

    try:
        with _db_session() as session:
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
            )
            await drain_background_memory_tasks()
    finally:
        invalidate_agent_runtime_cache()

    assert "turn 1" in result.response
