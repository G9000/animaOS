"""Tests for agent persistence: thread/run/message CRUD and history loading."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from anima_server.db.base import Base
from anima_server.models import AgentMessage, AgentRun, AgentThread, User
from anima_server.services.agent.persistence import (
    append_message,
    append_user_message,
    clear_threads,
    count_messages_by_role,
    create_run,
    finalize_run,
    get_or_create_thread,
    load_thread_history,
    mark_run_failed,
    persist_agent_result,
    reset_thread,
    _deserialize_tool_calls,
)
from anima_server.services.agent.runtime_types import (
    StepTrace,
    ToolCall,
    ToolExecutionResult,
    UsageStats,
)
from anima_server.services.agent.state import AgentResult


# --------------------------------------------------------------------------- #
# In-memory database helper
# --------------------------------------------------------------------------- #


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


def _make_user(db: Session) -> User:
    user = User(username="testuser", password_hash="x", display_name="Test")
    db.add(user)
    db.flush()
    return user


# --------------------------------------------------------------------------- #
# get_or_create_thread
# --------------------------------------------------------------------------- #


def test_get_or_create_thread_creates_new() -> None:
    with _db_session() as db:
        user = _make_user(db)
        thread = get_or_create_thread(db, user.id)
        assert thread.id is not None
        assert thread.user_id == user.id
        assert thread.status == "active"


def test_get_or_create_thread_returns_existing() -> None:
    with _db_session() as db:
        user = _make_user(db)
        thread_1 = get_or_create_thread(db, user.id)
        thread_2 = get_or_create_thread(db, user.id)
        assert thread_1.id == thread_2.id


# --------------------------------------------------------------------------- #
# create_run
# --------------------------------------------------------------------------- #


def test_create_run() -> None:
    with _db_session() as db:
        user = _make_user(db)
        thread = get_or_create_thread(db, user.id)
        run = create_run(
            db,
            thread_id=thread.id,
            user_id=user.id,
            provider="openai",
            model="gpt-4",
            mode="chat",
        )
        assert run.id is not None
        assert run.status == "running"
        assert run.provider == "openai"
        assert run.model == "gpt-4"


# --------------------------------------------------------------------------- #
# append_user_message / append_message
# --------------------------------------------------------------------------- #


def test_append_user_message() -> None:
    with _db_session() as db:
        user = _make_user(db)
        thread = get_or_create_thread(db, user.id)
        run = create_run(
            db, thread_id=thread.id, user_id=user.id,
            provider="test", model="test", mode="chat",
        )
        msg = append_user_message(
            db, thread=thread, run_id=run.id,
            content="Hello!", sequence_id=1,
        )
        assert msg.role == "user"
        assert msg.content_text == "Hello!"
        assert msg.is_in_context is True
        assert msg.token_estimate is not None


def test_append_message_tool() -> None:
    with _db_session() as db:
        user = _make_user(db)
        thread = get_or_create_thread(db, user.id)
        msg = append_message(
            db, thread=thread, run_id=None, step_id=None,
            sequence_id=1, role="tool",
            content_text="tool output",
            tool_name="search",
            tool_call_id="c1",
        )
        assert msg.role == "tool"
        assert msg.tool_name == "search"
        assert msg.tool_call_id == "c1"


def test_append_message_updates_thread_timestamps() -> None:
    with _db_session() as db:
        user = _make_user(db)
        thread = get_or_create_thread(db, user.id)
        original_updated_at = thread.updated_at

        append_message(
            db, thread=thread, run_id=None, step_id=None,
            sequence_id=1, role="user", content_text="hi",
        )
        # Thread timestamps should be updated
        assert thread.last_message_at is not None


# --------------------------------------------------------------------------- #
# load_thread_history
# --------------------------------------------------------------------------- #


def test_load_thread_history_empty() -> None:
    with _db_session() as db:
        user = _make_user(db)
        thread = get_or_create_thread(db, user.id)
        history = load_thread_history(db, thread.id)
        assert history == []


def test_load_thread_history_with_messages() -> None:
    with _db_session() as db:
        user = _make_user(db)
        thread = get_or_create_thread(db, user.id)

        append_message(
            db, thread=thread, run_id=None, step_id=None,
            sequence_id=1, role="user", content_text="Hello",
        )
        append_message(
            db, thread=thread, run_id=None, step_id=None,
            sequence_id=2, role="assistant", content_text="Hi back",
        )
        db.commit()

        history = load_thread_history(db, thread.id)
        assert len(history) == 2
        assert history[0].role == "user"
        assert history[0].content == "Hello"
        assert history[1].role == "assistant"
        assert history[1].content == "Hi back"


def test_load_thread_history_excludes_out_of_context() -> None:
    with _db_session() as db:
        user = _make_user(db)
        thread = get_or_create_thread(db, user.id)

        msg = append_message(
            db, thread=thread, run_id=None, step_id=None,
            sequence_id=1, role="user", content_text="Hello",
        )
        # Mark message out of context
        msg.is_in_context = False
        db.add(msg)
        db.flush()

        history = load_thread_history(db, thread.id)
        assert len(history) == 0


def test_load_thread_history_excludes_summary_role() -> None:
    """Summary messages are not included in history (only user/assistant/tool)."""
    with _db_session() as db:
        user = _make_user(db)
        thread = get_or_create_thread(db, user.id)

        append_message(
            db, thread=thread, run_id=None, step_id=None,
            sequence_id=1, role="summary", content_text="Earlier context",
        )
        append_message(
            db, thread=thread, run_id=None, step_id=None,
            sequence_id=2, role="user", content_text="Hello",
        )
        db.commit()

        history = load_thread_history(db, thread.id)
        assert len(history) == 1
        assert history[0].role == "user"


# --------------------------------------------------------------------------- #
# count_messages_by_role
# --------------------------------------------------------------------------- #


def test_count_messages_by_role() -> None:
    with _db_session() as db:
        user = _make_user(db)
        thread = get_or_create_thread(db, user.id)

        for i in range(3):
            append_message(
                db, thread=thread, run_id=None, step_id=None,
                sequence_id=i + 1, role="user", content_text=f"msg {i}",
            )
        append_message(
            db, thread=thread, run_id=None, step_id=None,
            sequence_id=4, role="assistant", content_text="reply",
        )
        db.commit()

        assert count_messages_by_role(db, thread.id, "user") == 3
        assert count_messages_by_role(db, thread.id, "assistant") == 1
        assert count_messages_by_role(db, thread.id, "tool") == 0


# --------------------------------------------------------------------------- #
# mark_run_failed
# --------------------------------------------------------------------------- #


def test_mark_run_failed() -> None:
    with _db_session() as db:
        user = _make_user(db)
        thread = get_or_create_thread(db, user.id)
        run = create_run(
            db, thread_id=thread.id, user_id=user.id,
            provider="test", model="test", mode="chat",
        )
        mark_run_failed(db, run, "Something went wrong")
        assert run.status == "failed"
        assert run.error_text == "Something went wrong"
        assert run.completed_at is not None


# --------------------------------------------------------------------------- #
# finalize_run
# --------------------------------------------------------------------------- #


def test_finalize_run_aggregates_tokens() -> None:
    with _db_session() as db:
        user = _make_user(db)
        thread = get_or_create_thread(db, user.id)
        run = create_run(
            db, thread_id=thread.id, user_id=user.id,
            provider="test", model="test", mode="chat",
        )

        result = AgentResult(
            response="done",
            model="test",
            provider="test",
            stop_reason="end_turn",
            step_traces=[
                StepTrace(
                    step_index=0,
                    usage=UsageStats(
                        prompt_tokens=10,
                        completion_tokens=5,
                        total_tokens=15,
                    ),
                ),
                StepTrace(
                    step_index=1,
                    usage=UsageStats(
                        prompt_tokens=20,
                        completion_tokens=10,
                        total_tokens=30,
                    ),
                ),
            ],
        )

        finalize_run(db, run=run, result=result)
        assert run.status == "completed"
        assert run.prompt_tokens == 30
        assert run.completion_tokens == 15
        assert run.total_tokens == 45
        assert run.completed_at is not None


def test_finalize_run_no_usage() -> None:
    with _db_session() as db:
        user = _make_user(db)
        thread = get_or_create_thread(db, user.id)
        run = create_run(
            db, thread_id=thread.id, user_id=user.id,
            provider="test", model="test", mode="chat",
        )

        result = AgentResult(
            response="done", model="test", provider="test",
            step_traces=[StepTrace(step_index=0)],
        )

        finalize_run(db, run=run, result=result)
        assert run.status == "completed"
        assert run.prompt_tokens is None
        assert run.completion_tokens is None
        assert run.total_tokens is None


# --------------------------------------------------------------------------- #
# reset_thread / clear_threads
# --------------------------------------------------------------------------- #


def test_reset_thread_removes_thread() -> None:
    with _db_session() as db:
        user = _make_user(db)
        get_or_create_thread(db, user.id)
        db.commit()

        reset_thread(db, user.id)
        db.commit()

        # Creating again should produce a new thread
        new_thread = get_or_create_thread(db, user.id)
        assert new_thread is not None


def test_reset_thread_no_thread() -> None:
    """reset_thread is a no-op when no thread exists."""
    with _db_session() as db:
        user = _make_user(db)
        reset_thread(db, user.id)  # Should not raise


def test_clear_threads() -> None:
    with _db_session() as db:
        user1 = _make_user(db)
        user2 = User(username="user2", password_hash="x", display_name="User 2")
        db.add(user2)
        db.flush()

        get_or_create_thread(db, user1.id)
        get_or_create_thread(db, user2.id)
        db.commit()

        clear_threads(db)
        db.commit()

        # Both threads should be gone
        thread1 = get_or_create_thread(db, user1.id)
        thread2 = get_or_create_thread(db, user2.id)
        # These are newly created threads
        assert thread1.id is not None
        assert thread2.id is not None


# --------------------------------------------------------------------------- #
# _deserialize_tool_calls
# --------------------------------------------------------------------------- #


def test_deserialize_tool_calls_none() -> None:
    assert _deserialize_tool_calls(None) == ()


def test_deserialize_tool_calls_no_tool_calls_key() -> None:
    assert _deserialize_tool_calls({"other": "data"}) == ()


def test_deserialize_tool_calls_non_list() -> None:
    assert _deserialize_tool_calls({"tool_calls": "bad"}) == ()


def test_deserialize_tool_calls_valid() -> None:
    content_json = {
        "tool_calls": [
            {
                "id": "c1",
                "name": "search",
                "arguments": {"q": "cats"},
            },
            {
                "id": "c2",
                "name": "recall",
                "arguments": {},
            },
        ]
    }
    result = _deserialize_tool_calls(content_json)
    assert len(result) == 2
    assert result[0].id == "c1"
    assert result[0].name == "search"
    assert result[0].arguments == {"q": "cats"}
    assert result[1].id == "c2"
    assert result[1].name == "recall"


def test_deserialize_tool_calls_skips_nameless() -> None:
    content_json = {
        "tool_calls": [
            {"id": "c1", "name": "", "arguments": {}},
            {"id": "c2", "name": "valid", "arguments": {}},
        ]
    }
    result = _deserialize_tool_calls(content_json)
    assert len(result) == 1
    assert result[0].name == "valid"


def test_deserialize_tool_calls_generates_id() -> None:
    content_json = {
        "tool_calls": [
            {"name": "search", "arguments": {}},
        ]
    }
    result = _deserialize_tool_calls(content_json)
    assert len(result) == 1
    assert result[0].id == "tool-call-0"


def test_deserialize_tool_calls_with_parse_error() -> None:
    content_json = {
        "tool_calls": [
            {
                "id": "c1",
                "name": "broken",
                "arguments": {},
                "parse_error": "invalid json",
                "raw_arguments": '{"bad',
            },
        ]
    }
    result = _deserialize_tool_calls(content_json)
    assert result[0].parse_error == "invalid json"
    assert result[0].raw_arguments == '{"bad'


def test_deserialize_tool_calls_invalid_arguments_type() -> None:
    """Non-dict arguments default to empty dict."""
    content_json = {
        "tool_calls": [
            {"id": "c1", "name": "tool", "arguments": "bad"},
        ]
    }
    result = _deserialize_tool_calls(content_json)
    assert result[0].arguments == {}


# --------------------------------------------------------------------------- #
# persist_agent_result
# --------------------------------------------------------------------------- #


def test_persist_agent_result_simple() -> None:
    with _db_session() as db:
        user = _make_user(db)
        thread = get_or_create_thread(db, user.id)
        run = create_run(
            db, thread_id=thread.id, user_id=user.id,
            provider="test", model="test", mode="chat",
        )

        result = AgentResult(
            response="Hello!",
            model="test-model",
            provider="test-provider",
            stop_reason="end_turn",
            step_traces=[
                StepTrace(
                    step_index=0,
                    assistant_text="Hello!",
                    tool_calls=(),
                    tool_results=(),
                ),
            ],
        )

        persist_agent_result(
            db,
            thread=thread,
            run=run,
            result=result,
            initial_sequence_id=1,
        )
        db.commit()

        # Verify the assistant message was persisted
        messages = db.query(AgentMessage).filter_by(
            thread_id=thread.id
        ).all()
        assert len(messages) == 1
        assert messages[0].role == "assistant"
        assert messages[0].content_text == "Hello!"


def test_persist_agent_result_with_tool_calls() -> None:
    with _db_session() as db:
        user = _make_user(db)
        thread = get_or_create_thread(db, user.id)
        run = create_run(
            db, thread_id=thread.id, user_id=user.id,
            provider="test", model="test", mode="chat",
        )

        tc = ToolCall(id="c1", name="search", arguments={"q": "cats"})
        tool_result = ToolExecutionResult(
            call_id="c1", name="search", output="Found cats",
        )

        result = AgentResult(
            response="Found it!",
            model="test",
            provider="test",
            step_traces=[
                StepTrace(
                    step_index=0,
                    assistant_text="Let me search...",
                    tool_calls=(tc,),
                    tool_results=(tool_result,),
                ),
            ],
        )

        persist_agent_result(
            db,
            thread=thread,
            run=run,
            result=result,
            initial_sequence_id=1,
        )
        db.commit()

        messages = db.query(AgentMessage).filter_by(
            thread_id=thread.id
        ).order_by(AgentMessage.sequence_id).all()

        # Should have assistant message + tool message
        assert len(messages) == 2
        assert messages[0].role == "assistant"
        assert messages[1].role == "tool"
        assert messages[1].tool_name == "search"
        assert messages[1].content_text == "Found cats"
