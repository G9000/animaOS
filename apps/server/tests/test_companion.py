"""Unit tests for AnimaCompanion — cache, invalidation, versioning, reset."""

from __future__ import annotations

from collections import deque
from contextlib import contextmanager
from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from anima_server.db.base import Base
from anima_server.models import AgentMessage, AgentThread, User
from anima_server.services.agent.adapters.base import BaseLLMAdapter
from anima_server.services.agent.companion import (
    AnimaCompanion,
    get_companion,
    get_or_build_companion,
    invalidate_companion,
)
from anima_server.services.agent.memory_blocks import MemoryBlock
from anima_server.services.agent.runtime import AgentRuntime
from anima_server.services.agent.runtime_types import LLMRequest, StepExecutionResult, StopReason, ToolCall
from anima_server.services.agent.state import StoredMessage
from anima_server.services.agent.tools import send_message
from anima_server.services.agent.rules import TerminalToolRule


class QueueAdapter(BaseLLMAdapter):
    provider = "test"
    model = "test-model"

    def __init__(self, responses: list[StepExecutionResult] | None = None) -> None:
        self._responses = deque(responses or [])
        self.requests: list[LLMRequest] = []

    async def invoke(self, request: LLMRequest) -> StepExecutionResult:
        self.requests.append(request)
        if not self._responses:
            raise AssertionError("No queued LLM responses remain.")
        return self._responses.popleft()


def _make_runtime(adapter: QueueAdapter | None = None) -> AgentRuntime:
    return AgentRuntime(
        adapter=adapter or QueueAdapter(),
        tools=[send_message],
        tool_rules=[TerminalToolRule(tool_name="send_message")],
        max_steps=2,
    )


def _make_companion(
    runtime: AgentRuntime | None = None,
    user_id: int = 1,
    keep_last_messages: int = 50,
) -> AnimaCompanion:
    return AnimaCompanion(
        runtime=runtime or _make_runtime(),
        user_id=user_id,
        keep_last_messages=keep_last_messages,
    )


SOUL_BLOCK = MemoryBlock(label="soul", value="I am Anima.",
                         description="origin", read_only=True)
PERSONA_BLOCK = MemoryBlock(
    label="persona", value="Curious and warm.", description="persona", read_only=True)
FACTS_BLOCK = MemoryBlock(
    label="facts", value="- Works as engineer", description="facts", read_only=True)


# ------------------------------------------------------------------
# Version-counter cache
# ------------------------------------------------------------------

class TestMemoryCache:
    def test_starts_stale(self) -> None:
        companion = _make_companion()
        assert companion.memory_stale is True
        assert companion.get_cached_memory_blocks() is None

    def test_set_cache_marks_current(self) -> None:
        companion = _make_companion()
        blocks = (SOUL_BLOCK, PERSONA_BLOCK)
        companion.set_memory_cache(blocks)
        assert companion.memory_stale is False
        assert companion.get_cached_memory_blocks() == blocks

    def test_invalidate_bumps_version_without_clearing_cache(self) -> None:
        companion = _make_companion()
        blocks = (SOUL_BLOCK,)
        companion.set_memory_cache(blocks)
        assert companion.memory_stale is False

        companion.invalidate_memory()

        # Cache data still present (for in-flight turns) but stale
        assert companion._memory_cache == blocks
        assert companion.memory_stale is True
        # get_cached_memory_blocks returns None because stale
        assert companion.get_cached_memory_blocks() is None

    def test_set_after_invalidate_re_marks_current(self) -> None:
        companion = _make_companion()
        companion.set_memory_cache((SOUL_BLOCK,))
        companion.invalidate_memory()
        assert companion.memory_stale is True

        companion.set_memory_cache((SOUL_BLOCK, FACTS_BLOCK))
        assert companion.memory_stale is False
        assert companion.get_cached_memory_blocks() == (SOUL_BLOCK, FACTS_BLOCK)

    def test_multiple_invalidations_require_one_reload(self) -> None:
        companion = _make_companion()
        companion.set_memory_cache((SOUL_BLOCK,))
        companion.invalidate_memory()
        companion.invalidate_memory()
        companion.invalidate_memory()

        assert companion._memory_version == 3
        # One set_memory_cache catches up to the latest version
        companion.set_memory_cache((PERSONA_BLOCK,))
        assert companion.memory_stale is False


# ------------------------------------------------------------------
# System prompt cache
# ------------------------------------------------------------------

class TestSystemPromptCache:
    def test_starts_none(self) -> None:
        companion = _make_companion()
        assert companion.get_cached_system_prompt() is None

    def test_set_and_get(self) -> None:
        companion = _make_companion()
        companion.set_memory_cache((SOUL_BLOCK,))
        companion.set_system_prompt("Hello, I am Anima.")
        assert companion.get_cached_system_prompt() == "Hello, I am Anima."

    def test_invalidated_when_memory_invalidated(self) -> None:
        companion = _make_companion()
        companion.set_memory_cache((SOUL_BLOCK,))
        companion.set_system_prompt("prompt v1")

        companion.invalidate_memory()
        assert companion.get_cached_system_prompt() is None

    def test_survives_when_memory_not_invalidated(self) -> None:
        companion = _make_companion()
        companion.set_memory_cache((SOUL_BLOCK,))
        companion.set_system_prompt("prompt v1")

        # No invalidation
        assert companion.get_cached_system_prompt() == "prompt v1"


# ------------------------------------------------------------------
# Conversation window
# ------------------------------------------------------------------

