"""Tests for IL4 — latent trace buffer and crystallization.

Covers: the pure scoring/fold/decay math (``inner_life/latent.py``), the
promotion-gate integration in ``soul_writer.plan_candidate_promotion``, the
soul-store fold/decay/crystallize/scrub edges (``latent_traces.py``), F7
right-to-forget integration, and vault export/import round-tripping.
"""

from __future__ import annotations

import hashlib
import inspect
import itertools
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from anima_server.db.base import Base
from anima_server.db.runtime_base import RuntimeBase
from anima_server.models import LatentTrace, MemoryItem, MemoryItemEvidence, User
from anima_server.models.runtime import RuntimeMessage, RuntimeThread
from anima_server.models.runtime_memory import MemoryCandidate
from anima_server.services.agent import latent_traces
from anima_server.services.agent.candidate_ops import create_memory_candidate
from anima_server.services.agent.claims import derive_topic_key
from anima_server.services.agent.forgetting import (
    forget_latent_traces_for_topic,
    forget_memory,
)
from anima_server.services.agent.inner_life import latent
from anima_server.services.agent.inner_life.latent import (
    DEFAULT_LATENT_CONFIG,
    LatentConfig,
    classify_score,
    decay_weight,
    fold_weight,
    score_candidate,
    should_crystallize,
    should_prune,
)
from anima_server.services.agent.latent_traces import (
    crystallize_due_traces,
    decay_and_cap_traces,
    fold_candidate_into_trace,
    forget_latent_traces_by_topic,
    get_latent_config,
    scrub_latent_traces_for_forgotten_sources,
)
from anima_server.services.agent.soul_writer import (
    plan_candidate_promotion,
    run_soul_writer,
)
from anima_server.services.vault import export_database_snapshot, restore_database_snapshot
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

# ---------------------------------------------------------------------------
# Helpers (mirrors tests/test_soul_writer.py conventions)
# ---------------------------------------------------------------------------


def _content_hash(user_id: int, category: str, importance_source: str, content: str) -> str:
    normalized = content.strip().lower()
    return hashlib.sha256(
        f"{user_id}:{category}:{importance_source}:{normalized}".encode()
    ).hexdigest()


def _create_soul_engine() -> Engine:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    return engine


def _create_runtime_engine() -> Engine:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    RuntimeBase.metadata.create_all(bind=engine)
    return engine


def _make_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@contextmanager
def _soul_db_session() -> Generator[Session, None, None]:
    engine = _create_soul_engine()
    factory = _make_factory(engine)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


class FakeCandidate:
    """Minimal stand-in for a MemoryCandidate, for pure decision tests that
    never reach the fold/promote DB edges (mirrors
    test_soul_writer.py's FakeCandidate for plan_candidate_promotion)."""

    def __init__(
        self,
        *,
        content: str,
        category: str = "fact",
        importance: int = 3,
        importance_source: str = "llm",
        source: str = "llm",
        supersedes_item_id: int | None = None,
        salience_json: dict[str, object] | None = None,
    ) -> None:
        self.content = content
        self.category = category
        self.importance = importance
        self.importance_source = importance_source
        self.source = source
        self.supersedes_item_id = supersedes_item_id
        self.salience_json = salience_json


def _make_user(db: Session, username: str = "latent-user") -> User:
    user = User(username=username, password_hash="x", display_name="Latent User")
    db.add(user)
    db.flush()
    return user


# ---------------------------------------------------------------------------
# 1. Pure math (inner_life/latent.py)
# ---------------------------------------------------------------------------


def test_score_candidate_matches_prd_formula():
    # s = clamp01(0.6*importance/5 + 0.3*emotional_salience + 0.1*evidence_strength)
    s = score_candidate(importance=3, emotional_salience=0.5, evidence_strength=0.9)
    assert s == pytest.approx(0.6 * 0.6 + 0.3 * 0.5 + 0.1 * 0.9)


def test_score_candidate_clamped_to_unit_interval():
    assert score_candidate(
        importance=5, emotional_salience=1.0, evidence_strength=1.0
    ) == pytest.approx(1.0)
    assert score_candidate(importance=0, emotional_salience=0.0, evidence_strength=0.0) == 0.0


def test_classify_score_boundaries():
    config = DEFAULT_LATENT_CONFIG
    floor = config.floor
    assert classify_score(floor - 0.001, config) == "reject"
    assert classify_score(floor, config) == "fold"
    assert classify_score(config.promotion_threshold - 0.001, config) == "fold"
    assert classify_score(config.promotion_threshold, config) == "promote"


