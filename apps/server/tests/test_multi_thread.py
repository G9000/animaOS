"""Tests for multi-thread persistence helpers."""

from __future__ import annotations

import pytest
from anima_server.models.runtime import RuntimeMessage, RuntimeThread
from anima_server.services.agent.persistence import (
    append_message,
    create_run,
    create_thread,
    get_or_create_thread,
    list_threads,
    load_thread_history,
)
from conftest_runtime import runtime_db_session
from sqlalchemy.orm import Session

_db_session = runtime_db_session

_COUNTER = 0


def _uid() -> int:
    global _COUNTER
    _COUNTER += 1
    return _COUNTER


@pytest.fixture()
def db() -> Session:  # type: ignore[misc]
    """Provide a runtime session backed by in-memory SQLite for reactivation tests."""
    from anima_server.db.runtime_base import RuntimeBase
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_connection, connection_record) -> None:  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode = WAL")
        cursor.execute("PRAGMA busy_timeout = 30000")
        cursor.close()

    RuntimeBase.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        engine.dispose()


def test_load_thread_history_excludes_archived_history() -> None:
    with _db_session() as db:
        uid = _uid()
        thread = get_or_create_thread(db, uid)
        run = create_run(
            db,
            thread_id=thread.id,
            user_id=uid,
            provider="test",
            model="m",
            mode="blocking",
        )
        db.flush()

        # Real message (visible to agent)
        append_message(
            db,
            thread=thread,
            run_id=run.id,
            step_id=None,
            sequence_id=1,
            role="user",
            content_text="hello",
        )

        # Archived history message (should NOT appear in agent context)
        append_message(
            db,
            thread=thread,
            run_id=run.id,
            step_id=None,
            sequence_id=2,
            role="user",
            content_text="old message from archive",
            is_archived_history=True,
        )
        db.flush()

        history = load_thread_history(db, thread.id)
        contents = [m.content for m in history]
        assert "hello" in contents
        assert "old message from archive" not in contents


