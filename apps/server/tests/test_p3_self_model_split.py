"""P3: Self-Model Split tests."""
from __future__ import annotations

import pytest
from anima_server.db.base import Base
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture()
def soul_db() -> Session:
    """In-memory SQLite session with soul tables."""
    from anima_server.models.user import User
    import anima_server.models.soul_consciousness  # noqa: F401

    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    user = User(id=1, username="test", display_name="Test", password_hash="x")
    session.add(user)
    session.commit()
    yield session
    session.close()
    engine.dispose()


class TestIdentityBlock:
    def test_create_and_read(self, soul_db: Session) -> None:
        from anima_server.models.soul_consciousness import IdentityBlock

        block = IdentityBlock(
            user_id=1,
            content="I am a companion.",
            version=1,
            updated_by="system",
        )
        soul_db.add(block)
        soul_db.flush()

        loaded = soul_db.get(IdentityBlock, block.id)
        assert loaded is not None
        assert loaded.content == "I am a companion."
        assert loaded.version == 1
        assert loaded.user_id == 1

    def test_unique_per_user(self, soul_db: Session) -> None:
        from sqlalchemy.exc import IntegrityError

        from anima_server.models.soul_consciousness import IdentityBlock

        soul_db.add(
            IdentityBlock(
                user_id=1,
                content="first",
                version=1,
                updated_by="system",
            )
        )
        soul_db.flush()
        soul_db.add(
            IdentityBlock(
                user_id=1,
                content="second",
                version=1,
                updated_by="system",
            )
        )
        with pytest.raises(IntegrityError):
            soul_db.flush()


class TestGrowthLogEntry:
    def test_create_and_list(self, soul_db: Session) -> None:
        from sqlalchemy import select

        from anima_server.models.soul_consciousness import GrowthLogEntry

        entries = [
            GrowthLogEntry(user_id=1, entry="Learned patience", source="sleep_time"),
            GrowthLogEntry(user_id=1, entry="Adapted tone", source="post_turn"),
        ]
        soul_db.add_all(entries)
        soul_db.flush()

        rows = soul_db.scalars(
            select(GrowthLogEntry)
            .where(GrowthLogEntry.user_id == 1)
            .order_by(GrowthLogEntry.id)
        ).all()
        assert len(rows) == 2
        assert rows[0].entry == "Learned patience"
        assert rows[1].source == "post_turn"

    def test_multiple_entries_per_user(self, soul_db: Session) -> None:
        """Unlike IdentityBlock, multiple entries per user are allowed."""
        from sqlalchemy import func, select

        from anima_server.models.soul_consciousness import GrowthLogEntry

        for i in range(5):
            soul_db.add(
                GrowthLogEntry(user_id=1, entry=f"Entry {i}", source="sleep_time")
            )
        soul_db.flush()

        count = soul_db.scalar(
            select(func.count())
            .select_from(GrowthLogEntry)
            .where(GrowthLogEntry.user_id == 1)
        )
        assert count == 5


class TestCoreEmotionalPattern:
    def test_create_and_read(self, soul_db: Session) -> None:
        from anima_server.models.soul_consciousness import CoreEmotionalPattern

        pattern = CoreEmotionalPattern(
            user_id=1,
            pattern="Tends toward frustration under deadline pressure",
            dominant_emotion="frustrated",
            trigger_context="work deadlines",
            frequency=6,
            confidence=0.8,
        )
        soul_db.add(pattern)
        soul_db.flush()

        loaded = soul_db.get(CoreEmotionalPattern, pattern.id)
        assert loaded is not None
        assert loaded.dominant_emotion == "frustrated"
        assert loaded.frequency == 6
