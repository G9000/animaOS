from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from anima_server.db.base import Base
from anima_server.models import MemoryItem, User
from anima_server.services.agent.embedding_integrity import compute_embedding_checksum
from anima_server.services.agent.memory_store import (
    _retrieval_score,
    get_memory_items_scored,
    touch_memory_items,
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


def _make_user(db: Session) -> User:
    user = User(username="scorer", display_name="Scorer", password_hash="x")
    db.add(user)
    db.flush()
    return user


def test_retrieval_score_importance_matters() -> None:
    now = datetime.now(UTC)
    high = MemoryItem(
        user_id=1,
        content="important",
        category="fact",
        importance=5,
        source="user",
        reference_count=0,
        created_at=now,
        updated_at=now,
    )
    low = MemoryItem(
        user_id=1,
        content="trivial",
        category="fact",
        importance=1,
        source="user",
        reference_count=0,
        created_at=now,
        updated_at=now,
    )
    assert _retrieval_score(high, now) > _retrieval_score(low, now)


def test_retrieval_score_recency_decays() -> None:
    now = datetime.now(UTC)
    recent = MemoryItem(
        user_id=1,
        content="new",
        category="fact",
        importance=3,
        source="user",
        reference_count=0,
        created_at=now,
        updated_at=now,
    )
    old = MemoryItem(
        user_id=1,
        content="old",
        category="fact",
        importance=3,
        source="user",
        reference_count=0,
        created_at=now - timedelta(days=60),
        updated_at=now,
    )
    assert _retrieval_score(recent, now) > _retrieval_score(old, now)


def test_retrieval_score_access_frequency_helps() -> None:
    now = datetime.now(UTC)
    accessed = MemoryItem(
        user_id=1,
        content="used",
        category="fact",
        importance=3,
        source="user",
        reference_count=10,
        last_referenced_at=now - timedelta(hours=1),
        created_at=now - timedelta(days=30),
        updated_at=now,
    )
    unused = MemoryItem(
        user_id=1,
        content="unused",
        category="fact",
        importance=3,
        source="user",
        reference_count=0,
        created_at=now - timedelta(days=30),
        updated_at=now,
    )
    assert _retrieval_score(accessed, now) > _retrieval_score(unused, now)


def test_scored_retrieval_returns_items_in_score_order() -> None:
    with _db_session() as db:
        user = _make_user(db)
        now = datetime.now(UTC)

        # Low importance, old
        item_low = MemoryItem(
            user_id=user.id,
            content="casual mention",
            category="fact",
            importance=1,
            source="extraction",
            reference_count=0,
            created_at=now - timedelta(days=90),
            updated_at=now,
        )
        # High importance, recent
        item_high = MemoryItem(
            user_id=user.id,
            content="core identity",
            category="fact",
            importance=5,
            source="user",
            reference_count=5,
            last_referenced_at=now - timedelta(hours=1),
            created_at=now - timedelta(days=1),
            updated_at=now,
        )
        db.add_all([item_low, item_high])
        db.flush()

        scored = get_memory_items_scored(db, user_id=user.id, category="fact", limit=10)
        assert len(scored) == 2
        assert scored[0].id == item_high.id
        assert scored[1].id == item_low.id


def test_touch_memory_items_updates_tracking() -> None:
    with _db_session() as db:
        user = _make_user(db)
        now = datetime.now(UTC)

        item = MemoryItem(
            user_id=user.id,
            content="test fact",
            category="fact",
            importance=3,
            source="user",
            reference_count=0,
            created_at=now,
            updated_at=now,
        )
        db.add(item)
        db.flush()

        assert item.reference_count == 0
        assert item.last_referenced_at is None

        touch_memory_items(db, [item])

        assert item.reference_count == 1
        assert item.last_referenced_at is not None

        touch_memory_items(db, [item])
        assert item.reference_count == 2


def test_scored_retrieval_skips_corrupted_embedding_boost() -> None:
    with _db_session() as db:
        user = _make_user(db)
        now = datetime.now(UTC)
        aligned_embedding = [1.0, 0.0]

        good = MemoryItem(
            user_id=user.id,
            content="validated semantic match",
            category="fact",
            importance=3,
            source="user",
            reference_count=0,
            embedding_json=aligned_embedding,
            embedding_checksum=compute_embedding_checksum(aligned_embedding),
            created_at=now,
            updated_at=now,
        )
        corrupt = MemoryItem(
            user_id=user.id,
            content="corrupted semantic match",
            category="fact",
            importance=3,
            source="user",
            reference_count=0,
            embedding_json=aligned_embedding,
            embedding_checksum="corrupt-checksum",
            created_at=now,
            updated_at=now,
        )
        db.add_all([good, corrupt])
        db.flush()

        scored = get_memory_items_scored(
            db,
            user_id=user.id,
            category="fact",
            limit=2,
            query_embedding=[1.0, 0.0],
        )

        assert [item.id for item in scored] == [good.id, corrupt.id]


def test_cosine_similarity() -> None:
    from anima_server.services.agent.embeddings import cosine_similarity

    # Identical vectors
    assert cosine_similarity([1, 0, 0], [1, 0, 0]) == 1.0
    # Orthogonal vectors
    assert abs(cosine_similarity([1, 0, 0], [0, 1, 0])) < 0.001
    # Opposite vectors
    assert abs(cosine_similarity([1, 0], [-1, 0]) + 1.0) < 0.001
    # Empty
    assert cosine_similarity([], []) == 0.0
    # Mismatched length
    assert cosine_similarity([1, 2], [1, 2, 3]) == 0.0