def test_fold_weight_is_additive_not_ema():
    """EMA-regression guard: repeated identical folds must strictly
    increase weight until the cap — an EMA would converge below threshold
    and never crystallize (the PR review finding this ticket fixes)."""
    config = DEFAULT_LATENT_CONFIG
    weight = 0.0
    seen = [weight]
    for _ in range(10):
        new_weight = fold_weight(weight, 0.3, config)
        if weight < 1.0:
            assert new_weight > weight, "fold must strictly increase weight below the cap"
        weight = new_weight
        seen.append(weight)
    assert weight == 1.0
    # Never plateaus below the cap partway through — an EMA converging to
    # ~0.3 would fail this.
    assert seen[4] >= config.crystallization_threshold


def test_fold_weight_crystallizes_after_expected_fold_count():
    """N mentions at s=0.3 crystallize after ceil(0.6/(0.5*0.3)) == 4 folds."""
    config = DEFAULT_LATENT_CONFIG
    weight = 0.0
    for i in range(1, 5):
        weight = fold_weight(weight, 0.3, config)
        if i < 4:
            assert not should_crystallize(weight, config), f"should not crystallize after {i} folds"
    assert weight == pytest.approx(0.6)
    assert should_crystallize(weight, config)


def test_fold_weight_caps_at_one():
    config = DEFAULT_LATENT_CONFIG
    weight = 0.95
    for _ in range(10):
        weight = fold_weight(weight, 1.0, config)
    assert weight == 1.0


def test_decay_weight_and_prune():
    config = DEFAULT_LATENT_CONFIG
    assert decay_weight(1.0, config) == pytest.approx(0.98)
    assert not should_prune(0.02, config)
    assert should_prune(0.0199, config)


def test_pure_latent_module_has_no_llm_seam():
    """Zero-LLM assertion: the pure fold/score module must never import or
    reference an LLM call — crystallization (latent_traces.py) is the ONLY
    LLM consumer in the IL4 pipeline."""
    source = inspect.getsource(latent)
    assert "call_llm_for_json" not in source
    assert "llm_json" not in source
    assert "async def" not in source


# ---------------------------------------------------------------------------
# 2. Scorer behavior-preservation property (soul_writer integration)
# ---------------------------------------------------------------------------


_SALIENCE_GRID = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
# Realistic evidence_strength floor for the behavior-preservation property.
# The PRD's own worst-case calibration ("worst-case imp-2 scores 0.32 >
# 0.30") is computed at evidence_strength=0.8, the documented default for
# "absent". `memory_salience._infer_salience` never infers below 0.7 for
# ANY category/importance combination, so 0.7-1.0 is the realistic range
# "current candidates" (the brief's phrase) actually produce — evidence
# strength near 0 is not a state the pre-IL4 system ever populated for a
# real candidate. (An LLM COULD in principle report an explicit near-zero
# evidence_strength even at importance>=2, in which case the new score gate
# folds instead of promoting — a deliberate consequence of IL4 adding a
# scoring path at all, not a behavior-preservation violation of the
# documented calibration target.)
_REALISTIC_EVIDENCE_GRID = [0.7, 0.8, 0.9, 1.0]


def test_behavior_preservation_importance_2_plus_always_promotes_pure():
    """Pure-function half of the property: for importance >= 2, across the
    full emotional-salience range and the realistic evidence_strength floor
    (see _REALISTIC_EVIDENCE_GRID), classification is always "promote" —
    never "fold" or "reject". This is the exact worst case the PRD
    calibrated theta_p against (imp-2, zero salience, default evidence
    strength = 0.32 > 0.30)."""
    config = DEFAULT_LATENT_CONFIG
    for importance, emotional_salience, evidence_strength in itertools.product(
        (2, 3, 4, 5), _SALIENCE_GRID, _REALISTIC_EVIDENCE_GRID
    ):
        s = score_candidate(
            importance=importance,
            emotional_salience=emotional_salience,
            evidence_strength=evidence_strength,
        )
        assert classify_score(s, config) == "promote", (
            f"importance={importance} salience={emotional_salience} "
            f"evidence={evidence_strength} scored {s} but did not promote"
        )


