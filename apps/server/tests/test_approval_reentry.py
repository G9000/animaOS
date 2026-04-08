"""Tests for Phase 3: Approval Re-Entry.

Covers:
- ``resume_after_approval`` on approve (terminal + non-terminal tools)
- ``resume_after_approval`` on deny
- Double-approve returns error (via persistence layer)
- Approval checkpoint persistence and loading
- Cancel-during-approval clears pending state
- Approval message excluded from LLM context
"""

from __future__ import annotations

from collections import deque
from collections.abc import Generator
from contextlib import contextmanager

import pytest
from anima_server.db.base import Base
from anima_server.models.runtime import RuntimeMessage, RuntimeRun, RuntimeThread
from anima_server.services.agent import persistence as persistence_module
from anima_server.services.agent import service as agent_service
from anima_server.services.agent.adapters.base import BaseLLMAdapter
from anima_server.services.agent.persistence import (
    append_message,
    cancel_run,
    clear_approval_checkpoint,
    create_run,
    get_or_create_thread,
    load_approval_checkpoint,
    load_thread_history,
    save_approval_checkpoint,
)
from anima_server.services.agent.rules import RequiresApprovalToolRule, TerminalToolRule
from anima_server.services.agent.runtime import AgentRuntime
from anima_server.services.agent.runtime_types import (
    LLMRequest,
    StepExecutionResult,
    StepTrace,
    StopReason,
    ToolCall,
    ToolExecutionResult,
)
from anima_server.services.agent.sequencing import reserve_message_sequences
from anima_server.services.agent.state import (
    AgentCitation,
    AgentContextFragment,
    AgentResult,
    AgentRetrievalStats,
    AgentRetrievalTrace,
)
from anima_server.services.agent.streaming import build_approval_pending_event
from anima_server.services.agent.tools import send_message, tool
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
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
            raise AssertionError("No queued LLM responses remain.")
        return self._responses.popleft()


@contextmanager
def _db_session() -> Generator[Session, None, None]:
    """Combined session with both RuntimeBase and Base tables."""
    from anima_server.db.runtime_base import RuntimeBase
    from anima_server.models import runtime as _rm  # noqa: F401

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
    RuntimeBase.metadata.create_all(bind=engine)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()

_TEST_USER_COUNTER = 0


class _FakeUser:
    def __init__(self) -> None:
        global _TEST_USER_COUNTER
        _TEST_USER_COUNTER += 1
        self.id = _TEST_USER_COUNTER


def _setup_thread_and_run(db: Session) -> tuple[RuntimeThread, RuntimeRun, _FakeUser]:
    user = _FakeUser()
    thread = get_or_create_thread(db, user.id)
    run = create_run(
        db,
        thread_id=thread.id,
        user_id=user.id,
        provider="test",
        model="test-model",
        mode="blocking",
    )
    db.flush()
    return thread, run, user


# ---------------------------------------------------------------------------
# runtime.resume_after_approval — approve terminal tool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_terminal_tool_no_llm_call() -> None:
    """Approving a terminal tool should execute it with zero LLM calls."""
    adapter = QueueAdapter([])  # empty — no LLM call expected

    @tool
    def delete_file(path: str) -> str:
        """Delete a file from disk."""
        return f"deleted {path}"

    runtime = AgentRuntime(
        adapter=adapter,
        tools=[delete_file],
        tool_rules=[
            RequiresApprovalToolRule(tool_name="delete_file"),
            TerminalToolRule(tool_name="delete_file"),
        ],
        max_steps=2,
    )

    pending_call = ToolCall(
        id="call-1",
        name="delete_file",
        arguments={"path": "/tmp/data.txt"},
    )

    result = await runtime.resume_after_approval(
        approved=True,
        tool_call=pending_call,
        user_id=1,
        history=[],
    )

    assert result.stop_reason == StopReason.TERMINAL_TOOL.value
    assert result.response == "deleted /tmp/data.txt"
    assert result.tools_used == ["delete_file"]
    assert len(adapter.requests) == 0  # zero LLM calls


