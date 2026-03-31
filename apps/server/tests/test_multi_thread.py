"""Tests for multi-thread persistence helpers."""

from __future__ import annotations

from anima_server.models.runtime import RuntimeThread
from anima_server.services.agent.persistence import (
    append_message,
    create_run,
    create_thread,
    get_or_create_thread,
    list_threads,
    load_thread_history,
)
from conftest_runtime import runtime_db_session

_db_session = runtime_db_session

_COUNTER = 0


def _uid() -> int:
    global _COUNTER
    _COUNTER += 1
    return _COUNTER


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