def test_behavior_preservation_via_plan_candidate_promotion_new_memory():
    """Integration half: real plan_candidate_promotion() calls, no existing
    matching memory (the "promote-family" new-memory path), across the same
    importance>=2 grid — decision.action must always be "promote", covering
    the actual live promotion decision the property protects, not just the
    pure classifier."""
    with _soul_db_session() as soul_db:
        user = _make_user(soul_db)
        for importance, emotional_salience, evidence_strength in itertools.product(
            (2, 3, 4, 5), _SALIENCE_GRID, _REALISTIC_EVIDENCE_GRID
        ):
            candidate = FakeCandidate(
                content=f"Unique content {importance}-{emotional_salience}-{evidence_strength}",
                importance=importance,
                salience_json={
                    "emotional_salience": emotional_salience,
                    "evidence_strength": evidence_strength,
                },
            )
            decision = plan_candidate_promotion(soul_db, candidate, user.id)
            assert decision.action == "promote", (
                f"importance={importance} salience={emotional_salience} "
                f"evidence={evidence_strength} -> {decision.action} ({decision.reason})"
            )


def test_behavior_preservation_covers_promote_family_reinforce_supersede_evolve():
    """The property also must hold for the OTHER promote-family outcomes
    (reinforce/supersede/evolve against an existing matching memory) —
    these must win over folding regardless of score, including for a
    candidate that would score below the fold floor on its own."""
    with _soul_db_session() as soul_db:
        user = _make_user(soul_db)
        existing = MemoryItem(
            user_id=user.id,
            content="Likes green tea",
            category="preference",
            importance=3,
            source="extraction",
        )
        soul_db.add(existing)
        soul_db.commit()

        # A near-identical, very low-score candidate (importance=1, zero
        # salience/evidence) duplicating an EXISTING memory must still
        # reinforce — dedup wins over folding.
        candidate = FakeCandidate(
            content="Likes green tea",
            category="preference",
            importance=1,
            salience_json={"emotional_salience": 0.0, "evidence_strength": 0.0},
        )
        decision = plan_candidate_promotion(soul_db, candidate, user.id)
        assert decision.action == "reinforce"


def test_importance_1_low_salience_folds():
    with _soul_db_session() as soul_db:
        user = _make_user(soul_db)
        candidate = FakeCandidate(
            content="Mentioned the commute again",
            category="minor_observation",
            importance=1,
            salience_json={"emotional_salience": 0.0, "evidence_strength": 0.8},
        )
        decision = plan_candidate_promotion(soul_db, candidate, user.id)
        assert decision.action == "fold_to_trace"
        assert decision.topic_key is not None


def test_importance_1_strong_emotional_salience_still_promotes():
    with _soul_db_session() as soul_db:
        user = _make_user(soul_db)
        candidate = FakeCandidate(
            content="Something small but emotionally loaded happened",
            importance=1,
            salience_json={"emotional_salience": 1.0, "evidence_strength": 0.8},
        )
        decision = plan_candidate_promotion(soul_db, candidate, user.id)
        assert decision.action == "promote"


def test_score_below_floor_is_rejected():
    """"below-floor drops": no real MemoryCandidate can score below the
    floor (importance is clamped to >= 1, which alone scores 0.12 > the
    default floor of 0.075), so this is exercised at the classifier level
    directly — the contract the promotion gate relies on."""
    config = DEFAULT_LATENT_CONFIG
    assert classify_score(0.05, config) == "reject"


def test_high_authority_user_explicit_never_folds():
    with _soul_db_session() as soul_db:
        user = _make_user(soul_db)
        candidate = FakeCandidate(
            content="Trivial aside",
            importance=1,
            importance_source="user_explicit",
            salience_json={"emotional_salience": 0.0, "evidence_strength": 0.0},
        )
        decision = plan_candidate_promotion(soul_db, candidate, user.id)
        assert decision.action == "promote"
        assert "user_explicit" in decision.reason


def test_high_authority_correction_never_folds():
    with _soul_db_session() as soul_db:
        user = _make_user(soul_db)
        old_item = MemoryItem(
            user_id=user.id, content="Age: 25", category="fact", importance=3, source="extraction"
        )
        soul_db.add(old_item)
        soul_db.commit()

        candidate = FakeCandidate(
            content="Age: 26",
            category="fact",
            importance=1,
            importance_source="correction",
            supersedes_item_id=old_item.id,
            salience_json={"emotional_salience": 0.0, "evidence_strength": 0.0},
        )
        decision = plan_candidate_promotion(soul_db, candidate, user.id)
        assert decision.action == "supersede"


# ---------------------------------------------------------------------------
# 3. Topic key derivation + dedup
# ---------------------------------------------------------------------------


def test_derive_topic_key_matches_claim_slot_for_structured_content():
    assert derive_topic_key("Likes hiking", "preference") == "user:preference:likes"