# ---------------------------------------------------------------------------
# runtime.resume_after_approval — approve non-terminal tool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_non_terminal_tool_makes_one_llm_call() -> None:
    """Approving a non-terminal tool should execute it, then make one LLM call."""
    adapter = QueueAdapter(
        [
            StepExecutionResult(
                assistant_text="I've executed the tool for you.",
                tool_calls=(
                    ToolCall(
                        id="call-resp",
                        name="send_message",
                        arguments={"message": "I've executed the tool for you."},
                    ),
                ),
            ),
        ]
    )

    @tool
    def search_memory(query: str) -> str:
        """Search memory for relevant content."""
        return f"Found results for: {query}"

    runtime = AgentRuntime(
        adapter=adapter,
        tools=[search_memory, send_message],
        tool_rules=[
            RequiresApprovalToolRule(tool_name="search_memory"),
            TerminalToolRule(tool_name="send_message"),
        ],
        max_steps=4,
    )

    pending_call = ToolCall(
        id="call-1",
        name="search_memory",
        arguments={"query": "user preferences"},
    )

    result = await runtime.resume_after_approval(
        approved=True,
        tool_call=pending_call,
        user_id=1,
        history=[],
    )

    assert result.tools_used == ["search_memory"]
    assert len(adapter.requests) == 1  # exactly one LLM call


# ---------------------------------------------------------------------------
# runtime.resume_after_approval — deny
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deny_produces_companion_response() -> None:
    """Denying a tool should inject error and produce an LLM response."""
    adapter = QueueAdapter(
        [
            StepExecutionResult(
                assistant_text="I understand you don't want me to do that.",
                tool_calls=(
                    ToolCall(
                        id="call-resp",
                        name="send_message",
                        arguments={"message": "I understand you don't want me to do that."},
                    ),
                ),
            ),
        ]
    )

    @tool
    def delete_file(path: str) -> str:
        """Delete a file from disk."""
        return f"deleted {path}"

    runtime = AgentRuntime(
        adapter=adapter,
        tools=[delete_file, send_message],
        tool_rules=[
            RequiresApprovalToolRule(tool_name="delete_file"),
            TerminalToolRule(tool_name="send_message"),
        ],
        max_steps=4,
    )

    pending_call = ToolCall(
        id="call-1",
        name="delete_file",
        arguments={"path": "/tmp/data.txt"},
    )

    await runtime.resume_after_approval(
        approved=False,
        tool_call=pending_call,
        user_id=1,
        history=[],
        denial_reason="I don't want to delete that",
    )

    assert len(adapter.requests) == 1  # one LLM call to produce response
    # The tool result injected into context should contain denial message
    tool_messages = [
        msg
        for msg in adapter.requests[0].messages
        if hasattr(msg, "content") and "denied by user" in str(getattr(msg, "content", "")).lower()
    ]
    assert len(tool_messages) >= 1


# ---------------------------------------------------------------------------
# Persistence: save / load / clear checkpoint
# ---------------------------------------------------------------------------


def test_save_and_load_approval_checkpoint() -> None:
    with _db_session() as db:
        thread, run, _user = _setup_thread_and_run(db)

        pending_call = ToolCall(
            id="call-1",
            name="delete_file",
            arguments={"path": "/tmp/data.txt"},
        )
        seq_id = reserve_message_sequences(db, thread_id=thread.id, count=1)
        approval_msg = save_approval_checkpoint(
            db,
            thread=thread,
            run=run,
            tool_call=pending_call,
            step_id=None,
            sequence_id=seq_id,
        )
        db.commit()

        assert run.status == "awaiting_approval"
        assert run.pending_approval_message_id == approval_msg.id
        assert approval_msg.role == "approval"
        assert approval_msg.tool_name == "delete_file"
        assert approval_msg.tool_call_id == "call-1"
        assert approval_msg.tool_args_json == {"path": "/tmp/data.txt"}

        # Load
        loaded = load_approval_checkpoint(db, run.id)
        assert loaded is not None
        loaded_run, loaded_msg = loaded
        assert loaded_run.id == run.id
        assert loaded_msg.id == approval_msg.id


def test_load_checkpoint_returns_none_for_completed_run() -> None:
    with _db_session() as db:
        _thread, run, _user = _setup_thread_and_run(db)
        run.status = "completed"
        db.add(run)
        db.commit()

        assert load_approval_checkpoint(db, run.id) is None


