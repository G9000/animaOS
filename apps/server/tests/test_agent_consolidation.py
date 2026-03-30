from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from anima_server.config import settings
from anima_server.db.base import Base
from anima_server.models import User
from anima_server.services.agent import invalidate_agent_runtime_cache, run_agent
from anima_server.services.agent.consolidation import (
    LLMExtractionResult,
    consolidate_turn_memory,
    consolidate_turn_memory_with_llm,
    drain_background_memory_tasks,
)
from anima_server.services.agent.memory_store import get_memory_items
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


def test_consolidate_turn_memory_extracts_user_memory() -> None:
    with _db_session() as session:
        user = User(
            username="consolidation-test",
            password_hash="not-used",
            display_name="Consolidation Test",
        )
        session.add(user)
        session.commit()

        # need the sessionmaker — engine disposal happens via test teardown
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


def test_consolidate_turn_memory_supersedes_conflicting_preference() -> None:
    with _db_session() as session:
        user = User(
            username="conflict-test",
            password_hash="not-used",
            display_name="Conflict Test",
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
            db_factory=test_factory,
        )
        second = consolidate_turn_memory(
            user_id=user.id,
            user_message="Actually I hate green tea now.",
            assistant_response="Updated.",
            db_factory=test_factory,
        )

        assert first.preferences_added == ["Likes green tea"]
        assert second.preferences_added == []
        assert second.conflicts_resolved == ["Likes green tea -> Dislikes green tea now"]

        with test_factory() as db2:
            active_prefs = get_memory_items(db2, user_id=user.id, category="preference")
            all_prefs = get_memory_items(
                db2, user_id=user.id, category="preference", active_only=False
            )

    assert len(active_prefs) == 1
    assert active_prefs[0].content == "Dislikes green tea now"
    assert len(all_prefs) == 2
    assert any(item.superseded_by is not None for item in all_prefs)


def test_consolidate_turn_memory_keeps_focus_stable_when_unchanged() -> None:
    with _db_session() as session:
        user = User(
            username="focus-dedup-test",
            password_hash="not-used",
            display_name="Focus Dedup Test",
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

        consolidate_turn_memory(
            user_id=user.id,
            user_message="My current focus is finishing the runtime migration.",
            assistant_response="Noted.",
            db_factory=test_factory,
        )
        second = consolidate_turn_memory(
            user_id=user.id,
            user_message="My main focus is finishing the runtime migration.",
            assistant_response="Still noted.",
            db_factory=test_factory,
        )

        with test_factory() as db2:
            focus_items = get_memory_items(
                db2, user_id=user.id, category="focus", active_only=False
            )

    assert second.current_focus_updated == "finishing the runtime migration"
    assert len(focus_items) == 1


@pytest.mark.asyncio
async def test_consolidate_turn_memory_with_llm_deduplicates_slot_paraphrase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _db_session() as session:
        user = User(
            username="llm-dedup-test",
            password_hash="not-used",
            display_name="LLM Dedup Test",
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

        async def fake_extract(**kwargs: object) -> LLMExtractionResult:
            del kwargs
            return LLMExtractionResult(
                memories=[
                    {
                        "content": "Works as product designer",
                        "category": "fact",
                        "importance": 4,
                    }
                ]
            )

        async def fail_conflict(**kwargs: object) -> str:
            raise AssertionError("resolve_conflict should not be called for slot duplicates")

        monkeypatch.setattr(
            "anima_server.services.agent.consolidation.extract_memories_via_llm",
            fake_extract,
        )
        monkeypatch.setattr(
            "anima_server.services.agent.consolidation.resolve_conflict",
            fail_conflict,
        )

        result = await consolidate_turn_memory_with_llm(
            user_id=user.id,
            user_message="I work as a product designer.",
            assistant_response="Noted.",
            db_factory=test_factory,
        )

        with test_factory() as db2:
            facts = get_memory_items(db2, user_id=user.id, category="fact")

    assert result.facts_added == ["Works as a product designer"]
    assert result.llm_items_added == []
    assert len(facts) == 1
    assert facts[0].content == "Works as a product designer"


@pytest.mark.asyncio
async def test_run_agent_schedules_background_memory_consolidation() -> None:
    # Pre-set turn counter so that the next bump lands on a frequency multiple
    # (F5 frequency gating runs every SLEEPTIME_FREQUENCY turns).
    from anima_server.services.agent.sleep_agent import SLEEPTIME_FREQUENCY, _turn_counters

    original_provider = settings.agent_provider
    invalidate_agent_runtime_cache()

    try:
        settings.agent_provider = "scaffold"
        invalidate_agent_runtime_cache()

        with _db_session() as session, runtime_db_session() as runtime_session:
            user = User(
                username="background-memory",
                password_hash="not-used",
                display_name="Background Memory",
            )
            session.add(user)
            session.commit()

            # Set turn counter so next bump triggers sleeptime (lands on frequency multiple)
            _turn_counters[user.id] = SLEEPTIME_FREQUENCY - 1

            result = await run_agent(
                "I prefer short walks. My current focus is finishing the memory pipeline.",
                user.id,
                session,
                runtime_session,
            )
            await drain_background_memory_tasks()
            session.expire_all()

            prefs = get_memory_items(session, user_id=user.id, category="preference")
            focus = get_memory_items(session, user_id=user.id, category="focus")
    finally:
        settings.agent_provider = original_provider
        invalidate_agent_runtime_cache()

    assert "turn 1" in result.response
    assert any("short walks" in item.content.lower() for item in prefs)
    assert any("memory pipeline" in item.content.lower() for item in focus)
