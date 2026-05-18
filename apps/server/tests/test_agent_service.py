from __future__ import annotations

import asyncio
from collections.abc import Generator
from contextlib import contextmanager

import pytest
from anima_server.db.base import Base
from anima_server.models import User
from anima_server.models.runtime import RuntimeMessage, RuntimeRun, RuntimeStep, RuntimeThread
from anima_server.services.agent import list_agent_history, run_agent
from anima_server.services.agent import service as agent_service
from anima_server.services.agent.client_actions import ActionToolConnection, action_registry
from anima_server.services.agent.compaction import compact_thread_context
from anima_server.services.agent.evidence_retrieval import RetrievalMode, WideEvidenceResult
from anima_server.services.agent.persistence import (
    append_message,
    cancel_run,
    create_run,
    persist_agent_result,
)
from anima_server.services.agent.prompt_budget import (
    PromptBudgetBlockDecision,
    PromptBudgetTrace,
)
from anima_server.services.agent.runtime_types import StepTrace
from anima_server.services.agent.state import AgentResult
from conftest_runtime import runtime_db_session
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


class FailingThenReplyRunner:
    def __init__(self) -> None:
        self.calls = 0

    async def invoke(self, *args, **kwargs) -> AgentResult:
        del args, kwargs
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("boom")
        return AgentResult(
            response="Recovered reply.",
            model="test-model",
            provider="test-provider",
            stop_reason="end_turn",
            step_traces=[StepTrace(step_index=0, assistant_text="Recovered reply.")],
        )


class RecordingRunner:
    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []

    async def invoke(
        self,
        user_message: str,
        user_id: int,
        history: list[agent_service.StoredMessage],
        **kwargs: object,
    ) -> AgentResult:
        self.requests.append(
            {
                "user_message": user_message,
                "user_id": user_id,
                "history": [
                    (message.role, message.content, message.tool_name, message.tool_call_id)
                    for message in history
                ],
                "extra_tool_schemas": kwargs.get("extra_tool_schemas"),
                "tool_executor": kwargs.get("tool_executor"),
            }
        )
        reply = f"Reply to: {user_message}"
        return AgentResult(
            response=reply,
            model="test-model",
            provider="test-provider",
            stop_reason="end_turn",
            step_traces=[StepTrace(step_index=0, assistant_text=reply)],
        )


class BlockingRunner:
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def invoke(self, *args, **kwargs) -> AgentResult:
        del args, kwargs
        self.started.set()
        await asyncio.Event().wait()
        raise AssertionError("blocking runner should be cancelled")


class FakeWebSocket:
    async def send_json(self, message: dict[str, object]) -> None:
        del message