def test_clear_checkpoint() -> None:
    with _db_session() as db:
        thread, run, _user = _setup_thread_and_run(db)

        pending_call = ToolCall(
            id="call-1",
            name="delete_file",
            arguments={"path": "/tmp/demo.txt"},
        )
        seq_id = reserve_message_sequences(db, thread_id=thread.id, count=1)
        approval_msg = save_approval_checkpoint(
            db,
            thread=thread,
            run=run,
            tool_call=pending_call,
            step_id=None,
            sequence_id=seq_id,
        )
        db.commit()

        clear_approval_checkpoint(db, run, approval_msg)
        db.commit()

        assert approval_msg.is_in_context is False
        assert run.pending_approval_message_id is None


# ---------------------------------------------------------------------------
# Cancel-during-approval clears pending state
# ---------------------------------------------------------------------------


def test_cancel_awaiting_approval_run_clears_checkpoint() -> None:
    with _db_session() as db:
        thread, run, _user = _setup_thread_and_run(db)

        pending_call = ToolCall(
            id="call-1",
            name="delete_file",
            arguments={"path": "/tmp/demo.txt"},
        )
        seq_id = reserve_message_sequences(db, thread_id=thread.id, count=1)
        approval_msg = save_approval_checkpoint(
            db,
            thread=thread,
            run=run,
            tool_call=pending_call,
            step_id=None,
            sequence_id=seq_id,
        )
        db.commit()
        assert run.status == "awaiting_approval"

        cancelled = cancel_run(db, run.id)
        db.commit()

        assert cancelled is not None
        assert cancelled.status == "cancelled"
        assert cancelled.pending_approval_message_id is None
        db.refresh(approval_msg)
        assert approval_msg.is_in_context is False


# ---------------------------------------------------------------------------
# Approval messages excluded from LLM history
# ---------------------------------------------------------------------------


def test_approval_role_excluded_from_thread_history() -> None:
    """role='approval' messages must not appear in load_thread_history."""
    with _db_session() as db:
        thread, run, _user = _setup_thread_and_run(db)

        # Add a normal user message
        append_message(
            db,
            thread=thread,
            run_id=run.id,
            step_id=None,
            sequence_id=1,
            role="user",
            content_text="hello",
        )
        # Add an approval message
        append_message(
            db,
            thread=thread,
            run_id=run.id,
            step_id=None,
            sequence_id=2,
            role="approval",
            content_text="Approval required for tool: delete_file",
            tool_name="delete_file",
        )
        # Add an assistant message
        append_message(
            db,
            thread=thread,
            run_id=run.id,
            step_id=None,
            sequence_id=3,
            role="assistant",
            content_text="Sure thing!",
        )
        db.commit()

        history = load_thread_history(db, thread.id)
        roles = [msg.role for msg in history]
        assert "approval" not in roles
        assert "user" in roles
        assert "assistant" in roles


# ---------------------------------------------------------------------------
# Double-approve (idempotency)
# ---------------------------------------------------------------------------


def test_double_load_after_clear_returns_none() -> None:
    """After clearing a checkpoint, loading it again returns None."""
    with _db_session() as db:
        thread, run, _user = _setup_thread_and_run(db)

        pending_call = ToolCall(
            id="call-1",
            name="delete_file",
            arguments={"path": "/tmp/demo.txt"},
        )
        seq_id = reserve_message_sequences(db, thread_id=thread.id, count=1)
        approval_msg = save_approval_checkpoint(
            db,
            thread=thread,
            run=run,
            tool_call=pending_call,
            step_id=None,
            sequence_id=seq_id,
        )
        db.commit()

        # First resolve
        clear_approval_checkpoint(db, run, approval_msg)
        run.status = "completed"
        db.add(run)
        db.commit()

        # Second load returns None
        assert load_approval_checkpoint(db, run.id) is None


# ---------------------------------------------------------------------------
# Streaming event builder
# ---------------------------------------------------------------------------


def test_build_approval_pending_event() -> None:
    event = build_approval_pending_event(
        run_id=42,
        tool_name="delete_file",
        tool_call_id="call-1",
        tool_arguments={"path": "/tmp/data.txt"},
    )
    assert event.event == "approval_pending"
    assert event.data["runId"] == 42
    assert event.data["toolName"] == "delete_file"
    assert event.data["toolCallId"] == "call-1"
    assert event.data["arguments"] == {"path": "/tmp/data.txt"}


