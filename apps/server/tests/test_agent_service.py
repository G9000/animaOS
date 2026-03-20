from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from anima_server.db.base import Base
from anima_server.models import AgentMessage, AgentRun, AgentStep, AgentThread, User
from anima_server.services.agent import list_agent_history, run_agent
from anima_server.services.agent.compaction import compact_thread_context
from anima_server.services.agent.persistence import create_run, persist_agent_result
from anima_server.services.agent.prompt_budget import (
    PromptBudgetBlockDecision,
    PromptBudgetTrace,
)
from anima_server.services.agent.runtime_types import StepTrace
from anima_server.services.agent.state import AgentResult
from anima_server.services.agent import service as agent_service


class FailingThenReplyRunner:
    def __init__(self) -> None:
        self.calls = 0

    async def invoke(self, *args, **kwargs) -> AgentResult:  # noqa: ANN002, ANN003
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
        del kwargs
        self.requests.append({
            "user_message": user_message,
            "user_id": user_id,
            "history": [
                (message.role, message.content, message.tool_name, message.tool_call_id)
                for message in history
            ],
        })
        reply = f"Reply to: {user_message}"
        return AgentResult(
            response=reply,
            model="test-model",
            provider="test-provider",
            stop_reason="end_turn",
            step_traces=[StepTrace(step_index=0, assistant_text=reply)],
        )


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
async def test_failed_turn_retry_keeps_history_clean(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = FailingThenReplyRunner()
    monkeypatch.setattr(agent_service, "get_or_build_runner", lambda: runner)
    monkeypatch.setattr(agent_service, "_run_post_turn_hooks", lambda **kwargs: None)

    with _db_session() as session:
        user = User(
            username="retry-me",
            password_hash="not-used",
            display_name="Retry Me",
        )
        session.add(user)
        session.commit()

        with pytest.raises(RuntimeError, match="boom"):
            await run_agent("first attempt", user.id, session)

        result = await run_agent("second attempt", user.id, session)

        thread = session.query(AgentThread).one()
        runs = session.query(AgentRun).order_by(AgentRun.id).all()
        messages = (
            session.query(AgentMessage)
            .order_by(AgentMessage.sequence_id)
            .all()
        )
        history = list_agent_history(user.id, session, limit=10)

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
        with _db_session() as session:
            user = User(
                username="history-check",
                password_hash="not-used",
                display_name="History Check",
            )
            session.add(user)
            session.commit()

            await run_agent("first turn", user.id, session)
            await run_agent("second turn", user.id, session)
    finally:
        agent_service.invalidate_agent_runtime_cache()

    assert runner.requests[0]["user_message"] == "first turn"
    assert runner.requests[0]["history"] == []
    assert runner.requests[1]["user_message"] == "second turn"
    assert runner.requests[1]["history"] == [
        ("user", "first turn", None, None),
        ("assistant", "Reply to: first turn", None, None),
    ]


def test_persist_agent_result_records_prompt_budget_on_first_step() -> None:
    with _db_session() as session:
        user = User(
            username="prompt-budget",
            password_hash="not-used",
            display_name="Prompt Budget",
        )
        session.add(user)
        session.flush()

        thread = AgentThread(user_id=user.id, status="active", next_message_sequence=2)
        session.add(thread)
        session.flush()

        run = create_run(
            session,
            thread_id=thread.id,
            user_id=user.id,
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

        step = session.query(AgentStep).one()

    prompt_budget = step.request_json["prompt_budget"]
    assert prompt_budget["system_prompt_token_estimate"] == 30
    assert prompt_budget["decisions"][0]["label"] == "current_focus"


def test_compaction_accounts_for_reserved_prompt_tokens() -> None:
    with _db_session() as session:
        user = User(
            username="compact-budget",
            password_hash="not-used",
            display_name="Compact Budget",
        )
        session.add(user)
        session.flush()

        thread = AgentThread(user_id=user.id, status="active", next_message_sequence=3)
        session.add(thread)
        session.flush()

        session.add_all(
            [
                AgentMessage(
                    thread_id=thread.id,
                    sequence_id=1,
                    role="user",
                    content_text="a" * 40,
                    is_in_context=True,
                ),
                AgentMessage(
                    thread_id=thread.id,
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
        summary = session.query(AgentMessage).filter(AgentMessage.role == "summary").one()

    assert result is not None
    assert result.effective_trigger_token_limit == 18
    assert result.reserved_prompt_tokens == 12
    assert summary.sequence_id == 3
    assert "Conversation summary:" in (summary.content_text or "")