def test_derive_topic_key_same_content_same_key():
    a = derive_topic_key("Mentioned feeling tired about the commute", "minor_observation")
    b = derive_topic_key("Mentioned feeling tired about the commute", "minor_observation")
    assert a == b


# ---------------------------------------------------------------------------
# 4. Fold DB edge — topic dedup, additive accumulation
# ---------------------------------------------------------------------------


_candidate_counter = itertools.count()


def _runtime_candidate(
    runtime_db: Session,
    *,
    user_id: int,
    content: str,
    category: str = "minor_observation",
    importance: int = 1,
) -> MemoryCandidate:
    # Each helper call represents a distinct extraction event (e.g. two
    # separate turns mentioning the same topic), so the hash is salted with
    # a counter to avoid colliding on identical content within one test.
    unique_content_hash = hashlib.sha256(
        f"{_content_hash(user_id, category, 'llm', content)}:{next(_candidate_counter)}".encode()
    ).hexdigest()
    candidate = MemoryCandidate(
        user_id=user_id,
        content=content,
        category=category,
        importance=importance,
        importance_source="llm",
        source="llm",
        content_hash=unique_content_hash,
        status="extracted",
        created_at=datetime.now(UTC),
    )
    runtime_db.add(candidate)
    runtime_db.commit()
    return candidate


def test_fold_same_topic_twice_creates_one_trace_with_two_evidence_refs():
    with _soul_db_session() as soul_db:
        user = _make_user(soul_db)
        runtime_engine = _create_runtime_engine()
        runtime_factory = _make_factory(runtime_engine)
        try:
            with runtime_factory() as runtime_db:
                c1 = _runtime_candidate(
                    runtime_db, user_id=user.id, content="Mentioned commute stress"
                )
                c2 = _runtime_candidate(
                    runtime_db, user_id=user.id, content="Mentioned commute stress"
                )

            topic_key = derive_topic_key("Mentioned commute stress", "minor_observation")
            fold_candidate_into_trace(
                soul_db, user_id=user.id, candidate=c1, topic_key=topic_key, score=0.3
            )
            fold_candidate_into_trace(
                soul_db, user_id=user.id, candidate=c2, topic_key=topic_key, score=0.3
            )

            traces = soul_db.scalars(
                select(LatentTrace).where(LatentTrace.user_id == user.id)
            ).all()
            assert len(traces) == 1
            trace = traces[0]
            assert trace.weight == pytest.approx(0.3)  # min(1, 0 + .5*.3) then min(1, .15+.5*.3)
            assert len(trace.evidence_refs) == 2
        finally:
            runtime_engine.dispose()


# ---------------------------------------------------------------------------
# 5. Decay + cap
# ---------------------------------------------------------------------------


def test_decay_and_cap_traces():
    with _soul_db_session() as soul_db:
        user = _make_user(soul_db)
        now = datetime.now(UTC)
        weights_by_topic = {"topic0": 0.5, "topic1": 0.019, "topic2": 0.9, "topic3": 0.03}
        soul_db.add_all(
            [
                LatentTrace(
                    user_id=user.id,
                    topic_key=f"user:minor_observation:{topic}",
                    kind="observation",
                    weight=weight,
                    evidence_refs=[{"candidate_id": i, "source_message_ids": []}],
                    first_seen=now,
                    last_seen=now,
                )
                for i, (topic, weight) in enumerate(weights_by_topic.items())
            ]
        )
        soul_db.commit()

        stats = decay_and_cap_traces(
            soul_db, user_id=user.id, config=LatentConfig(max_traces_per_user=500)
        )
        assert stats["decayed"] == 4
        # 0.019 * 0.98 stays below the 0.02 floor -> pruned
        assert stats["pruned"] == 1

        remaining = {
            trace.topic_key.rsplit(":", 1)[-1]: trace.weight
            for trace in soul_db.scalars(
                select(LatentTrace).where(LatentTrace.user_id == user.id)
            ).all()
        }
        assert set(remaining) == {"topic0", "topic2", "topic3"}
        assert remaining["topic0"] == pytest.approx(0.5 * 0.98)
        assert remaining["topic2"] == pytest.approx(0.9 * 0.98)
        assert remaining["topic3"] == pytest.approx(0.03 * 0.98)


