from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from anima_server.db.base import Base
from anima_server.models import AgentThread, User
from anima_server.services.agent.session_memory import (
    clear_session_notes,
    get_session_notes,
    promote_session_note,
    remove_session_note,
    render_session_memory_text,
    write_session_note,
)
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


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


def _setup(db: Session) -> tuple[User, AgentThread]:
    user = User(username="session-test", display_name="Tester", password_hash="x")
    db.add(user)
    db.flush()

    thread = AgentThread(user_id=user.id, status="active")
    db.add(thread)
    db.flush()

    return user, thread


def test_write_and_read_session_notes() -> None:
    with _db_session() as db:
        user, thread = _setup(db)

        write_session_note(
            db,
            thread_id=thread.id,
            user_id=user.id,
            key="user_mood",
            value="seems happy today",
            note_type="emotion",
        )
        write_session_note(
            db,
            thread_id=thread.id,
            user_id=user.id,
            key="topic",
            value="discussing Python async",
            note_type="context",
        )

        notes = get_session_notes(db, thread_id=thread.id)
        assert len(notes) == 2
        keys = {n.key for n in notes}
        assert keys == {"user_mood", "topic"}


def test_update_existing_note_by_key() -> None:
    with _db_session() as db:
        user, thread = _setup(db)

        write_session_note(
            db,
            thread_id=thread.id,
            user_id=user.id,
            key="mood",
            value="neutral",
        )
        write_session_note(
            db,
            thread_id=thread.id,
            user_id=user.id,
            key="mood",
            value="excited about project",
        )

        notes = get_session_notes(db, thread_id=thread.id)
        assert len(notes) == 1
        assert notes[0].value == "excited about project"


def test_remove_session_note() -> None:
    with _db_session() as db:
        user, thread = _setup(db)

        write_session_note(
            db,
            thread_id=thread.id,
            user_id=user.id,
            key="temp",
            value="temporary note",
        )
        assert len(get_session_notes(db, thread_id=thread.id)) == 1

        removed = remove_session_note(db, thread_id=thread.id, key="temp")
        assert removed is True
        assert len(get_session_notes(db, thread_id=thread.id)) == 0

        # Removing nonexistent returns False
        assert remove_session_note(db, thread_id=thread.id, key="nope") is False


def test_clear_all_session_notes() -> None:
    with _db_session() as db:
        user, thread = _setup(db)

        for i in range(5):
            write_session_note(
                db,
                thread_id=thread.id,
                user_id=user.id,
                key=f"note_{i}",
                value=f"value {i}",
            )

        count = clear_session_notes(db, thread_id=thread.id)
        assert count == 5
        assert len(get_session_notes(db, thread_id=thread.id)) == 0


def test_promote_session_note_to_memory() -> None:
    with _db_session() as db:
        user, thread = _setup(db)

        write_session_note(
            db,
            thread_id=thread.id,
            user_id=user.id,
            key="job",
            value="Works as a data scientist",
        )

        # Legacy path (no runtime_db) — still creates MemoryItem directly
        promoted = promote_session_note(
            db,
            thread_id=thread.id,
            user_id=user.id,
            key="job",
            category="fact",
            importance=5,
        )
        assert promoted is True

        # Note should now be inactive
        notes = get_session_notes(db, thread_id=thread.id)
        assert len(notes) == 0

        all_notes = get_session_notes(db, thread_id=thread.id, active_only=False)
        assert len(all_notes) == 1
        assert all_notes[0].promoted_to_item_id is not None


def test_render_session_memory_text() -> None:
    with _db_session() as db:
        user, thread = _setup(db)

        write_session_note(
            db,
            thread_id=thread.id,
            user_id=user.id,
            key="mood",
            value="calm",
            note_type="emotion",
        )
        write_session_note(
            db,
            thread_id=thread.id,
            user_id=user.id,
            key="goal",
            value="plan the weekend",
            note_type="plan",
        )

        notes = get_session_notes(db, thread_id=thread.id)
        text = render_session_memory_text(notes)
        assert "[emotion] mood: calm" in text
        assert "[plan] goal: plan the weekend" in text


def test_max_notes_enforced() -> None:
    with _db_session() as db:
        user, thread = _setup(db)

        # Write more than the max (default 20)
        for i in range(25):
            write_session_note(
                db,
                thread_id=thread.id,
                user_id=user.id,
                key=f"note_{i:03d}",
                value=f"value {i}",
            )

        notes = get_session_notes(db, thread_id=thread.id)
        assert len(notes) <= 20