# ---------------------------------------------------------------------------
# Service-layer integration: approve_or_deny_turn
# ---------------------------------------------------------------------------


class _ApproveResumeRunner:
    """Fake runner for service integration tests.

    ``resume_after_approval`` returns a canned AgentResult so we can
    verify the service layer orchestration (checkpoint load, persist,
    companion update) without a real LLM.
    """

    def __init__(self, canned: AgentResult) -> None:
        self._canned = canned
        self.resume_calls: list[dict] = []

    # The companion needs a runtime-looking object; provide stubs.
    def prepare_system_prompt(self) -> str:
        return ""

    async def resume_after_approval(self, **kwargs) -> AgentResult:
        self.resume_calls.append(kwargs)
        return self._canned


@pytest.mark.asyncio
async def test_approve_or_deny_turn_persists_and_finalizes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: save checkpoint → call approve_or_deny_turn → verify
    the run is finalised, messages are persisted, and companion is updated.
    """
    canned_result = AgentResult(
        response="File deleted.",
        model="test-model",
        provider="test-provider",
        stop_reason="terminal_tool",
        tools_used=["delete_file"],
        step_traces=[
            StepTrace(
                step_index=0,
                assistant_text="File deleted.",
                tool_calls=(
                    ToolCall(id="call-1", name="delete_file", arguments={"path": "/tmp/data.txt"}),
                ),
                tool_results=(
                    ToolExecutionResult(
                        call_id="call-1",
                        name="delete_file",
                        output="deleted /tmp/data.txt",
                        is_error=False,
                    ),
                ),
            ),
        ],
    )
    runner = _ApproveResumeRunner(canned_result)

    # Patch module-level singletons used by approve_or_deny_turn.
    monkeypatch.setattr(agent_service, "get_or_build_runner", lambda: runner)
    monkeypatch.setattr(agent_service, "_run_post_turn_hooks", lambda **kw: None)

    with _db_session() as db:
        thread, run, user = _setup_thread_and_run(db)

        # Create a real approval checkpoint.
        pending_call = ToolCall(
            id="call-1",
            name="delete_file",
            arguments={"path": "/tmp/data.txt"},
        )
        seq_id = reserve_message_sequences(db, thread_id=thread.id, count=1)
        save_approval_checkpoint(
            db,
            thread=thread,
            run=run,
            tool_call=pending_call,
            step_id=None,
            sequence_id=seq_id,
        )
        db.commit()
        assert run.status == "awaiting_approval"

        # Call the service function.
        from anima_server.services.agent import approve_or_deny_turn

        result = await approve_or_deny_turn(
            run_id=run.id,
            user_id=user.id,
            approved=True,
            db=db,
            runtime_db=db,
        )

    assert result.response == "File deleted."
    assert result.stop_reason == "terminal_tool"
    assert runner.resume_calls, "resume_after_approval was not called"
    assert runner.resume_calls[0]["approved"] is True


@pytest.mark.asyncio
async def test_approve_or_deny_turn_raises_on_wrong_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Service must reject approval from a different user."""
    canned = AgentResult(
        response="x",
        model="m",
        provider="p",
        stop_reason="end_turn",
        step_traces=[],
    )
    runner = _ApproveResumeRunner(canned)
    monkeypatch.setattr(agent_service, "get_or_build_runner", lambda: runner)

    with _db_session() as db:
        thread, run, user = _setup_thread_and_run(db)
        pending_call = ToolCall(id="c-1", name="t", arguments={})
        seq_id = reserve_message_sequences(db, thread_id=thread.id, count=1)
        save_approval_checkpoint(
            db,
            thread=thread,
            run=run,
            tool_call=pending_call,
            step_id=None,
            sequence_id=seq_id,
        )
        db.commit()

        from anima_server.services.agent import approve_or_deny_turn

        with pytest.raises(PermissionError):
            await approve_or_deny_turn(
                run_id=run.id,
                user_id=user.id + 999,
                approved=True,
                db=db,
                runtime_db=db,
            )


