from __future__ import annotations

import json
from collections.abc import Generator
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from types import SimpleNamespace

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
def _bi_sqlite(_type: BigInteger, _compiler: object, **_kw: object) -> str:
    return "INTEGER"


with suppress(Exception):
    compiles(BigInteger, "sqlite")(_bi_sqlite)


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
async def test_maybe_generate_episode_uses_user_scoped_soul_factory_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _dual_db_sessions() as (soul_session, soul_factory, _rt_session, rt_factory):
        user = User(
            username="episode-default-factory",
            password_hash="not-used",
            display_name="Episode Default Factory",
        )
        soul_session.add(user)
        soul_session.commit()

        seen_user_ids: list[int] = []

        def fake_get_user_session_factory(user_id: int) -> sessionmaker[Session]:
            seen_user_ids.append(user_id)
            return soul_factory

        def fail_global_session() -> Session:
            raise AssertionError("global SessionLocal should not be used")

        import anima_server.db.session as session_module

        monkeypatch.setattr(
            session_module,
            "get_user_session_factory",
            fake_get_user_session_factory,
        )
        monkeypatch.setattr(session_module, "SessionLocal", fail_global_session)

        result = await maybe_generate_episode(
            user_id=user.id,
            runtime_db_factory=rt_factory,
        )

        assert result is None
        assert seen_user_ids == [user.id]


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
                ("I want it to remember things.",
                 "Memory is crucial for companionship."),
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
            episodes = db2.query(MemoryEpisode).filter_by(
                user_id=user.id).all()
            assert len(episodes) == 1


@pytest.mark.asyncio
async def test_maybe_generate_episode_uses_preview_when_llm_summary_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.agent import episodes as episodes_module
    from anima_server.services.data_crypto import df

    with _dual_db_sessions() as (soul_session, soul_factory, rt_session, rt_factory):
        user = User(
            username="episode-missing-summary",
            password_hash="not-used",
            display_name="Episode Missing Summary",
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
                ("Julio asked about makeup class reminders.", "I helped him track it."),
                ("He prefers cool neutral tones.", "I noted the palette."),
                ("Galaxy nail art is part of the plan.", "I connected it to the class."),
            ],
        )

        async def empty_episode_payload(*_args: object, **_kwargs: object) -> dict[str, object]:
            return {}

        monkeypatch.setattr(
            episodes_module,
            "_call_llm_for_episode_safe",
            empty_episode_payload,
        )

        result = await maybe_generate_episode(
            user_id=user.id,
            db_factory=soul_factory,
            runtime_db_factory=rt_factory,
        )

        assert result is not None
        summary = df(user.id, result.summary, table="memory_episodes", field="summary")
        assert summary == "Session: Julio asked about makeup class reminders...."


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


@pytest.mark.asyncio
async def test_episode_generation_prompt_uses_names_and_agent_perspective(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.agent import episodes as episodes_module

    captured_prompt = ""

    class FakeLLM:
        async def ainvoke(self, messages: list[object]) -> SimpleNamespace:
            nonlocal captured_prompt
            captured_prompt = str(getattr(messages[1], "content", ""))
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "summary": "Leo showed me a photo and I noticed the style.",
                        "topics": ["appearance", "style"],
                        "emotional_arc": "curious -> reflective",
                        "significance": 3,
                    }
                )
            )

    monkeypatch.setattr(
        "anima_server.services.agent.llm.create_llm",
        lambda: FakeLLM(),
    )

    parsed = await episodes_module._call_llm_for_episode(
        [("I sent a photo.", "I noticed your softer style.")],
        user_id=1,
        user_name="Leo",
        agent_name="Alo",
    )

    assert parsed["summary"] == "Leo showed me a photo and I noticed the style."
    assert "Leo: I sent a photo." in captured_prompt
    assert "Alo: I noticed your softer style." in captured_prompt
    assert "Use first person for Alo" in captured_prompt
    assert "Use Leo's name" in captured_prompt
    assert '"the user"' in captured_prompt
    assert '"the assistant"' in captured_prompt
