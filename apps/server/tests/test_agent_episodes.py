from __future__ import annotations

import json
from collections.abc import Generator
from contextlib import contextmanager, suppress
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from anima_server.db.base import Base
from anima_server.db.runtime_base import RuntimeBase
from anima_server.models import AgentProfile, MemoryEpisode, User
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
    start_at: datetime | None = None,
) -> None:
    """Insert paired user/assistant RuntimeMessages for a thread."""
    seq = 1
    timestamp = start_at or datetime.now(UTC)
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
                created_at=timestamp + timedelta(minutes=seq - 1),
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
                created_at=timestamp + timedelta(minutes=seq - 1),
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
        conversation_started_at=datetime(2026, 6, 29, 2, 15, 30, tzinfo=UTC),
    )

    assert parsed["summary"] == "Leo showed me a photo and I noticed the style."
    assert "Conversation timestamp: 2026-06-29T02:15:30+00:00" in captured_prompt
    assert "Leo: I sent a photo." in captured_prompt
    assert "Alo: I noticed your softer style." in captured_prompt
    assert "Use first person for Alo" in captured_prompt
    assert "Use Leo's name" in captured_prompt
    assert '"salient_user_details"' in captured_prompt
    assert "Preserve the user's original language" in captured_prompt
    assert '"the user"' in captured_prompt
    assert '"the assistant"' in captured_prompt


@pytest.mark.asyncio
async def test_maybe_generate_episode_passes_timestamp_names_and_preserves_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.agent import episodes as episodes_module
    from anima_server.services.data_crypto import df

    captured: dict[str, object] = {}

    async def terse_episode_payload(
        pairs: list[tuple[str, str]],
        *,
        user_id: int = 0,
        user_name: str = "the user",
        agent_name: str = "Anima",
        conversation_started_at: datetime | None = None,
    ) -> dict[str, object]:
        captured["pairs"] = pairs
        captured["user_id"] = user_id
        captured["user_name"] = user_name
        captured["agent_name"] = agent_name
        captured["conversation_started_at"] = conversation_started_at
        return {
            "summary": "Leo discussed model kits and pets.",
            "topics": ["hobbies", "pets"],
            "emotional_arc": "curious -> warm",
            "significance": 3,
            "salient_user_details": [
                "1/72 scale B-29 bomber",
                "1/24 scale Camaro",
                "Muffin, Tappy, and Whiskers",
            ],
        }

    monkeypatch.setattr(
        episodes_module,
        "_call_llm_for_episode_safe",
        terse_episode_payload,
    )

    first_turn_at = datetime(2026, 6, 29, 2, 15, 30, tzinfo=UTC)
    with _dual_db_sessions() as (soul_session, soul_factory, rt_session, rt_factory):
        user = User(
            username="episode-details",
            password_hash="not-used",
            display_name="Leo",
        )
        soul_session.add(user)
        soul_session.flush()
        soul_session.add(
            AgentProfile(
                user_id=user.id,
                agent_name="Alo",
                creator_name="Leo",
            )
        )
        soul_session.commit()

        thread = RuntimeThread(user_id=user.id, status="active")
        rt_session.add(thread)
        rt_session.commit()

        _create_runtime_messages(
            rt_session,
            user_id=user.id,
            thread_id=thread.id,
            start_at=first_turn_at,
            message_pairs=[
                (
                    "I bought a 1/72 scale B-29 bomber and a 1/24 scale Camaro.",
                    "I helped Leo compare paint and display options.",
                ),
                (
                    "My cats are Muffin, Tappy, and Whiskers.",
                    "I noted each cat's name so I could remember them.",
                ),
                (
                    "Please remember the model kits and the cats together.",
                    "I tied the hobby details and pet names into one memory.",
                ),
            ],
        )

        result = await maybe_generate_episode(
            user_id=user.id,
            db_factory=soul_factory,
            runtime_db_factory=rt_factory,
        )

        assert result is not None
        summary = df(user.id, result.summary, table="memory_episodes", field="summary")

    assert captured["user_name"] == "Leo"
    assert captured["agent_name"] == "Alo"
    assert captured["conversation_started_at"] == first_turn_at
    assert "1/72" in summary
    assert "B-29" in summary
    assert "1/24" in summary
    assert "Camaro" in summary
    assert "Muffin" in summary
    assert "Tappy" in summary
    assert "Whiskers" in summary