class TestConversationWindow:
    def test_starts_empty(self) -> None:
        companion = _make_companion()
        assert companion.conversation_window == []

    def test_set_and_get(self) -> None:
        companion = _make_companion()
        msgs = [StoredMessage(role="user", content="hi")]
        companion.set_conversation_window(msgs)
        assert len(companion.conversation_window) == 1

    def test_bounded_on_set(self) -> None:
        companion = _make_companion(keep_last_messages=3)
        msgs = [StoredMessage(
            role="user", content=f"msg {i}") for i in range(10)]
        companion.set_conversation_window(msgs)
        assert len(companion.conversation_window) == 3
        assert companion.conversation_window[0].content == "msg 7"

    def test_append_grows(self) -> None:
        companion = _make_companion(keep_last_messages=50)
        companion.set_conversation_window(
            [StoredMessage(role="user", content="first")])
        companion.append_to_window(
            [StoredMessage(role="assistant", content="reply")])
        assert len(companion.conversation_window) == 2

    def test_append_bounded(self) -> None:
        companion = _make_companion(keep_last_messages=3)
        companion.set_conversation_window([
            StoredMessage(role="user", content="a"),
            StoredMessage(role="assistant", content="b"),
            StoredMessage(role="user", content="c"),
        ])
        companion.append_to_window(
            [StoredMessage(role="assistant", content="d")])
        assert len(companion.conversation_window) == 3
        assert companion.conversation_window[-1].content == "d"
        assert companion.conversation_window[0].content == "b"


# ------------------------------------------------------------------
# Reset
# ------------------------------------------------------------------

class TestReset:
    def test_clears_all_caches(self) -> None:
        companion = _make_companion()
        companion.set_memory_cache((SOUL_BLOCK, PERSONA_BLOCK))
        companion.set_system_prompt("prompt")
        companion.set_conversation_window(
            [StoredMessage(role="user", content="hi")])
        companion.emotional_state = {"emotion": "calm"}
        companion.thread_id = 42

        companion.reset(new_thread_id=99)

        assert companion.get_cached_memory_blocks() is None
        assert companion.memory_stale is True
        assert companion.get_cached_system_prompt() is None
        assert companion.conversation_window == []
        assert companion.emotional_state is None
        assert companion.thread_id == 99

    def test_reset_without_new_thread_id(self) -> None:
        companion = _make_companion()
        companion.thread_id = 42
        companion.reset()
        assert companion.thread_id is None


# ------------------------------------------------------------------
# Singleton management
# ------------------------------------------------------------------

class TestSingletonManagement:
    def setup_method(self) -> None:
        invalidate_companion()

    def teardown_method(self) -> None:
        invalidate_companion()

    def test_get_companion_returns_none_initially(self) -> None:
        assert get_companion() is None

    def test_get_or_build_creates_instance(self) -> None:
        runtime = _make_runtime()
        companion = get_or_build_companion(runtime, user_id=1)
        assert companion is not None
        assert companion.user_id == 1

    def test_get_or_build_returns_same_for_same_user(self) -> None:
        runtime = _make_runtime()
        c1 = get_or_build_companion(runtime, user_id=1)
        c2 = get_or_build_companion(runtime, user_id=1)
        assert c1 is c2

    def test_invalidate_clears_companion(self) -> None:
        runtime = _make_runtime()
        get_or_build_companion(runtime, user_id=1)
        invalidate_companion()
        assert get_companion() is None


# ------------------------------------------------------------------
# ensure_memory_loaded (integration with DB mock)
# ------------------------------------------------------------------

class TestEnsureMemoryLoaded:
    def test_uses_cache_on_second_call(self) -> None:
        companion = _make_companion()
        companion.thread_id = 1

        mock_blocks = (SOUL_BLOCK, PERSONA_BLOCK, FACTS_BLOCK)

        with patch(
            "anima_server.services.agent.companion.build_runtime_memory_blocks",
            return_value=mock_blocks,
        ) as mock_build:
            db = MagicMock(spec=Session)

            # First call: loads from DB
            result1 = companion.ensure_memory_loaded(db)
            assert result1 == mock_blocks
            assert mock_build.call_count == 1

            # Second call: cache hit, no DB query
            result2 = companion.ensure_memory_loaded(db)
            assert result2 == mock_blocks
            assert mock_build.call_count == 1  # still 1

    def test_reloads_after_invalidation(self) -> None:
        companion = _make_companion()
        companion.thread_id = 1

        blocks_v1 = (SOUL_BLOCK,)
        blocks_v2 = (SOUL_BLOCK, FACTS_BLOCK)

        with patch(
            "anima_server.services.agent.companion.build_runtime_memory_blocks",
            side_effect=[blocks_v1, blocks_v2],
        ) as mock_build:
            db = MagicMock(spec=Session)

            result1 = companion.ensure_memory_loaded(db)
            assert result1 == blocks_v1

            companion.invalidate_memory()

            result2 = companion.ensure_memory_loaded(db)
            assert result2 == blocks_v2
            assert mock_build.call_count == 2


# ------------------------------------------------------------------
# Emotional state
# ------------------------------------------------------------------

class TestEmotionalState:
    def test_starts_none(self) -> None:
        companion = _make_companion()
        assert companion.emotional_state is None

    def test_set_and_read(self) -> None:
        companion = _make_companion()
        companion.emotional_state = {"emotion": "curious", "confidence": 0.8}
        assert companion.emotional_state["emotion"] == "curious"

    def test_cleared_on_reset(self) -> None:
        companion = _make_companion()
        companion.emotional_state = {"emotion": "calm"}
        companion.reset()
        assert companion.emotional_state is None
