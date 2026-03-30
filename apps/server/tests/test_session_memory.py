from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from anima_server.db.runtime_base import RuntimeBase
from anima_server.models.runtime import RuntimeThread
from anima_server.models.runtime_memory import MemoryCandidate, RuntimeSessionNote
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
def _runtime_db_session() -> Generator[Session, None, None]:
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
    RuntimeBase.metadata.create_all(bind=engine)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        RuntimeBase.metadata.drop_all(bind=engine)
        engine.dispose()


def _setup(db: Session) -> tuple[int, RuntimeThread]:
    """Create a RuntimeThread and return (user_id, thread)."""
    user_id = 1
    thread = RuntimeThread(user_id=user_id, status="active")
    db.add(thread)
    db.flush()
    return user_id, thread


def test_write_and_read_session_notes() -> None:
    with _runtime_db_session() as db:
        user_id, thread = _setup(db)

        write_session_note(
            db,
            thread_id=thread.id,
            user_id=user_id,
            key="user_mood",
            value="seems happy today",
            note_type="emotion",
        )
        write_session_note(
            db,
            thread_id=thread.id,
            user_id=user_id,
            key="topic",
            value="discussing Python async",
            note_type="context",
        )

        notes = get_session_notes(db, thread_id=thread.id)
        assert len(notes) == 2
        keys = {n.key for n in notes}
        assert keys == {"user_mood", "topic"}


def test_update_existing_note_by_key() -> None:
    with _runtime_db_session() as db:
        user_id, thread = _setup(db)

        write_session_note(
            db,
            thread_id=thread.id,
            user_id=user_id,
            key="mood",
            value="neutral",
        )
        write_session_note(
            db,
            thread_id=thread.id,
            user_id=user_id,
            key="mood",
            value="excited about project",
        )

        notes = get_session_notes(db, thread_id=thread.id)
        assert len(notes) == 1
        assert notes[0].value == "excited about project"


def test_remove_session_note() -> None:
    with _runtime_db_session() as db:
        user_id, thread = _setup(db)

        write_session_note(
            db,
            thread_id=thread.id,
            user_id=user_id,
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
    with _runtime_db_session() as db:
        user_id, thread = _setup(db)

        for i in range(5):
            write_session_note(
                db,
                thread_id=thread.id,
                user_id=user_id,
                key=f"note_{i}",
                value=f"value {i}",
            )

        count = clear_session_notes(db, thread_id=thread.id)
        assert count == 5
        assert len(get_session_notes(db, thread_id=thread.id)) == 0


def test_promote_session_note_to_memory() -> None:
    with _runtime_db_session() as db:
        user_id, thread = _setup(db)

        write_session_note(
            db,
            thread_id=thread.id,
            user_id=user_id,
            key="job",
            value="Works as a data scientist",
        )

        promoted = promote_session_note(
            db,
            thread_id=thread.id,
            user_id=user_id,
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
        assert all_notes[0].is_active is False

        # MemoryCandidate should have been created
        from sqlalchemy import select

        candidates = list(db.scalars(select(MemoryCandidate)).all())
        assert len(candidates) == 1
        assert candidates[0].content == "Works as a data scientist"
        assert candidates[0].category == "fact"


def test_render_session_memory_text() -> None:
    with _runtime_db_session() as db:
        user_id, thread = _setup(db)

        write_session_note(
            db,
            thread_id=thread.id,
            user_id=user_id,
            key="mood",
            value="calm",
            note_type="emotion",
        )
        write_session_note(
            db,
            thread_id=thread.id,
            user_id=user_id,
            key="goal",
            value="plan the weekend",
            note_type="plan",
        )

        notes = get_session_notes(db, thread_id=thread.id)
        text = render_session_memory_text(notes)
        assert "[emotion] mood: calm" in text
        assert "[plan] goal: plan the weekend" in text


def test_max_notes_enforced() -> None:
    with _runtime_db_session() as db:
        user_id, thread = _setup(db)

        # Write more than the max (default 20)
        for i in range(25):
            write_session_note(
                db,
                thread_id=thread.id,
                user_id=user_id,
                key=f"note_{i:03d}",
                value=f"value {i}",
            )

        notes = get_session_notes(db, thread_id=thread.id)
        assert len(notes) <= 20
