"""Tests for Retrieval Phase 2: Active Recall and Context Compaction.

Covers:
- 2.1 recall_conversation tool + conversation_search backend
- 2.2 recall_memory upgraded to hybrid_search
- 2.3 LLM-powered summarization upgrade to compaction
- 2.4 Memory pressure warning signal injection
"""

from __future__ import annotations

import gc
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from anima_server.db.base import Base
from anima_server.models.runtime import RuntimeMessage, RuntimeThread
from anima_server.services.agent.compaction import (
    _build_transcript,
    compact_thread_context_with_llm,
    estimate_message_tokens,
    summarize_with_llm,
)
from anima_server.services.agent.conversation_search import (
    ConversationHit,
    _parse_date,
    _text_overlap_score,
    search_conversation_history,
)
from anima_server.services.agent.memory_blocks import MemoryBlock
from anima_server.services.agent.state import StoredMessage
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# _TEST_USER_COUNTER used for stable unique user IDs in runtime DB
_TEST_USER_COUNTER = 0


@contextmanager
def _soul_db_session() -> Generator[Session, None, None]:
    """Soul DB session (for User, etc.)."""
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
        gc.collect()


@contextmanager
def _db_session() -> Generator[Session, None, None]:
    """Combined session with both RuntimeBase and Base tables.

    search_conversation_history needs RuntimeBase tables (messages).
    For unit tests we create both sets of tables on the same in-memory
    SQLite engine.
    """
    from anima_server.db.runtime_base import RuntimeBase
    from anima_server.models import runtime as _rm  # noqa: F401

    engine: Engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # Create soul tables first (Base), then runtime tables (RuntimeBase)
    Base.metadata.create_all(bind=engine)
    RuntimeBase.metadata.create_all(bind=engine)

    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        class_=Session,
    )
    session = factory()
    try:
        yield session
    finally:
        session.close()
        # Drop in reverse order to avoid FK issues
        RuntimeBase.metadata.drop_all(bind=engine)
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
        gc.collect()


class _FakeUser:
    """Stub user for runtime-only tests (runtime DB has no User table)."""

    def __init__(self, user_id: int = 0) -> None:
        global _TEST_USER_COUNTER
        if user_id:
            self.id = user_id
        else:
            _TEST_USER_COUNTER += 1
            self.id = _TEST_USER_COUNTER


def _create_user(db: Session, *, user_id: int = 1) -> _FakeUser:
    return _FakeUser(user_id=user_id)


def _create_thread(db: Session, *, user_id: int = 1, next_seq: int = 1) -> RuntimeThread:
    thread = RuntimeThread(user_id=user_id, status="active", next_message_sequence=next_seq)
    db.add(thread)
    db.flush()
    return thread


def _add_message(
    db: Session,
    *,
    thread_id: int,
    role: str,
    content: str,
    sequence_id: int,
    user_id: int = 1,
    created_at: datetime | None = None,
    is_in_context: bool = True,
    tool_name: str | None = None,
    content_json: dict[str, Any] | None = None,
) -> RuntimeMessage:
    msg = RuntimeMessage(
        thread_id=thread_id,
        user_id=user_id,
        run_id=None,
        step_id=None,
        sequence_id=sequence_id,
        role=role,
        content_text=content,
        content_json=content_json,
        is_in_context=is_in_context,
        tool_name=tool_name,
        token_estimate=estimate_message_tokens(content_text=content),
    )
    if created_at is not None:
        msg.created_at = created_at
    db.add(msg)
    db.flush()
    return msg


# ===========================================================================
# 2.1 — Conversation Search Tests
# ===========================================================================


class TestTextOverlapScore:
    def test_empty_query(self):
        assert _text_overlap_score("", "hello world") == 0.0

    def test_substring_match(self):
        assert _text_overlap_score("hello", "hello world") == 1.0

    def test_word_overlap(self):
        score = _text_overlap_score("job work career", "i talked about my job")
        assert 0.3 <= score <= 0.5

    def test_no_overlap(self):
        assert _text_overlap_score("xyz", "hello world") == 0.0