def test_list_threads_sorted_by_last_message() -> None:
    from datetime import UTC, datetime

    with _db_session() as db:
        uid = _uid()
        t1 = RuntimeThread(
            user_id=uid,
            status="active",
            last_message_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        t2 = RuntimeThread(
            user_id=uid,
            status="closed",
            last_message_at=datetime(2026, 3, 1, tzinfo=UTC),
        )
        db.add_all([t1, t2])
        db.flush()

        threads = list_threads(db, user_id=uid)
        assert len(threads) == 2
        assert threads[0].id == t2.id  # most recent first


def test_list_threads_excludes_other_users() -> None:
    with _db_session() as db:
        uid_a = _uid()
        uid_b = _uid()
        t = RuntimeThread(user_id=uid_a, status="active")
        db.add(t)
        db.flush()

        threads = list_threads(db, user_id=uid_b)
        assert len(threads) == 0


def test_create_thread_returns_active_thread() -> None:
    with _db_session() as db:
        uid = _uid()
        thread = create_thread(db, uid)
        assert thread.id is not None
        assert thread.user_id == uid
        assert thread.status == "active"


def test_reactivate_thread_with_pg_messages(db: Session) -> None:
    """If PG messages still exist (within TTL), reactivation sets status=active only."""
    from anima_server.services.agent.thread_manager import reactivate_thread_if_needed
    from sqlalchemy import select

    uid = _uid()
    thread = RuntimeThread(user_id=uid, status="closed", is_archived=True)
    db.add(thread)
    db.flush()

    # Message still in PG (within TTL window)
    msg = RuntimeMessage(
        thread_id=thread.id,
        user_id=uid,
        sequence_id=1,
        role="user",
        content_text="old message still in PG",
        is_in_context=True,
        is_archived_history=False,
    )
    db.add(msg)
    db.flush()

    reactivate_thread_if_needed(db, thread=thread, user_id=uid, transcripts_dir=None, dek=None)

    assert thread.status == "active"
    assert thread.is_archived is False
    # No extra messages were inserted (PG messages suffice)
    msgs = db.scalars(
        select(RuntimeMessage).where(RuntimeMessage.thread_id == thread.id)
    ).all()
    assert len(msgs) == 1


def test_maybe_set_thread_title() -> None:
    from anima_server.services.agent.thread_manager import maybe_set_thread_title

    # Sets title from short message
    thread = RuntimeThread(user_id=1, status="active", title=None)
    maybe_set_thread_title(thread, "Hello world")
    assert thread.title == "Hello world"

    # Does not overwrite existing title
    maybe_set_thread_title(thread, "New message")
    assert thread.title == "Hello world"

    # Truncates long message
    thread2 = RuntimeThread(user_id=1, status="active", title=None)
    maybe_set_thread_title(thread2, "A" * 100)
    assert thread2.title == "A" * 60 + "..."


def test_display_messages_deduplicates_send_message(db: Session) -> None:
    """Assistant tool-call wrapper + send_message tool row should produce one message, not two."""
    from anima_server.services.agent.thread_manager import get_thread_messages_for_display

    uid = _uid()
    thread = RuntimeThread(user_id=uid, status="active")
    db.add(thread)
    db.flush()

    # User message
    db.add(RuntimeMessage(
        thread_id=thread.id, user_id=uid, sequence_id=1,
        role="user", content_text="hello",
    ))
    # Assistant tool-call wrapper (should be filtered out)
    db.add(RuntimeMessage(
        thread_id=thread.id, user_id=uid, sequence_id=2,
        role="assistant", content_text="Hi there!",
        content_json={"tool_calls": [{"id": "synthetic-send_message-0-0", "name": "send_message", "arguments": {"message": "Hi there!"}}]},
    ))
    # send_message tool result (should be kept, displayed as "assistant")
    db.add(RuntimeMessage(
        thread_id=thread.id, user_id=uid, sequence_id=3,
        role="tool", content_text="Hi there!",
        tool_name="send_message", tool_call_id="synthetic-send_message-0-0",
    ))
    # Internal tool result (should be filtered out)
    db.add(RuntimeMessage(
        thread_id=thread.id, user_id=uid, sequence_id=4,
        role="tool", content_text="memory saved",
        tool_name="save_to_memory", tool_call_id="call_123",
    ))
    db.flush()

    messages = get_thread_messages_for_display(
        db, thread=thread, user_id=uid, transcripts_dir=None, dek=None,
    )

    assert len(messages) == 2  # user + one assistant, not three
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "hello"
    assert messages[1]["role"] == "assistant"
    assert messages[1]["content"] == "Hi there!"


def test_reactivate_thread_from_jsonl(db: Session, tmp_path) -> None:
    """If PG messages are gone, rehydrate from JSONL and insert summary."""
    import json
    from anima_server.services.agent.thread_manager import reactivate_thread_if_needed
    from sqlalchemy import select

    uid = _uid()
    thread = RuntimeThread(user_id=uid, status="closed", is_archived=True)
    db.add(thread)
    db.flush()

    # Write JSONL and meta sidecar
    transcripts_dir = tmp_path / "transcripts"
    transcripts_dir.mkdir()
    jsonl_path = transcripts_dir / f"2026-01-01_thread-{thread.id}.jsonl"
    meta_path = transcripts_dir / f"2026-01-01_thread-{thread.id}.meta.json"

    messages = [
        {"role": "user", "content": "old user message", "ts": "2026-01-01T00:00:00Z", "seq": 1},
        {"role": "assistant", "content": "old reply", "ts": "2026-01-01T00:01:00Z", "seq": 2},
    ]
    jsonl_path.write_text(
        "\n".join(json.dumps(m) for m in messages), encoding="utf-8"
    )
    meta_path.write_text(
        json.dumps({"thread_id": thread.id, "user_id": uid, "summary": "Talked about old stuff"}),
        encoding="utf-8",
    )

    reactivate_thread_if_needed(
        db, thread=thread, user_id=uid, transcripts_dir=transcripts_dir, dek=None
    )

    assert thread.status == "active"
    assert thread.is_archived is False

    all_msgs = db.scalars(
        select(RuntimeMessage)
        .where(RuntimeMessage.thread_id == thread.id)
        .order_by(RuntimeMessage.sequence_id)
    ).all()

    # 2 archived history + 1 summary system message
    assert len(all_msgs) == 3
    archived = [m for m in all_msgs if m.is_archived_history]
    summary_msg = [m for m in all_msgs if not m.is_archived_history]
    assert len(archived) == 2
    assert len(summary_msg) == 1
    assert summary_msg[0].role == "system"
    assert "Talked about old stuff" in (summary_msg[0].content_text or "")
