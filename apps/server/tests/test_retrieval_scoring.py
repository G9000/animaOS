"""ARH-012: retrieval scoring correctness.

Covers four gates that used to misbehave:

1. ``absolute_min`` gated on min-max-normalized scores (top always → 1.0), so
   it never rejected a low-confidence result set.  It now gates on the raw
   input scale.
2. Heat ``0.0`` was reserved for "never scored" yet a fully-decayed item could
   underflow to exactly ``0.0`` and *bypass* the visibility floor.  Scored heat
   is now clamped to a tiny epsilon so decayed items stay below the floor.
3. The native retrieval fallback emitted ``(cosine + 1) / 2`` while the rust
   index and pgvector emit raw cosine — one ``similarity_threshold`` behaved
   differently per backend.  The fallback now conforms to the raw-cosine
   contract.
4. ``PgVecStore.search_by_vector`` capped ANN candidates then filtered
   checksums downstream, so it could return fewer than ``limit`` valid rows.
   It now expands the fetch (bounded) until ``limit`` valid rows are collected.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from anima_server.db.base import Base
from anima_server.models import MemoryItem, User
from anima_server.services.agent import adaptive_retrieval, embeddings
from anima_server.services.agent.adaptive_retrieval import (
    AdaptiveRetrievalConfig,
    apply_adaptive_filter,
    find_adaptive_cutoff,
)
from anima_server.services.agent.forgetting import HEAT_VISIBILITY_FLOOR
from anima_server.services.agent.heat_scoring import (
    HEAT_SCORED_EPSILON,
    compute_heat,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture()
def soul_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as db:
        db.add(User(username="scoring", password_hash="x", display_name="S"))
        db.commit()
    yield factory
    engine.dispose()


# --------------------------------------------------------------------------- #
# 1. absolute_min acts on the raw score scale
# --------------------------------------------------------------------------- #


@pytest.fixture()
def _python_cutoff_path(monkeypatch: pytest.MonkeyPatch):
    """Force the pure-Python cutoff path so the raw-scale gating is exercised
    deterministically regardless of whether the rust binding is present."""
    monkeypatch.setattr(adaptive_retrieval, "_rust_find_adaptive_cutoff", None)


def test_absolute_min_rejects_low_confidence_top_score(_python_cutoff_path) -> None:
    # Best match is a barely-related cosine 0.26; the whole set is junk.
    scores = [0.26, 0.24, 0.22, 0.21, 0.2]
    config = AdaptiveRetrievalConfig.combined(
        max_results=12, min_results=3, absolute_min=0.3
    )

    cutoff, trigger, _normalized = find_adaptive_cutoff(scores, config=config)

    assert cutoff == 0
    assert trigger == "below_absolute_min"

    result = apply_adaptive_filter(
        [(f"item-{i}", score) for i, score in enumerate(scores)], config=config
    )
    assert result.results == []


def test_absolute_min_keeps_confident_top_score(_python_cutoff_path) -> None:
    # A genuinely-similar top hit clears the floor; the normalized shape
    # strategies then decide the tail.
    scores = [0.82, 0.80, 0.78, 0.29, 0.10]
    config = AdaptiveRetrievalConfig.combined(
        max_results=12, min_results=3, absolute_min=0.3
    )

    cutoff, trigger, _normalized = find_adaptive_cutoff(scores, config=config)

    assert cutoff >= 3
    assert trigger != "below_absolute_min"


def test_absolute_min_trims_tail_on_raw_scale(_python_cutoff_path) -> None:
    # Top clears the floor, but items past min_results fall below the raw
    # absolute_min and must be cut — even though normalization would keep the
    # relative shape looking healthy.
    scores = [0.9, 0.85, 0.8, 0.12, 0.11]
    config = AdaptiveRetrievalConfig.combined(
        max_results=12, min_results=3, absolute_min=0.3, relative_threshold=0.0
    )

    cutoff, trigger, _normalized = find_adaptive_cutoff(scores, config=config)

    assert cutoff == 3
    assert trigger == "absolute_min"


# --------------------------------------------------------------------------- #
# 2. Heat floor: scored-to-zero vs never-scored
# --------------------------------------------------------------------------- #


def test_scored_heat_never_lands_on_exact_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    from anima_server.services import anima_core_bindings

    # Force the Python path so the clamp is asserted directly.
    monkeypatch.setattr(anima_core_bindings, "rust_compute_heat", None)

    # A superseded item (floor 0.0) last touched decades ago: recency decays
    # to exactly 0.0 via float underflow.
    ancient = datetime(2000, 1, 1, tzinfo=UTC)
    heat = compute_heat(
        access_count=0,
        interaction_depth=0,
        last_accessed_at=ancient,
        importance=0,
        superseded=True,
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert heat > 0.0
    assert heat == pytest.approx(HEAT_SCORED_EPSILON)
    # Crucially: above 0.0 (so it is not mistaken for "never scored") but below
    # the visibility floor (so retrieval filters it out).
    assert heat < HEAT_VISIBILITY_FLOOR


@pytest.mark.asyncio
async def test_decayed_item_filtered_but_unscored_item_visible(
    soul_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    with soul_factory() as db:
        hot = MemoryItem(
            user_id=1, content="hot fact", category="fact", importance=3,
            source="extraction", heat=0.5,
        )
        decayed = MemoryItem(
            user_id=1, content="forgotten fact", category="fact", importance=3,
            source="extraction", heat=HEAT_SCORED_EPSILON,  # scored, decayed
        )
        unscored = MemoryItem(
            user_id=1, content="new fact", category="fact", importance=3,
            source="extraction", heat=0.0,  # never scored
        )
        db.add_all([hot, decayed, unscored])
        db.commit()
        ids = {"hot": hot.id, "decayed": decayed.id, "unscored": unscored.id}

    async def fake_embedding(text: str) -> list[float]:
        return [0.1] * 8

    monkeypatch.setattr(embeddings, "generate_embedding", fake_embedding)
    monkeypatch.setattr(
        embeddings,
        "_semantic_ranked_ids",
        lambda *args, **kwargs: [
            (ids["hot"], 0.9),
            (ids["decayed"], 0.85),
            (ids["unscored"], 0.8),
        ],
    )

    with soul_factory() as db:
        results = await embeddings.semantic_search(
            db, user_id=1, query="fact", limit=10
        )

    surfaced = {item.id for item, _score in results}
    assert ids["hot"] in surfaced
    assert ids["unscored"] in surfaced  # never scored → stays visible
    assert ids["decayed"] not in surfaced  # scored below floor → filtered


# --------------------------------------------------------------------------- #
# 3. Backend score contract: raw cosine in [0, 1]
# --------------------------------------------------------------------------- #


def test_native_vector_search_emits_raw_cosine(tmp_path) -> None:
    from anima_server.services import anima_core_retrieval

    root = tmp_path / "retrieval"

    # query is the +x axis; doc_parallel shares it (cosine 1.0), doc_45 sits at
    # 45 degrees (cosine ~0.707), doc_ortho is orthogonal (cosine 0.0).
    query = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    docs = {
        "parallel": [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        "diagonal": [1.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        "ortho": [0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    }
    expected_cosine = {"parallel": 1.0, "diagonal": 1.0 / math.sqrt(2.0), "ortho": 0.0}

    record_ids = {"parallel": 101, "diagonal": 102, "ortho": 103}
    for name, embedding in docs.items():
        anima_core_retrieval.memory_index_upsert(
            root=root,
            record_id=record_ids[name],
            user_id=1,
            text=name,
            embedding=embedding,
            source_type="memory_item",
            category="fact",
            importance=3,
            created_at=0,
        )

    hits = anima_core_retrieval.memory_index_vector_search(
        root=root, user_id=1, query_embedding=query, limit=10
    )
    score_by_id = {int(hit["record_id"]): float(hit["score"]) for hit in hits}

    for name, rid in record_ids.items():
        assert rid in score_by_id, f"{name} missing from results"
        score = score_by_id[rid]
        assert 0.0 <= score <= 1.0
        # Raw-cosine contract — not the old (cosine + 1) / 2 remap, which would
        # have mapped the orthogonal doc to 0.5 and the diagonal to ~0.85.
        assert score == pytest.approx(expected_cosine[name], abs=1e-4)


# --------------------------------------------------------------------------- #
# 4. pgvector expands the candidate fetch past downstream filtering
# --------------------------------------------------------------------------- #


class _FakeResult:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def all(self) -> list[object]:
        return self._rows


class _ExpandingDb:
    """Returns a growing candidate pool per call, mirroring the store's ``limit
    *= 2`` expansion schedule.  Odd source_ids carry an invalid checksum."""

    def __init__(self, total: int, expansion_sizes: list[int]) -> None:
        self._pool = [
            SimpleNamespace(
                RuntimeEmbedding=SimpleNamespace(
                    source_id=i,
                    content_preview=f"item-{i}",
                    category="fact",
                    importance=3,
                    embedding=[0.1] * 8,
                    embedding_checksum="x",
                    source_type="memory_item",
                ),
                similarity=1.0 - i / 1000.0,
            )
            for i in range(total)
        ]
        self._sizes = expansion_sizes
        self.calls = 0

    def execute(self, _stmt):
        size = self._sizes[min(self.calls, len(self._sizes) - 1)]
        self.calls += 1
        return _FakeResult(self._pool[:size])

    def flush(self) -> None:  # pragma: no cover - not exercised here
        pass


def test_search_by_vector_expands_until_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    from anima_server.services.agent import pgvec_store
    from anima_server.services.agent.embedding_integrity import CheckedEmbedding

    # Only every 4th row is valid — sparse enough that limit=10 needs the
    # expansion from 20 to 40 candidates.  Invalid rows carry a None embedding.
    valid_ids = {i for i in range(40) if i % 4 == 0}  # 10 valid across 40 rows
    db = _ExpandingDb(total=40, expansion_sizes=[20, 40])
    for row in db._pool:
        if row.RuntimeEmbedding.source_id not in valid_ids:
            row.RuntimeEmbedding.embedding = None

    def check_null_aware(embedding, checksum):
        if embedding is None:
            return CheckedEmbedding(None, None, "invalid")
        return CheckedEmbedding(embedding, "x", "valid")

    monkeypatch.setattr(pgvec_store, "check_embedding", check_null_aware)

    store = pgvec_store.PgVecStore(db)
    results = store.search_by_vector(1, query_embedding=[0.1] * 8, limit=10)

    assert len(results) == 10
    assert db.calls == 2  # first pool (20) under-delivered, expanded to 40
    assert all(r.item_id in valid_ids for r in results)


def test_combined_strategy_bypasses_rust_for_raw_absolute_cutoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Rust cutoff compares absolute_min against normalized scores, so
    combined/absolute_threshold must stay on the Python path to cut on the raw
    scale — e.g. [0.9, 0.85, 0.8, 0.29, 0.0] with absolute_min=0.3 cuts at 3."""
    from anima_server.services.agent import adaptive_retrieval as ar

    calls: list[int] = []

    def _rust_spy(capped_scores, **kwargs):
        calls.append(1)
        # Would keep the 0.29 tail hit (normalized 0.32 > 0.3).
        return len(capped_scores), "rust_no_cut", ar.normalize_scores(capped_scores)

    monkeypatch.setattr(ar, "_rust_find_adaptive_cutoff", _rust_spy)

    config = ar.AdaptiveRetrievalConfig.combined(
        max_results=12, min_results=3, absolute_min=0.3, relative_threshold=0.0
    )
    cutoff, trigger, _norm = ar.find_adaptive_cutoff(
        [0.9, 0.85, 0.8, 0.29, 0.0], config=config
    )

    assert cutoff == 3
    assert trigger == "absolute_min"
    assert calls == []  # Rust bypassed for the raw-absolute strategy
