"""Tests for batch episode segmentation (F6)."""
from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from anima_server.db.base import Base
from anima_server.models import MemoryDailyLog, MemoryEpisode, User
from anima_server.services.agent.batch_segmenter import (
    BATCH_THRESHOLD,
    indices_to_0based,
    segment_messages_batch,
    should_batch_segment,
    validate_indices,
)


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
    messages = [
        (f"User message {i}", f"Response {i}") for i in range(1, 9)
    ]

    with patch(
        "anima_server.services.agent.batch_segmenter._call_llm_for_segmentation",
        new_callable=AsyncMock,
        side_effect=RuntimeError("LLM timeout"),
    ):
        groups = await segment_messages_batch(messages)

    # Falls back to single group with all indices
    assert groups == [list(range(1, 9))]


@pytest.mark.asyncio
async def test_segment_messages_batch_invalid_indices_fallback() -> None:
    messages = [
        (f"User message {i}", f"Response {i}") for i in range(1, 9)
    ]

    # LLM returns indices that don't cover all messages (missing 7 and 8)
    with patch(
        "anima_server.services.agent.batch_segmenter._call_llm_for_segmentation",
        new_callable=AsyncMock,
        return_value=[[1, 2, 3], [4, 5, 6]],
    ):
        groups = await segment_messages_batch(messages)

    # Falls back to single group
    assert groups == [list(range(1, 9))]


