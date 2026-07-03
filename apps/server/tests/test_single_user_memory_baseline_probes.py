from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from anima_server.db.base import Base
from anima_server.models import (
    MemoryEpisode,
    MemoryItem,
    MemoryItemEvidence,
    SelfModelBlock,
    User,
)
from anima_server.services.agent.emotional_intelligence import record_emotional_signal
from anima_server.services.agent.evidence_retrieval import RetrievalMode, retrieve_wide_evidence
from anima_server.services.agent.memory_blocks import (
    build_emotional_context_block,
    build_episodes_memory_block,
    build_facts_memory_block,
    build_human_core_block,
)
from anima_server.services.data_crypto import ef
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


def _add_user(db: Session) -> User:
    user = User(
        username="sum-baseline",
        password_hash="x",
        display_name="Leo",
        age=31,
        birthday="1995-04-12",
    )
    db.add(user)
    db.flush()
    return user


def test_baseline_probe_factual_recall_uses_fact_memory_block() -> None:
    with _db_session() as db:
        user = _add_user(db)
        db.add(
            MemoryItem(
                user_id=user.id,
                content="The user keeps the launch checklist in Notion.",
                category="fact",
                importance=4,
                source="eval_probe",
            )
        )
        db.flush()

        block = build_facts_memory_block(db, user_id=user.id)

    assert block is not None
    assert block.label == "facts"
    assert "launch checklist in Notion" in block.value


def test_baseline_probe_emotional_recall_uses_recent_signal_block() -> None:
    with _db_session() as db:
        user = _add_user(db)
        record_emotional_signal(
            db,
            user_id=user.id,
            emotion="stressed",
            confidence=0.82,
            evidence_type="explicit",
            evidence="User said the launch review felt heavy.",
            trajectory="escalating",
            topic="launch review",
        )
        db.flush()

        block = build_emotional_context_block(db, user_id=user.id)

    assert block is not None
    assert block.label == "emotional_context"
    assert "Dominant recent emotion: stressed" in block.value
    assert "launch review" in block.value


def test_baseline_probe_profile_recall_combines_user_profile_and_human_block() -> None:
    with _db_session() as db:
        user = _add_user(db)
        db.add(
            SelfModelBlock(
                user_id=user.id,
                section="human",
                content=ef(
                    user.id,
                    "Prefers direct engineering notes with concrete file references.",
                    table="self_model_blocks",
                    field="content",
                ),
                version=1,
                updated_by="eval_probe",
            )
        )
        db.flush()

        block = build_human_core_block(db, user_id=user.id)

    assert block is not None
    assert block.label == "human"
    assert "Name: Leo" in block.value
    assert "Age: 31" in block.value
    assert "direct engineering notes" in block.value


@pytest.mark.asyncio()
async def test_baseline_probe_temporal_recall_orders_evidence_by_observed_time(
    monkeypatch,
) -> None:
    with _db_session() as db:
        user = _add_user(db)
        item = MemoryItem(
            user_id=user.id,
            content="Rachel moved offices.",
            category="fact",
            importance=3,
            source="eval_probe",
        )
        db.add(item)
        db.flush()
        db.add_all(
            [
                MemoryItemEvidence(
                    user_id=user.id,
                    memory_item_id=item.id,
                    source_kind="eval_import",
                    observed_at=datetime(2026, 1, 3, 9, 0, tzinfo=UTC),
                    speaker="user",
                    confidence=0.8,
                    evidence_text=ef(
                        user.id,
                        "User: Rachel moved to the east office.",
                        table="memory_item_evidence",
                        field="evidence_text",
                    ),
                ),
                MemoryItemEvidence(
                    user_id=user.id,
                    memory_item_id=item.id,
                    source_kind="eval_import",
                    observed_at=datetime(2026, 2, 14, 10, 0, tzinfo=UTC),
                    speaker="user",
                    confidence=0.8,
                    evidence_text=ef(
                        user.id,
                        "User: Rachel moved to the west office.",
                        table="memory_item_evidence",
                        field="evidence_text",
                    ),
                ),
            ]
        )
        db.flush()

        async def fake_hybrid_search(*args, **kwargs) -> SimpleNamespace:
            return SimpleNamespace(items=[(item, 0.9)], query_embedding=[0.1])

        monkeypatch.setattr(
            "anima_server.services.agent.evidence_retrieval.hybrid_search",
            fake_hybrid_search,
        )
        result = await retrieve_wide_evidence(
            db=db,
            user_id=user.id,
            query="Where did Rachel move most recently?",
            mode=RetrievalMode.LATEST_UPDATE,
        )

    assert result.semantic_results
    assert "west office" in result.semantic_results[0][1]


def test_baseline_probe_pattern_recall_surfaces_repeated_episode_evidence() -> None:
    with _db_session() as db:
        user = _add_user(db)
        db.add_all(
            [
                MemoryEpisode(
                    user_id=user.id,
                    date="2026-06-20",
                    time="09:00",
                    topics_json=["launch", "stress"],
                    summary=ef(
                        user.id,
                        "Launch planning again produced decision fatigue.",
                        table="memory_episodes",
                        field="summary",
                    ),
                    emotional_arc=ef(
                        user.id,
                        "focused -> tired",
                        table="memory_episodes",
                        field="emotional_arc",
                    ),
                    significance_score=4,
                    turn_count=4,
                ),
                MemoryEpisode(
                    user_id=user.id,
                    date="2026-06-21",
                    time="09:00",
                    topics_json=["launch", "stress"],
                    summary=ef(
                        user.id,
                        "A second launch planning session repeated the same fatigue pattern.",
                        table="memory_episodes",
                        field="summary",
                    ),
                    emotional_arc=ef(
                        user.id,
                        "steady -> depleted",
                        table="memory_episodes",
                        field="emotional_arc",
                    ),
                    significance_score=4,
                    turn_count=4,
                ),
            ]
        )
        db.flush()

        block = build_episodes_memory_block(db, user_id=user.id)

    assert block is not None
    assert block.label == "recent_episodes"
    assert "decision fatigue" in block.value
    assert "same fatigue pattern" in block.value