@pytest.mark.asyncio
async def test_approve_or_deny_turn_raises_on_no_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Calling approve on a run with no checkpoint should raise ValueError."""
    canned = AgentResult(
        response="x",
        model="m",
        provider="p",
        stop_reason="end_turn",
        step_traces=[],
    )
    runner = _ApproveResumeRunner(canned)
    monkeypatch.setattr(agent_service, "get_or_build_runner", lambda: runner)

    with _db_session() as db:
        _thread, run, user = _setup_thread_and_run(db)
        run.status = "completed"
        db.add(run)
        db.commit()

        from anima_server.services.agent import approve_or_deny_turn

        with pytest.raises(ValueError, match="not awaiting approval"):
            await approve_or_deny_turn(
                run_id=run.id,
                user_id=user.id,
                approved=True,
                db=db,
                runtime_db=db,
            )


# ---------------------------------------------------------------------------
# _persist_approval_checkpoint integration
# ---------------------------------------------------------------------------


def test_persist_approval_checkpoint_creates_message_and_sets_status() -> None:
    """Verify the service helper persists step traces + approval checkpoint."""
    with _db_session() as db:
        thread, run, _user = _setup_thread_and_run(db)

        result = AgentResult(
            response="",
            model="test-model",
            provider="test-provider",
            stop_reason=StopReason.AWAITING_APPROVAL.value,
            step_traces=[
                StepTrace(
                    step_index=0,
                    assistant_text="I need to delete a file.",
                    tool_calls=(
                        ToolCall(
                            id="call-1", name="delete_file", arguments={"path": "/tmp/data.txt"}
                        ),
                    ),
                    tool_results=(
                        ToolExecutionResult(
                            call_id="call-1",
                            name="delete_file",
                            output="Approval required for tool: delete_file",
                            is_error=True,
                        ),
                    ),
                ),
            ],
        )

        pending_tc = agent_service._persist_approval_checkpoint(
            db,
            thread=thread,
            run=run,
            result=result,
            initial_sequence_id=1,
        )
        # Must return the ToolCall that was checkpointed.
        assert pending_tc is not None
        assert pending_tc.name == "delete_file"
        assert pending_tc.id == "call-1"

        # Verify DB state.
        assert run.status == "awaiting_approval"
        assert run.pending_approval_message_id is not None

        approval_msgs = db.query(RuntimeMessage).filter_by(role="approval").all()
        assert len(approval_msgs) == 1
        assert approval_msgs[0].tool_name == "delete_file"


def test_persist_approval_checkpoint_fails_gracefully_without_tool_call() -> None:
    """If approval trace has no matching tool call, run should be marked failed."""
    with _db_session() as db:
        thread, run, _user = _setup_thread_and_run(db)

        # Result with no tool_calls at all.
        result = AgentResult(
            response="",
            model="test-model",
            provider="test-provider",
            stop_reason=StopReason.AWAITING_APPROVAL.value,
            step_traces=[
                StepTrace(step_index=0, assistant_text="Something went wrong."),
            ],
        )

        pending_tc = agent_service._persist_approval_checkpoint(
            db,
            thread=thread,
            run=run,
            result=result,
            initial_sequence_id=1,
        )
        assert pending_tc is None
        assert run.status == "failed"


def test_persist_approval_checkpoint_skips_retrieval_feedback_logging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _db_session() as db:
        thread, run, _user = _setup_thread_and_run(db)
        feedback_calls: list[dict[str, object]] = []

        monkeypatch.setattr(
            persistence_module,
            "record_retrieval_feedback",
            lambda *args, **kwargs: feedback_calls.append(
                {
                    "args": args,
                    "kwargs": kwargs,
                }
            ),
        )

        result = AgentResult(
            response="Agent runtime is waiting for approval before running a tool.",
            model="test-model",
            provider="test-provider",
            stop_reason=StopReason.AWAITING_APPROVAL.value,
            retrieval=AgentRetrievalTrace(
                retriever="hybrid",
                citations=(
                    AgentCitation(
                        index=1,
                        memory_item_id=7,
                        uri="memory://items/7",
                        score=0.91,
                        category="fact",
                    ),
                ),
                context_fragments=(
                    AgentContextFragment(
                        rank=1,
                        memory_item_id=7,
                        uri="memory://items/7",
                        text="Important remembered detail.",
                        score=0.91,
                        category="fact",
                    ),
                ),
                stats=AgentRetrievalStats(
                    retrieval_ms=12.5,
                    total_considered=4,
                    returned=1,
                    cutoff_index=1,
                    cutoff_score=0.91,
                    top_score=0.91,
                    cutoff_ratio=1.0,
                    triggered_by="adaptive_ratio",
                ),
            ),
            step_traces=[
                StepTrace(
                    step_index=0,
                    assistant_text="I need approval before running that search.",
                    tool_calls=(
                        ToolCall(
                            id="call-1",
                            name="search_memory",
                            arguments={"query": "user preferences"},
                        ),
                    ),
                    tool_results=(
                        ToolExecutionResult(
                            call_id="call-1",
                            name="search_memory",
                            output="Approval required before running tool: search_memory",
                            is_error=True,
                        ),
                    ),
                ),
            ],
        )

        pending_tc = agent_service._persist_approval_checkpoint(
            db,
            thread=thread,
            run=run,
            result=result,
            initial_sequence_id=1,
        )

        assert pending_tc is not None
        assert feedback_calls == []


@pytest.mark.asyncio
async def test_approve_or_deny_turn_restores_retrieval_from_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canned_result = AgentResult(
        response="Here is the answer from memory.",
        model="test-model",
        provider="test-provider",
        stop_reason="terminal_tool",
        tools_used=["search_memory", "send_message"],
        step_traces=[
            StepTrace(
                step_index=1,
                assistant_text="Here is the answer from memory.",
                tool_calls=(
                    ToolCall(
                        id="call-send",
                        name="send_message",
                        arguments={"message": "Here is the answer from memory."},
                    ),
                ),
                tool_results=(
                    ToolExecutionResult(
                        call_id="call-send",
                        name="send_message",
                        output="Here is the answer from memory.",
                        is_terminal=True,
                    ),
                ),
            ),
        ],
    )
    runner = _ApproveResumeRunner(canned_result)

    monkeypatch.setattr(agent_service, "get_or_build_runner", lambda: runner)
    monkeypatch.setattr(agent_service, "_run_post_turn_hooks", lambda **kw: None)

    with _db_session() as db:
        thread, run, user = _setup_thread_and_run(db)
        checkpoint_result = AgentResult(
            response="Agent runtime is waiting for approval before running a tool.",
            model="test-model",
            provider="test-provider",
            stop_reason=StopReason.AWAITING_APPROVAL.value,
            retrieval=AgentRetrievalTrace(
                retriever="hybrid",
                citations=(
                    AgentCitation(
                        index=1,
                        memory_item_id=7,
                        uri="memory://items/7",
                        score=0.91,
                        category="fact",
                    ),
                ),
                context_fragments=(
                    AgentContextFragment(
                        rank=1,
                        memory_item_id=7,
                        uri="memory://items/7",
                        text="Important remembered detail.",
                        score=0.91,
                        category="fact",
                    ),
                ),
                stats=AgentRetrievalStats(
                    retrieval_ms=12.5,
                    total_considered=4,
                    returned=1,
                    cutoff_index=1,
                    cutoff_score=0.91,
                    top_score=0.91,
                    cutoff_ratio=1.0,
                    triggered_by="adaptive_ratio",
                ),
            ),
            step_traces=[
                StepTrace(
                    step_index=0,
                    assistant_text="I need approval before running that search.",
                    tool_calls=(
                        ToolCall(
                            id="call-1",
                            name="search_memory",
                            arguments={"query": "user preferences"},
                        ),
                    ),
                    tool_results=(
                        ToolExecutionResult(
                            call_id="call-1",
                            name="search_memory",
                            output="Approval required before running tool: search_memory",
                            is_error=True,
                        ),
                    ),
                ),
            ],
        )
        pending_tc = agent_service._persist_approval_checkpoint(
            db,
            thread=thread,
            run=run,
            result=checkpoint_result,
            initial_sequence_id=1,
        )
        assert pending_tc is not None
        db.refresh(run)

        result = await agent_service.approve_or_deny_turn(
            run_id=run.id,
            user_id=user.id,
            approved=True,
            db=db,
            runtime_db=db,
        )

        send_message_row = (
            db.query(RuntimeMessage)
            .filter(RuntimeMessage.run_id == run.id, RuntimeMessage.tool_name == "send_message")
            .one()
        )

    assert result.retrieval is not None
    assert result.retrieval.retriever == "hybrid"
    assert send_message_row.content_json is not None
    assert send_message_row.content_json["retrieval"]["retriever"] == "hybrid"