def test_decay_and_cap_enforces_per_user_cap():
    with _soul_db_session() as soul_db:
        user = _make_user(soul_db)
        now = datetime.now(UTC)
        soul_db.add_all(
            [
                LatentTrace(
                    user_id=user.id,
                    topic_key=f"user:minor_observation:cap{i}",
                    weight=0.1 + i * 0.01,
                    evidence_refs=[],
                    first_seen=now,
                    last_seen=now,
                )
                for i in range(5)
            ]
        )
        soul_db.commit()

        stats = decay_and_cap_traces(
            soul_db, user_id=user.id, config=LatentConfig(max_traces_per_user=3)
        )
        assert stats["capped"] == 2
        remaining = soul_db.scalars(
            select(LatentTrace).where(LatentTrace.user_id == user.id)
        ).all()
        assert len(remaining) == 3
        # The lowest-weight traces (topic0, topic1) were dropped.
        kept_keys = {t.topic_key for t in remaining}
        assert "user:minor_observation:cap0" not in kept_keys
        assert "user:minor_observation:cap1" not in kept_keys


# ---------------------------------------------------------------------------
# 6. Crystallization (LLM seam mocked like test_pattern_synthesis.py)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_crystallize_due_trace_creates_one_memory_with_provenance(monkeypatch):
    soul_engine = _create_soul_engine()
    runtime_engine = _create_runtime_engine()
    soul_factory = _make_factory(soul_engine)
    runtime_factory = _make_factory(runtime_engine)
    try:
        with soul_factory() as soul_db:
            user = _make_user(soul_db)
            user_id = user.id
            soul_db.commit()

        with runtime_factory() as runtime_db:
            c1 = _runtime_candidate(
                runtime_db, user_id=user_id, content="Mentioned being tired after work again"
            )
            c2 = _runtime_candidate(
                runtime_db, user_id=user_id, content="Said work has been draining lately"
            )
            c1_id, c2_id = c1.id, c2.id

        topic_key = "user:minor_observation:work_fatigue"
        with soul_factory() as soul_db:
            soul_db.add(
                LatentTrace(
                    user_id=user_id,
                    topic_key=topic_key,
                    kind="observation",
                    weight=0.65,
                    evidence_refs=[
                        {"candidate_id": c1_id, "source_message_ids": []},
                        {"candidate_id": c2_id, "source_message_ids": []},
                    ],
                    first_seen=datetime.now(UTC),
                    last_seen=datetime.now(UTC),
                )
            )
            soul_db.commit()

        async def _fake_call_llm_for_json(*_args, **_kwargs):
            return {
                "content": "Work has been consistently draining lately",
                "category": "fact",
                "importance": 3,
            }

        monkeypatch.setattr(latent_traces, "call_llm_for_json", _fake_call_llm_for_json)

        stats = await crystallize_due_traces(
            user_id=user_id,
            db_factory=soul_factory,
            runtime_db_factory=runtime_factory,
        )
        assert stats["crystallized"] == 1

        with soul_factory() as soul_db:
            traces = soul_db.scalars(
                select(LatentTrace).where(LatentTrace.user_id == user_id)
            ).all()
            assert traces == []  # topic cleared

            items = soul_db.scalars(
                select(MemoryItem).where(MemoryItem.user_id == user_id)
            ).all()
            assert len(items) == 1
            item = items[0]
            assert item.source == "latent_crystallization"

            evidence = soul_db.scalars(
                select(MemoryItemEvidence).where(MemoryItemEvidence.memory_item_id == item.id)
            ).all()
            assert len(evidence) == 1
            refs = evidence[0].metadata_json["contributing_evidence_refs"]
            assert {ref["candidate_id"] for ref in refs} == {c1_id, c2_id}
    finally:
        soul_engine.dispose()
        runtime_engine.dispose()


