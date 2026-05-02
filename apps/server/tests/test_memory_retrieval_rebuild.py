from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from anima_server.db.base import Base
from anima_server.models import MemoryItem, User
from anima_server.services import anima_core_retrieval as retrieval_module
from anima_server.services.agent.memory_store import (
    ensure_memory_retrieval_index_ready,
    memory_retrieval_index_needs_rebuild,
    rebuild_memory_retrieval_index,
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


def test_memory_rebuild_preserves_other_users_index_entries(tmp_path: Path) -> None:
    with _db_session() as db:
        user_one = User(username="retrieval_tester_1", display_name="Tester 1", password_hash="x")
        user_two = User(username="retrieval_tester_2", display_name="Tester 2", password_hash="x")
        db.add_all([user_one, user_two])
        db.flush()

        item_one = MemoryItem(
            user_id=user_one.id,
            content="user one likes pour over coffee",
            category="preference",
            importance=4,
            source="test",
            created_at=datetime.now(UTC),
        )
        item_two = MemoryItem(
            user_id=user_two.id,
            content="user two likes jasmine tea",
            category="preference",
            importance=4,
            source="test",
            created_at=datetime.now(UTC),
        )
        db.add_all([item_one, item_two])
        db.commit()

        root = tmp_path / "indices"
        retrieval_module.memory_index_upsert(
            root=root,
            record_id=item_one.id,
            user_id=user_one.id,
            text=item_one.content,
            source_type="memory_item",
            category=item_one.category,
            importance=item_one.importance,
            created_at=int(item_one.created_at.timestamp()),
        )
        retrieval_module.memory_index_upsert(
            root=root,
            record_id=item_two.id,
            user_id=user_two.id,
            text=item_two.content,
            source_type="memory_item",
            category=item_two.category,
            importance=item_two.importance,
            created_at=int(item_two.created_at.timestamp()),
        )
        retrieval_module.mark_retrieval_index_dirty(root=root, family="memory")

        rebuilt = rebuild_memory_retrieval_index(db, user_id=user_one.id, root=root)
        user_two_hits = retrieval_module.memory_index_search(
            root=root,
            user_id=user_two.id,
            query="jasmine",
            limit=5,
        )

        assert rebuilt == 1
        assert [hit["record_id"] for hit in user_two_hits] == [item_two.id]


def test_memory_ready_recovers_from_corrupt_manifest(tmp_path: Path) -> None:
    with _db_session() as db:
        user = User(username="retrieval_tester_corrupt", display_name="Tester", password_hash="x")
        db.add(user)
        db.flush()

        original = MemoryItem(
            user_id=user.id,
            content="user likes pour over coffee",
            category="preference",
            importance=4,
            source="test",
            created_at=datetime.now(UTC),
        )
        rebuilt_only = MemoryItem(
            user_id=user.id,
            content="user likes coffee from a tea shop",
            category="preference",
            importance=3,
            source="test",
            created_at=datetime.now(UTC),
        )
        db.add_all([original, rebuilt_only])
        db.flush()

        root = tmp_path / "indices"
        retrieval_module.memory_index_upsert(
            root=root,
            record_id=original.id,
            user_id=user.id,
            text=original.content,
            source_type="memory_item",
            category=original.category,
            importance=original.importance,
            created_at=int(original.created_at.timestamp()),
        )
        (root / "manifest.json").write_text("{ invalid", encoding="utf-8")

        assert memory_retrieval_index_needs_rebuild(root=root) is True

        ready = ensure_memory_retrieval_index_ready(db, user_id=user.id, root=root)
        hits = retrieval_module.memory_index_search(
            root=root,
            user_id=user.id,
            query="coffee",
            limit=10,
        )

        assert ready is True
        assert {hit["record_id"] for hit in hits} == {original.id, rebuilt_only.id}
