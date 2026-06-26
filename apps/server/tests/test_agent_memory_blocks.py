from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from anima_server.db.base import Base
from anima_server.models import AgentThread, MemoryItem, User
from anima_server.models.runtime import RuntimeMessage, RuntimeThread
from anima_server.services.agent.memory_blocks import build_runtime_memory_blocks
from anima_server.services.agent.persistence import load_thread_history
from conftest_runtime import runtime_db_session
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


def test_build_runtime_memory_blocks_includes_human_and_thread_summary() -> None:
    with _db_session() as session, runtime_db_session() as rt_session:
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

        # Insert summary into runtime DB (P2: summaries live in PG)
        rt_thread = RuntimeThread(user_id=user.id, status="active")
        rt_session.add(rt_thread)
        rt_session.flush()

        rt_session.add(
            RuntimeMessage(
                thread_id=rt_thread.id,
                user_id=user.id,
                sequence_id=1,
                role="summary",
                content_text="Conversation summary:\n- User likes green tea.",
                is_in_context=True,
            )
        )
        rt_session.commit()

        blocks = build_runtime_memory_blocks(
            session,
            user_id=user.id,
            thread_id=rt_thread.id,
            runtime_db=rt_session,
        )

    labels = [block.label for block in blocks]
    assert "human" in labels
    assert "thread_summary" in labels
    human_block = next(b for b in blocks if b.label == "human")
    summary_block = next(b for b in blocks if b.label == "thread_summary")
    assert "Alice" in human_block.value
    assert "Age: 30" in human_block.value
    assert "User likes green tea." in summary_block.value


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

        session.add_all(
            [
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
            ]
        )
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

    labels = [block.label for block in blocks]
    assert "human" in labels


def test_load_thread_history_excludes_summary_messages() -> None:
    with runtime_db_session() as session:
        user_id = 99

        thread = RuntimeThread(user_id=user_id, status="active")
        session.add(thread)
        session.flush()

        session.add_all(
            [
                RuntimeMessage(
                    thread_id=thread.id,
                    user_id=user_id,
                    sequence_id=1,
                    role="summary",
                    content_text="Conversation summary:\n- Earlier context.",
                    is_in_context=True,
                ),
                RuntimeMessage(
                    thread_id=thread.id,
                    user_id=user_id,
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


def test_semantic_block_items_excluded_from_category_blocks() -> None:
    """Items rendered in relevant_memories must not be duplicated in the
    facts/preferences category blocks."""
    with _db_session() as session:
        user = User(
            username="dedup-blocks",
            password_hash="not-used",
            display_name="Dedup Blocks",
        )
        session.add(user)
        session.flush()

        thread = AgentThread(user_id=user.id, status="active")
        session.add(thread)
        session.flush()

        shown = MemoryItem(
            user_id=user.id,
            content="Works as a software engineer",
            category="fact",
            importance=4,
            source="extraction",
        )
        other = MemoryItem(
            user_id=user.id,
            content="Has a dog named Biscuit",
            category="fact",
            importance=3,
            source="extraction",
        )
        session.add_all([shown, other])
        session.commit()

        blocks = build_runtime_memory_blocks(
            session,
            user_id=user.id,
            thread_id=thread.id,
            semantic_results=[
                (shown.id, "Works as a software engineer", 0.92)],
        )

    semantic_block = next(b for b in blocks if b.label == "relevant_memories")
    facts_block = next(b for b in blocks if b.label == "facts")
    assert "software engineer" in semantic_block.value
    assert "software engineer" not in facts_block.value
    assert "Biscuit" in facts_block.value


def test_static_and_turn_block_partition() -> None:
    """Static blocks hold slow-changing identity; per-turn blocks hold
    query-ranked and volatile state. Their composition matches the full
    builder."""
    from anima_server.services.agent.memory_blocks import (
        build_static_memory_blocks,
        build_turn_memory_blocks,
    )

    with _db_session() as session:
        user = User(
            username="partition",
            password_hash="not-used",
            display_name="Partition",
        )
        session.add(user)
        session.flush()
        thread = AgentThread(user_id=user.id, status="active")
        session.add(thread)
        session.flush()
        session.add(
            MemoryItem(
                user_id=user.id,
                content="Works as a software engineer",
                category="fact",
                importance=4,
                source="extraction",
            )
        )
        session.commit()

        static = build_static_memory_blocks(session, user_id=user.id)
        turn = build_turn_memory_blocks(
            session, user_id=user.id, thread_id=thread.id)
        full = build_runtime_memory_blocks(
            session, user_id=user.id, thread_id=thread.id)

    static_labels = {b.label for b in static}
    turn_labels = {b.label for b in turn}
    assert "human" in static_labels
    assert "facts" not in static_labels
    assert "facts" in turn_labels
    assert "human" not in turn_labels
    # No overlap, and composition covers the full set.
    assert not (static_labels & turn_labels)
    assert {b.label for b in full} == static_labels | turn_labels

    # Safety invariant: only blocks whose writers invalidate the companion
    # cache may be cached as static.  Blocks written by background tasks
    # (self-model, emotional patterns, current focus, episodes) MUST stay
    # per-turn or they go stale — this guards against that regression.
    SAFE_STATIC = {
        "soul", "persona", "human", "world_context", "user_directive",
    }
    assert static_labels <= SAFE_STATIC, (
        f"unsafe blocks cached as static: {static_labels - SAFE_STATIC}"
    )