@pytest.mark.asyncio
async def test_maybe_generate_episode_preserves_multilingual_user_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.agent import episodes as episodes_module
    from anima_server.services.data_crypto import df

    async def terse_episode_payload(
        pairs: list[tuple[str, str]],
        *,
        user_id: int = 0,
        user_name: str = "the user",
        agent_name: str = "Anima",
        conversation_started_at: datetime | None = None,
    ) -> dict[str, object]:
        return {
            "summary": "Leo discussed food and travel memories.",
            "topics": ["food", "travel"],
            "emotional_arc": "nostalgic -> settled",
            "significance": 3,
            "salient_user_details": [
                "今日は東京で寿司を食べた",
                "nasi lemak dekat Kampung Baru",
                "猫の名前はモモ",
            ],
        }

    monkeypatch.setattr(
        episodes_module,
        "_call_llm_for_episode_safe",
        terse_episode_payload,
    )

    with _dual_db_sessions() as (soul_session, soul_factory, rt_session, rt_factory):
        user = User(
            username="episode-multilingual-details",
            password_hash="not-used",
            display_name="Leo",
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
                (
                    "今日は東京で寿司を食べた。忘れないで。",
                    "I noted that Tokyo sushi mattered today.",
                ),
                (
                    "Saya rindu nasi lemak dekat Kampung Baru.",
                    "I held onto that food memory with you.",
                ),
                (
                    "猫の名前はモモです。",
                    "I remembered the cat's name.",
                ),
            ],
        )

        result = await maybe_generate_episode(
            user_id=user.id,
            db_factory=soul_factory,
            runtime_db_factory=rt_factory,
        )

        assert result is not None
        summary = df(user.id, result.summary, table="memory_episodes", field="summary")

    assert "今日は東京で寿司を食べた" in summary
    assert "nasi lemak" in summary
    assert "猫の名前はモモ" in summary


@pytest.mark.asyncio
async def test_maybe_generate_episode_uses_grounded_llm_salient_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.agent import episodes as episodes_module
    from anima_server.services.data_crypto import df

    async def terse_episode_payload(
        pairs: list[tuple[str, str]],
        *,
        user_id: int = 0,
        user_name: str = "the user",
        agent_name: str = "Anima",
        conversation_started_at: datetime | None = None,
    ) -> dict[str, object]:
        return {
            "summary": "Leo shared food memories.",
            "topics": ["food"],
            "emotional_arc": "warm -> reflective",
            "significance": 3,
            "salient_user_details": [
                "東京で寿司",
                "nasi lemak dekat Kampung Baru",
                "大阪でラーメン",
            ],
        }

    monkeypatch.setattr(
        episodes_module,
        "_call_llm_for_episode_safe",
        terse_episode_payload,
    )

    with _dual_db_sessions() as (soul_session, soul_factory, rt_session, rt_factory):
        user = User(
            username="episode-llm-grounded-details",
            password_hash="not-used",
            display_name="Leo",
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
                (
                    "Long setup that should not be copied wholesale. 今日は東京で寿司を食べた。",
                    "I noticed the Tokyo sushi detail.",
                ),
                (
                    "Saya rindu nasi lemak dekat Kampung Baru.",
                    "I held onto that Kuala Lumpur food memory.",
                ),
                (
                    "Keep the important food memories, not every filler word.",
                    "I focused on the concrete details.",
                ),
            ],
        )

        result = await maybe_generate_episode(
            user_id=user.id,
            db_factory=soul_factory,
            runtime_db_factory=rt_factory,
        )

        assert result is not None
        summary = df(user.id, result.summary, table="memory_episodes", field="summary")

    assert "東京で寿司" in summary
    assert "nasi lemak dekat Kampung Baru" in summary
    assert "大阪でラーメン" not in summary
    assert "Long setup that should not be copied wholesale" not in summary


def test_ground_salient_user_details_truncates_after_grounding_long_excerpt() -> None:
    from anima_server.services.agent.episodes import _ground_salient_user_details

    detail = (
        "Leo said the deployment password is a single-use emergency recovery phrase "
        "that should be preserved exactly for the audit trail."
    )

    grounded = _ground_salient_user_details(
        [detail],
        [(detail, "I will preserve that carefully.")],
        max_chars=72,
    )

    assert grounded == [f"{detail[:72].rstrip()}..."]


@pytest.mark.asyncio
async def test_maybe_generate_episode_does_not_append_ordinary_turns_without_salient_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.agent import episodes as episodes_module
    from anima_server.services.data_crypto import df

    async def terse_episode_payload(
        pairs: list[tuple[str, str]],
        *,
        user_id: int = 0,
        user_name: str = "the user",
        agent_name: str = "Anima",
        conversation_started_at: datetime | None = None,
    ) -> dict[str, object]:
        return {
            "summary": "Leo asked a few ordinary setup questions.",
            "topics": ["setup"],
            "emotional_arc": "neutral -> neutral",
            "significance": 2,
        }

    monkeypatch.setattr(
        episodes_module,
        "_call_llm_for_episode_safe",
        terse_episode_payload,
    )

    with _dual_db_sessions() as (soul_session, soul_factory, rt_session, rt_factory):
        user = User(
            username="episode-no-ordinary-fallback",
            password_hash="not-used",
            display_name="Leo",
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
                ("Can you help me set this up?", "Yes."),
                ("What should I try next?", "Try this small step."),
                ("Okay, continue.", "Continuing."),
            ],
        )

        result = await maybe_generate_episode(
            user_id=user.id,
            db_factory=soul_factory,
            runtime_db_factory=rt_factory,
        )

        assert result is not None
        summary = df(user.id, result.summary, table="memory_episodes", field="summary")

    assert "Key details from user" not in summary
    assert "Can you help me set this up?" not in summary
    assert "What should I try next?" not in summary


