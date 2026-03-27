"""P3: Self-Model Split tests."""
from __future__ import annotations

import pytest
from anima_server.db.base import Base
from anima_server.db.runtime_base import RuntimeBase
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


@pytest.fixture()
def runtime_db() -> Session:
    """In-memory SQLite session with runtime consciousness tables."""
    import anima_server.models.runtime_consciousness  # noqa: F401

    engine = create_engine("sqlite://", poolclass=StaticPool)
    RuntimeBase.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
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


class TestWorkingContext:
    def test_create_and_read(self, runtime_db: Session) -> None:
        from anima_server.models.runtime_consciousness import WorkingContext

        wc = WorkingContext(
            user_id=1,
            section="inner_state",
            content="Feeling reflective.",
            version=1,
            updated_by="post_turn",
        )
        runtime_db.add(wc)
        runtime_db.flush()

        loaded = runtime_db.get(WorkingContext, wc.id)
        assert loaded is not None
        assert loaded.section == "inner_state"
        assert loaded.content == "Feeling reflective."

    def test_unique_constraint(self, runtime_db: Session) -> None:
        """Only one row per (user_id, section)."""
        from sqlalchemy.exc import IntegrityError

        from anima_server.models.runtime_consciousness import WorkingContext

        runtime_db.add(WorkingContext(user_id=1, section="inner_state", content="a"))
        runtime_db.flush()
        runtime_db.add(WorkingContext(user_id=1, section="inner_state", content="b"))
        with pytest.raises(IntegrityError):
            runtime_db.flush()


class TestActiveIntention:
    def test_create_and_read(self, runtime_db: Session) -> None:
        from anima_server.models.runtime_consciousness import ActiveIntention

        ai = ActiveIntention(user_id=1, content="Learn their preferences", version=1)
        runtime_db.add(ai)
        runtime_db.flush()

        loaded = runtime_db.get(ActiveIntention, ai.id)
        assert loaded is not None
        assert loaded.content == "Learn their preferences"

    def test_unique_per_user(self, runtime_db: Session) -> None:
        from sqlalchemy.exc import IntegrityError

        from anima_server.models.runtime_consciousness import ActiveIntention

        runtime_db.add(ActiveIntention(user_id=1, content="a"))
        runtime_db.flush()
        runtime_db.add(ActiveIntention(user_id=1, content="b"))
        with pytest.raises(IntegrityError):
            runtime_db.flush()


class TestCurrentEmotion:
    def test_create_and_read(self, runtime_db: Session) -> None:
        from anima_server.models.runtime_consciousness import CurrentEmotion

        ce = CurrentEmotion(
            user_id=1,
            emotion="excited",
            confidence=0.8,
            evidence_type="linguistic",
            evidence="Used exclamation marks",
            trajectory="stable",
            topic="weekend plans",
        )
        runtime_db.add(ce)
        runtime_db.flush()

        loaded = runtime_db.get(CurrentEmotion, ce.id)
        assert loaded is not None
        assert loaded.emotion == "excited"
        assert loaded.confidence == 0.8

    def test_multiple_per_user(self, runtime_db: Session) -> None:
        """Rolling buffer - many signals per user."""
        from sqlalchemy import func, select

        from anima_server.models.runtime_consciousness import CurrentEmotion

        for emotion in ["excited", "calm", "curious"]:
            runtime_db.add(CurrentEmotion(user_id=1, emotion=emotion, confidence=0.6))
        runtime_db.flush()

        count = runtime_db.scalar(
            select(func.count())
            .select_from(CurrentEmotion)
            .where(CurrentEmotion.user_id == 1)
        )
        assert count == 3


class TestSelfModelIdentityBlock:
    def test_get_identity_block_returns_none_when_missing(self, soul_db: Session) -> None:
        from anima_server.services.agent.self_model import get_identity_block

        result = get_identity_block(soul_db, user_id=1)
        assert result is None

    def test_set_and_get_identity_block(self, soul_db: Session) -> None:
        from anima_server.services.agent.self_model import (
            get_identity_block,
            set_identity_block,
        )

        set_identity_block(soul_db, user_id=1, content="I am a companion.", updated_by="system")
        soul_db.flush()

        block = get_identity_block(soul_db, user_id=1)
        assert block is not None
        assert block.content == "I am a companion."
        assert block.version == 1

    def test_set_identity_block_bumps_version(self, soul_db: Session) -> None:
        from anima_server.services.agent.self_model import (
            get_identity_block,
            set_identity_block,
        )

        set_identity_block(soul_db, user_id=1, content="v1", updated_by="system")
        soul_db.flush()
        set_identity_block(soul_db, user_id=1, content="v2", updated_by="sleep_time")
        soul_db.flush()

        block = get_identity_block(soul_db, user_id=1)
        assert block is not None
        assert block.content == "v2"
        assert block.version == 2

    def test_identity_stability_threshold(self, soul_db: Session) -> None:
        from anima_server.services.agent.self_model import (
            get_identity_block,
            set_identity_block,
        )

        set_identity_block(soul_db, user_id=1, content="original", updated_by="system")
        soul_db.flush()

        set_identity_block(
            soul_db,
            user_id=1,
            content="completely different text here",
            updated_by="sleep_time",
        )
        soul_db.flush()

        block = get_identity_block(soul_db, user_id=1)
        assert block is not None
        assert block.content == "original"


