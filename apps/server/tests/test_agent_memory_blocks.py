from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from anima_server.db.base import Base
from anima_server.models import AgentMessage, AgentThread, User
from anima_server.services.agent.memory_blocks import build_runtime_memory_blocks
from anima_server.services.agent.persistence import load_thread_history
from anima_server.services.storage import get_user_data_dir


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


def test_build_runtime_memory_blocks_includes_human_and_thread_summary(
    monkeypatch,
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "anima-data"
    monkeypatch.setattr("anima_server.config.settings.data_dir", data_dir)

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


def _write_current_focus(user_id: int, content: str) -> None:
    current_focus_path = (
        get_user_data_dir(user_id) / "memory" / "user" / "current-focus.md"
    )
    current_focus_path.parent.mkdir(parents=True, exist_ok=True)
    current_focus_path.write_text(content, encoding="utf-8")


def test_build_runtime_memory_blocks_includes_current_focus_from_local_memory(
    monkeypatch,
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "anima-data"
    monkeypatch.setattr("anima_server.config.settings.data_dir", data_dir)

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
        session.commit()

        _write_current_focus(
            user.id,
            "\n".join(
                [
                    "---",
                    "category: goal",
                    "updated: 2026-03-14T12:00:00Z",
                    "---",
                    "",
                    "# Current Focus",
                    "",
                    "- [ ] Finish the loop-runtime migration",
                    "",
                    "## Note",
                    "Keep memory ownership inside ANIMA.",
                ]
            ),
        )

        blocks = build_runtime_memory_blocks(
            session,
            user_id=user.id,
            thread_id=thread.id,
        )

    assert [block.label for block in blocks] == ["human", "current_focus"]
    assert "# Current Focus" in blocks[1].value
    assert "Finish the loop-runtime migration" in blocks[1].value
    assert "Keep memory ownership inside ANIMA." in blocks[1].value
    assert "category: goal" not in blocks[1].value


def test_build_runtime_memory_blocks_omits_placeholder_current_focus(
    monkeypatch,
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "anima-data"
    monkeypatch.setattr("anima_server.config.settings.data_dir", data_dir)

    with _db_session() as session:
        user = User(
            username="placeholder-focus",
            password_hash="not-used",
            display_name="Placeholder Focus",
        )
        session.add(user)
        session.flush()

        thread = AgentThread(user_id=user.id, status="active")
        session.add(thread)
        session.flush()
        session.commit()

        _write_current_focus(
            user.id,
            "# Current Focus\n\n- [ ] Define your current focus\n",
        )

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
