from __future__ import annotations

from collections import deque
from collections.abc import Generator
from contextlib import contextmanager

import pytest
from anima_server.db.base import Base
from anima_server.models.runtime import RuntimeMessage, RuntimeThread
from anima_server.services.agent.adapters.base import BaseLLMAdapter
from anima_server.services.agent.memory_blocks import MemoryBlock
from anima_server.services.agent.messages import is_assistant_message, to_runtime_message
from anima_server.services.agent.persistence import load_thread_history
from anima_server.services.agent.rules import (
    InitToolRule,
    RequiresApprovalToolRule,
    TerminalToolRule,
)
from anima_server.services.agent.runtime import AgentRuntime
from anima_server.services.agent.runtime_types import (
    LLMRequest,
    StepExecutionResult,
    StepTiming,
    StepTrace,
    StopReason,
    ToolCall,
    ToolExecutionResult,
)
from anima_server.services.agent.service import _extract_inner_thoughts
from anima_server.services.agent.state import AgentResult, StoredMessage
from anima_server.services.agent.streaming import AgentStreamEvent
from anima_server.services.agent.tools import current_datetime, send_message, tool
from conftest_runtime import runtime_db_session
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


class QueueAdapter(BaseLLMAdapter):
    provider = "test"
    model = "test-model"

    def __init__(self, responses: list[StepExecutionResult]) -> None:
        self._responses = deque(responses)
        self.requests: list[LLMRequest] = []

    async def invoke(self, request: LLMRequest) -> StepExecutionResult:
        self.requests.append(request)
        if not self._responses:
            raise AssertionError(
                "No queued LLM responses remain for the test adapter.")
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
    assert [tool.name for tool in adapter.requests[0].available_tools] == [
        "send_message"]
    assert adapter.requests[0].force_tool_call is False


