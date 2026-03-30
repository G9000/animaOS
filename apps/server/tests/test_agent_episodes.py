from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from anima_server.db.base import Base
from anima_server.db.runtime_base import RuntimeBase
from anima_server.models import MemoryEpisode, User
from anima_server.models.runtime import RuntimeMessage, RuntimeThread
from anima_server.services.agent.episodes import maybe_generate_episode
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


# BigInteger → INTEGER override is already registered in conftest.py.
# Re-registering is harmless (SQLAlchemy deduplicates), but we include
# the import guard so this test file can also run standalone.
try:
    compiles(BigInteger, "sqlite")(_bi_sqlite := lambda t, c, **kw: "INTEGER")
except Exception:
    pass


@contextmanager
def _dual_db_sessions() -> Generator[
    tuple[Session, sessionmaker[Session], Session, sessionmaker[Session]],
    None,
    None,
]:
    """Create two in-memory SQLite engines: soul (Base) + runtime (RuntimeBase).

    Yields (soul_session, soul_factory, runtime_session, runtime_factory).
    """
    # Soul engine — User, MemoryEpisode tables
    soul_engine: Engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    soul_factory = sessionmaker(
        bind=soul_engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        class_=Session,
    )
    Base.metadata.create_all(bind=soul_engine)

    # Runtime engine — RuntimeThread, RuntimeMessage tables
    runtime_engine: Engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    runtime_factory = sessionmaker(
        bind=runtime_engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        class_=Session,
    )
    RuntimeBase.metadata.create_all(bind=runtime_engine)

    soul_session = soul_factory()
    runtime_session = runtime_factory()
    try:
        yield soul_session, soul_factory, runtime_session, runtime_factory
    finally:
        soul_session.close()
        runtime_session.close()
        Base.metadata.drop_all(bind=soul_engine)
        RuntimeBase.metadata.drop_all(bind=runtime_engine)
        soul_engine.dispose()
        runtime_engine.dispose()


def _create_runtime_messages(
    rt_session: Session,
    *,
    user_id: int,
    thread_id: int,
    message_pairs: list[tuple[str, str]],
) -> None:
    """Insert paired user/assistant RuntimeMessages for a thread."""
    seq = 1
    for user_msg, assistant_msg in message_pairs:
        rt_session.add(
            RuntimeMessage(
                thread_id=thread_id,
                user_id=user_id,
                run_id=None,
                step_id=None,
                sequence_id=seq,
                role="user",
                content_text=user_msg,
                is_in_context=True,
                created_at=datetime.now(UTC),
            )
        )
        seq += 1
        rt_session.add(
            RuntimeMessage(
                thread_id=thread_id,
                user_id=user_id,
                run_id=None,
                step_id=None,
                sequence_id=seq,
                role="assistant",
                content_text=assistant_msg,
                is_in_context=True,
                created_at=datetime.now(UTC),
            )
        )
        seq += 1
    rt_session.commit()


@pytest.mark.asyncio
async def test_maybe_generate_episode_requires_minimum_turns() -> None:
    with _dual_db_sessions() as (soul_session, soul_factory, rt_session, rt_factory):
        user = User(
            username="episode-test",
            password_hash="not-used",
            display_name="Episode Test",
        )
        soul_session.add(user)
        soul_session.commit()

        thread = RuntimeThread(user_id=user.id, status="active")
        rt_session.add(thread)
        rt_session.commit()

        _create_runtime_messages(
            rt_session,
            user_id=user.id,
            thread_id=thread.id,
            message_pairs=[
                ("Hello", "Hi there!"),
                ("How are you?", "I'm great!"),
            ],
        )

        result = await maybe_generate_episode(
            user_id=user.id,
            db_factory=soul_factory,
            runtime_db_factory=rt_factory,
        )
        assert result is None


@pytest.mark.asyncio
async def test_maybe_generate_episode_creates_episode_with_enough_turns() -> None:
    with _dual_db_sessions() as (soul_session, soul_factory, rt_session, rt_factory):
        user = User(
            username="episode-gen",
            password_hash="not-used",
            display_name="Episode Gen",
        )
        soul_session.add(user)
        soul_session.commit()

        thread = RuntimeThread(user_id=user.id, status="active")
        rt_session.add(thread)
        rt_session.commit()

        today = datetime.now(UTC).date().isoformat()

        _create_runtime_messages(
            rt_session,
            user_id=user.id,
            thread_id=thread.id,
            message_pairs=[
                ("I'm working on a project.", "Tell me more about it!"),
                ("It's an AI companion.", "Sounds fascinating."),
                ("I want it to remember things.", "Memory is crucial for companionship."),
            ],
        )

        result = await maybe_generate_episode(
            user_id=user.id,
            db_factory=soul_factory,
            runtime_db_factory=rt_factory,
        )
        assert result is not None
        assert result.user_id == user.id
        assert result.date == today
        assert result.turn_count == 3
        assert result.summary

        with soul_factory() as db2:
            episodes = db2.query(MemoryEpisode).filter_by(user_id=user.id).all()
            assert len(episodes) == 1


@pytest.mark.asyncio
async def test_maybe_generate_episode_skips_already_episoded_turns() -> None:
    with _dual_db_sessions() as (soul_session, soul_factory, rt_session, rt_factory):
        user = User(
            username="episode-dedup",
            password_hash="not-used",
            display_name="Episode Dedup",
        )
        soul_session.add(user)
        soul_session.commit()

        thread = RuntimeThread(user_id=user.id, status="active")
        rt_session.add(thread)
        rt_session.commit()

        _create_runtime_messages(
            rt_session,
            user_id=user.id,
            thread_id=thread.id,
            message_pairs=[
                (f"Message {i}", f"Response {i}")
                for i in range(4)
            ],
        )

        first = await maybe_generate_episode(
            user_id=user.id,
            db_factory=soul_factory,
            runtime_db_factory=rt_factory,
        )
        assert first is not None

        # Only 4 pairs, first episode used 3, only 1 remaining < 3 minimum
        second = await maybe_generate_episode(
            user_id=user.id,
            db_factory=soul_factory,
            runtime_db_factory=rt_factory,
        )
        assert second is None
