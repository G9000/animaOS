from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Generator
from contextlib import contextmanager

import pytest
from anima_server.services.agent.tools import tool
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from anima_server.db.base import Base
from anima_server.models import AgentMessage, AgentThread, User
from anima_server.services.agent.adapters.base import BaseLLMAdapter
from anima_server.services.agent.memory_blocks import MemoryBlock
from anima_server.services.agent.messages import is_assistant_message, to_runtime_message
from anima_server.services.agent.persistence import load_thread_history
from anima_server.services.agent.rules import InitToolRule, RequiresApprovalToolRule, TerminalToolRule
from anima_server.services.agent.runtime import AgentRuntime
from anima_server.services.agent.runtime_types import LLMRequest, StepExecutionResult, StopReason, ToolCall
from anima_server.services.agent.state import StoredMessage
from anima_server.services.agent.tools import current_datetime, send_message
from anima_server.services.agent.streaming import AgentStreamEvent


class QueueAdapter(BaseLLMAdapter):
    provider = "test"
    model = "test-model"

    def __init__(self, responses: list[StepExecutionResult]) -> None:
        self._responses = deque(responses)
        self.requests: list[LLMRequest] = []

    async def invoke(self, request: LLMRequest) -> StepExecutionResult:
        self.requests.append(request)
        if not self._responses:
            raise AssertionError("No queued LLM responses remain for the test adapter.")
        return self._responses.popleft()


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
async def test_runtime_uses_terminal_send_message_tool_output() -> None:
    adapter = QueueAdapter(
        [
            StepExecutionResult(
                tool_calls=(
                    ToolCall(
                        id="call-1",
                        name="send_message",
                        arguments={"message": "Hello from the terminal tool."},
                    ),
                )
            )
        ]
    )
    runtime = AgentRuntime(
        adapter=adapter,
        tools=[send_message],
        tool_rules=[TerminalToolRule(tool_name="send_message")],
        max_steps=2,
    )

    result = await runtime.invoke("hello", user_id=1, history=[])

    assert result.response == "Hello from the terminal tool."
    assert result.stop_reason == StopReason.TERMINAL_TOOL.value
    assert result.tools_used == ["send_message"]
    assert len(result.step_traces) == 1
    assert result.step_traces[0].tool_results[0].is_terminal is True
    assert [tool.name for tool in adapter.requests[0].available_tools] == ["send_message"]
    assert adapter.requests[0].force_tool_call is True


@pytest.mark.asyncio
async def test_runtime_coerces_plain_assistant_text_into_terminal_send_message() -> None:
    adapter = QueueAdapter(
        [
            StepExecutionResult(assistant_text="Hello from coerced terminal output."),
        ]
    )
    runtime = AgentRuntime(
        adapter=adapter,
        tools=[send_message],
        tool_rules=[TerminalToolRule(tool_name="send_message")],
        max_steps=2,
    )

    result = await runtime.invoke("hello", user_id=1, history=[])

    assert result.response == "Hello from coerced terminal output."
    assert result.stop_reason == StopReason.TERMINAL_TOOL.value
    assert result.tools_used == ["send_message"]
    assert len(result.step_traces) == 1
    assert result.step_traces[0].tool_calls == (
        ToolCall(
            id="synthetic-send-message-0",
            name="send_message",
            arguments={"message": "Hello from coerced terminal output."},
        ),
    )
    assert result.step_traces[0].tool_results[0].output == "Hello from coerced terminal output."
    assert result.step_traces[0].tool_results[0].is_terminal is True
    assert adapter.requests[0].force_tool_call is True


@pytest.mark.asyncio
async def test_runtime_returns_rule_violation_to_next_step() -> None:
    @tool
    def think() -> str:
        """Record an internal planning step."""
        return "planned"

    adapter = QueueAdapter(
        [
            StepExecutionResult(
                tool_calls=(
                    ToolCall(
                        id="call-1",
                        name="current_datetime",
                        arguments={},
                    ),
                )
            ),
            StepExecutionResult(assistant_text="Recovered after tool rule violation."),
        ]
    )
    runtime = AgentRuntime(
        adapter=adapter,
        tools=[think, current_datetime],
        tool_rules=[InitToolRule(tool_name="think")],
        max_steps=3,
    )

    result = await runtime.invoke("start", user_id=1, history=[])

    assert result.response == "Recovered after tool rule violation."
    assert result.stop_reason == StopReason.END_TURN.value
    assert result.tools_used == []
    assert len(result.step_traces) == 2
    assert result.step_traces[0].tool_results[0].is_error is True
    assert "The first tool call must be one of: think." in result.step_traces[0].tool_results[0].output
    assert [tool.name for tool in adapter.requests[0].available_tools] == ["think"]
    assert adapter.requests[0].force_tool_call is True
    assert [tool.name for tool in adapter.requests[1].available_tools] == ["think"]
    assert adapter.requests[1].force_tool_call is True