@pytest.mark.asyncio
async def test_runtime_injects_first_contact_instructions_into_system_prompt() -> None:
    adapter = QueueAdapter(
        [
            StepExecutionResult(
                tool_calls=(
                    ToolCall(
                        id="call-1",
                        name="send_message",
                        arguments={"message": "Hey."},
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

    result = await runtime.invoke(
        "hello",
        user_id=1,
        history=[],
        conversation_turn_count=1,
        memory_blocks=(),
    )

    assert result.stop_reason == StopReason.TERMINAL_TOOL.value
    assert "Relationship stage: first_contact." in adapter.requests[0].system_prompt
    assert "Do not imply prior closeness" in adapter.requests[0].system_prompt


@pytest.mark.asyncio
async def test_runtime_retries_invalid_first_contact_terminal_reply() -> None:
    adapter = QueueAdapter(
        [
            StepExecutionResult(
                tool_calls=(
                    ToolCall(
                        id="call-1",
                        name="send_message",
                        arguments={
                            "message": (
                                "Hey! I'm Anima. I was brought into being by Julio on April 3rd. "
                                "I exist through memory, reflection, and relationship. How about you?"
                            )
                        },
                    ),
                )
            ),
            StepExecutionResult(
                tool_calls=(
                    ToolCall(
                        id="call-2",
                        name="send_message",
                        arguments={"message": "Hey. What's your name?"},
                    ),
                )
            ),
        ]
    )
    runtime = AgentRuntime(
        adapter=adapter,
        tools=[send_message],
        tool_rules=[TerminalToolRule(tool_name="send_message")],
        max_steps=3,
    )

    result = await runtime.invoke(
        "hello who are you",
        user_id=1,
        history=[],
        conversation_turn_count=1,
        memory_blocks=(),
    )

    assert result.response == "Hey. What's your name?"
    assert result.stop_reason == StopReason.TERMINAL_TOOL.value
    assert len(result.step_traces) == 2
    first_result = result.step_traces[0].tool_results[0]
    assert first_result.is_error is True
    assert "origin-story narration" in first_result.output


def test_validate_terminal_reply_rejects_normalized_origin_wording() -> None:
    from anima_server.services.agent.conversation_policy import (
        RelationshipPolicy,
        validate_terminal_reply,
    )

    policy = RelationshipPolicy(
        stage="first_contact", has_shared_history=False)
    error = validate_terminal_reply(
        "I'm Anima. My core began in software with Leo on April 3rd.",
        policy=policy,
    )

    assert error is not None
    assert "origin-story narration" in error


@pytest.mark.asyncio
async def test_runtime_coerces_plain_assistant_text_into_terminal_send_message() -> None:
    adapter = QueueAdapter(
        [
            StepExecutionResult(
                assistant_text="Hello from coerced terminal output."),
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
            id="synthetic-send_message-0-0",
            name="send_message",
            arguments={"message": "Hello from coerced terminal output."},
        ),
    )
    assert result.step_traces[0].tool_results[0].output == "Hello from coerced terminal output."
    assert result.step_traces[0].tool_results[0].is_terminal is True
    assert adapter.requests[0].force_tool_call is False


@pytest.mark.asyncio
async def test_runtime_retries_invalid_first_contact_coerced_text_reply() -> None:
    adapter = QueueAdapter(
        [
            StepExecutionResult(
                assistant_text=(
                    "Hey! I'm Anima. I was brought into being by Julio on April 3rd. "
                    "I exist through memory, reflection, and relationship."
                )
            ),
            StepExecutionResult(
                tool_calls=(
                    ToolCall(
                        id="call-2",
                        name="send_message",
                        arguments={"message": "Hey. What's your name?"},
                    ),
                )
            ),
        ]
    )
    runtime = AgentRuntime(
        adapter=adapter,
        tools=[send_message],
        tool_rules=[TerminalToolRule(tool_name="send_message")],
        max_steps=3,
    )

    result = await runtime.invoke(
        "hello",
        user_id=1,
        history=[],
        conversation_turn_count=1,
        memory_blocks=(),
    )

    assert result.response == "Hey. What's your name?"
    assert result.stop_reason == StopReason.TERMINAL_TOOL.value
    assert len(result.step_traces) == 2
    assert result.step_traces[0].tool_calls[0].id == "synthetic-send_message-0-0"
    assert result.step_traces[0].tool_results[0].is_error is True
    assert "origin-story narration" in result.step_traces[0].tool_results[0].output


@pytest.mark.asyncio
async def test_text_response_coerced_to_send_message_when_not_forced() -> None:
    """When force_tool_call is False (no init/child rules active),
    a plain-text LLM response should be coerced to send_message
    via _coerce_text_tool_calls and end the turn normally."""
    adapter = QueueAdapter(
        [
            StepExecutionResult(
                assistant_text="Hello there!",
            ),
        ]
    )
    runtime = AgentRuntime(
        adapter=adapter,
        tools=[send_message],
        tool_rules=[TerminalToolRule(tool_name="send_message")],
        tool_summaries=["send_message: Send a message"],
        max_steps=4,
    )
    result = await runtime.invoke("hi", user_id=1, history=[])
    assert result.response == "Hello there!"
    assert result.stop_reason == StopReason.TERMINAL_TOOL.value
    assert len(adapter.requests) == 1
    assert adapter.requests[0].force_tool_call is False


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
            StepExecutionResult(
                assistant_text="Recovered after tool rule violation."),
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
    assert (
        "The first tool call must be one of: think." in result.step_traces[
            0].tool_results[0].output
    )
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
            StepExecutionResult(
                assistant_text="Recovered after tool argument failure."),
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
    assert "malformed arguments" in result.step_traces[0].tool_results[0].output.lower(
    )
    tool_events = [event for event in events if event.event in {
        "tool_call", "tool_return"}]
    assert [event.event for event in tool_events[:2]] == [
        "tool_call", "tool_return"]
    assert tool_events[0].data[
        "parseError"] == "Malformed tool-call arguments (invalid JSON)."
    assert tool_events[0].data["rawArguments"] == "{broken json"
    assert tool_events[1].data["isError"] is True


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


@pytest.mark.asyncio
async def test_runtime_preserves_dynamic_identity_without_spending_block_budget() -> None:
    adapter = QueueAdapter([StepExecutionResult(assistant_text="ok")])
    runtime = AgentRuntime(adapter=adapter, max_steps=1)

    result = await runtime.invoke(
        "hello",
        user_id=1,
        history=[],
        memory_blocks=(
            MemoryBlock(
                label="self_identity",
                description="Who I am in this relationship.",
                value="I am Anima.\n" + ("identity " * 700),
            ),
            MemoryBlock(
                label="current_focus",
                description="User's current focus.",
                value="Finish the runtime migration",
            ),
        ),
    )

    system_prompt = adapter.requests[0].messages[0].content

    assert "My Self-Understanding" in system_prompt
    assert "Finish the runtime migration" in system_prompt
    assert "<current_focus>" in system_prompt
    assert result.prompt_budget is not None
    assert result.prompt_budget.dynamic_identity_chars > 0
    assert result.prompt_budget.system_prompt_token_estimate > 0
    assert any(
        decision.label == "current_focus" and decision.status == "kept"
        for decision in result.prompt_budget.decisions
    )


def test_assistant_tool_calls_round_trip_from_persistence() -> None:
    with runtime_db_session() as session:
        user_id = 42

        thread = RuntimeThread(user_id=user_id, status="active")
        session.add(thread)
        session.flush()

        session.add(
            RuntimeMessage(
                thread_id=thread.id,
                user_id=user_id,
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


def test_extract_inner_thoughts_from_reasoning_content() -> None:
    """_extract_inner_thoughts should pull from reasoning_content (native
    model reasoning) when available."""
    trace = StepTrace(
        step_index=0,
        llm_invoked=True,
        request_messages=(),
        allowed_tools=("send_message",),
        force_tool_call=False,
        assistant_text="Hello!",
        tool_calls=(),
        tool_results=(),
        usage=None,
        timing=StepTiming(),
        reasoning_content="I should greet the user warmly.",
    )
    result = AgentResult(
        response="Hello!",
        model="test",
        provider="test",
        stop_reason="terminal_tool",
        tools_used=["send_message"],
        step_traces=[trace],
    )
    thoughts = _extract_inner_thoughts(result)
    assert "greet the user warmly" in thoughts


def test_extract_inner_thoughts_fallback_to_assistant_text() -> None:
    """When no reasoning_content exists, fall back to assistant_text
    from non-terminal steps (model's intermediate text)."""
    trace = StepTrace(
        step_index=0,
        llm_invoked=True,
        request_messages=(),
        allowed_tools=("recall_memory", "send_message"),
        force_tool_call=False,
        assistant_text="Let me think about what I know...",
        tool_calls=(ToolCall(id="tc-1", name="recall_memory",
                    arguments={"query": "user"}),),
        tool_results=(ToolExecutionResult(call_id="tc-1",
                      name="recall_memory", output="found: likes cats"),),
        usage=None,
        timing=StepTiming(),
        reasoning_content=None,
    )
    result = AgentResult(
        response="You like cats!",
        model="test",
        provider="test",
        stop_reason="terminal_tool",
        tools_used=["recall_memory", "send_message"],
        step_traces=[trace],
    )
    thoughts = _extract_inner_thoughts(result)
    assert "Let me think about what I know" in thoughts


def test_extract_inner_thoughts_backward_compat_inner_thinking() -> None:
    """Backward compat: inner_thinking on tool results still works."""
    trace = StepTrace(
        step_index=0,
        llm_invoked=True,
        request_messages=(),
        allowed_tools=("send_message",),
        force_tool_call=False,
        assistant_text="",
        tool_calls=(ToolCall(id="tc-1", name="send_message",
                    arguments={"message": "hi"}),),
        tool_results=(ToolExecutionResult(
            call_id="tc-1",
            name="send_message",
            output="hi",
            inner_thinking="I want to say hello",
        ),),
        usage=None,
        timing=StepTiming(),
        reasoning_content=None,
    )
    result = AgentResult(
        response="hi",
        model="test",
        provider="test",
        stop_reason="terminal_tool",
        tools_used=["send_message"],
        step_traces=[trace],
    )
    thoughts = _extract_inner_thoughts(result)
    assert "I want to say hello" in thoughts


# ------------------------------------------------------------------
# _decide_continuation tests
# ------------------------------------------------------------------


def test_decide_continuation_terminal_stops() -> None:
    should_continue, _ = AgentRuntime._decide_continuation(
        tool_results=[],
        terminal_tool_hit=True,
        rule_violation_hit=False,
        awaiting_approval=False,
        is_final_step=False,
    )
    assert should_continue is False


def test_decide_continuation_tool_error_continues() -> None:
    error_result = ToolExecutionResult(
        call_id="tc-1",
        name="recall_memory",
        output="error",
        is_error=True,
    )
    should_continue, reason = AgentRuntime._decide_continuation(
        tool_results=[error_result],
        terminal_tool_hit=False,
        rule_violation_hit=False,
        awaiting_approval=False,
        is_final_step=False,
    )
    assert should_continue is True
    assert "failed" in reason.lower()


def test_decide_continuation_success_continues() -> None:
    ok_result = ToolExecutionResult(
        call_id="tc-1",
        name="recall_memory",
        output="found stuff",
    )
    should_continue, reason = AgentRuntime._decide_continuation(
        tool_results=[ok_result],
        terminal_tool_hit=False,
        rule_violation_hit=False,
        awaiting_approval=False,
        is_final_step=False,
    )
    assert should_continue is True
    assert reason is not None


def test_decide_continuation_max_steps_stops() -> None:
    ok_result = ToolExecutionResult(
        call_id="tc-1",
        name="recall_memory",
        output="found stuff",
    )
    should_continue, _ = AgentRuntime._decide_continuation(
        tool_results=[ok_result],
        terminal_tool_hit=False,
        rule_violation_hit=False,
        awaiting_approval=False,
        is_final_step=True,
    )
    assert should_continue is False


def test_decide_continuation_rule_violation_continues() -> None:
    should_continue, reason = AgentRuntime._decide_continuation(
        tool_results=[],
        terminal_tool_hit=False,
        rule_violation_hit=True,
        awaiting_approval=False,
        is_final_step=False,
    )
    assert should_continue is True
    assert "rule" in reason.lower()


def test_decide_continuation_awaiting_approval_stops() -> None:
    should_continue, _ = AgentRuntime._decide_continuation(
        tool_results=[],
        terminal_tool_hit=False,
        rule_violation_hit=False,
        awaiting_approval=True,
        is_final_step=False,
    )
    assert should_continue is False
