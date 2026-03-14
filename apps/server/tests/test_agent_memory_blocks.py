from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from anima_server.db.base import Base
from anima_server.models import AgentMessage, AgentThread, MemoryItem, User
from anima_server.services.agent.memory_blocks import build_runtime_memory_blocks
from anima_server.services.agent.persistence import load_thread_history


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


def test_build_runtime_memory_blocks_includes_human_and_thread_summary() -> None:
    with _db_session() as session:
        user = User(
            username="alice-memory",
            password_hash="not-used",
            display_name="Alice",
            age=30,
            birthday="1995-03-15",
        )
        session.add(user)
        session.flush()

        thread = AgentThread(user_id=user.id, status="active")
        session.add(thread)
        session.flush()

        session.add(
            AgentMessage(
                thread_id=thread.id,
                sequence_id=1,
                role="summary",
                content_text="Conversation summary:\n- User likes green tea.",
                is_in_context=True,
            )
        )
        session.commit()

        blocks = build_runtime_memory_blocks(
            session,
            user_id=user.id,
            thread_id=thread.id,
        )

    assert [block.label for block in blocks] == ["human", "thread_summary"]
    assert "Display name: Alice" in blocks[0].value
    assert "Age: 30" in blocks[0].value
    assert "User likes green tea." in blocks[1].value


def test_build_runtime_memory_blocks_includes_facts_and_preferences() -> None:
    with _db_session() as session:
        user = User(
            username="facts-prefs",
            password_hash="not-used",
            display_name="Facts Prefs",
        )
        session.add(user)
        session.flush()

        thread = AgentThread(user_id=user.id, status="active")
        session.add(thread)
        session.flush()

        session.add_all([
            MemoryItem(
                user_id=user.id,
                content="Works as a software engineer",
                category="fact",
                importance=4,
                source="extraction",
            ),
            MemoryItem(
                user_id=user.id,
                content="Likes green tea",
                category="preference",
                importance=3,
                source="extraction",
            ),
        ])
        session.commit()

        blocks = build_runtime_memory_blocks(
            session,
            user_id=user.id,
            thread_id=thread.id,
        )

    labels = [block.label for block in blocks]
    assert "facts" in labels
    assert "preferences" in labels
    facts_block = next(b for b in blocks if b.label == "facts")
    prefs_block = next(b for b in blocks if b.label == "preferences")
    assert "software engineer" in facts_block.value
    assert "green tea" in prefs_block.value


def test_build_runtime_memory_blocks_includes_current_focus_from_db() -> None:
    with _db_session() as session:
        user = User(
            username="focus-memory",
            password_hash="not-used",
            display_name="Focus Memory",
        )
        session.add(user)
        session.flush()

        thread = AgentThread(user_id=user.id, status="active")
        session.add(thread)
        session.flush()

        session.add(
            MemoryItem(
                user_id=user.id,
                content="Finish the loop-runtime migration",
                category="focus",
                importance=4,
                source="user",
            )
        )
        session.commit()

        blocks = build_runtime_memory_blocks(
            session,
            user_id=user.id,
            thread_id=thread.id,
        )

    labels = [block.label for block in blocks]
    assert "current_focus" in labels
    focus_block = next(b for b in blocks if b.label == "current_focus")
    assert "loop-runtime migration" in focus_block.value


def test_build_runtime_memory_blocks_omits_empty_focus() -> None:
    with _db_session() as session:
        user = User(
            username="no-focus",
            password_hash="not-used",
            display_name="No Focus",
        )
        session.add(user)
        session.flush()

        thread = AgentThread(user_id=user.id, status="active")
        session.add(thread)
        session.flush()
        session.commit()

        blocks = build_runtime_memory_blocks(
            session,
            user_id=user.id,
            thread_id=thread.id,
        )

    assert [block.label for block in blocks] == ["human"]


def test_load_thread_history_excludes_summary_messages() -> None:
    with _db_session() as session:
        user = User(
            username="history-filter",
            password_hash="not-used",
            display_name="History Filter",
        )
        session.add(user)
        session.flush()

        thread = AgentThread(user_id=user.id, status="active")
        session.add(thread)
        session.flush()

        session.add_all(
            [
                AgentMessage(
                    thread_id=thread.id,
                    sequence_id=1,
                    role="summary",
                    content_text="Conversation summary:\n- Earlier context.",
                    is_in_context=True,
                ),
                AgentMessage(
                    thread_id=thread.id,
                    sequence_id=2,
                    role="assistant",
                    content_text="Latest assistant message.",
                    is_in_context=True,
                ),
            ]
        )
        session.commit()

        history = load_thread_history(session, thread.id)

    assert [message.role for message in history] == ["assistant"]
    assert history[0].content == "Latest assistant message."