class TestSelfModelGrowthLog:
    def test_append_growth_log_entry_row(self, soul_db: Session) -> None:
        from anima_server.services.agent.self_model import (
            append_growth_log_entry_row,
            get_growth_log_entries,
        )

        append_growth_log_entry_row(soul_db, user_id=1, entry="Learned patience")
        soul_db.flush()

        entries = get_growth_log_entries(soul_db, user_id=1)
        assert len(entries) == 1
        assert entries[0].entry == "Learned patience"

    def test_dedup_by_word_overlap(self, soul_db: Session) -> None:
        from anima_server.services.agent.self_model import (
            append_growth_log_entry_row,
            get_growth_log_entries,
        )

        append_growth_log_entry_row(soul_db, user_id=1, entry="Learned to be patient with the user")
        soul_db.flush()
        result = append_growth_log_entry_row(
            soul_db,
            user_id=1,
            entry="Learned to be patient with the user today",
        )
        soul_db.flush()

        assert result is None
        entries = get_growth_log_entries(soul_db, user_id=1)
        assert len(entries) == 1

    def test_trim_to_max_entries(self, soul_db: Session) -> None:
        from anima_server.services.agent.self_model import (
            append_growth_log_entry_row,
            get_growth_log_entries,
        )

        for i in range(25):
            append_growth_log_entry_row(
                soul_db,
                user_id=1,
                entry=f"Unique entry number {i} with distinctive words {i * 100}",
            )
            soul_db.flush()

        entries = get_growth_log_entries(soul_db, user_id=1)
        assert len(entries) <= 20


class TestSelfModelWorkingContext:
    def test_get_working_context_empty(self, runtime_db: Session) -> None:
        from anima_server.services.agent.self_model import get_working_context

        result = get_working_context(runtime_db, user_id=1)
        assert result == {}

    def test_set_and_get_working_context(self, runtime_db: Session) -> None:
        from anima_server.services.agent.self_model import (
            get_working_context,
            set_working_context,
        )

        set_working_context(
            runtime_db,
            user_id=1,
            section="inner_state",
            content="Feeling curious.",
        )
        runtime_db.flush()

        result = get_working_context(runtime_db, user_id=1)
        assert "inner_state" in result
        assert result["inner_state"].content == "Feeling curious."

    def test_get_active_intentions(self, runtime_db: Session) -> None:
        from anima_server.services.agent.self_model import (
            get_active_intentions,
            set_active_intentions,
        )

        set_active_intentions(runtime_db, user_id=1, content="Learn preferences")
        runtime_db.flush()

        result = get_active_intentions(runtime_db, user_id=1)
        assert result is not None
        assert result.content == "Learn preferences"


class TestEmotionalIntelligenceRuntime:
    def test_record_signal_to_runtime(self, runtime_db: Session) -> None:
        from anima_server.services.agent.emotional_intelligence import record_emotional_signal

        signal = record_emotional_signal(
            runtime_db,
            user_id=1,
            emotion="excited",
            confidence=0.8,
            evidence="Used exclamation marks",
            topic="weekend",
        )
        assert signal is not None
        assert signal.emotion == "excited"

    def test_get_recent_signals_from_runtime(self, runtime_db: Session) -> None:
        from anima_server.services.agent.emotional_intelligence import (
            get_recent_signals,
            record_emotional_signal,
        )

        record_emotional_signal(runtime_db, user_id=1, emotion="calm", confidence=0.7)
        record_emotional_signal(runtime_db, user_id=1, emotion="curious", confidence=0.6)
        runtime_db.flush()

        signals = get_recent_signals(runtime_db, user_id=1)
        assert len(signals) == 2

    def test_synthesize_from_runtime(self, runtime_db: Session) -> None:
        from anima_server.services.agent.emotional_intelligence import (
            record_emotional_signal,
            synthesize_emotional_context,
        )

        record_emotional_signal(
            runtime_db,
            user_id=1,
            emotion="frustrated",
            confidence=0.8,
            evidence="Short replies",
        )
        runtime_db.flush()

        context = synthesize_emotional_context(runtime_db, user_id=1)
        assert "frustrated" in context

    def test_trim_buffer(self, runtime_db: Session) -> None:
        from sqlalchemy import func, select

        from anima_server.models.runtime_consciousness import CurrentEmotion
        from anima_server.services.agent.emotional_intelligence import record_emotional_signal

        for _ in range(25):
            record_emotional_signal(runtime_db, user_id=1, emotion="calm", confidence=0.5)
            runtime_db.flush()

        count = runtime_db.scalar(
            select(func.count()).select_from(CurrentEmotion).where(CurrentEmotion.user_id == 1)
        )
        assert count <= 20