class TestSearchConversationHistory:
    @pytest.mark.asyncio
    async def test_search_messages_by_text(self):
        with _db_session() as db:
            user = _create_user(db)
            thread = _create_thread(db, user_id=user.id)
            now = datetime.now(UTC)
            _add_message(
                db,
                thread_id=thread.id,
                role="user",
                content="I love cooking pasta",
                sequence_id=1,
                created_at=now,
            )
            _add_message(
                db,
                thread_id=thread.id,
                role="assistant",
                content="That's great! What kind of pasta?",
                sequence_id=2,
                created_at=now,
            )
            _add_message(
                db,
                thread_id=thread.id,
                role="user",
                content="Mostly spaghetti and ravioli",
                sequence_id=3,
                created_at=now,
            )
            db.commit()

            hits = await search_conversation_history(
                db, db,
                user_id=user.id,
                query="cooking pasta",
            )
            assert len(hits) >= 1
            assert any("pasta" in h.content.lower() for h in hits)

    @pytest.mark.asyncio
    async def test_search_empty_query_with_date_range(self):
        with _db_session() as db:
            user = _create_user(db)
            thread = _create_thread(db, user_id=user.id)
            now = datetime.now(UTC)
            _add_message(
                db,
                thread_id=thread.id,
                role="user",
                content="Hello!",
                sequence_id=1,
                created_at=now,
            )
            db.commit()

            hits = await search_conversation_history(
                db, db,
                user_id=user.id,
                query="",
                start_date=now.date().isoformat(),
                end_date=now.date().isoformat(),
            )
            assert len(hits) >= 1

    @pytest.mark.asyncio
    async def test_role_filter(self):
        with _db_session() as db:
            user = _create_user(db)
            thread = _create_thread(db, user_id=user.id)
            now = datetime.now(UTC)
            _add_message(
                db,
                thread_id=thread.id,
                role="user",
                content="I work at Google",
                sequence_id=1,
                created_at=now,
            )
            _add_message(
                db,
                thread_id=thread.id,
                role="assistant",
                content="Google is a great company!",
                sequence_id=2,
                created_at=now,
            )
            db.commit()

            hits = await search_conversation_history(
                db, db,
                user_id=user.id,
                query="Google",
                role_filter="user",
            )
            assert all(h.role == "user" for h in hits)

    @pytest.mark.asyncio
    async def test_excludes_tool_messages(self):
        with _db_session() as db:
            user = _create_user(db)
            thread = _create_thread(db, user_id=user.id)
            now = datetime.now(UTC)
            _add_message(
                db,
                thread_id=thread.id,
                role="tool",
                content="save_to_memory result",
                sequence_id=1,
                created_at=now,
                tool_name="save_to_memory",
            )
            _add_message(
                db,
                thread_id=thread.id,
                role="user",
                content="Can you save that?",
                sequence_id=2,
                created_at=now,
            )
            db.commit()

            hits = await search_conversation_history(
                db, db,
                user_id=user.id,
                query="save",
            )
            # Tool messages should never appear
            assert all(h.role != "tool" for h in hits)

    @pytest.mark.asyncio
    async def test_excludes_tool_call_wrapper_messages(self):
        """Assistant messages that are tool-call wrappers should be excluded."""
        with _db_session() as db:
            user = _create_user(db)
            thread = _create_thread(db, user_id=user.id)
            now = datetime.now(UTC)
            _add_message(
                db,
                thread_id=thread.id,
                role="assistant",
                content="",
                sequence_id=1,
                created_at=now,
                content_json={"tool_calls": [{"name": "save_to_memory"}]},
            )
            _add_message(
                db,
                thread_id=thread.id,
                role="user",
                content="I like sushi",
                sequence_id=2,
                created_at=now,
            )
            db.commit()

            hits = await search_conversation_history(
                db, db,
                user_id=user.id,
                query="sushi",
            )
            assert len(hits) >= 1

    @pytest.mark.asyncio
    async def test_search_runtime_messages(self):
        """Search finds RuntimeMessage rows (MemoryDailyLog path removed)."""
        with _db_session() as db:
            user = _create_user(db)
            thread = _create_thread(db, user_id=user.id)
            _add_message(
                db,
                thread_id=thread.id,
                user_id=user.id,
                role="user",
                content="I got promoted at work today!",
                sequence_id=1,
            )
            _add_message(
                db,
                thread_id=thread.id,
                user_id=user.id,
                role="assistant",
                content="Congratulations! That's wonderful news.",
                sequence_id=2,
            )
            db.commit()

            hits = await search_conversation_history(
                db, db,
                user_id=user.id,
                query="promoted",
            )
            assert len(hits) >= 1
            assert any("promoted" in h.content.lower() for h in hits)

    @pytest.mark.asyncio
    async def test_date_filter_excludes_old(self):
        with _db_session() as db:
            user = _create_user(db)
            thread = _create_thread(db, user_id=user.id)
            old = datetime(2026, 3, 1, tzinfo=UTC)
            recent = datetime(2026, 3, 15, tzinfo=UTC)
            _add_message(
                db,
                thread_id=thread.id,
                role="user",
                content="old message about cats",
                sequence_id=1,
                created_at=old,
            )
            _add_message(
                db,
                thread_id=thread.id,
                role="user",
                content="recent message about cats",
                sequence_id=2,
                created_at=recent,
            )
            db.commit()

            hits = await search_conversation_history(
                db, db,
                user_id=user.id,
                query="cats",
                start_date="2026-03-10",
            )
            assert all("recent" in h.content.lower() for h in hits)

    @pytest.mark.asyncio
    async def test_no_results(self):
        with _db_session() as db:
            user = _create_user(db)
            # No messages at all
            hits = await search_conversation_history(
                db, db,
                user_id=user.id,
                query="nonexistent topic",
            )
            assert hits == []