@pytest.mark.asyncio
async def test_runtime_stops_before_executing_approval_required_tool() -> None:
    calls: list[str] = []

    @tool
    def delete_file(path: str) -> str:
        """Delete a file from disk."""
        calls.append(path)
        return f"deleted {path}"

    adapter = QueueAdapter(
        [
            StepExecutionResult(
                tool_calls=(
                    ToolCall(
                        id="call-1",
                        name="delete_file",
                        arguments={"path": "C:/tmp/demo.txt"},
                    ),
                )
            )
        ]
    )
    runtime = AgentRuntime(
        adapter=adapter,
        tools=[delete_file],
        tool_rules=[RequiresApprovalToolRule(tool_name="delete_file")],
        max_steps=2,
    )

    result = await runtime.invoke("delete it", user_id=1, history=[])

    assert calls == []
    assert result.response == "Agent runtime is waiting for approval before running a tool."
    assert result.stop_reason == StopReason.AWAITING_APPROVAL.value
    assert result.tools_used == []
    assert result.step_traces[0].tool_results[0].output == (
        "Approval required before running tool: delete_file"
    )


@pytest.mark.asyncio
async def test_runtime_surfaces_malformed_tool_args_as_explicit_step_error() -> None:
    calls: list[str] = []
    events: list[AgentStreamEvent] = []

    @tool
    def remember(note: str = "default") -> str:
        """Store a note."""
        calls.append(note)
        return note

    adapter = QueueAdapter(
        [
            StepExecutionResult(
                tool_calls=(
                    ToolCall(
                        id="call-bad",
                        name="remember",
                        arguments={},
                        parse_error="Malformed tool-call arguments (invalid JSON).",
                        raw_arguments="{broken json",
                    ),
                )
            ),
            StepExecutionResult(assistant_text="Recovered after tool argument failure."),
        ]
    )
    runtime = AgentRuntime(
        adapter=adapter,
        tools=[remember, send_message],
        max_steps=3,
    )

    async def collect(event: AgentStreamEvent) -> None:
        events.append(event)

    result = await runtime.invoke(
        "start",
        user_id=1,
        history=[],
        event_callback=collect,
    )

    assert calls == []
    assert result.response == "Recovered after tool argument failure."
    assert len(result.step_traces) == 2
    assert result.step_traces[0].tool_calls[0].parse_error == (
        "Malformed tool-call arguments (invalid JSON)."
    )
    assert result.step_traces[0].tool_results[0].is_error is True
    assert "malformed arguments" in result.step_traces[0].tool_results[0].output.lower()
    assert [event.event for event in events[:2]] == ["tool_call", "tool_return"]
    assert events[0].data["parseError"] == "Malformed tool-call arguments (invalid JSON)."
    assert events[0].data["rawArguments"] == "{broken json"
    assert events[1].data["isError"] is True


@pytest.mark.asyncio
async def test_runtime_renders_memory_blocks_into_system_prompt() -> None:
    adapter = QueueAdapter([StepExecutionResult(assistant_text="ok")])
    runtime = AgentRuntime(adapter=adapter, max_steps=1)

    await runtime.invoke(
        "hello",
        user_id=1,
        history=[],
        memory_blocks=(
            MemoryBlock(
                label="human",
                description="Stable facts about the user for this thread.",
                value="Display name: Alice",
            ),
            MemoryBlock(
                label="current_focus",
                description="User-declared current focus from local memory.",
                value="# Current Focus\n\n- [ ] Finish the runtime migration",
            ),
            MemoryBlock(
                label="thread_summary",
                description="Compressed summary of earlier conversation context.",
                value="Conversation summary:\n- User likes green tea.",
            ),
        ),
    )

    system_prompt = adapter.requests[0].messages[0].content

    assert "Memory Blocks:" in system_prompt
    assert "<human>" in system_prompt
    assert "Display name: Alice" in system_prompt
    assert "<current_focus>" in system_prompt
    assert "Finish the runtime migration" in system_prompt
    assert "<thread_summary>" in system_prompt
    assert "User likes green tea." in system_prompt


def test_assistant_tool_calls_round_trip_from_persistence() -> None:
    with _db_session() as session:
        user = User(
            username="tool-history",
            password_hash="not-used",
            display_name="Tool History",
        )
        session.add(user)
        session.flush()

        thread = AgentThread(user_id=user.id, status="active")
        session.add(thread)
        session.flush()

        session.add(
            AgentMessage(
                thread_id=thread.id,
                sequence_id=1,
                role="assistant",
                content_text="",
                content_json={
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "name": "send_message",
                            "arguments": {"message": "hello"},
                        }
                    ]
                },
                is_in_context=True,
            )
        )
        session.commit()

        history = load_thread_history(session, thread.id)

    assert history == [
        StoredMessage(
            role="assistant",
            content="",
            tool_calls=(
                ToolCall(
                    id="call-1",
                    name="send_message",
                    arguments={"message": "hello"},
                ),
            ),
        )
    ]

    message = to_runtime_message(history[0])

    assert is_assistant_message(message) is True
    assert message.tool_calls == [
        {
            "id": "call-1",
            "name": "send_message",
            "args": {"message": "hello"},
            "type": "tool_call",
        }
    ]
