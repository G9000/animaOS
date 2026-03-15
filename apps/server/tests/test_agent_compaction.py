"""Tests for context compaction: token estimation and summary rendering."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from anima_server.db.base import Base
from anima_server.models import AgentMessage, AgentThread, User
from anima_server.services.agent.compaction import (
    CompactionResult,
    SUMMARY_LINE_LIMIT,
    SUMMARY_TEXT_LIMIT,
    _summarize_row,
    _trim_summary_text,
    compact_thread_context,
    estimate_message_tokens,
    render_summary_text,
)


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


def _make_thread(db: Session, user_id: int) -> AgentThread:
    thread = AgentThread(user_id=user_id, status="active")
    db.add(thread)
    db.flush()
    return thread


def _add_message(
    db: Session,
    *,
    thread_id: int,
    sequence_id: int,
    role: str,
    content_text: str,
    is_in_context: bool = True,
    tool_name: str | None = None,
    token_estimate: int | None = None,
) -> AgentMessage:
    msg = AgentMessage(
        thread_id=thread_id,
        run_id=None,
        step_id=None,
        sequence_id=sequence_id,
        role=role,
        content_text=content_text,
        is_in_context=is_in_context,
        tool_name=tool_name,
        token_estimate=token_estimate,
    )
    db.add(msg)
    db.flush()
    return msg


# --------------------------------------------------------------------------- #
# estimate_message_tokens
# --------------------------------------------------------------------------- #


def test_estimate_tokens_empty() -> None:
    assert estimate_message_tokens(content_text=None) == 0


def test_estimate_tokens_text_only() -> None:
    # "hello world" = 11 chars => ceil(11/4) = 3
    tokens = estimate_message_tokens(content_text="hello world")
    assert tokens == 3


def test_estimate_tokens_with_tool_name() -> None:
    tokens = estimate_message_tokens(
        content_text="result", tool_name="search"
    )
    assert tokens > 0


def test_estimate_tokens_with_json() -> None:
    tokens = estimate_message_tokens(
        content_text=None, content_json={"key": "value"}
    )
    assert tokens > 0


def test_estimate_tokens_minimum_one() -> None:
    # Single character => ceil(1/4) = 1
    assert estimate_message_tokens(content_text="x") == 1


# --------------------------------------------------------------------------- #
# _trim_summary_text
# --------------------------------------------------------------------------- #


def test_trim_summary_text_short() -> None:
    assert _trim_summary_text("  hello world  ") == "hello world"


def test_trim_summary_text_long_truncated() -> None:
    long_text = "a" * 300
    trimmed = _trim_summary_text(long_text)
    assert len(trimmed) <= SUMMARY_TEXT_LIMIT
    assert trimmed.endswith("...")


def test_trim_summary_text_normalizes_whitespace() -> None:
    assert _trim_summary_text("hello   world\n\nfoo") == "hello world foo"


# --------------------------------------------------------------------------- #
# _summarize_row
# --------------------------------------------------------------------------- #


def test_summarize_row_user() -> None:
    with _db_session() as db:
        user = _make_user(db)
        thread = _make_thread(db, user.id)
        msg = _add_message(
            db, thread_id=thread.id, sequence_id=1, role="user",
            content_text="Hello there",
        )
        result = _summarize_row(msg, user_id=user.id)
        assert result.startswith("User:")
        assert "Hello there" in result


def test_summarize_row_assistant() -> None:
    with _db_session() as db:
        user = _make_user(db)
        thread = _make_thread(db, user.id)
        msg = _add_message(
            db, thread_id=thread.id, sequence_id=1, role="assistant",
            content_text="Hi back!",
        )
        result = _summarize_row(msg, user_id=user.id)
        assert result.startswith("Assistant:")


def test_summarize_row_tool_with_name() -> None:
    with _db_session() as db:
        user = _make_user(db)
        thread = _make_thread(db, user.id)
        msg = _add_message(
            db, thread_id=thread.id, sequence_id=1, role="tool",
            content_text="search results", tool_name="search",
        )
        result = _summarize_row(msg, user_id=user.id)
        assert "Tool search:" in result


def test_summarize_row_empty_content() -> None:
    with _db_session() as db:
        user = _make_user(db)
        thread = _make_thread(db, user.id)
        msg = _add_message(
            db, thread_id=thread.id, sequence_id=1, role="user",
            content_text="",
        )
        result = _summarize_row(msg, user_id=user.id)
        assert "[empty]" in result


# --------------------------------------------------------------------------- #
# render_summary_text
# --------------------------------------------------------------------------- #


def test_render_summary_text_basic() -> None:
    with _db_session() as db:
        user = _make_user(db)
        thread = _make_thread(db, user.id)

        msgs = []
        for i in range(3):
            msgs.append(
                _add_message(
                    db, thread_id=thread.id, sequence_id=i + 1,
                    role="user", content_text=f"Message {i}",
                )
            )

        summary = render_summary_text([], msgs, user_id=user.id)
        assert summary.startswith("Conversation summary:")
        assert "Message 0" in summary
        assert "Message 2" in summary


def test_render_summary_text_with_existing_summary() -> None:
    with _db_session() as db:
        user = _make_user(db)
        thread = _make_thread(db, user.id)

        summary_msg = _add_message(
            db, thread_id=thread.id, sequence_id=1,
            role="summary", content_text="Earlier summary text",
        )
        compacted = _add_message(
            db, thread_id=thread.id, sequence_id=2,
            role="user", content_text="Hello",
        )

        summary = render_summary_text(
            [summary_msg], [compacted], user_id=user.id
        )
        assert "Earlier summary" in summary
        assert "Hello" in summary


def test_render_summary_text_hidden_count() -> None:
    with _db_session() as db:
        user = _make_user(db)
        thread = _make_thread(db, user.id)

        msgs = []
        for i in range(SUMMARY_LINE_LIMIT + 5):
            msgs.append(
                _add_message(
                    db, thread_id=thread.id, sequence_id=i + 1,
                    role="user", content_text=f"msg {i}",
                )
            )

        summary = render_summary_text([], msgs, user_id=user.id)
        assert "additional earlier messages were compacted" in summary


# --------------------------------------------------------------------------- #
# compact_thread_context
# --------------------------------------------------------------------------- #


def test_compact_thread_context_no_messages() -> None:
    with _db_session() as db:
        user = _make_user(db)
        thread = _make_thread(db, user.id)
        result = compact_thread_context(
            db, thread=thread, run_id=None,
            trigger_token_limit=100, keep_last_messages=4,
        )
        assert result is None


def test_compact_thread_context_under_limit() -> None:
    with _db_session() as db:
        user = _make_user(db)
        thread = _make_thread(db, user.id)

        for i in range(3):
            _add_message(
                db, thread_id=thread.id, sequence_id=i + 1,
                role="user", content_text="short",
                token_estimate=5,
            )

        # 3 msgs * 5 tokens = 15, well under limit of 10000
        result = compact_thread_context(
            db, thread=thread, run_id=None,
            trigger_token_limit=10000, keep_last_messages=2,
        )
        assert result is None


def test_compact_thread_context_triggers_compaction() -> None:
    with _db_session() as db:
        user = _make_user(db)
        thread = _make_thread(db, user.id)

        # Create messages that exceed the token limit
        num_messages = 10
        for i in range(num_messages):
            _add_message(
                db, thread_id=thread.id, sequence_id=i + 1,
                role="user" if i % 2 == 0 else "assistant",
                content_text=f"Message content number {i} " * 20,
                token_estimate=200,
            )

        # Update thread sequence counter so reserve_message_sequences works
        thread.next_message_sequence = num_messages + 1
        db.add(thread)
        db.commit()

        # Total tokens = 10 * 200 = 2000, trigger limit = 500
        result = compact_thread_context(
            db, thread=thread, run_id=None,
            trigger_token_limit=500, keep_last_messages=2,
        )
        assert result is not None
        assert isinstance(result, CompactionResult)
        assert result.compacted_message_count > 0
        assert result.kept_message_count == 2
        assert result.estimated_tokens_after < result.estimated_tokens_before


def test_compact_thread_context_too_few_messages() -> None:
    """If there are fewer messages than keep_last, no compaction occurs."""
    with _db_session() as db:
        user = _make_user(db)
        thread = _make_thread(db, user.id)

        for i in range(3):
            _add_message(
                db, thread_id=thread.id, sequence_id=i + 1,
                role="user", content_text="short",
                token_estimate=200,
            )

        result = compact_thread_context(
            db, thread=thread, run_id=None,
            trigger_token_limit=100, keep_last_messages=5,
        )
        assert result is None


def test_compact_thread_context_reserved_prompt_tokens() -> None:
    """Reserved prompt tokens reduce the effective trigger limit."""
    with _db_session() as db:
        user = _make_user(db)
        thread = _make_thread(db, user.id)

        num_messages = 10
        for i in range(num_messages):
            _add_message(
                db, thread_id=thread.id, sequence_id=i + 1,
                role="user" if i % 2 == 0 else "assistant",
                content_text=f"Msg {i} " * 20,
                token_estimate=100,
            )

        # Update thread sequence counter so reserve_message_sequences works
        thread.next_message_sequence = num_messages + 1
        db.add(thread)
        db.commit()

        # 10*100=1000 tokens, trigger=1200 but reserved=500 → effective=700
        result = compact_thread_context(
            db, thread=thread, run_id=None,
            trigger_token_limit=1200, keep_last_messages=2,
            reserved_prompt_tokens=500,
        )
        assert result is not None
        assert result.reserved_prompt_tokens == 500
        assert result.effective_trigger_token_limit == 700