# ===========================================================================
# 2.2 — Upgraded recall_memory Tests
# ===========================================================================


class TestRecallMemoryHybrid:
    def test_recall_memory_uses_hybrid_search(self):
        """recall_memory should attempt hybrid_search before falling back."""
        from anima_server.services.agent.tools import recall_memory

        mock_ctx = MagicMock()
        mock_ctx.user_id = 1

        mock_result = MagicMock()
        mock_item = MagicMock()
        mock_item.content = "Loves Thai food"
        mock_item.category = "preference"
        mock_result.items = [(mock_item, 0.85)]

        with (
            patch(
                "anima_server.services.agent.tool_context.get_tool_context", return_value=mock_ctx
            ),
            patch(
                "anima_server.services.agent.embeddings.hybrid_search",
                new_callable=AsyncMock,
                return_value=mock_result,
            ) as mock_hs,
        ):
            result = recall_memory("food preferences")
            assert "Thai food" in result
            mock_hs.assert_called_once()

    def test_recall_memory_fallback_on_hybrid_failure(self):
        """recall_memory should fall back to text search when hybrid fails."""
        from anima_server.services.agent.tools import recall_memory

        mock_ctx = MagicMock()
        mock_ctx.user_id = 1

        mock_items = [MagicMock()]
        mock_items[0].content = "allergic to peanuts"
        mock_items[0].category = "fact"

        with (
            patch(
                "anima_server.services.agent.tool_context.get_tool_context", return_value=mock_ctx
            ),
            patch(
                "anima_server.services.agent.embeddings.hybrid_search",
                side_effect=RuntimeError("Provider down"),
            ),
            patch(
                "anima_server.services.agent.memory_store.get_memory_items", return_value=mock_items
            ),
        ):
            result = recall_memory("peanuts")
            assert "peanuts" in result

    def test_recall_memory_category_filter(self):
        """Category filter should be applied to hybrid search results."""
        from anima_server.services.agent.tools import recall_memory

        mock_ctx = MagicMock()
        mock_ctx.user_id = 1

        mock_result = MagicMock()
        fact_item = MagicMock()
        fact_item.content = "Works at Google"
        fact_item.category = "fact"
        pref_item = MagicMock()
        pref_item.content = "Likes Google products"
        pref_item.category = "preference"
        mock_result.items = [(fact_item, 0.8), (pref_item, 0.7)]

        with (
            patch(
                "anima_server.services.agent.tool_context.get_tool_context", return_value=mock_ctx
            ),
            patch(
                "anima_server.services.agent.embeddings.hybrid_search",
                new_callable=AsyncMock,
                return_value=mock_result,
            ),
        ):
            result = recall_memory("Google", category="fact")
            assert "Works at Google" in result
            assert "Likes Google products" not in result


# ===========================================================================
# 2.3 — LLM Summarization Tests
# ===========================================================================


class TestBuildTranscript:
    def test_basic_transcript(self):
        with _db_session() as db:
            user = _create_user(db)
            thread = _create_thread(db, user_id=user.id)
            msg1 = _add_message(
                db, thread_id=thread.id, role="user", content="Hello!", sequence_id=1
            )
            msg2 = _add_message(
                db, thread_id=thread.id, role="assistant", content="Hi there!", sequence_id=2
            )
            db.commit()

            transcript = _build_transcript([msg1, msg2])
            assert "User: Hello!" in transcript
            assert "Assistant: Hi there!" in transcript

    def test_clamp_tools(self):
        with _db_session() as db:
            user = _create_user(db)
            thread = _create_thread(db, user_id=user.id)
            long_result = "x" * 500
            msg = _add_message(
                db,
                thread_id=thread.id,
                role="tool",
                content=long_result,
                sequence_id=1,
                tool_name="recall_memory",
            )
            db.commit()

            transcript = _build_transcript([msg], clamp_tools=True)
            assert len(transcript) < 200  # clamped

            transcript_full = _build_transcript([msg], clamp_tools=False)
            assert len(transcript_full) > 400  # not clamped

    def test_empty_messages_skipped(self):
        with _db_session() as db:
            user = _create_user(db)
            thread = _create_thread(db, user_id=user.id)
            msg = _add_message(db, thread_id=thread.id, role="user", content="", sequence_id=1)
            db.commit()

            transcript = _build_transcript([msg])
            assert transcript == ""