@contextmanager
def _soul_db_session() -> Generator[Session, None, None]:
    """Soul DB session (for User model)."""
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
async def test_failed_turn_retry_keeps_history_clean(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = FailingThenReplyRunner()
    monkeypatch.setattr(agent_service, "get_or_build_runner", lambda: runner)
    monkeypatch.setattr(agent_service, "_run_post_turn_hooks", lambda **kwargs: None)

    with _soul_db_session() as soul_session, runtime_db_session() as runtime_session:
        user = User(
            username="retry-me",
            password_hash="not-used",
            display_name="Retry Me",
        )
        soul_session.add(user)
        soul_session.commit()

        with pytest.raises(RuntimeError, match="boom"):
            await run_agent("first attempt", user.id, soul_session, runtime_session)

        result = await run_agent("second attempt", user.id, soul_session, runtime_session)

        thread = runtime_session.query(RuntimeThread).one()
        runs = runtime_session.query(RuntimeRun).order_by(RuntimeRun.id).all()
        messages = runtime_session.query(RuntimeMessage).order_by(RuntimeMessage.sequence_id).all()
        history = list_agent_history(user.id, runtime_session, limit=10)

    assert result.response == "Recovered reply."
    assert [run.status for run in runs] == ["failed", "completed"]
    assert [message.sequence_id for message in messages] == [1, 2, 3]
    assert messages[0].content_text == "first attempt"
    assert messages[0].is_in_context is False
    assert messages[1].content_text == "second attempt"
    assert messages[1].is_in_context is True
    assert messages[2].content_text == "Recovered reply."
    assert messages[2].is_in_context is True
    assert thread.next_message_sequence == 4
    assert [message.content_text for message in history] == [
        "second attempt",
        "Recovered reply.",
    ]


@pytest.mark.asyncio
async def test_run_agent_passes_only_prior_turns_in_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_service.invalidate_agent_runtime_cache()
    runner = RecordingRunner()
    monkeypatch.setattr(agent_service, "get_or_build_runner", lambda: runner)
    monkeypatch.setattr(agent_service, "_run_post_turn_hooks", lambda **kwargs: None)

    try:
        with _soul_db_session() as soul_session, runtime_db_session() as runtime_session:
            user = User(
                username="history-check",
                password_hash="not-used",
                display_name="History Check",
            )
            soul_session.add(user)
            soul_session.commit()

            await run_agent("first turn", user.id, soul_session, runtime_session)
            await run_agent("second turn", user.id, soul_session, runtime_session)
    finally:
        agent_service.invalidate_agent_runtime_cache()

    assert runner.requests[0]["user_message"] == "first turn"
    assert runner.requests[0]["history"] == []
    assert runner.requests[1]["user_message"] == "second turn"
    assert runner.requests[1]["history"] == [
        ("user", "first turn", None, None),
        ("assistant", "Reply to: first turn", None, None),
    ]


@pytest.mark.asyncio
async def test_run_agent_attaches_connected_animus_action_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_service.invalidate_agent_runtime_cache()
    runner = RecordingRunner()
    monkeypatch.setattr(agent_service, "get_or_build_runner", lambda: runner)
    monkeypatch.setattr(agent_service, "_run_post_turn_hooks", lambda **kwargs: None)

    try:
        with _soul_db_session() as soul_session, runtime_db_session() as runtime_session:
            user = User(
                username="action-tools",
                password_hash="not-used",
                display_name="Action Tools",
            )
            soul_session.add(user)
            soul_session.commit()

            conn = ActionToolConnection(
                websocket=FakeWebSocket(),
                user_id=user.id,
                username="animus",
                action_tool_schemas=[
                    {
                        "name": "bash",
                        "description": "Execute a shell command through Animus.",
                        "parameters": {
                            "type": "object",
                            "properties": {"command": {"type": "string"}},
                            "required": ["command"],
                        },
                    }
                ],
            )
            action_registry.add(conn)
            try:
                await run_agent("inspect files", user.id, soul_session, runtime_session)
            finally:
                action_registry.remove(conn)
    finally:
        agent_service.invalidate_agent_runtime_cache()

    extra_tool_schemas = runner.requests[0]["extra_tool_schemas"]
    assert isinstance(extra_tool_schemas, list)
    assert extra_tool_schemas[0]["function"]["name"] == "bash"
    assert runner.requests[0]["tool_executor"] is not None


@pytest.mark.asyncio
async def test_run_agent_does_not_run_hidden_wide_evidence_retrieval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_service.invalidate_agent_runtime_cache()
    runner = RecordingRunner()
    calls: list[dict[str, object]] = []

    async def fake_retrieve_wide_evidence(**kwargs: object) -> WideEvidenceResult:
        calls.append(kwargs)
        return WideEvidenceResult(mode=RetrievalMode.AGGREGATE)

    monkeypatch.setattr(agent_service, "get_or_build_runner", lambda: runner)
    monkeypatch.setattr(agent_service, "_run_post_turn_hooks", lambda **kwargs: None)
    monkeypatch.setattr(
        "anima_server.services.agent.evidence_retrieval.retrieve_wide_evidence",
        fake_retrieve_wide_evidence,
    )

    try:
        with _soul_db_session() as soul_session, runtime_db_session() as runtime_session:
            user = User(
                username="no-hidden-wide-retrieval",
                password_hash="not-used",
                display_name="No Hidden Wide Retrieval",
            )
            soul_session.add(user)
            soul_session.commit()

            await run_agent(
                "How many model kits have I worked on or bought?",
                user.id,
                soul_session,
                runtime_session,
            )
    finally:
        agent_service.invalidate_agent_runtime_cache()

    assert calls == []


def test_persist_agent_result_records_prompt_budget_on_first_step() -> None:
    with runtime_db_session() as session:
        user_id = 42

        thread = RuntimeThread(user_id=user_id, status="active", next_message_sequence=2)
        session.add(thread)
        session.flush()

        run = create_run(
            session,
            thread_id=thread.id,
            user_id=user_id,
            provider="test-provider",
            model="test-model",
            mode="blocking",
        )
        result = AgentResult(
            response="ok",
            model="test-model",
            provider="test-provider",
            stop_reason="end_turn",
            step_traces=[StepTrace(step_index=0, assistant_text="ok")],
            prompt_budget=PromptBudgetTrace(
                total_budget=100,
                retained_chars=24,
                dropped_chars=8,
                retained_token_estimate=6,
                dropped_token_estimate=2,
                tier_usage={"0": 0, "1": 24, "2": 0, "3": 0},
                tier_budgets={"0": 0, "1": 100, "2": 0, "3": 0},
                system_prompt_chars=120,
                system_prompt_token_estimate=30,
                decisions=(
                    PromptBudgetBlockDecision(
                        label="current_focus",
                        tier=1,
                        status="kept",
                        original_chars=24,
                        final_chars=24,
                        reason="within_budget",
                    ),
                ),
            ),
        )

        persist_agent_result(
            session,
            thread=thread,
            run=run,
            result=result,
            initial_sequence_id=1,
        )
        session.commit()

        step = session.query(RuntimeStep).one()

    prompt_budget = step.request_json["prompt_budget"]
    assert prompt_budget["system_prompt_token_estimate"] == 30
    assert prompt_budget["decisions"][0]["label"] == "current_focus"


def test_compaction_accounts_for_reserved_prompt_tokens() -> None:
    with runtime_db_session() as session:
        user_id = 43

        thread = RuntimeThread(user_id=user_id, status="active", next_message_sequence=3)
        session.add(thread)
        session.flush()

        session.add_all(
            [
                RuntimeMessage(
                    thread_id=thread.id,
                    user_id=user_id,
                    sequence_id=1,
                    role="user",
                    content_text="a" * 40,
                    is_in_context=True,
                ),
                RuntimeMessage(
                    thread_id=thread.id,
                    user_id=user_id,
                    sequence_id=2,
                    role="assistant",
                    content_text="b" * 40,
                    is_in_context=True,
                ),
            ]
        )
        session.flush()

        result = compact_thread_context(
            session,
            thread=thread,
            run_id=None,
            trigger_token_limit=30,
            keep_last_messages=1,
            reserved_prompt_tokens=12,
        )
        summary = session.query(RuntimeMessage).filter(RuntimeMessage.role == "summary").one()

    assert result is not None
    assert result.effective_trigger_token_limit == 18
    assert result.reserved_prompt_tokens == 12
    assert summary.sequence_id == 3
    assert "Conversation summary:" in (summary.content_text or "")


def test_companion_history_cache_is_scoped_by_thread() -> None:
    agent_service.invalidate_agent_runtime_cache()
    try:
        with runtime_db_session() as session:
            user_id = 44
            thread_one = RuntimeThread(
                user_id=user_id,
                status="active",
                next_message_sequence=2,
            )
            thread_two = RuntimeThread(
                user_id=user_id,
                status="active",
                next_message_sequence=2,
            )
            session.add_all([thread_one, thread_two])
            session.flush()

            append_message(
                session,
                thread=thread_one,
                run_id=None,
                step_id=None,
                sequence_id=1,
                role="user",
                content_text="thread one history",
            )
            append_message(
                session,
                thread=thread_two,
                run_id=None,
                step_id=None,
                sequence_id=1,
                role="user",
                content_text="thread two history",
            )
            session.commit()

            companion = agent_service._get_companion(user_id)
            history_one = companion.ensure_history_loaded(
                session,
                thread_id=thread_one.id,
            )
            history_two = companion.ensure_history_loaded(
                session,
                thread_id=thread_two.id,
            )

            history_one.clear()
            history_one_again = companion.ensure_history_loaded(
                session,
                thread_id=thread_one.id,
            )

        assert [message.content for message in history_one_again] == [
            "thread one history"
        ]
        assert [message.content for message in history_two] == [
            "thread two history"
        ]
    finally:
        agent_service.invalidate_agent_runtime_cache()


def test_persist_agent_result_does_not_overwrite_cancelled_run() -> None:
    with runtime_db_session() as session:
        user_id = 45
        thread = RuntimeThread(user_id=user_id, status="active", next_message_sequence=2)
        session.add(thread)
        session.flush()
        run = create_run(
            session,
            thread_id=thread.id,
            user_id=user_id,
            provider="test-provider",
            model="test-model",
            mode="blocking",
        )
        cancel_run(session, run.id)
        session.flush()

        result = AgentResult(
            response="late reply",
            model="test-model",
            provider="test-provider",
            stop_reason="terminal_tool",
            step_traces=[StepTrace(step_index=0, assistant_text="late reply")],
        )

        persist_agent_result(
            session,
            thread=thread,
            run=run,
            result=result,
            initial_sequence_id=1,
        )
        session.commit()

        session.refresh(run)
        persisted_message_count = (
            session.query(RuntimeMessage)
            .filter(RuntimeMessage.run_id == run.id)
            .count()
        )

    assert run.status == "cancelled"
    assert run.stop_reason == "cancelled"
    assert persisted_message_count == 0


@pytest.mark.asyncio
async def test_cancelled_agent_task_marks_running_run_cancelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = BlockingRunner()
    monkeypatch.setattr(agent_service, "get_or_build_runner", lambda: runner)
    monkeypatch.setattr(agent_service, "_run_post_turn_hooks", lambda **kwargs: None)

    with _soul_db_session() as soul_session, runtime_db_session() as runtime_session:
        user = User(
            username="cancel-stream",
            password_hash="not-used",
            display_name="Cancel Stream",
        )
        soul_session.add(user)
        soul_session.commit()

        task = asyncio.create_task(
            run_agent("please wait", user.id, soul_session, runtime_session)
        )
        await asyncio.wait_for(runner.started.wait(), timeout=1)

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        run = runtime_session.query(RuntimeRun).one()

    assert run.status == "cancelled"
    assert run.stop_reason == "cancelled"
