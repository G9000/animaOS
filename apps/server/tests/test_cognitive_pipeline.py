"""Integration tests for the full cognitive pipeline:
act (with thinking) → respond, with memory persistence and thinking extraction."""

from __future__ import annotations

from collections import deque
from collections.abc import Generator
from contextlib import contextmanager

import pytest
from anima_server.db.base import Base
from anima_server.models import SelfModelBlock, User
from anima_server.services.agent.adapters.base import BaseLLMAdapter
from anima_server.services.agent.executor import ToolExecutor
from anima_server.services.agent.memory_blocks import MemoryBlock
from anima_server.services.agent.rules import TerminalToolRule
from anima_server.services.agent.runtime import AgentRuntime
from anima_server.services.agent.runtime_types import (
    LLMRequest,
    StepExecutionResult,
    StopReason,
    ToolCall,
    ToolExecutionResult,
)
from anima_server.services.agent.state import AgentResult
from anima_server.services.agent.tool_context import (
    ToolContext,
    clear_tool_context,
    set_tool_context,
)
from anima_server.services.agent.tools import (
    core_memory_append,
    core_memory_replace,
    inject_inner_thoughts_into_tools,
    send_message,
)
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class QueueAdapter(BaseLLMAdapter):
    provider = "test"
    model = "test-model"

    def __init__(self, responses: list[StepExecutionResult]) -> None:
        self._responses = deque(responses)
        self.requests: list[LLMRequest] = []

    async def invoke(self, request: LLMRequest) -> StepExecutionResult:
        self.requests.append(request)
        if not self._responses:
            raise AssertionError("No queued responses.")
        return self._responses.popleft()