class TestSummarizeWithLlm:
    @pytest.mark.asyncio
    async def test_scaffold_provider_returns_none(self):
        """LLM summarization is skipped for scaffold provider."""
        with patch("anima_server.config.settings") as mock_settings:
            mock_settings.agent_provider = "scaffold"
            result = await summarize_with_llm([])
            assert result is None

    @pytest.mark.asyncio
    async def test_successful_summarization(self):
        """LLM summarization returns summary text on success."""
        with _db_session() as db:
            user = _create_user(db)
            thread = _create_thread(db, user_id=user.id)
            msg = _add_message(
                db, thread_id=thread.id, role="user", content="Hello!", sequence_id=1
            )
            db.commit()

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "choices": [{"message": {"content": "User greeted the assistant."}}],
            }
            mock_response.raise_for_status = MagicMock()

            with (
                patch("anima_server.config.settings") as mock_settings,
                patch(
                    "anima_server.services.agent.llm.resolve_base_url",
                    return_value="http://localhost:8000/v1",
                ),
                patch(
                    "anima_server.services.agent.llm.build_provider_headers",
                    return_value={"Authorization": "Bearer test"},
                ),
                patch("httpx.AsyncClient") as mock_client_cls,
            ):
                mock_settings.agent_provider = "openrouter"
                mock_settings.agent_extraction_model = "test-model"
                mock_settings.agent_model = "test-model"

                mock_client = AsyncMock()
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=False)
                mock_client.post = AsyncMock(return_value=mock_response)
                mock_client_cls.return_value = mock_client

                result = await summarize_with_llm([msg])
                assert result == "User greeted the assistant."

    @pytest.mark.asyncio
    async def test_llm_failure_returns_none(self):
        """LLM summarization returns None on HTTP failure."""
        with _db_session() as db:
            user = _create_user(db)
            thread = _create_thread(db, user_id=user.id)
            msg = _add_message(
                db, thread_id=thread.id, role="user", content="Hello!", sequence_id=1
            )
            db.commit()

            with (
                patch("anima_server.config.settings") as mock_settings,
                patch(
                    "anima_server.services.agent.llm.resolve_base_url",
                    return_value="http://localhost:8000/v1",
                ),
                patch(
                    "anima_server.services.agent.llm.build_provider_headers",
                    return_value={"Authorization": "Bearer test"},
                ),
                patch("httpx.AsyncClient") as mock_client_cls,
            ):
                mock_settings.agent_provider = "openrouter"
                mock_settings.agent_extraction_model = ""
                mock_settings.agent_model = "test-model"

                mock_client = AsyncMock()
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=False)
                mock_client.post = AsyncMock(side_effect=RuntimeError("Connection failed"))
                mock_client_cls.return_value = mock_client

                result = await summarize_with_llm([msg])
                assert result is None