@pytest.mark.asyncio()
async def test_crystallize_respects_per_run_cap(monkeypatch):
    soul_engine = _create_soul_engine()
    runtime_engine = _create_runtime_engine()
    soul_factory = _make_factory(soul_engine)
    runtime_factory = _make_factory(runtime_engine)
    try:
        with soul_factory() as soul_db:
            user = _make_user(soul_db)
            user_id = user.id
            soul_db.commit()

        with runtime_factory() as runtime_db:
            candidate_ids = [
                _runtime_candidate(runtime_db, user_id=user_id, content=f"Observation {i}").id
                for i in range(5)
            ]

        with soul_factory() as soul_db:
            soul_db.add_all(
                [
                    LatentTrace(
                        user_id=user_id,
                        topic_key=f"user:minor_observation:cap_topic{i}",
                        weight=0.7,
                        evidence_refs=[{"candidate_id": candidate_ids[i], "source_message_ids": []}],
                        first_seen=datetime.now(UTC),
                        last_seen=datetime.now(UTC),
                    )
                    for i in range(5)
                ]
            )
            soul_db.commit()

        calls = 0

        async def _fake_call_llm_for_json(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            return {"content": f"Synthesized memory {calls}", "category": "fact", "importance": 2}

        monkeypatch.setattr(latent_traces, "call_llm_for_json", _fake_call_llm_for_json)

        stats = await crystallize_due_traces(
            user_id=user_id,
            db_factory=soul_factory,
            runtime_db_factory=runtime_factory,
            max_per_run=3,
        )
        assert stats["crystallized"] == 3
        assert calls == 3

        with soul_factory() as soul_db:
            remaining = soul_db.scalars(
                select(LatentTrace).where(LatentTrace.user_id == user_id)
            ).all()
            assert len(remaining) == 2  # left for the next run
    finally:
        soul_engine.dispose()
        runtime_engine.dispose()


@pytest.mark.asyncio()
async def test_crystallize_drops_stale_refs_and_skips_synthesis_when_none_survive(monkeypatch):
    """Defense-in-depth: a ref whose candidate no longer resolves is
    dropped; if NONE survive, the trace is deleted and the LLM is never
    called."""
    soul_engine = _create_soul_engine()
    runtime_engine = _create_runtime_engine()
    soul_factory = _make_factory(soul_engine)
    runtime_factory = _make_factory(runtime_engine)
    try:
        with soul_factory() as soul_db:
            user = _make_user(soul_db)
            user_id = user.id
            soul_db.commit()

        with soul_factory() as soul_db:
            soul_db.add(
                LatentTrace(
                    user_id=user_id,
                    topic_key="user:minor_observation:stale_topic",
                    weight=0.7,
                    evidence_refs=[{"candidate_id": 999999, "source_message_ids": []}],
                    first_seen=datetime.now(UTC),
                    last_seen=datetime.now(UTC),
                )
            )
            soul_db.commit()

        calls = 0

        async def _fake_call_llm_for_json(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            return {"content": "should never happen", "category": "fact", "importance": 3}

        monkeypatch.setattr(latent_traces, "call_llm_for_json", _fake_call_llm_for_json)

        stats = await crystallize_due_traces(
            user_id=user_id, db_factory=soul_factory, runtime_db_factory=runtime_factory
        )
        assert stats["crystallized"] == 0
        assert stats["dropped_stale"] == 1
        assert calls == 0

        with soul_factory() as soul_db:
            assert soul_db.scalars(
                select(LatentTrace).where(LatentTrace.user_id == user_id)
            ).all() == []
            assert soul_db.scalars(select(MemoryItem).where(MemoryItem.user_id == user_id)).all() == []
    finally:
        soul_engine.dispose()
        runtime_engine.dispose()


# ---------------------------------------------------------------------------
# 7. F7 right-to-forget integration (forget-then-sleep, P1 acceptance test)
# ---------------------------------------------------------------------------


def test_forget_then_sleep_scrubs_refs_and_deletes_emptied_trace():
    """P1 acceptance test: forgetting a memory whose source contributed to
    a latent trace scrubs that evidence — a trace left with no surviving
    evidence is deleted outright, so a later crystallization pass can
    synthesize nothing from it."""
    soul_engine = _create_soul_engine()
    runtime_engine = _create_runtime_engine()
    soul_factory = _make_factory(soul_engine)
    runtime_factory = _make_factory(runtime_engine)
    try:
        with soul_factory() as soul_db:
            user = _make_user(soul_db)
            user_id = user.id
            soul_db.commit()

        with runtime_factory() as runtime_db:
            thread = RuntimeThread(user_id=user_id, status="active", next_message_sequence=2)
            runtime_db.add(thread)
            runtime_db.flush()
            message = RuntimeMessage(
                thread_id=thread.id,
                user_id=user_id,
                sequence_id=1,
                role="user",
                content_text="I've been so stressed about the commute lately.",
            )
            runtime_db.add(message)
            runtime_db.commit()
            message_id = message.id

        with soul_factory() as soul_db:
            memory = MemoryItem(
                user_id=user_id,
                content="Stressed about commute",
                category="fact",
                importance=3,
                source="extraction",
            )
            soul_db.add(memory)
            soul_db.flush()
            evidence = MemoryItemEvidence(
                user_id=user_id,
                memory_item_id=memory.id,
                source_kind="llm_extraction",
                runtime_message_id=message_id,
                evidence_text="I've been so stressed about the commute lately.",
            )
            soul_db.add(evidence)
            soul_db.commit()
            memory_id = memory.id

            # A latent trace whose ONLY evidence ref shares the same
            # forgotten source message — this is the "same source
            # contributes to both a promoted memory and a folded trace"
            # scenario the P1 review finding is about.
            soul_db.add(
                LatentTrace(
                    user_id=user_id,
                    topic_key="user:minor_observation:commute_stress",
                    weight=0.3,
                    evidence_refs=[
                        {"candidate_id": None, "source_message_ids": [message_id]}
                    ],
                    first_seen=datetime.now(UTC),
                    last_seen=datetime.now(UTC),
                )
            )
            soul_db.commit()

            result = forget_memory(
                soul_db,
                memory_id=memory_id,
                user_id=user_id,
                runtime_db_factory=runtime_factory,
            )
            soul_db.commit()

            assert result.latent_traces_scrubbed == 1
            remaining_traces = soul_db.scalars(
                select(LatentTrace).where(LatentTrace.user_id == user_id)
            ).all()
            assert remaining_traces == []
    finally:
        soul_engine.dispose()
        runtime_engine.dispose()


def test_forget_scrubs_ref_but_keeps_trace_when_other_evidence_survives():
    with _soul_db_session() as soul_db:
        user = _make_user(soul_db)
        soul_db.add(
            LatentTrace(
                user_id=user.id,
                topic_key="user:minor_observation:mixed_evidence",
                weight=0.4,
                evidence_refs=[
                    {"candidate_id": None, "source_message_ids": [101]},
                    {"candidate_id": None, "source_message_ids": [202]},
                ],
                first_seen=datetime.now(UTC),
                last_seen=datetime.now(UTC),
            )
        )
        soul_db.commit()

        scrubbed = scrub_latent_traces_for_forgotten_sources(
            soul_db, user_id=user.id, source_message_ids=[101]
        )
        assert scrubbed == 1

        trace = soul_db.scalar(select(LatentTrace).where(LatentTrace.user_id == user.id))
        assert trace is not None
        assert len(trace.evidence_refs) == 1
        assert trace.evidence_refs[0]["source_message_ids"] == [202]


def test_topic_scoped_forget_deletes_matching_trace():
    with _soul_db_session() as soul_db:
        user = _make_user(soul_db)
        soul_db.add(
            LatentTrace(
                user_id=user.id,
                topic_key="user:minor_observation:topic_to_forget",
                weight=0.2,
                evidence_refs=[],
                first_seen=datetime.now(UTC),
                last_seen=datetime.now(UTC),
            )
        )
        soul_db.add(
            LatentTrace(
                user_id=user.id,
                topic_key="user:minor_observation:other_topic",
                weight=0.2,
                evidence_refs=[],
                first_seen=datetime.now(UTC),
                last_seen=datetime.now(UTC),
            )
        )
        soul_db.commit()

        count = forget_latent_traces_for_topic(
            soul_db, user_id=user.id, topic_key="user:minor_observation:topic_to_forget"
        )
        assert count == 1

        remaining = soul_db.scalars(
            select(LatentTrace).where(LatentTrace.user_id == user.id)
        ).all()
        assert len(remaining) == 1
        assert remaining[0].topic_key == "user:minor_observation:other_topic"


def test_forget_latent_traces_by_topic_direct():
    with _soul_db_session() as soul_db:
        user = _make_user(soul_db)
        soul_db.add(
            LatentTrace(
                user_id=user.id,
                topic_key="user:preference:likes",
                weight=0.5,
                evidence_refs=[],
                first_seen=datetime.now(UTC),
                last_seen=datetime.now(UTC),
            )
        )
        soul_db.commit()
        count = forget_latent_traces_by_topic(
            soul_db, user_id=user.id, topic_key="user:preference:likes"
        )
        assert count == 1
        assert soul_db.scalar(select(LatentTrace).where(LatentTrace.user_id == user.id)) is None


# ---------------------------------------------------------------------------
# 8. End-to-end via run_soul_writer (dedup precedence + full fold pipeline)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_soul_writer_folds_weak_candidate_into_trace():
    soul_engine = _create_soul_engine()
    runtime_engine = _create_runtime_engine()
    soul_factory = _make_factory(soul_engine)
    runtime_factory = _make_factory(runtime_engine)
    try:
        with soul_factory() as soul_db:
            user = _make_user(soul_db)
            user_id = user.id
            soul_db.commit()

        with runtime_factory() as runtime_db:
            create_memory_candidate(
                runtime_db,
                user_id=user_id,
                content="Mentioned the neighbor's dog barking again",
                category="minor_observation",
                importance=1,
                importance_source="llm",
                source="llm",
                salience={"emotional_salience": 0.0, "evidence_strength": 0.8},
            )
            runtime_db.commit()

        result = await run_soul_writer(
            user_id, soul_db_factory=soul_factory, runtime_db_factory=runtime_factory
        )
        assert result.candidates_folded == 1
        assert result.candidates_promoted == 0

        with runtime_factory() as runtime_db:
            candidate = runtime_db.scalar(
                select(MemoryCandidate).where(MemoryCandidate.user_id == user_id)
            )
            assert candidate.status == "folded"

        with soul_factory() as soul_db:
            traces = soul_db.scalars(
                select(LatentTrace).where(LatentTrace.user_id == user_id)
            ).all()
            assert len(traces) == 1
            assert traces[0].weight == pytest.approx(0.1)  # 0.5 * score(0.2)
    finally:
        soul_engine.dispose()
        runtime_engine.dispose()


@pytest.mark.asyncio
async def test_repeated_folded_candidates_accumulate_to_crystallization_threshold():
    """Duplicate-topic churn across separate extraction events accumulates
    onto the SAME trace instead of double-counting into parallel rows."""
    soul_engine = _create_soul_engine()
    runtime_engine = _create_runtime_engine()
    soul_factory = _make_factory(soul_engine)
    runtime_factory = _make_factory(runtime_engine)
    try:
        with soul_factory() as soul_db:
            user = _make_user(soul_db)
            user_id = user.id
            soul_db.commit()

        content = "Mentioned feeling drained by the commute"
        # score(importance=1, salience=0.0, evidence=0.8) == 0.2 (still
        # below the 0.30 fold threshold); fold_rate 0.5 -> +0.1/fold, so 6
        # repeats cross the 0.6 crystallization threshold exactly.
        for _ in range(6):
            with runtime_factory() as runtime_db:
                create_memory_candidate(
                    runtime_db,
                    user_id=user_id,
                    content=content,
                    category="minor_observation",
                    importance=1,
                    importance_source="llm",
                    source="llm",
                    salience={"emotional_salience": 0.0, "evidence_strength": 0.8},
                )
                runtime_db.commit()

            await run_soul_writer(
                user_id, soul_db_factory=soul_factory, runtime_db_factory=runtime_factory
            )

        with soul_factory() as soul_db:
            traces = soul_db.scalars(
                select(LatentTrace).where(LatentTrace.user_id == user_id)
            ).all()
            assert len(traces) == 1
            trace = traces[0]
            assert len(trace.evidence_refs) == 6
            assert trace.weight == pytest.approx(0.6)  # 6 folds of s=0.2, fold_rate 0.5
            assert should_crystallize(trace.weight)
    finally:
        soul_engine.dispose()
        runtime_engine.dispose()


# ---------------------------------------------------------------------------
# 9. Vault export/import round-trip
# ---------------------------------------------------------------------------


def test_vault_round_trip_preserves_latent_traces():
    with _soul_db_session() as soul_db:
        user = _make_user(soul_db)
        soul_db.add(
            LatentTrace(
                user_id=user.id,
                topic_key="user:minor_observation:vault_topic",
                kind="observation",
                weight=0.42,
                evidence_refs=[{"candidate_id": 7, "source_message_ids": [1, 2]}],
                first_seen=datetime.now(UTC),
                last_seen=datetime.now(UTC),
            )
        )
        soul_db.commit()
        user_id = user.id

        snapshot = export_database_snapshot(soul_db, user_id=user_id)
        assert len(snapshot["latentTraces"]) == 1
        assert snapshot["latentTraces"][0]["topic_key"] == "user:minor_observation:vault_topic"

        restore_database_snapshot(soul_db, snapshot, scope="full")
        soul_db.commit()

        traces = soul_db.scalars(
            select(LatentTrace).where(LatentTrace.user_id == user_id)
        ).all()
        assert len(traces) == 1
        assert traces[0].topic_key == "user:minor_observation:vault_topic"
        assert traces[0].weight == pytest.approx(0.42)
        assert traces[0].evidence_refs == [{"candidate_id": 7, "source_message_ids": [1, 2]}]


# ---------------------------------------------------------------------------
# 10. get_latent_config wiring
# ---------------------------------------------------------------------------


def test_get_latent_config_reads_settings_defaults():
    config = get_latent_config()
    assert config.promotion_threshold == 0.30
    assert config.crystallization_threshold == 0.60
    assert config.fold_rate == 0.5
    assert config.weekly_decay == 0.98
    assert config.max_traces_per_user == 500
