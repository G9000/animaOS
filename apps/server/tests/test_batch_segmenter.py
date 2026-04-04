"""Tests for batch episode segmentation (F6)."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from anima_server.db.base import Base
from anima_server.db.runtime_base import RuntimeBase
from anima_server.models import MemoryEpisode, User
from anima_server.models.runtime import RuntimeMessage, RuntimeThread
from anima_server.services.agent.batch_segmenter import (
    BATCH_THRESHOLD,
    indices_to_0based,
    segment_messages_batch,
    should_batch_segment,
    validate_indices,
)
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

# ---------------------------------------------------------------------------
# should_batch_segment
# ---------------------------------------------------------------------------


def test_should_batch_segment_below_threshold() -> None:
    assert should_batch_segment(7) is False


def test_should_batch_segment_at_threshold() -> None:
    assert should_batch_segment(8) is True


def test_should_batch_segment_above_threshold() -> None:
    assert should_batch_segment(15) is True


def test_batch_threshold_value() -> None:
    assert BATCH_THRESHOLD == 8


# ---------------------------------------------------------------------------
# validate_indices
# ---------------------------------------------------------------------------


def test_validate_indices_valid() -> None:
    assert validate_indices([[1, 2, 3], [4, 5]], total_messages=5) is True


def test_validate_indices_missing_index() -> None:
    assert validate_indices([[1, 2], [4, 5]], total_messages=5) is False


def test_validate_indices_duplicate() -> None:
    assert validate_indices([[1, 2, 3], [3, 4, 5]], total_messages=5) is False


def test_validate_indices_out_of_range() -> None:
    assert validate_indices([[1, 2, 6]], total_messages=5) is False


def test_validate_indices_zero_index() -> None:
    assert validate_indices([[0, 1, 2]], total_messages=3) is False


def test_validate_indices_single_group() -> None:
    assert validate_indices([[1, 2, 3, 4, 5]], total_messages=5) is True


def test_validate_indices_non_contiguous() -> None:
    assert validate_indices([[1, 3, 5], [2, 4]], total_messages=5) is True


def test_validate_indices_empty_groups() -> None:
    assert validate_indices([], total_messages=5) is False


def test_validate_indices_complex_valid() -> None:
    groups = [[1, 2, 3], [4, 5], [6, 8], [7, 9]]
    assert validate_indices(groups, total_messages=9) is True


# ---------------------------------------------------------------------------
# indices_to_0based
# ---------------------------------------------------------------------------


def test_indices_to_0based() -> None:
    result = indices_to_0based([[1, 2, 3], [4, 5]])
    assert result == [[0, 1, 2], [3, 4]]


def test_indices_to_0based_non_contiguous() -> None:
    result = indices_to_0based([[1, 3, 5], [2, 4]])
    assert result == [[0, 2, 4], [1, 3]]


def test_indices_to_0based_single_group() -> None:
    result = indices_to_0based([[1, 2, 3, 4]])
    assert result == [[0, 1, 2, 3]]


# ---------------------------------------------------------------------------
# segment_messages_batch (with mocked LLM)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_segment_messages_batch_success() -> None:
    messages = [
        ("How is my project?", "Making good progress."),
        ("What should I cook?", "Try pasta!"),
        ("Any blockers on the project?", "The API integration."),
        ("Back to cooking - ingredients?", "Garlic and olive oil."),
        ("Let me check the timeline.", "Deadline is Friday."),
        ("Perfect, thanks for the recipe.", "You're welcome!"),
        ("One more question about work.", "Sure, ask away."),
        ("What about the deployment?", "Should be ready Monday."),
    ]

    with patch(
        "anima_server.services.agent.batch_segmenter._call_llm_for_segmentation",
        new_callable=AsyncMock,
        return_value=[[1, 3, 5, 7, 8], [2, 4, 6]],
    ):
        groups = await segment_messages_batch(messages)

    assert groups == [[1, 3, 5, 7, 8], [2, 4, 6]]
    assert validate_indices(groups, len(messages))


@pytest.mark.asyncio
async def test_segment_messages_batch_llm_failure() -> None:
    messages = [(f"User message {i}", f"Response {i}") for i in range(1, 9)]

    with patch(
        "anima_server.services.agent.batch_segmenter._call_llm_for_segmentation",
        new_callable=AsyncMock,
        side_effect=RuntimeError("LLM timeout"),
    ):
        groups = await segment_messages_batch(messages)

    assert groups == []


@pytest.mark.asyncio
async def test_segment_messages_batch_invalid_indices_fallback() -> None:
    messages = [(f"User message {i}", f"Response {i}") for i in range(1, 9)]

    # LLM returns indices that don't cover all messages (missing 7 and 8)
    with patch(
        "anima_server.services.agent.batch_segmenter._call_llm_for_segmentation",
        new_callable=AsyncMock,
        return_value=[[1, 2, 3], [4, 5, 6]],
    ):
        groups = await segment_messages_batch(messages)

    assert groups == []


@pytest.mark.asyncio
async def test_call_llm_for_segmentation_prefers_extraction_model_and_caps_request() -> None:
    from anima_server.services.agent.batch_segmenter import _call_llm_for_segmentation

    messages = [("User message 1", "Response 1"),
                ("User message 2", "Response 2")]
    mock_client = AsyncMock()
    mock_client.ainvoke = AsyncMock(
        return_value=SimpleNamespace(content="[[1], [2]]"))
    mock_client.aclose = AsyncMock()
    prompt_loader = MagicMock()
    prompt_loader.batch_segmentation.return_value = "prompt"

    with (
        patch("anima_server.services.agent.batch_segmenter.settings") as mock_settings,
        patch(
            "anima_server.services.agent.prompt_loader.PromptLoader",
            return_value=prompt_loader,
        ),
        patch(
            "anima_server.services.agent.llm.resolve_base_url",
            return_value="https://example.test/v1",
        ),
        patch(
            "anima_server.services.agent.llm.build_provider_headers",
            return_value={"Authorization": "Bearer test"},
        ),
        patch(
            "anima_server.services.agent.openai_compatible_client.OpenAICompatibleChatClient",
            return_value=mock_client,
        ) as mock_client_cls,
    ):
        mock_settings.agent_provider = "openai"
        mock_settings.agent_model = "primary-model"
        mock_settings.agent_extraction_model = "cheap-model"
        mock_settings.agent_llm_timeout = 120.0
        mock_settings.agent_max_tokens = 4096

        groups = await _call_llm_for_segmentation(messages)

    assert groups == [[1], [2]]
    assert mock_client_cls.call_args.kwargs["model"] == "cheap-model"
    assert mock_client_cls.call_args.kwargs["timeout"] == 15.0
    assert mock_client_cls.call_args.kwargs["max_tokens"] == 512
    prompt_loader.batch_segmentation.assert_called_once()
    mock_client.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_call_llm_for_segmentation_falls_back_to_primary_model_after_empty_response() -> None:
    from anima_server.services.agent.batch_segmenter import _call_llm_for_segmentation

    messages = [("User message 1", "Response 1")]
    extraction_client = AsyncMock()
    extraction_client.ainvoke = AsyncMock(
        side_effect=[
            SimpleNamespace(content=""),
            SimpleNamespace(content=""),
        ]
    )
    extraction_client.aclose = AsyncMock()

    primary_client = AsyncMock()
    primary_client.ainvoke = AsyncMock(
        return_value=SimpleNamespace(content="[[1]]"))
    primary_client.aclose = AsyncMock()

    prompt_loader = MagicMock()
    prompt_loader.batch_segmentation.return_value = "prompt"

    with (
        patch("anima_server.services.agent.batch_segmenter.settings") as mock_settings,
        patch(
            "anima_server.services.agent.prompt_loader.PromptLoader",
            return_value=prompt_loader,
        ),
        patch(
            "anima_server.services.agent.llm.resolve_base_url",
            return_value="https://example.test/v1",
        ),
        patch(
            "anima_server.services.agent.llm.build_provider_headers",
            return_value={"Authorization": "Bearer test"},
        ),
        patch(
            "anima_server.services.agent.openai_compatible_client.OpenAICompatibleChatClient",
            side_effect=[extraction_client, primary_client],
        ) as mock_client_cls,
    ):
        mock_settings.agent_provider = "openai"
        mock_settings.agent_model = "primary-model"
        mock_settings.agent_extraction_model = "cheap-model"
        mock_settings.agent_llm_timeout = 120.0
        mock_settings.agent_max_tokens = 4096

        groups = await _call_llm_for_segmentation(messages)

    assert groups == [[1]]
    assert [call.kwargs["model"] for call in mock_client_cls.call_args_list] == [
        "cheap-model",
        "primary-model",
    ]
    assert extraction_client.ainvoke.await_count == 2
    primary_client.ainvoke.assert_awaited_once()
    extraction_client.aclose.assert_awaited_once()
    primary_client.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_segment_messages_batch_non_contiguous() -> None:
    """Non-contiguous indices are valid when properly covering all messages."""
    messages = [(f"User message {i}", f"Response {i}") for i in range(1, 10)]

    with patch(
        "anima_server.services.agent.batch_segmenter._call_llm_for_segmentation",
        new_callable=AsyncMock,
        return_value=[[1, 3, 5], [2, 4], [6, 8], [7, 9]],
    ):
        groups = await segment_messages_batch(messages)

    assert groups == [[1, 3, 5], [2, 4], [6, 8], [7, 9]]
    assert validate_indices(groups, 9)


# ---------------------------------------------------------------------------
# Integration: generate_episodes_from_segments
# ---------------------------------------------------------------------------


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


@contextmanager
def _dual_db_sessions() -> Generator[
    tuple[Session, sessionmaker[Session], Session, sessionmaker[Session]],
    None,
    None,
]:
    """Create two in-memory SQLite engines: soul (Base) + runtime (RuntimeBase)."""
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
    count: int,
    msg_prefix: str = "Message",
    resp_prefix: str = "Response",
) -> None:
    """Insert paired user/assistant RuntimeMessages for a thread."""
    seq = 1
    for i in range(1, count + 1):
        rt_session.add(
            RuntimeMessage(
                thread_id=thread_id,
                user_id=user_id,
                run_id=None,
                step_id=None,
                sequence_id=seq,
                role="user",
                content_text=f"{msg_prefix} {i}",
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
                content_text=f"{resp_prefix} {i}",
                is_in_context=True,
                created_at=datetime.now(UTC),
            )
        )
        seq += 1
    rt_session.commit()


@pytest.mark.asyncio
async def test_generate_episodes_from_segments() -> None:
    from anima_server.services.agent.batch_segmenter import (
        generate_episodes_from_segments,
    )

    with _db_session() as db:
        user = User(
            username="batch-seg-test",
            password_hash="not-used",
            display_name="Batch Test",
        )
        db.add(user)
        db.commit()

        today = datetime.now(UTC).date().isoformat()
        # Build pairs as (user_msg, assistant_msg) tuples
        pairs = [(f"Message {i + 1}", f"Response {i + 1}") for i in range(8)]

        # Two segments: [0,1,2,4,5] and [3,6,7] (0-based)
        segments_0based = [[0, 1, 2, 4, 5], [3, 6, 7]]

        # Use scaffold provider to avoid LLM calls for episode summary
        with patch("anima_server.services.agent.batch_segmenter.settings") as mock_settings:
            mock_settings.agent_provider = "scaffold"
            episodes = await generate_episodes_from_segments(
                db,
                user_id=user.id,
                thread_id=None,
                pairs=pairs,
                segments=segments_0based,
                today=today,
            )

        assert len(episodes) == 2

        # First episode: 5 messages, indices [1,2,3,5,6] (1-based)
        assert episodes[0].turn_count == 5
        assert episodes[0].message_indices_json == [1, 2, 3, 5, 6]
        assert episodes[0].segmentation_method == "batch_llm"

        # Second episode: 3 messages, indices [4,7,8] (1-based)
        assert episodes[1].turn_count == 3
        assert episodes[1].message_indices_json == [4, 7, 8]
        assert episodes[1].segmentation_method == "batch_llm"


# ---------------------------------------------------------------------------
# Integration: maybe_generate_episode with batch path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_maybe_generate_episode_batch_path() -> None:
    """With >= 8 pairs, batch segmentation is used."""
    from anima_server.services.agent.episodes import maybe_generate_episode

    with _dual_db_sessions() as (soul_session, soul_factory, rt_session, rt_factory):
        user = User(
            username="batch-episode-test",
            password_hash="not-used",
            display_name="Batch Episode",
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
            count=10,
        )

        # Mock segment_messages_batch to return two groups
        # Mock settings to use scaffold provider (avoids LLM for episode summaries)
        with (
            patch(
                "anima_server.services.agent.batch_segmenter.segment_messages_batch",
                new_callable=AsyncMock,
                return_value=[[1, 2, 3, 7, 8], [4, 5, 6, 9, 10]],
            ),
            patch("anima_server.services.agent.batch_segmenter.settings") as mock_settings,
        ):
            mock_settings.agent_provider = "scaffold"
            result = await maybe_generate_episode(
                user_id=user.id,
                db_factory=soul_factory,
                runtime_db_factory=rt_factory,
            )

        assert result is not None
        assert result.segmentation_method == "batch_llm"
        assert result.message_indices_json is not None

        # Check that episodes were created in DB (may be merged if topics overlap)
        with soul_factory() as db2:
            episodes = db2.query(MemoryEpisode).filter_by(
                user_id=user.id).all()
            assert len(episodes) >= 1
            assert all(e.segmentation_method == "batch_llm" for e in episodes)
            total_turns = sum(e.turn_count for e in episodes)
            assert total_turns == 10


@pytest.mark.asyncio
async def test_maybe_generate_episode_sequential_under_threshold() -> None:
    """With < 8 pairs, sequential method is used (unchanged behavior)."""
    from anima_server.services.agent.episodes import maybe_generate_episode

    with _dual_db_sessions() as (soul_session, soul_factory, rt_session, rt_factory):
        user = User(
            username="seq-episode-test",
            password_hash="not-used",
            display_name="Seq Episode",
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
            count=5,
        )

        # Use scaffold to avoid LLM call for sequential episode too
        with patch("anima_server.services.agent.episodes.settings") as mock_settings:
            mock_settings.agent_provider = "scaffold"
            result = await maybe_generate_episode(
                user_id=user.id,
                db_factory=soul_factory,
                runtime_db_factory=rt_factory,
            )

        assert result is not None
        assert result.segmentation_method == "sequential"
        assert result.message_indices_json is None

        with soul_factory() as db2:
            episodes = db2.query(MemoryEpisode).filter_by(
                user_id=user.id).all()
            assert len(episodes) == 1


@pytest.mark.asyncio
async def test_maybe_generate_episode_batch_fallback_on_error() -> None:
    """Batch segmentation failure falls back to sequential method."""
    from anima_server.services.agent.episodes import maybe_generate_episode

    with _dual_db_sessions() as (soul_session, soul_factory, rt_session, rt_factory):
        user = User(
            username="batch-fallback-test",
            password_hash="not-used",
            display_name="Fallback Test",
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
            count=10,
        )

        # Mock segment_messages_batch to raise an error, use scaffold for fallback
        with (
            patch(
                "anima_server.services.agent.batch_segmenter.segment_messages_batch",
                new_callable=AsyncMock,
                side_effect=RuntimeError("LLM down"),
            ),
            patch("anima_server.services.agent.episodes.settings") as mock_settings,
        ):
            mock_settings.agent_provider = "scaffold"
            result = await maybe_generate_episode(
                user_id=user.id,
                db_factory=soul_factory,
                runtime_db_factory=rt_factory,
            )

        assert result is not None
        # Falls back to sequential
        assert result.segmentation_method == "sequential"

        with soul_factory() as db2:
            episodes = db2.query(MemoryEpisode).filter_by(
                user_id=user.id).all()
            assert len(episodes) == 1
            # Sequential takes up to 6 pairs
            assert episodes[0].turn_count <= 6


@pytest.mark.asyncio
async def test_maybe_generate_episode_batch_empty_groups_falls_back_to_sequential() -> None:
    """Empty batch groups fall back to sequential method."""
    from anima_server.services.agent.episodes import maybe_generate_episode

    with _dual_db_sessions() as (soul_session, soul_factory, rt_session, rt_factory):
        user = User(
            username="batch-empty-fallback-test",
            password_hash="not-used",
            display_name="Empty Fallback Test",
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
            count=10,
        )

        with (
            patch(
                "anima_server.services.agent.batch_segmenter.segment_messages_batch",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch("anima_server.services.agent.episodes.settings") as mock_settings,
        ):
            mock_settings.agent_provider = "scaffold"
            result = await maybe_generate_episode(
                user_id=user.id,
                db_factory=soul_factory,
                runtime_db_factory=rt_factory,
            )

        assert result is not None
        assert result.segmentation_method == "sequential"

        with soul_factory() as db2:
            episodes = db2.query(MemoryEpisode).filter_by(
                user_id=user.id).all()
            assert len(episodes) == 1
            assert episodes[0].turn_count <= 6


@pytest.mark.asyncio
async def test_log_pointer_advances_correctly_after_batch() -> None:
    """After batch segmentation, calling maybe_generate_episode again
    should not re-process already-consumed messages."""
    from anima_server.services.agent.episodes import maybe_generate_episode

    with _dual_db_sessions() as (soul_session, soul_factory, rt_session, rt_factory):
        user = User(
            username="pointer-test",
            password_hash="not-used",
            display_name="Pointer Test",
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
            count=10,
        )

        # First call: batch segmentation consumes all 10
        with (
            patch(
                "anima_server.services.agent.batch_segmenter.segment_messages_batch",
                new_callable=AsyncMock,
                return_value=[[1, 2, 3, 4, 5], [6, 7, 8, 9, 10]],
            ),
            patch("anima_server.services.agent.batch_segmenter.settings") as mock_settings,
        ):
            mock_settings.agent_provider = "scaffold"
            first = await maybe_generate_episode(
                user_id=user.id,
                db_factory=soul_factory,
                runtime_db_factory=rt_factory,
            )

        assert first is not None

        # Second call: no remaining pairs
        second = await maybe_generate_episode(
            user_id=user.id,
            db_factory=soul_factory,
            runtime_db_factory=rt_factory,
        )
        assert second is None

        with soul_factory() as db2:
            episodes = db2.query(MemoryEpisode).filter_by(
                user_id=user.id).all()
            assert len(episodes) >= 1  # may be merged if topics overlap
            total = sum(e.turn_count for e in episodes)
            assert total == 10