class TestCompactThreadContextWithLlm:
    @pytest.mark.asyncio
    async def test_falls_back_to_text_summary(self):
        """When LLM fails, compact_thread_context_with_llm uses text fallback."""
        with _db_session() as db:
            user = _create_user(db)
            thread = _create_thread(db, user_id=user.id, next_seq=100)

            # Create enough messages to trigger compaction
            for i in range(20):
                role = "user" if i % 2 == 0 else "assistant"
                content = f"Message number {i} with some content to pad tokens" * 5
                _add_message(
                    db,
                    thread_id=thread.id,
                    role=role,
                    content=content,
                    sequence_id=i + 1,
                )
            db.commit()

            with patch(
                "anima_server.services.agent.compaction.summarize_with_llm",
                new_callable=AsyncMock,
                return_value=None,
            ):
                result = await compact_thread_context_with_llm(
                    db,
                    thread=thread,
                    run_id=None,
                    trigger_token_limit=100,
                    keep_last_messages=4,
                )

            assert result is not None
            assert result.compacted_message_count > 0
            assert result.kept_message_count == 4

    @pytest.mark.asyncio
    async def test_no_compaction_below_threshold(self):
        """No compaction when tokens are below trigger limit."""
        with _db_session() as db:
            user = _create_user(db)
            thread = _create_thread(db, user_id=user.id, next_seq=100)
            _add_message(db, thread_id=thread.id, role="user", content="Hi", sequence_id=1)
            _add_message(db, thread_id=thread.id, role="assistant", content="Hello", sequence_id=2)
            db.commit()

            result = await compact_thread_context_with_llm(
                db,
                thread=thread,
                run_id=None,
                trigger_token_limit=99999,
                keep_last_messages=4,
            )
            assert result is None

    @pytest.mark.asyncio
    async def test_llm_summary_used_when_available(self):
        """When LLM succeeds, the summary text should include the LLM output."""
        with _db_session() as db:
            user = _create_user(db)
            thread = _create_thread(db, user_id=user.id, next_seq=100)

            for i in range(20):
                role = "user" if i % 2 == 0 else "assistant"
                content = f"Message number {i} with padding content " * 5
                _add_message(
                    db,
                    thread_id=thread.id,
                    role=role,
                    content=content,
                    sequence_id=i + 1,
                )
            db.commit()

            with patch(
                "anima_server.services.agent.compaction.summarize_with_llm",
                new_callable=AsyncMock,
                return_value="The user discussed 20 topics with the assistant.",
            ):
                result = await compact_thread_context_with_llm(
                    db,
                    thread=thread,
                    run_id=None,
                    trigger_token_limit=100,
                    keep_last_messages=4,
                )

            assert result is not None
            # Check that the LLM summary was used
            from sqlalchemy import select

            summary_msg = db.scalar(
                select(RuntimeMessage).where(
                    RuntimeMessage.thread_id == thread.id,
                    RuntimeMessage.role == "summary",
                    RuntimeMessage.is_in_context.is_(True),
                )
            )
            assert summary_msg is not None
            assert "20 topics" in summary_msg.content_text

    @pytest.mark.asyncio
    async def test_metadata_in_summary(self):
        """Summary message should include metadata prefix."""
        with _db_session() as db:
            user = _create_user(db)
            thread = _create_thread(db, user_id=user.id, next_seq=100)

            for i in range(20):
                role = "user" if i % 2 == 0 else "assistant"
                content = f"Message {i} " * 20
                _add_message(
                    db,
                    thread_id=thread.id,
                    role=role,
                    content=content,
                    sequence_id=i + 1,
                )
            db.commit()

            with patch(
                "anima_server.services.agent.compaction.summarize_with_llm",
                new_callable=AsyncMock,
                return_value=None,
            ):
                result = await compact_thread_context_with_llm(
                    db,
                    thread=thread,
                    run_id=None,
                    trigger_token_limit=100,
                    keep_last_messages=4,
                )

            assert result is not None
            from sqlalchemy import select

            summary_msg = db.scalar(
                select(RuntimeMessage).where(
                    RuntimeMessage.thread_id == thread.id,
                    RuntimeMessage.role == "summary",
                    RuntimeMessage.is_in_context.is_(True),
                )
            )
            assert summary_msg is not None
            assert "[Summary of previous conversation" in summary_msg.content_text
            assert "messages compacted" in summary_msg.content_text


# ===========================================================================
# 2.4 — Memory Pressure Warning Tests
# ===========================================================================


class TestMemoryPressureWarning:
    def test_no_warning_below_threshold(self):
        from anima_server.services.agent.service import _inject_memory_pressure_warning

        blocks = (MemoryBlock(label="soul", value="short text"),)
        history: list[StoredMessage] = [
            StoredMessage(role="user", content="Hello"),
        ]
        companion = MagicMock()
        companion._memory_pressure_alerted = False

        with patch("anima_server.services.agent.service.settings") as mock_settings:
            mock_settings.agent_max_tokens = 4096
            result = _inject_memory_pressure_warning(blocks, history, companion)

        # Should be unchanged — no warning injected
        assert len(result) == len(blocks)

    def test_warning_injected_above_threshold(self):
        from anima_server.services.agent.service import _inject_memory_pressure_warning

        # Create blocks that exceed 80% of 4096 tokens ≈ 3277 tokens ≈ 13108 chars
        large_value = "x" * 14000
        blocks = (MemoryBlock(label="soul", value=large_value),)
        history: list[StoredMessage] = [
            StoredMessage(role="user", content="Hello"),
        ]
        companion = MagicMock()
        companion._memory_pressure_alerted = False

        with patch("anima_server.services.agent.service.settings") as mock_settings:
            mock_settings.agent_max_tokens = 4096
            result = _inject_memory_pressure_warning(blocks, history, companion)

        assert len(result) == len(blocks) + 1
        assert result[-1].label == "memory_pressure_warning"
        assert "context is getting full" in result[-1].value

    def test_warning_only_fires_once(self):
        from anima_server.services.agent.service import _inject_memory_pressure_warning

        large_value = "x" * 14000
        blocks = (MemoryBlock(label="soul", value=large_value),)
        history: list[StoredMessage] = [
            StoredMessage(role="user", content="Hello"),
        ]
        companion = MagicMock()
        companion._memory_pressure_alerted = False

        with patch("anima_server.services.agent.service.settings") as mock_settings:
            mock_settings.agent_max_tokens = 4096

            # First call: warning injected
            result1 = _inject_memory_pressure_warning(blocks, history, companion)
            assert len(result1) == len(blocks) + 1

            # Second call: already alerted, no duplicate
            result2 = _inject_memory_pressure_warning(blocks, history, companion)
            assert len(result2) == len(blocks)

    def test_warning_resets_when_pressure_drops(self):
        from anima_server.services.agent.service import _inject_memory_pressure_warning

        companion = MagicMock()
        companion._memory_pressure_alerted = True  # Was previously alerted

        small_blocks = (MemoryBlock(label="soul", value="short"),)
        history: list[StoredMessage] = [
            StoredMessage(role="user", content="Hi"),
        ]

        with patch("anima_server.services.agent.service.settings") as mock_settings:
            mock_settings.agent_max_tokens = 4096
            _inject_memory_pressure_warning(small_blocks, history, companion)

        # Flag should be reset since we're below threshold
        assert companion._memory_pressure_alerted is False


