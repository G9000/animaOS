from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from anima_server.db.base import Base
from anima_server.models import User
from anima_server.services.agent import invalidate_agent_runtime_cache, run_agent
from anima_server.services.agent.consolidation import (
    consolidate_turn_memory,
    drain_background_memory_tasks,
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


def test_consolidate_turn_memory_writes_daily_log_and_user_memory(
    monkeypatch,
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "anima-data"
    monkeypatch.setattr("anima_server.config.settings.data_dir", data_dir)

    result = consolidate_turn_memory(
        user_id=7,
        user_message=(
            "I love green tea. I work as a product designer. "
            "My current focus is finishing the runtime migration."
        ),
        assistant_response="I hear you. Let's keep the migration tight.",
        now=datetime(2026, 3, 14, 10, 30, tzinfo=UTC),
    )

    daily_log = data_dir / "users" / "7" / "memory" / "daily" / "2026-03-14.md"
    facts = data_dir / "users" / "7" / "memory" / "user" / "facts.md"
    preferences = data_dir / "users" / "7" / "memory" / "user" / "preferences.md"
    current_focus = (
        data_dir / "users" / "7" / "memory" / "user" / "current-focus.md"
    )

    assert result.daily_log_path is not None
    assert "### User" in daily_log.read_text(encoding="utf-8")
    assert "- Works as a product designer" in facts.read_text(encoding="utf-8")
    assert "- Likes green tea" in preferences.read_text(encoding="utf-8")
    assert "- [ ] finishing the runtime migration" in current_focus.read_text(
        encoding="utf-8"
    )


def test_consolidate_turn_memory_deduplicates_bullet_memory(
    monkeypatch,
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "anima-data"
    monkeypatch.setattr("anima_server.config.settings.data_dir", data_dir)

    first = consolidate_turn_memory(
        user_id=7,
        user_message="I love green tea.",
        assistant_response="Noted.",
        now=datetime(2026, 3, 14, 10, 30, tzinfo=UTC),
    )
    second = consolidate_turn_memory(
        user_id=7,
        user_message="I love green tea.",
        assistant_response="Still noted.",
        now=datetime(2026, 3, 14, 10, 31, tzinfo=UTC),
    )

    preferences = data_dir / "users" / "7" / "memory" / "user" / "preferences.md"

    assert first.preferences_added == ["Likes green tea"]
    assert second.preferences_added == []
    assert preferences.read_text(encoding="utf-8").count("Likes green tea") == 1


@pytest.mark.asyncio
async def test_run_agent_schedules_background_memory_consolidation(
    monkeypatch,
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "anima-data"
    monkeypatch.setattr("anima_server.config.settings.data_dir", data_dir)
    invalidate_agent_runtime_cache()

    try:
        with _db_session() as session:
            user = User(
                username="background-memory",
                password_hash="not-used",
                display_name="Background Memory",
            )
            session.add(user)
            session.commit()

            result = await run_agent(
                "I prefer short walks. My current focus is finishing the memory pipeline.",
                user.id,
                session,
            )
            await drain_background_memory_tasks()
            user_id = user.id
    finally:
        invalidate_agent_runtime_cache()

    current_focus = (
        data_dir / "users" / str(user_id) / "memory" / "user" / "current-focus.md"
    )
    preferences = data_dir / "users" / str(user_id) / "memory" / "user" / "preferences.md"
    daily_log_dir = data_dir / "users" / str(user_id) / "memory" / "daily"

    assert "turn 1" in result.response
    assert "- [ ] finishing the memory pipeline" in current_focus.read_text(
        encoding="utf-8"
    )
    assert "- Prefers short walks" in preferences.read_text(encoding="utf-8")
    assert any(path.suffix == ".md" for path in daily_log_dir.iterdir())