@contextmanager
def _db_session() -> Generator[Session, None, None]:
    engine = create_engine(
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


# ---------------------------------------------------------------------------
# Integration: core_memory_append → send_message (with thinking kwarg)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_append_memory_respond_pipeline() -> None:
    """Full pipeline: core_memory_append (with thinking) → send_message.
    Verifies memory is actually written to DB and visible in the next step."""

    with _db_session() as db:
        user = User(username="pipeline", password_hash="x", display_name="Test")
        db.add(user)
        db.flush()

        # Seed a human memory block
        db.add(
            SelfModelBlock(
                user_id=user.id,
                section="human",
                content="Name: Alice",
                version=1,
                updated_by="seed",
            )
        )
        db.commit()

        set_tool_context(ToolContext(db=db, user_id=user.id, thread_id=1))
        try:
            # Step 1: core_memory_append (with thinking)
            # Step 2: send_message (with thinking)
            adapter = QueueAdapter(
                [
                    StepExecutionResult(
                        tool_calls=(
                            ToolCall(
                                id="c1",
                                name="core_memory_append",
                                arguments={
                                    "thinking": "User mentioned they have a dog named Biscuit.",
                                    "label": "human",
                                    "content": "Has a dog named Biscuit.",
                                    "request_heartbeat": True,
                                },
                            ),
                        )
                    ),
                    StepExecutionResult(
                        tool_calls=(
                            ToolCall(
                                id="c2",
                                name="send_message",
                                arguments={
                                    "thinking": "Should acknowledge the dog warmly.",
                                    "message": "Biscuit sounds adorable!",
                                },
                            ),
                        )
                    ),
                ]
            )

            # Custom executor that propagates memory_modified from real tools
            class RealToolExecutor(ToolExecutor):
                async def execute(self, tool_call, *, is_terminal=False):
                    result = await super().execute(tool_call, is_terminal=is_terminal)
                    return result

            tools = [core_memory_append, send_message]
            inject_inner_thoughts_into_tools(tools)

            runtime = AgentRuntime(
                adapter=adapter,
                tools=tools,
                tool_rules=[
                    TerminalToolRule(tool_name="send_message"),
                ],
                tool_executor=RealToolExecutor(tools),
                max_steps=5,
            )

            # Memory refresher that reads from DB
            async def refresher():
                block = db.scalar(
                    select(SelfModelBlock).where(
                        SelfModelBlock.user_id == user.id,
                        SelfModelBlock.section == "human",
                    )
                )
                if block is None:
                    return None
                return (
                    MemoryBlock(
                        label="human",
                        value=block.content,
                        description="User understanding",
                    ),
                )

            result = await runtime.invoke(
                "I just got a rescue dog named Biscuit!",
                user_id=user.id,
                history=[],
                memory_blocks=(
                    MemoryBlock(
                        label="human",
                        value="Name: Alice",
                        description="User understanding",
                    ),
                ),
                memory_refresher=refresher,
            )

            # Verify the response
            assert result.response == "Biscuit sounds adorable!"
            assert result.stop_reason == StopReason.TERMINAL_TOOL.value
            assert len(result.step_traces) == 2

            # Verify the memory was actually persisted to DB
            block = db.scalar(
                select(SelfModelBlock).where(
                    SelfModelBlock.user_id == user.id,
                    SelfModelBlock.section == "human",
                )
            )
            assert block is not None
            assert "Biscuit" in block.content
            assert "Alice" in block.content  # original content preserved

        finally:
            clear_tool_context()


@pytest.mark.asyncio
async def test_core_memory_replace_pipeline() -> None:
    """Pipeline: core_memory_replace (with thinking) → send_message."""

    with _db_session() as db:
        user = User(username="replace", password_hash="x", display_name="Test")
        db.add(user)
        db.flush()

        db.add(
            SelfModelBlock(
                user_id=user.id,
                section="human",
                content="Works at Google",
                version=1,
                updated_by="seed",
            )
        )
        db.commit()

        set_tool_context(ToolContext(db=db, user_id=user.id, thread_id=1))
        try:
            adapter = QueueAdapter(
                [
                    StepExecutionResult(
                        tool_calls=(
                            ToolCall(
                                id="c1",
                                name="core_memory_replace",
                                arguments={
                                    "thinking": "User switched jobs to Apple.",
                                    "label": "human",
                                    "old_text": "Works at Google",
                                    "new_text": "Works at Apple (switched March 2026)",
                                    "request_heartbeat": True,
                                },
                            ),
                        )
                    ),
                    StepExecutionResult(
                        tool_calls=(
                            ToolCall(
                                id="c2",
                                name="send_message",
                                arguments={
                                    "thinking": "Congratulate on the new role.",
                                    "message": "Congrats on the new role at Apple!",
                                },
                            ),
                        )
                    ),
                ]
            )

            tools = [core_memory_replace, send_message]
            inject_inner_thoughts_into_tools(tools)

            runtime = AgentRuntime(
                adapter=adapter,
                tools=tools,
                tool_rules=[
                    TerminalToolRule(tool_name="send_message"),
                ],
                max_steps=5,
            )

            result = await runtime.invoke(
                "I just started at Apple!",
                user_id=user.id,
                history=[],
            )

            assert result.response == "Congrats on the new role at Apple!"

            block = db.scalar(
                select(SelfModelBlock).where(
                    SelfModelBlock.user_id == user.id,
                    SelfModelBlock.section == "human",
                )
            )
            assert "Apple" in block.content
            assert "Google" not in block.content

        finally:
            clear_tool_context()


# ---------------------------------------------------------------------------
# Inner thought extraction for consolidation
# ---------------------------------------------------------------------------


def test_extract_inner_thoughts_from_traces() -> None:
    """_extract_inner_thoughts pulls thinking content from tool result inner_thinking."""
    from anima_server.services.agent.runtime_types import StepTrace
    from anima_server.services.agent.service import _extract_inner_thoughts

    result = AgentResult(
        response="Hello!",
        model="test",
        provider="test",
        stop_reason="terminal_tool",
        tools_used=["send_message"],
        step_traces=[
            StepTrace(
                step_index=0,
                tool_calls=(
                    ToolCall(
                        id="c1",
                        name="send_message",
                        arguments={"message": "Hey!"},
                    ),
                ),
                tool_results=(
                    ToolExecutionResult(
                        call_id="c1",
                        name="send_message",
                        output="Hey!",
                        inner_thinking="User seems happy today.",
                    ),
                ),
            ),
        ],
    )

    thoughts = _extract_inner_thoughts(result)
    assert "User seems happy today." in thoughts


def test_extract_inner_thoughts_empty_when_no_thoughts() -> None:
    """No thinking content means empty string."""
    from anima_server.services.agent.runtime_types import StepTrace
    from anima_server.services.agent.service import _extract_inner_thoughts

    result = AgentResult(
        response="Hi",
        model="test",
        provider="test",
        stop_reason="end_turn",
        tools_used=[],
        step_traces=[
            StepTrace(step_index=0, assistant_text="Hi"),
        ],
    )

    assert _extract_inner_thoughts(result) == ""


# ---------------------------------------------------------------------------
# Deep monologue persona evolution
# ---------------------------------------------------------------------------


def test_deep_monologue_result_tracks_persona() -> None:
    """DeepMonologueResult has a persona_updated field."""
    from anima_server.services.agent.inner_monologue import DeepMonologueResult

    r = DeepMonologueResult()
    assert r.persona_updated is False
    r.persona_updated = True
    assert r.persona_updated is True


def test_deep_monologue_prompt_includes_persona() -> None:
    """The deep monologue prompt template includes a persona section."""
    from pathlib import Path

    template_path = (
        Path(__file__).parent.parent
        / "src/anima_server/services/agent/templates/prompts/deep_monologue.md.j2"
    )
    content = template_path.read_text(encoding="utf-8")

    assert "{{ persona }}" in content
    assert "persona_update" in content
    assert "EVOLVE" in content