# ===========================================================================
# recall_conversation tool integration tests
# ===========================================================================


class TestRecallConversationTool:
    def test_recall_conversation_returns_results(self):
        from anima_server.services.agent.tools import recall_conversation

        mock_ctx = MagicMock()
        mock_ctx.user_id = 1

        mock_hits = [
            ConversationHit(
                source="message",
                role="user",
                content="I love cooking pasta",
                date="2026-03-15",
                score=0.9,
            ),
        ]

        with (
            patch(
                "anima_server.services.agent.tool_context.get_tool_context", return_value=mock_ctx
            ),
            patch(
                "anima_server.services.agent.conversation_search.search_conversation_history",
                new_callable=AsyncMock,
                return_value=mock_hits,
            ),
        ):
            result = recall_conversation("cooking pasta")
            assert "cooking pasta" in result
            assert "2026-03-15" in result

    def test_recall_conversation_no_results(self):
        from anima_server.services.agent.tools import recall_conversation

        mock_ctx = MagicMock()
        mock_ctx.user_id = 1

        with (
            patch(
                "anima_server.services.agent.tool_context.get_tool_context", return_value=mock_ctx
            ),
            patch(
                "anima_server.services.agent.conversation_search.search_conversation_history",
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
            result = recall_conversation("nonexistent")
            assert "No past conversations found" in result

    def test_recall_conversation_empty_query_date_browse(self):
        from anima_server.services.agent.tools import recall_conversation

        mock_ctx = MagicMock()
        mock_ctx.user_id = 1

        with (
            patch(
                "anima_server.services.agent.tool_context.get_tool_context", return_value=mock_ctx
            ),
            patch(
                "anima_server.services.agent.conversation_search.search_conversation_history",
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
            result = recall_conversation("", start_date="2026-03-01", end_date="2026-03-15")
            assert "No conversations found in that date range" in result


# ===========================================================================
# Additional edge-case tests (coverage gaps)
# ===========================================================================


class TestParseDate:
    def test_valid_date(self):
        from datetime import date

        assert _parse_date("2026-03-15") == date(2026, 3, 15)

    def test_whitespace_trimmed(self):
        from datetime import date

        assert _parse_date("  2026-03-15  ") == date(2026, 3, 15)

    def test_empty_string(self):
        assert _parse_date("") is None

    def test_whitespace_only(self):
        assert _parse_date("   ") is None

    def test_invalid_format_slash(self):
        assert _parse_date("2026/03/15") is None

    def test_invalid_format_us(self):
        assert _parse_date("03-15-2026") is None

    def test_garbage(self):
        assert _parse_date("not-a-date") is None


class TestBuildTranscriptToolCallWrapper:
    def test_skip_assistant_tool_call_wrapper_with_content(self):
        """Assistant messages that are tool-call wrappers should be excluded
        even when they have some content text."""
        with _db_session() as db:
            user = _create_user(db)
            thread = _create_thread(db, user_id=user.id)
            # Assistant message with tool_calls AND text content
            msg_wrapper = _add_message(
                db,
                thread_id=thread.id,
                role="assistant",
                content="I'll save that.",
                sequence_id=1,
                content_json={"tool_calls": [{"name": "save_to_memory"}]},
            )
            msg_user = _add_message(
                db,
                thread_id=thread.id,
                role="user",
                content="Thanks!",
                sequence_id=2,
            )
            db.commit()

            transcript = _build_transcript([msg_wrapper, msg_user])
            # The wrapper should be excluded, only user message remains
            assert "I'll save that" not in transcript
            assert "User: Thanks!" in transcript

    def test_skip_assistant_tool_call_wrapper_empty_content(self):
        """Assistant messages that are tool-call wrappers with empty content."""
        with _db_session() as db:
            user = _create_user(db)
            thread = _create_thread(db, user_id=user.id)
            msg = _add_message(
                db,
                thread_id=thread.id,
                role="assistant",
                content="",
                sequence_id=1,
                content_json={"tool_calls": [{"name": "recall_memory"}]},
            )
            db.commit()

            transcript = _build_transcript([msg])
            assert transcript == ""

    def test_normal_assistant_message_included(self):
        """Regular assistant messages without tool_calls should be included."""
        with _db_session() as db:
            user = _create_user(db)
            thread = _create_thread(db, user_id=user.id)
            msg = _add_message(
                db,
                thread_id=thread.id,
                role="assistant",
                content="Hello there!",
                sequence_id=1,
            )
            db.commit()

            transcript = _build_transcript([msg])
            assert "Assistant: Hello there!" in transcript


class TestSummarizeWithLlmTranscriptOverride:
    @pytest.mark.asyncio
    async def test_transcript_override_used(self):
        """When transcript_override is provided, it should be used instead of
        building one from rows."""
        with _db_session() as db:
            user = _create_user(db)
            thread = _create_thread(db, user_id=user.id)
            msg = _add_message(
                db,
                thread_id=thread.id,
                role="user",
                content="This should not appear in the prompt",
                sequence_id=1,
            )
            db.commit()

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "choices": [{"message": {"content": "Summary from clamped."}}],
            }
            mock_response.raise_for_status = MagicMock()

            with (
                patch("anima_server.config.settings") as mock_settings,
                patch(
                    "anima_server.services.agent.llm.resolve_base_url",
                    return_value="http://localhost:8000/v1",
                ),
                patch(
                    "anima_server.services.agent.llm.build_provider_headers",
                    return_value={"Authorization": "Bearer test"},
                ),
                patch("httpx.AsyncClient") as mock_client_cls,
            ):
                mock_settings.agent_provider = "openrouter"
                mock_settings.agent_extraction_model = "test-model"
                mock_settings.agent_model = "test-model"

                mock_client = AsyncMock()
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=False)
                mock_client.post = AsyncMock(return_value=mock_response)
                mock_client_cls.return_value = mock_client

                result = await summarize_with_llm(
                    [msg],
                    transcript_override="Clamped: User said hello",
                )
                assert result == "Summary from clamped."
                # Verify the override was used in the prompt
                call_args = mock_client.post.call_args
                body = call_args.kwargs.get("json") or call_args[1].get("json")
                user_msg = body["messages"][1]["content"]
                assert "Clamped: User said hello" in user_msg
                assert "This should not appear" not in user_msg


class TestRecallConversationLimitParsing:
    def test_limit_clamps_to_max_20(self):
        from anima_server.services.agent.tools import recall_conversation

        mock_ctx = MagicMock()
        mock_ctx.user_id = 1

        with (
            patch(
                "anima_server.services.agent.tool_context.get_tool_context", return_value=mock_ctx
            ),
            patch(
                "anima_server.services.agent.conversation_search.search_conversation_history",
                new_callable=AsyncMock,
                return_value=[],
            ) as mock_search,
        ):
            recall_conversation("test", limit="99999")
            # limit should have been clamped to 20
            assert mock_search.call_args.kwargs["limit"] == 20

    def test_limit_clamps_to_min_1(self):
        from anima_server.services.agent.tools import recall_conversation

        mock_ctx = MagicMock()
        mock_ctx.user_id = 1

        with (
            patch(
                "anima_server.services.agent.tool_context.get_tool_context", return_value=mock_ctx
            ),
            patch(
                "anima_server.services.agent.conversation_search.search_conversation_history",
                new_callable=AsyncMock,
                return_value=[],
            ) as mock_search,
        ):
            recall_conversation("test", limit="-5")
            assert mock_search.call_args.kwargs["limit"] == 1

    def test_limit_invalid_string_defaults_to_10(self):
        from anima_server.services.agent.tools import recall_conversation

        mock_ctx = MagicMock()
        mock_ctx.user_id = 1

        with (
            patch(
                "anima_server.services.agent.tool_context.get_tool_context", return_value=mock_ctx
            ),
            patch(
                "anima_server.services.agent.conversation_search.search_conversation_history",
                new_callable=AsyncMock,
                return_value=[],
            ) as mock_search,
        ):
            recall_conversation("test", limit="not_a_number")
            assert mock_search.call_args.kwargs["limit"] == 10


class TestSearchEdgeCases:
    @pytest.mark.asyncio
    async def test_empty_content_message(self):
        """Message with empty content shouldn't crash search."""
        with _db_session() as db:
            user = _create_user(db)
            thread = _create_thread(db, user_id=user.id)
            _add_message(
                db,
                thread_id=thread.id,
                user_id=user.id,
                role="user",
                content="",
                sequence_id=1,
            )
            _add_message(
                db,
                thread_id=thread.id,
                user_id=user.id,
                role="assistant",
                content="The user didn't say anything specific.",
                sequence_id=2,
            )
            db.commit()

            hits = await search_conversation_history(
                db, db,
                user_id=user.id,
                query="didn't say",
            )
            # Should find the assistant response, not crash
            assert any("didn't say" in h.content.lower() for h in hits)

    @pytest.mark.asyncio
    async def test_date_range_boundary_inclusive(self):
        """Start and end dates should be inclusive."""
        with _db_session() as db:
            user = _create_user(db)
            thread = _create_thread(db, user_id=user.id)
            now = datetime(2026, 3, 15, 12, 0, 0, tzinfo=UTC)
            _add_message(
                db,
                thread_id=thread.id,
                role="user",
                content="boundary test message",
                sequence_id=1,
                created_at=now,
            )
            db.commit()

            # Exact date as both start and end — should be included
            hits = await search_conversation_history(
                db, db,
                user_id=user.id,
                query="boundary",
                start_date="2026-03-15",
                end_date="2026-03-15",
            )
            assert len(hits) >= 1

    @pytest.mark.asyncio
    async def test_invalid_date_ignored(self):
        """Invalid date strings should be ignored (not crash)."""
        with _db_session() as db:
            user = _create_user(db)
            thread = _create_thread(db, user_id=user.id)
            now = datetime.now(UTC)
            _add_message(
                db,
                thread_id=thread.id,
                role="user",
                content="test message for invalid date",
                sequence_id=1,
                created_at=now,
            )
            db.commit()

            # Should not raise — invalid dates treated as no filter
            hits = await search_conversation_history(
                db, db,
                user_id=user.id,
                query="invalid date",
                start_date="not-a-date",
                end_date="also-not-a-date",
            )
            assert len(hits) >= 1


class TestMemoryPressureWarningEdgeCases:
    def test_empty_context_no_warning(self):
        """Empty blocks + empty history should not trigger warning."""
        from anima_server.services.agent.service import _inject_memory_pressure_warning

        companion = MagicMock()
        companion._memory_pressure_alerted = False

        with patch("anima_server.services.agent.service.settings") as mock_settings:
            mock_settings.agent_max_tokens = 4096
            result = _inject_memory_pressure_warning((), [], companion)

        assert result == ()

    def test_companion_without_alert_attribute(self):
        """Companion that never had _memory_pressure_alerted should work."""
        from anima_server.services.agent.service import _inject_memory_pressure_warning

        companion = MagicMock(spec=[])  # no attributes at all

        with patch("anima_server.services.agent.service.settings") as mock_settings:
            mock_settings.agent_max_tokens = 4096
            result = _inject_memory_pressure_warning(
                (MemoryBlock(label="soul", value="short"),),
                [],
                companion,
            )

        assert len(result) == 1  # no warning added


class TestLevel2CascadeFix:
    @pytest.mark.asyncio
    async def test_level2_passes_clamped_transcript(self):
        """Level 2 cascade should pass the clamped transcript to summarize_with_llm."""
        with _db_session() as db:
            user = _create_user(db)
            thread = _create_thread(db, user_id=user.id, next_seq=100)

            # Create messages with long tool content that would be clamped
            _add_message(
                db, thread_id=thread.id, role="user", content="Tell me about X " * 20, sequence_id=1
            )
            _add_message(
                db,
                thread_id=thread.id,
                role="tool",
                content="T" * 500,
                sequence_id=2,
                tool_name="recall_memory",
            )
            _add_message(
                db,
                thread_id=thread.id,
                role="assistant",
                content="Here is what I found " * 20,
                sequence_id=3,
            )
            for i in range(4, 20):
                role = "user" if i % 2 == 0 else "assistant"
                _add_message(
                    db, thread_id=thread.id, role=role, content=f"Msg {i} " * 20, sequence_id=i
                )
            db.commit()

            call_count = 0
            received_overrides: list[str | None] = []

            async def mock_summarize(rows, *, transcript_override=None):
                nonlocal call_count
                call_count += 1
                received_overrides.append(transcript_override)
                if call_count == 1:
                    return None  # Level 1 fails
                return "Level 2 succeeded with clamped transcript"

            with patch(
                "anima_server.services.agent.compaction.summarize_with_llm",
                side_effect=mock_summarize,
            ):
                result = await compact_thread_context_with_llm(
                    db,
                    thread=thread,
                    run_id=None,
                    trigger_token_limit=100,
                    keep_last_messages=4,
                )

            assert result is not None
            # Level 1 was called without override, Level 2 with override
            assert call_count == 2
            assert received_overrides[0] is None  # Level 1: no override
            # Level 2: clamped transcript
            assert received_overrides[1] is not None
