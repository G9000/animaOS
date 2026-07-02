from __future__ import annotations

import json
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime

import pytest
from anima_server.db.base import Base
from anima_server.models import MemoryEpisode, MemoryItem, User
from anima_server.services.data_crypto import df, ef
from anima_server.services.sessions import unlock_session_store
from sqlalchemy import create_engine, select
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
    user = User(username="pattern-user", display_name="Pattern User", password_hash="x")
    db.add(user)
    db.flush()
    return user


def _episode(
    db: Session,
    *,
    user_id: int,
    date: str,
    summary: str,
    topics: list[str],
    significance: int,
    emotional_arc: str | None = None,
) -> MemoryEpisode:
    episode = MemoryEpisode(
        user_id=user_id,
        date=date,
        topics_json=topics,
        summary=ef(user_id, summary, table="memory_episodes", field="summary"),
        emotional_arc=emotional_arc,
        significance_score=significance,
        turn_count=4,
        created_at=datetime.fromisoformat(f"{date}T12:00:00+00:00"),
    )
    db.add(episode)
    db.flush()
    return episode


def test_episode_sampling_keeps_temporal_topic_and_salience_diversity() -> None:
    from anima_server.services.agent.pattern_synthesis import sample_pattern_episodes

    with _db_session() as db:
        user = _make_user(db)
        old_high = _episode(
            db,
            user_id=user.id,
            date="2026-01-04",
            summary="Old but important launch anxiety resurfaced.",
            topics=["launch", "stress"],
            significance=5,
        )
        mid_goal = _episode(
            db,
            user_id=user.id,
            date="2026-03-12",
            summary="The user returned to the same shipping goal.",
            topics=["shipping", "goal"],
            significance=3,
        )
        recent_goal = _episode(
            db,
            user_id=user.id,
            date="2026-06-20",
            summary="Recent check-in about the shipping goal.",
            topics=["shipping", "goal"],
            significance=2,
        )
        recent_low = _episode(
            db,
            user_id=user.id,
            date="2026-06-21",
            summary="A low-salience casual note.",
            topics=["casual"],
            significance=1,
        )

        sampled = sample_pattern_episodes(db, user_id=user.id, limit=3)

    sampled_ids = {episode.id for episode in sampled}
    assert old_high.id in sampled_ids
    assert mid_goal.id in sampled_ids or recent_goal.id in sampled_ids
    assert recent_low.id not in sampled_ids


def test_strict_parser_requires_repeated_episode_evidence() -> None:
    from anima_server.services.agent.pattern_synthesis import parse_pattern_response

    payload = json.dumps(
        [
            {
                "pattern": "A single mention should not become memory.",
                "category": "emotional_patterns",
                "confidence": 0.95,
                "source_episode_ids": [101],
                "evidence": ["one isolated mention"],
            },
            {
                "pattern": "Repeated launch pressure tends to cause fatigue.",
                "category": "emotional_patterns",
                "confidence": 0.82,
                "source_episode_ids": [101, 104],
                "evidence": ["launch fatigue in January", "launch fatigue in March"],
            },
        ]
    )

    patterns = parse_pattern_response(payload)

    assert [pattern.pattern for pattern in patterns] == [
        "Repeated launch pressure tends to cause fatigue."
    ]
    assert patterns[0].source_episode_ids == (101, 104)


def test_prompt_episode_rendering_decrypts_emotional_arc() -> None:
    from anima_server.services.agent.pattern_synthesis import _render_episodes_for_prompt

    with _db_session() as db:
        user = _make_user(db)
        token = unlock_session_store.create(user.id, {"memories": b"m" * 32})
        try:
            encrypted_arc = ef(
                user.id,
                "anxious -> relieved",
                table="memory_episodes",
                field="emotional_arc",
            )
            assert encrypted_arc is not None
            assert encrypted_arc.startswith("enc2:")

            episode = _episode(
                db,
                user_id=user.id,
                date="2026-02-01",
                summary="The user felt anxious before the demo and relieved afterward.",
                topics=["demo", "emotion"],
                significance=4,
                emotional_arc=encrypted_arc,
            )
            rendered = _render_episodes_for_prompt([episode], user_id=user.id)
        finally:
            unlock_session_store.revoke(token)


    assert "anxious -> relieved" in rendered
    assert "enc2:" not in rendered


