from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Generator

from anima_server.db.base import Base
from anima_server.models import MemoryItem, User
from anima_server.services import anima_core_retrieval as retrieval_module
from anima_server.services.agent.memory_store import rebuild_memory_retrieval_index
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


def test_memory_rebuild_clears_dirty_manifest(tmp_path: Path) -> None:
    with _db_session() as db:
        user = User(username="retrieval_tester", display_name="Tester", password_hash="x")
        db.add(user)
        db.flush()

        item = MemoryItem(
            user_id=user.id,
            content="user likes pour over coffee",
            category="preference",
            importance=4,
            source="test",
            created_at=datetime.now(UTC),
        )
        db.add(item)
        db.commit()

        root = tmp_path / "indices"
        retrieval_module.memory_index_upsert(
            root=root,
            record_id=item.id,
            user_id=user.id,
            text=item.content,
            source_type="memory_item",
            category=item.category,
            importance=item.importance,
            created_at=int(item.created_at.timestamp()),
        )
        retrieval_module.mark_retrieval_index_dirty(root=root, family="memory")

        rebuilt = rebuild_memory_retrieval_index(db, user_id=user.id, root=root)

        assert rebuilt == 1
        assert retrieval_module.is_retrieval_family_dirty(root=root, family="memory") is False