@pytest.mark.asyncio
async def test_maybe_generate_episode_resolves_relative_dates_in_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.agent import episodes as episodes_module
    from anima_server.services.data_crypto import df

    async def relative_date_payload(
        pairs: list[tuple[str, str]],
        *,
        user_id: int = 0,
        user_name: str = "the user",
        agent_name: str = "Anima",
        conversation_started_at: datetime | None = None,
    ) -> dict[str, object]:
        return {
            "summary": "Leo said the presentation is tomorrow.",
            "topics": ["presentation"],
            "emotional_arc": "focused -> prepared",
            "significance": 3,
            "salient_user_details": ["presentation is tomorrow"],
        }

    monkeypatch.setattr(
        episodes_module,
        "_call_llm_for_episode_safe",
        relative_date_payload,
    )

    first_turn_at = datetime(2026, 6, 29, 9, 0, tzinfo=UTC)
    with _dual_db_sessions() as (soul_session, soul_factory, rt_session, rt_factory):
        user = User(
            username="episode-relative-date",
            password_hash="not-used",
            display_name="Leo",
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
            start_at=first_turn_at,
            message_pairs=[
                ("My presentation is tomorrow.", "I noted the timing."),
                ("Please help me remember the prep.", "I will keep the prep in view."),
                ("The slides still need polish.", "We can focus on the slides."),
            ],
        )

        result = await maybe_generate_episode(
            user_id=user.id,
            db_factory=soul_factory,
            runtime_db_factory=rt_factory,
        )

        assert result is not None
        summary = df(user.id, result.summary, table="memory_episodes", field="summary")

    assert "tomorrow" in summary
    assert "2026-06-30" in summary


@pytest.mark.asyncio
async def test_maybe_generate_episode_resolves_relative_dates_from_matching_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.agent import episodes as episodes_module
    from anima_server.services.data_crypto import df

    async def relative_date_payload(
        pairs: list[tuple[str, str]],
        *,
        user_id: int = 0,
        user_name: str = "the user",
        agent_name: str = "Anima",
        conversation_started_at: datetime | None = None,
    ) -> dict[str, object]:
        return {
            "summary": "Leo said the presentation is tomorrow.",
            "topics": ["presentation"],
            "emotional_arc": "focused -> prepared",
            "significance": 3,
            "salient_user_details": ["presentation is tomorrow"],
        }

    monkeypatch.setattr(
        episodes_module,
        "_call_llm_for_episode_safe",
        relative_date_payload,
    )

    with _dual_db_sessions() as (soul_session, soul_factory, rt_session, rt_factory):
        user = User(
            username="episode-relative-date-cross-midnight",
            password_hash="not-used",
            display_name="Leo",
        )
        soul_session.add(user)
        soul_session.commit()

        thread = RuntimeThread(user_id=user.id, status="active")
        rt_session.add(thread)
        rt_session.commit()

        messages = [
            (
                "user",
                "We were talking before midnight.",
                datetime(2026, 6, 28, 23, 50, tzinfo=UTC),
            ),
            (
                "assistant",
                "I kept that earlier context.",
                datetime(2026, 6, 28, 23, 51, tzinfo=UTC),
            ),
            (
                "user",
                "My presentation is tomorrow.",
                datetime(2026, 6, 29, 0, 10, tzinfo=UTC),
            ),
            (
                "assistant",
                "I noted the presentation timing.",
                datetime(2026, 6, 29, 0, 11, tzinfo=UTC),
            ),
            (
                "user",
                "The slides still need polish.",
                datetime(2026, 6, 29, 0, 12, tzinfo=UTC),
            ),
            (
                "assistant",
                "We can focus on the slides.",
                datetime(2026, 6, 29, 0, 13, tzinfo=UTC),
            ),
        ]
        for sequence_id, (role, content, created_at) in enumerate(messages, start=1):
            rt_session.add(
                RuntimeMessage(
                    thread_id=thread.id,
                    user_id=user.id,
                    run_id=None,
                    step_id=None,
                    sequence_id=sequence_id,
                    role=role,
                    content_text=content,
                    is_in_context=True,
                    created_at=created_at,
                )
            )
        rt_session.commit()

        result = await maybe_generate_episode(
            user_id=user.id,
            db_factory=soul_factory,
            runtime_db_factory=rt_factory,
        )

        assert result is not None
        summary = df(user.id, result.summary, table="memory_episodes", field="summary")

    assert "tomorrow" in summary
    assert "2026-06-30" in summary
    assert "tomorrow=2026-06-29" not in summary