@pytest.mark.asyncio
async def test_segment_messages_batch_non_contiguous() -> None:
    """Non-contiguous indices are valid when properly covering all messages."""
    messages = [
        (f"User message {i}", f"Response {i}") for i in range(1, 10)
    ]

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
        logs = []
        for i in range(8):
            log = MemoryDailyLog(
                user_id=user.id,
                date=today,
                user_message=f"Message {i + 1}",
                assistant_response=f"Response {i + 1}",
            )
            db.add(log)
            logs.append(log)
        db.commit()

        # Two segments: [0,1,2,4,5] and [3,6,7] (0-based)
        segments_0based = [[0, 1, 2, 4, 5], [3, 6, 7]]

        # Use scaffold provider to avoid LLM calls for episode summary
        with patch(
            "anima_server.services.agent.batch_segmenter.settings"
        ) as mock_settings:
            mock_settings.agent_provider = "scaffold"
            episodes = await generate_episodes_from_segments(
                db,
                user_id=user.id,
                thread_id=None,
                logs=logs,
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
    """With >= 8 logs, batch segmentation is used."""
    from anima_server.services.agent.episodes import maybe_generate_episode

    with _db_session() as session:
        user = User(
            username="batch-episode-test",
            password_hash="not-used",
            display_name="Batch Episode",
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

        today = datetime.now(UTC).date().isoformat()
        for i in range(10):
            session.add(
                MemoryDailyLog(
                    user_id=user.id,
                    date=today,
                    user_message=f"Message {i + 1}",
                    assistant_response=f"Response {i + 1}",
                )
            )
        session.commit()

        # Mock segment_messages_batch to return two groups
        # Mock settings to use scaffold provider (avoids LLM for episode summaries)
        with (
            patch(
                "anima_server.services.agent.batch_segmenter.segment_messages_batch",
                new_callable=AsyncMock,
                return_value=[[1, 2, 3, 7, 8], [4, 5, 6, 9, 10]],
            ),
            patch(
                "anima_server.services.agent.batch_segmenter.settings"
            ) as mock_settings,
        ):
            mock_settings.agent_provider = "scaffold"
            result = await maybe_generate_episode(
                user_id=user.id,
                db_factory=test_factory,
            )

        assert result is not None
        assert result.segmentation_method == "batch_llm"
        assert result.message_indices_json is not None

        # Check that episodes were created in DB (may be merged if topics overlap)
        with test_factory() as db2:
            episodes = db2.query(MemoryEpisode).filter_by(user_id=user.id).all()
            assert len(episodes) >= 1
            assert all(e.segmentation_method == "batch_llm" for e in episodes)
            total_turns = sum(e.turn_count for e in episodes)
            assert total_turns == 10


@pytest.mark.asyncio
async def test_maybe_generate_episode_sequential_under_threshold() -> None:
    """With < 8 logs, sequential method is used (unchanged behavior)."""
    from anima_server.services.agent.episodes import maybe_generate_episode

    with _db_session() as session:
        user = User(
            username="seq-episode-test",
            password_hash="not-used",
            display_name="Seq Episode",
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

        today = datetime.now(UTC).date().isoformat()
        for i in range(5):
            session.add(
                MemoryDailyLog(
                    user_id=user.id,
                    date=today,
                    user_message=f"Message {i + 1}",
                    assistant_response=f"Response {i + 1}",
                )
            )
        session.commit()

        # Use scaffold to avoid LLM call for sequential episode too
        with patch(
            "anima_server.services.agent.episodes.settings"
        ) as mock_settings:
            mock_settings.agent_provider = "scaffold"
            result = await maybe_generate_episode(
                user_id=user.id,
                db_factory=test_factory,
            )

        assert result is not None
        assert result.segmentation_method == "sequential"
        assert result.message_indices_json is None

        with test_factory() as db2:
            episodes = db2.query(MemoryEpisode).filter_by(user_id=user.id).all()
            assert len(episodes) == 1


@pytest.mark.asyncio
async def test_maybe_generate_episode_batch_fallback_on_error() -> None:
    """Batch segmentation failure falls back to sequential method."""
    from anima_server.services.agent.episodes import maybe_generate_episode

    with _db_session() as session:
        user = User(
            username="batch-fallback-test",
            password_hash="not-used",
            display_name="Fallback Test",
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

        today = datetime.now(UTC).date().isoformat()
        for i in range(10):
            session.add(
                MemoryDailyLog(
                    user_id=user.id,
                    date=today,
                    user_message=f"Message {i + 1}",
                    assistant_response=f"Response {i + 1}",
                )
            )
        session.commit()

        # Mock segment_messages_batch to raise an error, use scaffold for fallback
        with (
            patch(
                "anima_server.services.agent.batch_segmenter.segment_messages_batch",
                new_callable=AsyncMock,
                side_effect=RuntimeError("LLM down"),
            ),
            patch(
                "anima_server.services.agent.episodes.settings"
            ) as mock_settings,
        ):
            mock_settings.agent_provider = "scaffold"
            result = await maybe_generate_episode(
                user_id=user.id,
                db_factory=test_factory,
            )

        assert result is not None
        # Falls back to sequential
        assert result.segmentation_method == "sequential"

        with test_factory() as db2:
            episodes = db2.query(MemoryEpisode).filter_by(user_id=user.id).all()
            assert len(episodes) == 1
            # Sequential takes up to 6 logs
            assert episodes[0].turn_count <= 6


@pytest.mark.asyncio
async def test_log_pointer_advances_correctly_after_batch() -> None:
    """After batch segmentation, calling maybe_generate_episode again
    should not re-process already-consumed messages."""
    from anima_server.services.agent.episodes import maybe_generate_episode

    with _db_session() as session:
        user = User(
            username="pointer-test",
            password_hash="not-used",
            display_name="Pointer Test",
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

        today = datetime.now(UTC).date().isoformat()
        for i in range(10):
            session.add(
                MemoryDailyLog(
                    user_id=user.id,
                    date=today,
                    user_message=f"Message {i + 1}",
                    assistant_response=f"Response {i + 1}",
                )
            )
        session.commit()

        # First call: batch segmentation consumes all 10
        with (
            patch(
                "anima_server.services.agent.batch_segmenter.segment_messages_batch",
                new_callable=AsyncMock,
                return_value=[[1, 2, 3, 4, 5], [6, 7, 8, 9, 10]],
            ),
            patch(
                "anima_server.services.agent.batch_segmenter.settings"
            ) as mock_settings,
        ):
            mock_settings.agent_provider = "scaffold"
            first = await maybe_generate_episode(
                user_id=user.id,
                db_factory=test_factory,
            )

        assert first is not None

        # Second call: no remaining logs
        second = await maybe_generate_episode(
            user_id=user.id,
            db_factory=test_factory,
        )
        assert second is None

        with test_factory() as db2:
            episodes = db2.query(MemoryEpisode).filter_by(user_id=user.id).all()
            assert len(episodes) >= 1  # may be merged if topics overlap
            total = sum(e.turn_count for e in episodes)
            assert total == 10