class TestIntentionsRuntime:
    def test_add_intention_to_runtime(self, runtime_db: Session) -> None:
        from anima_server.services.agent.self_model import set_active_intentions
        from anima_server.services.agent.intentions import add_intention

        set_active_intentions(runtime_db, user_id=1, content="# Active Intentions\n\n## Ongoing")
        runtime_db.flush()

        content = add_intention(
            runtime_db,
            user_id=1,
            title="Learn communication style",
            evidence="New relationship",
        )
        assert "Learn communication style" in content

    def test_complete_intention_in_runtime(self, runtime_db: Session) -> None:
        from anima_server.services.agent.self_model import set_active_intentions
        from anima_server.services.agent.intentions import add_intention, complete_intention

        set_active_intentions(runtime_db, user_id=1, content="# Active Intentions\n\n## Ongoing")
        runtime_db.flush()

        add_intention(runtime_db, user_id=1, title="Test goal")
        runtime_db.flush()

        result = complete_intention(runtime_db, user_id=1, title="Test goal")
        assert result is True


class TestMemoryBlocksDualRead:
    def test_build_self_model_memory_blocks_dual_store(
        self,
        soul_db: Session,
        runtime_db: Session,
    ) -> None:
        """build_self_model_memory_blocks reads identity from soul and working context from runtime."""
        from anima_server.services.agent.memory_blocks import build_self_model_memory_blocks
        from anima_server.services.agent.self_model import (
            set_active_intentions,
            set_identity_block,
            set_working_context,
        )

        set_identity_block(
            soul_db,
            user_id=1,
            content="I am a caring companion.",
            updated_by="system",
        )
        soul_db.flush()

        set_working_context(
            runtime_db,
            user_id=1,
            section="inner_state",
            content="Feeling curious.",
        )
        set_working_context(
            runtime_db,
            user_id=1,
            section="working_memory",
            content="- Remember to ask about project",
        )
        set_active_intentions(
            runtime_db,
            user_id=1,
            content="# Intentions\n- Learn preferences",
        )
        runtime_db.flush()

        blocks = build_self_model_memory_blocks(soul_db, pg_db=runtime_db, user_id=1)

        labels = {b.label for b in blocks}
        assert "self_identity" in labels
        assert "self_inner_state" in labels
        assert "self_working_memory" in labels
        assert "self_intentions" in labels

    def test_build_emotional_context_from_runtime(self, runtime_db: Session) -> None:
        from anima_server.services.agent.emotional_intelligence import record_emotional_signal
        from anima_server.services.agent.memory_blocks import build_emotional_context_block

        record_emotional_signal(runtime_db, user_id=1, emotion="curious", confidence=0.7)
        runtime_db.flush()

        block = build_emotional_context_block(runtime_db, user_id=1)
        assert block is not None
        assert "curious" in block.value

    def test_build_emotional_patterns_block(self, soul_db: Session) -> None:
        from anima_server.models.soul_consciousness import CoreEmotionalPattern
        from anima_server.services.agent.memory_blocks import build_emotional_patterns_block

        soul_db.add(
            CoreEmotionalPattern(
                user_id=1,
                pattern="Gets frustrated under deadline pressure",
                dominant_emotion="frustrated",
                trigger_context="work deadlines",
                frequency=6,
                confidence=0.8,
            )
        )
        soul_db.flush()

        block = build_emotional_patterns_block(soul_db, user_id=1)
        assert block is not None
        assert "frustrated" in block.value


class TestEmotionalPatternPromotion:
    def test_promote_from_signals(self, soul_db: Session, runtime_db: Session) -> None:
        from sqlalchemy import select

        from anima_server.models.soul_consciousness import CoreEmotionalPattern
        from anima_server.services.agent.emotional_intelligence import record_emotional_signal
        from anima_server.services.agent.emotional_patterns import promote_emotional_patterns

        for _ in range(5):
            record_emotional_signal(
                runtime_db,
                user_id=1,
                emotion="frustrated",
                confidence=0.7,
                evidence="Deadline talk",
                topic="work",
            )
        runtime_db.flush()

        promoted = promote_emotional_patterns(soul_db=soul_db, pg_db=runtime_db, user_id=1)
        soul_db.flush()

        assert promoted >= 1
        patterns = soul_db.scalars(
            select(CoreEmotionalPattern).where(CoreEmotionalPattern.user_id == 1)
        ).all()
        assert len(patterns) >= 1
        assert any(pattern.dominant_emotion == "frustrated" for pattern in patterns)