@pytest.mark.asyncio()
async def test_synthesis_creates_evidence_backed_pattern_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.agent import pattern_synthesis

    with _db_session() as db:
        user = _make_user(db)
        first = _episode(
            db,
            user_id=user.id,
            date="2026-02-01",
            summary="The user was exhausted after launch planning.",
            topics=["launch", "energy"],
            significance=4,
        )
        second = _episode(
            db,
            user_id=user.id,
            date="2026-04-01",
            summary="Another launch planning push led to the same exhaustion.",
            topics=["launch", "energy"],
            significance=4,
        )

        async def _fake_call_llm_for_json(*_args: object, **_kwargs: object) -> object:
            return [
                {
                    "pattern": "Launch planning repeatedly drains the user's energy.",
                    "category": "emotional_patterns",
                    "confidence": 0.86,
                    "source_episode_ids": [first.id, second.id],
                    "source_evidence_ids": [501, 502],
                    "evidence": [
                        "exhausted after launch planning",
                        "same exhaustion after another launch push",
                    ],
                }
            ]

        monkeypatch.setattr(pattern_synthesis, "call_llm_for_json", _fake_call_llm_for_json)

        result = await pattern_synthesis.synthesize_cross_episode_patterns(
            user_id=user.id,
            db_factory=lambda: db,
        )

        item = db.scalar(select(MemoryItem).where(MemoryItem.user_id == user.id))
        assert item is not None
        assert result.created == 1
        assert item.source == "pattern_synthesis"
        assert item.memory_class == "emotional_pattern"
        assert item.evidence_strength == pytest.approx(0.86)
        assert df(user.id, item.content, table="memory_items", field="content") == (
            "Launch planning repeatedly drains the user's energy"
        )
        assert len(item.evidence) == 1
        assert item.evidence[0].source_kind == "pattern_synthesis"
        assert item.evidence[0].metadata_json == {
            "memory_source": "pattern_synthesis",
            "source_episode_ids": [first.id, second.id],
            "source_evidence_ids": [501, 502],
        }


@pytest.mark.asyncio()
async def test_synthesis_skips_duplicate_pattern_evidence_for_same_episodes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.agent import pattern_synthesis

    with _db_session() as db:
        user = _make_user(db)
        first = _episode(
            db,
            user_id=user.id,
            date="2026-02-01",
            summary="The user was exhausted after launch planning.",
            topics=["launch", "energy"],
            significance=4,
        )
        second = _episode(
            db,
            user_id=user.id,
            date="2026-04-01",
            summary="Another launch planning push led to the same exhaustion.",
            topics=["launch", "energy"],
            significance=4,
        )

        async def _fake_call_llm_for_json(*_args: object, **_kwargs: object) -> object:
            return [
                {
                    "pattern": "Launch planning repeatedly drains the user's energy.",
                    "category": "emotional_patterns",
                    "confidence": 0.86,
                    "source_episode_ids": [first.id, second.id],
                    "source_evidence_ids": [501, 502],
                    "evidence": [
                        "exhausted after launch planning",
                        "same exhaustion after another launch push",
                    ],
                }
            ]

        monkeypatch.setattr(pattern_synthesis, "call_llm_for_json", _fake_call_llm_for_json)

        first_result = await pattern_synthesis.synthesize_cross_episode_patterns(
            user_id=user.id,
            db_factory=lambda: db,
        )
        second_result = await pattern_synthesis.synthesize_cross_episode_patterns(
            user_id=user.id,
            db_factory=lambda: db,
        )

        item = db.scalar(select(MemoryItem).where(MemoryItem.user_id == user.id))
        assert item is not None
        assert first_result.created == 1
        assert second_result.updated == 0
        assert second_result.skipped == 1
        assert item.evidence_strength == pytest.approx(0.86)
        assert len(item.evidence) == 1


def test_pattern_prompt_block_renders_only_high_confidence_active_patterns() -> None:
    from anima_server.services.agent.memory_blocks import build_cross_episode_patterns_block

    with _db_session() as db:
        user = _make_user(db)
        active = MemoryItem(
            user_id=user.id,
            category="pattern",
            source="pattern_synthesis",
            content=ef(
                user.id,
                "Launch planning repeatedly drains the user's energy.",
                table="memory_items",
                field="content",
            ),
            importance=4,
            memory_class="emotional_pattern",
            evidence_strength=0.86,
            emotional_salience=0.7,
            decay_class="slow",
        )
        low_confidence = MemoryItem(
            user_id=user.id,
            category="pattern",
            source="pattern_synthesis",
            content=ef(
                user.id,
                "Low confidence pattern should stay out.",
                table="memory_items",
                field="content",
            ),
            importance=4,
            memory_class="emotional_pattern",
            evidence_strength=0.59,
        )
        superseded = MemoryItem(
            user_id=user.id,
            category="pattern",
            source="pattern_synthesis",
            content=ef(
                user.id,
                "Superseded pattern should stay out.",
                table="memory_items",
                field="content",
            ),
            importance=5,
            memory_class="emotional_pattern",
            evidence_strength=0.95,
        )
        db.add_all([active, low_confidence, superseded])
        db.flush()
        superseded.superseded_by = active.id
        db.flush()

        block = build_cross_episode_patterns_block(db, user_id=user.id, max_chars=80)

    assert block is not None
    assert block.label == "cross_episode_patterns"
    assert "Launch planning repeatedly drains" in block.value
    assert "Low confidence" not in block.value
    assert "Superseded" not in block.value
    assert len(block.value) <= 80
