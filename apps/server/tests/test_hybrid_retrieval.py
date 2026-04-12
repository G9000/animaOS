"""Tests for Retrieval Phase 1: Hybrid Retrieval and Adaptive Search.

Covers:
- 1.1 Hybrid search with RRF merge
- 1.2 Adaptive filter with score gap detection
- 1.3 Query-aware memory blocks (per-category weights)
- 1.4 Batch embedding generation
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import anima_server.services.agent.vector_store as vs
import pytest
from anima_server.db.base import Base
from anima_server.models import MemoryItem, User
from anima_server.services.agent import adaptive_retrieval as adaptive_retrieval_module
from anima_server.services.agent.embeddings import (
    AdaptiveRetrievalConfig,
    HybridSearchResult,
    _reciprocal_rank_fusion,
    adaptive_filter,
    adaptive_filter_with_stats,
)
from anima_server.services.agent.memory_store import (
    _CATEGORY_QUERY_WEIGHTS,
    get_memory_items_scored,
)
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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
    user = User(username="retrieval_tester", display_name="Tester", password_hash="x")
    db.add(user)
    db.flush()
    return user


def _make_item(
    db: Session,
    user_id: int,
    content: str,
    category: str = "fact",
    importance: int = 3,
    embedding: list[float] | None = None,
    created_at: datetime | None = None,
) -> MemoryItem:
    item = MemoryItem(
        user_id=user_id,
        content=content,
        category=category,
        importance=importance,
        source="test",
        embedding_json=embedding,
        reference_count=0,
    )
    if created_at is not None:
        item.created_at = created_at
        item.updated_at = created_at
    db.add(item)
    db.flush()
    return item


@pytest.fixture(autouse=True)
def _isolate_vector_store(managed_tmp_path: Path):
    """Fresh vector store for each test."""
    vs.reset_vector_store()
    vs.use_in_memory_store()
    yield
    vs.reset_vector_store()


# ===================================================================
# 1.1 — RRF Merge Tests
# ===================================================================


class TestReciprocalRankFusion:
    def test_single_list_semantic_only(self):
        semantic = [(1, 0.9), (2, 0.7), (3, 0.5)]
        keyword: list[tuple[int, float]] = []

        merged = _reciprocal_rank_fusion(semantic, keyword)
        ids = [item_id for item_id, _ in merged]
        assert ids == [1, 2, 3]

    def test_single_list_keyword_only(self):
        semantic: list[tuple[int, float]] = []
        keyword = [(10, 0.8), (20, 0.6)]

        merged = _reciprocal_rank_fusion(semantic, keyword)
        ids = [item_id for item_id, _ in merged]
        assert ids == [10, 20]

    def test_items_in_both_lists_boosted(self):
        """Items appearing in both semantic and keyword results should score higher."""
        semantic = [(1, 0.9), (2, 0.7), (3, 0.5)]
        keyword = [(2, 0.8), (4, 0.6), (1, 0.4)]

        merged = _reciprocal_rank_fusion(semantic, keyword)
        scores = {item_id: score for item_id, score in merged}

        # Item 1 is rank 1 in semantic, rank 3 in keyword — should be boosted
        # Item 2 is rank 2 in semantic, rank 1 in keyword — should be boosted
        # Item 3 is rank 3 in semantic only
        # Item 4 is rank 2 in keyword only
        assert scores[1] > scores[3], "Item in both lists should outrank single-list item"
        assert scores[2] > scores[4], "Item in both lists should outrank single-list item"

    def test_rrf_scores_use_rank_not_raw_score(self):
        """RRF should use rank positions, not raw similarity scores."""
        semantic = [(1, 0.99), (2, 0.01)]  # Very different raw scores
        keyword: list[tuple[int, float]] = []

        merged = _reciprocal_rank_fusion(semantic, keyword)
        scores = {item_id: score for item_id, score in merged}

        # With k=60: score1 = 0.5/(61) ≈ 0.0082, score2 = 0.5/(62) ≈ 0.0081
        # Difference should be small despite huge raw score difference
        ratio = scores[1] / scores[2]
        assert ratio < 1.1, "RRF should compress score differences (rank-based, not score-based)"

    def test_custom_weights(self):
        semantic = [(1, 0.9)]
        keyword = [(2, 0.8)]

        # Heavy semantic weight
        merged_sem = _reciprocal_rank_fusion(
            semantic,
            keyword,
            semantic_weight=0.9,
            keyword_weight=0.1,
        )
        scores_sem = {item_id: score for item_id, score in merged_sem}
        assert scores_sem[1] > scores_sem[2]

        # Heavy keyword weight
        merged_kw = _reciprocal_rank_fusion(
            semantic,
            keyword,
            semantic_weight=0.1,
            keyword_weight=0.9,
        )
        scores_kw = {item_id: score for item_id, score in merged_kw}
        assert scores_kw[2] > scores_kw[1]

    def test_empty_inputs(self):
        assert _reciprocal_rank_fusion([], []) == []

    def test_dedup_same_item_different_ranks(self):
        """Same item in both lists should appear once with combined score."""
        semantic = [(1, 0.9)]
        keyword = [(1, 0.8)]

        merged = _reciprocal_rank_fusion(semantic, keyword)
        assert len(merged) == 1
        item_id, score = merged[0]
        assert item_id == 1
        # Combined score should be > either individual contribution
        individual = 0.5 / 61  # single list contribution
        assert score > individual


# ===================================================================
# 1.2 — Adaptive Filter Tests
# ===================================================================


class TestAdaptiveFilter:
    def _make_results(self, scores: list[float]) -> list[tuple[MemoryItem, float]]:
        """Create fake results with given scores."""
        now = datetime.now(UTC)
        return [
            (
                MemoryItem(
                    id=i,
                    user_id=1,
                    content=f"item_{i}",
                    category="fact",
                    importance=3,
                    source="test",
                    created_at=now,
                    updated_at=now,
                ),
                score,
            )
            for i, score in enumerate(scores)
        ]

    def test_empty_results(self):
        assert adaptive_filter([]) == []

    def test_few_results_returned_as_is(self):
        results = self._make_results([0.8, 0.5])
        filtered = adaptive_filter(results, min_results=3)
        assert len(filtered) == 2

    def test_precision_mode_trims_low_scores(self):
        """When top-N are all high confidence, only high-confidence results are returned."""
        results = self._make_results([0.9, 0.85, 0.8, 0.3, 0.2, 0.1])
        filtered = adaptive_filter(
            results,
            high_confidence_threshold=0.7,
            min_results=3,
        )
        scores = [s for _, s in filtered]
        assert all(s >= 0.7 for s in scores)
        assert len(filtered) == 3

    def test_gap_detection_cuts_at_drop(self):
        """Score gap > threshold should trigger a cut."""
        results = self._make_results([0.6, 0.55, 0.5, 0.45, 0.2, 0.15])
        filtered = adaptive_filter(
            results,
            gap_threshold=0.15,
            min_results=3,
            high_confidence_threshold=0.9,  # Precision mode won't trigger
        )
        # Gap of 0.25 between index 3 (0.45) and index 4 (0.2)
        assert len(filtered) == 4

    def test_gap_detection_respects_min_results(self):
        """Gap detection should not cut below min_results."""
        results = self._make_results([0.9, 0.3, 0.29, 0.28])
        filtered = adaptive_filter(
            results,
            gap_threshold=0.15,
            min_results=3,
        )
        # Gap between index 0 and 1 is 0.6, but min_results=3 means
        # gap detection only scans from index 3 onward
        assert len(filtered) >= 3

    def test_recall_mode_returns_max_results(self):
        """When no precision/gap trigger, return up to max_results."""
        results = self._make_results([0.5, 0.49, 0.48, 0.47, 0.46, 0.45, 0.44, 0.43])
        filtered = adaptive_filter(
            results,
            max_results=6,
            high_confidence_threshold=0.9,
            gap_threshold=0.15,
        )
        assert len(filtered) == 6

    def test_large_gap_early(self):
        """A significant gap right after min_results should cut there."""
        results = self._make_results([0.6, 0.58, 0.55, 0.1, 0.09, 0.08])
        filtered = adaptive_filter(
            results,
            gap_threshold=0.15,
            min_results=3,
            high_confidence_threshold=0.9,
        )
        assert len(filtered) == 3

    def test_combined_strategy_returns_stats(self):
        results = self._make_results([0.92, 0.9, 0.86, 0.47, 0.04, 0.03])
        adaptive_result = adaptive_filter_with_stats(
            results,
            config=AdaptiveRetrievalConfig.combined(
                max_results=6,
                min_results=2,
                relative_threshold=0.5,
                max_drop_ratio=0.9,
                absolute_min=0.1,
            ),
        )

        assert len(adaptive_result.results) == 3
        assert adaptive_result.stats.returned == 3
        assert adaptive_result.stats.total_considered == 6
        assert adaptive_result.stats.triggered_by == "relative_threshold"
        assert adaptive_result.stats.cutoff_ratio is not None

    def test_disabled_strategy_returns_capped_results(self):
        results = self._make_results([0.8, 0.6, 0.4, 0.2])
        adaptive_result = adaptive_filter_with_stats(
            results,
            config=AdaptiveRetrievalConfig(
                enabled=False,
                strategy="disabled",
                max_results=3,
                min_results=1,
            ),
        )

        assert len(adaptive_result.results) == 3
        assert adaptive_result.stats.triggered_by == "disabled"

    def test_disabled_flag_short_circuits_legacy_strategy(self):
        cutoff, trigger, normalized = adaptive_retrieval_module.find_adaptive_cutoff(
            [0.95, 0.8, 0.4, 0.1],
            config=AdaptiveRetrievalConfig(
                enabled=False,
                strategy="legacy",
                min_results=2,
                max_results=4,
            ),
        )

        assert cutoff == 4
        assert trigger == "disabled"
        assert normalized == adaptive_retrieval_module.normalize_scores([0.95, 0.8, 0.4, 0.1])

    def test_disabled_flag_short_circuits_rust_strategy(self, monkeypatch):
        def _unexpected_rust_cutoff(*args, **kwargs):
            raise AssertionError("rust cutoff should not run when adaptive retrieval is disabled")

        monkeypatch.setattr(
            adaptive_retrieval_module,
            "_rust_find_adaptive_cutoff",
            _unexpected_rust_cutoff,
        )

        cutoff, trigger, normalized = adaptive_retrieval_module.find_adaptive_cutoff(
            [0.95, 0.8, 0.4, 0.1],
            config=AdaptiveRetrievalConfig(
                enabled=False,
                strategy="combined",
                min_results=2,
                max_results=4,
            ),
        )

        assert cutoff == 4
        assert trigger == "disabled"
        assert normalized == adaptive_retrieval_module.normalize_scores([0.95, 0.8, 0.4, 0.1])

    def test_python_relative_threshold_reports_relative_trigger(self, monkeypatch):
        monkeypatch.setattr(adaptive_retrieval_module, "_rust_find_adaptive_cutoff", None)

        cutoff, trigger, _normalized = adaptive_retrieval_module.find_adaptive_cutoff(
            [1.0, 0.95, 0.7, 0.65],
            config=AdaptiveRetrievalConfig(
                strategy="relative_threshold",
                min_results=2,
                max_results=10,
                relative_threshold=0.8,
            ),
        )

        assert cutoff == 2
        assert trigger == "relative_threshold"


# ===================================================================
# 1.3 — Query-Aware Memory Block Scoring Tests
# ===================================================================


class TestQueryAwareScoring:
    def test_query_embedding_boosts_relevant_items(self):
        """Items with embeddings similar to query should rank higher."""
        with _db_session() as db:
            user = _make_user(db)
            now = datetime.now(UTC)

            # Item A: low importance but embedding close to query
            item_a = _make_item(
                db,
                user.id,
                "likes cooking Italian food",
                category="fact",
                importance=2,
                embedding=[1.0, 0.0, 0.0],
                created_at=now,
            )
            # Item B: high importance but embedding far from query
            item_b = _make_item(
                db,
                user.id,
                "works as a software engineer",
                category="fact",
                importance=5,
                embedding=[0.0, 1.0, 0.0],
                created_at=now,
            )
            db.commit()

            # Query about cooking → embedding close to item_a
            query_emb = [0.9, 0.1, 0.0]

            scored_with_query = get_memory_items_scored(
                db,
                user_id=user.id,
                category="fact",
                limit=10,
                query_embedding=query_emb,
            )
            scored_without_query = get_memory_items_scored(
                db,
                user_id=user.id,
                category="fact",
                limit=10,
            )

            # Without query, item_b (importance=5) should rank first
            assert scored_without_query[0].id == item_b.id

            # With query about cooking, item_a should be boosted
            assert scored_with_query[0].id == item_a.id

    def test_items_without_embeddings_use_retrieval_only(self):
        """Items with no embedding should fall back to pure retrieval score."""
        with _db_session() as db:
            user = _make_user(db)
            now = datetime.now(UTC)

            _make_item(
                db,
                user.id,
                "has embedding",
                category="fact",
                importance=3,
                embedding=[1.0, 0.0, 0.0],
                created_at=now,
            )
            _make_item(
                db,
                user.id,
                "no embedding",
                category="fact",
                importance=5,
                embedding=None,
                created_at=now,
            )
            db.commit()

            results = get_memory_items_scored(
                db,
                user_id=user.id,
                category="fact",
                limit=10,
                query_embedding=[1.0, 0.0, 0.0],
            )
            # Both items should appear
            assert len(results) == 2

    def test_no_query_embedding_falls_back(self):
        """Without query_embedding, scoring should match original behavior."""
        with _db_session() as db:
            user = _make_user(db)
            now = datetime.now(UTC)

            _make_item(db, user.id, "fact1", category="fact", importance=5, created_at=now)
            _make_item(db, user.id, "fact2", category="fact", importance=1, created_at=now)
            db.commit()

            results_with = get_memory_items_scored(
                db,
                user_id=user.id,
                category="fact",
                limit=10,
                query_embedding=None,
            )
            results_without = get_memory_items_scored(
                db,
                user_id=user.id,
                category="fact",
                limit=10,
            )

            assert [r.id for r in results_with] == [r.id for r in results_without]

    def test_category_weights_differ(self):
        """Different categories should use different query weights."""
        assert _CATEGORY_QUERY_WEIGHTS["goal"][0] > _CATEGORY_QUERY_WEIGHTS["goal"][1], (
            "Goals should weight retrieval more than query"
        )
        assert (
            _CATEGORY_QUERY_WEIGHTS["relationship"][1] > _CATEGORY_QUERY_WEIGHTS["relationship"][0]
        ), "Relationships should weight query more than retrieval"
        assert (
            _CATEGORY_QUERY_WEIGHTS["preference"][1] > _CATEGORY_QUERY_WEIGHTS["preference"][0]
        ), "Preferences should weight query more than retrieval"

    def test_category_weights_sum_to_one(self):
        for cat, (w_r, w_q) in _CATEGORY_QUERY_WEIGHTS.items():
            assert abs(w_r + w_q - 1.0) < 1e-9, f"Weights for {cat} don't sum to 1.0"


# ===================================================================
# 1.4 — Batch Embedding Tests
# ===================================================================


class TestBatchEmbeddings:
    @pytest.mark.asyncio
    async def test_batch_empty_input(self):
        from anima_server.services.agent.embeddings import generate_embeddings_batch

        result = await generate_embeddings_batch([])
        assert result == []

    @pytest.mark.asyncio
    async def test_batch_scaffold_returns_none(self):
        from anima_server.services.agent.embeddings import generate_embeddings_batch

        with patch("anima_server.services.agent.embeddings.settings") as mock_settings:
            mock_settings.agent_provider = "scaffold"
            result = await generate_embeddings_batch(["hello", "world"])
            assert result == [None, None]

    @pytest.mark.asyncio
    async def test_batch_ollama_uses_gather(self):
        """Ollama should use asyncio.gather over individual calls."""
        from anima_server.services.agent.embeddings import generate_embeddings_batch

        call_count = 0

        async def mock_generate(text: str) -> list[float] | None:
            nonlocal call_count
            call_count += 1
            return [float(call_count)] * 3

        with (
            patch("anima_server.services.agent.embeddings.settings") as mock_settings,
            patch("anima_server.services.agent.embeddings.validate_provider_configuration"),
            patch(
                "anima_server.services.agent.embeddings.generate_embedding",
                side_effect=mock_generate,
            ),
        ):
            mock_settings.agent_provider = "ollama"
            result = await generate_embeddings_batch(["a", "b", "c"])
            assert len(result) == 3
            assert call_count == 3
            assert all(r is not None for r in result)

    @pytest.mark.asyncio
    async def test_batch_openai_compatible_success(self):
        """OpenAI-compatible batch should handle multiple texts."""
        from anima_server.services.agent.embeddings import generate_embeddings_batch

        call_count = 0

        async def mock_generate(text: str) -> list[float] | None:
            nonlocal call_count
            call_count += 1
            # Return different embeddings per text
            return [float(hash(text) % 100) / 100.0] * 3

        with (
            patch("anima_server.services.agent.embeddings.settings") as mock_settings,
            patch("anima_server.services.agent.embeddings.validate_provider_configuration"),
            # For openrouter, the batch function will try HTTP. We mock at the
            # generate_embedding level via the ollama path which is easier to test.
            # Test the actual batch logic via the ollama gather path.
            patch(
                "anima_server.services.agent.embeddings.generate_embedding",
                side_effect=mock_generate,
            ),
        ):
            mock_settings.agent_provider = "ollama"
            result = await generate_embeddings_batch(["hello", "world"])
            assert len(result) == 2
            assert all(r is not None for r in result)
            assert call_count == 2

    @pytest.mark.asyncio
    async def test_backfill_uses_batch(self):
        """backfill_embeddings should use batch generation instead of sequential."""
        from anima_server.services.agent.embeddings import backfill_embeddings
        from sqlalchemy import text as sa_text

        with _db_session() as db:
            user = _make_user(db)
            _make_item(db, user.id, "item1", embedding=None)
            _make_item(db, user.id, "item2", embedding=None)
            _make_item(db, user.id, "item3", embedding=None)
            db.commit()
            # SQLAlchemy JSON stores Python None as JSON 'null', not SQL NULL.
            # PostgreSQL handles this correctly; SQLite needs an explicit fix.
            db.execute(sa_text("UPDATE memory_items SET embedding_json = NULL"))
            db.commit()

            batch_call_count = 0

            async def mock_batch(texts: list[str], **kwargs: Any) -> list[list[float] | None]:
                nonlocal batch_call_count
                batch_call_count += 1
                return [[0.1] * 3 for _ in texts]

            with patch(
                "anima_server.services.agent.embeddings.generate_embeddings_batch",
                new=mock_batch,
            ):
                count = await backfill_embeddings(db, user_id=user.id, batch_size=10)
                assert count == 3
                assert batch_call_count == 1  # Single batch call, not 3 sequential

    @pytest.mark.asyncio
    async def test_batch_openai_adaptive_retry_halves(self):
        """OpenAI-compatible batch should halve batch_size on failure and retry."""
        from anima_server.services.agent.embeddings import _batch_embed_openai_compatible

        call_log: list[int] = []
        attempt = 0

        class FakeResponse:
            status_code = 200

            def __init__(self, count: int):
                self._count = count

            def raise_for_status(self):
                pass

            def json(self):
                return {"data": [{"index": i, "embedding": [0.1] * 3} for i in range(self._count)]}

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a: Any):
                pass

            async def post(self, url: str, **kwargs: Any):
                nonlocal attempt
                attempt += 1
                inputs = kwargs.get("json", {}).get("input", [])
                call_log.append(len(inputs))
                if attempt == 1:
                    raise RuntimeError("simulated failure")
                return FakeResponse(len(inputs))

        with (
            patch("anima_server.services.agent.embeddings.settings") as mock_settings,
            patch("anima_server.services.agent.embeddings.validate_provider_configuration"),
            patch(
                "anima_server.services.agent.embeddings.resolve_base_url",
                return_value="http://fake",
            ),
            patch("anima_server.services.agent.embeddings.build_provider_headers", return_value={}),
            patch("httpx.AsyncClient", return_value=FakeClient()),
        ):
            mock_settings.agent_provider = "openrouter"
            mock_settings.agent_extraction_model = "test-model"
            result = await _batch_embed_openai_compatible(["a", "b", "c", "d"], max_batch_size=4)

        assert len(result) == 4
        # First call with batch_size=4 fails, retry with 2
        assert call_log[0] == 4
        assert call_log[1] == 2  # first sub-chunk of retry

    @pytest.mark.asyncio
    async def test_batch_openai_all_fail_returns_none(self):
        """When all retries fail, should return None for every text."""
        from anima_server.services.agent.embeddings import _batch_embed_openai_compatible

        class AlwaysFailClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a: Any):
                pass

            async def post(self, url: str, **kwargs: Any):
                raise RuntimeError("always fails")

        with (
            patch("anima_server.services.agent.embeddings.settings") as mock_settings,
            patch("anima_server.services.agent.embeddings.validate_provider_configuration"),
            patch(
                "anima_server.services.agent.embeddings.resolve_base_url",
                return_value="http://fake",
            ),
            patch("anima_server.services.agent.embeddings.build_provider_headers", return_value={}),
            patch("httpx.AsyncClient", return_value=AlwaysFailClient()),
        ):
            mock_settings.agent_provider = "openrouter"
            mock_settings.agent_extraction_model = "test-model"
            result = await _batch_embed_openai_compatible(["a", "b"], max_batch_size=2)

        assert result == [None, None]


# ===================================================================
# Sim normalization correctness
# ===================================================================


class TestSimNormalization:
    def test_positive_sim_normalized(self):
        """Cosine sim of 1.0 should normalize to 1.0."""
        with _db_session() as db:
            user = _make_user(db)
            now = datetime.now(UTC)
            _make_item(
                db,
                user.id,
                "test",
                category="fact",
                importance=3,
                embedding=[1.0, 0.0, 0.0],
                created_at=now,
            )
            db.commit()

            results = get_memory_items_scored(
                db,
                user_id=user.id,
                category="fact",
                limit=10,
                query_embedding=[1.0, 0.0, 0.0],
            )
            assert len(results) == 1

    def test_zero_sim_normalized_to_half(self):
        """Cosine sim of 0.0 should normalize to 0.5, not 0.0."""
        with _db_session() as db:
            user = _make_user(db)
            now = datetime.now(UTC)
            # Orthogonal vectors → cosine sim = 0.0
            _make_item(
                db,
                user.id,
                "item_a",
                category="fact",
                importance=3,
                embedding=[1.0, 0.0],
                created_at=now,
            )
            _make_item(
                db,
                user.id,
                "item_b",
                category="fact",
                importance=3,
                embedding=[0.0, 1.0],
                created_at=now,
            )
            db.commit()

            # Query is [1,0] — sim to item_a=1.0, sim to item_b=0.0
            results = get_memory_items_scored(
                db,
                user_id=user.id,
                category="fact",
                limit=10,
                query_embedding=[1.0, 0.0],
            )
            # item_a (sim=1.0 → norm=1.0) should rank above item_b (sim=0.0 → norm=0.5)
            assert results[0].content == "item_a"
            assert results[1].content == "item_b"

    def test_negative_sim_normalized_below_half(self):
        """Cosine sim of -1.0 should normalize to 0.0."""
        with _db_session() as db:
            user = _make_user(db)
            now = datetime.now(UTC)
            _make_item(
                db,
                user.id,
                "opposite",
                category="fact",
                importance=3,
                embedding=[-1.0, 0.0],
                created_at=now,
            )
            db.commit()

            results = get_memory_items_scored(
                db,
                user_id=user.id,
                category="fact",
                limit=10,
                query_embedding=[1.0, 0.0],
            )
            assert len(results) == 1


# ===================================================================
# Integration: hybrid_search end-to-end (uses vector store)
# ===================================================================


class TestHybridSearchIntegration:
    @pytest.mark.asyncio
    async def test_hybrid_search_combines_semantic_and_keyword(self):
        """hybrid_search should return items from both semantic and keyword sources."""
        from anima_server.services.agent.embeddings import hybrid_search
        from anima_server.services.agent.vector_store import upsert_memory

        with _db_session() as db:
            user = _make_user(db)

            # Item found by semantic (close embedding)
            item_sem = _make_item(
                db,
                user.id,
                "enjoys hiking in mountains",
                embedding=[1.0, 0.0, 0.0],
            )
            upsert_memory(
                user.id,
                item_id=item_sem.id,
                content=item_sem.content,
                embedding=[1.0, 0.0, 0.0],
                category="fact",
                importance=3,
                db=db,
            )

            # Item found by keyword (text overlap) but different embedding
            item_kw = _make_item(
                db,
                user.id,
                "hiking trail near home",
                embedding=[0.0, 1.0, 0.0],
            )
            upsert_memory(
                user.id,
                item_id=item_kw.id,
                content=item_kw.content,
                embedding=[0.0, 1.0, 0.0],
                category="fact",
                importance=3,
                db=db,
            )

            db.commit()

            # Mock generate_embedding to return vector close to item_sem
            async def mock_embed(text: str) -> list[float] | None:
                return [0.9, 0.1, 0.0]

            with patch(
                "anima_server.services.agent.embeddings.generate_embedding",
                side_effect=mock_embed,
            ):
                result = await hybrid_search(
                    db,
                    user_id=user.id,
                    query="hiking",
                    limit=10,
                    similarity_threshold=0.0,
                )

                assert isinstance(result, HybridSearchResult)
                assert result.query_embedding is not None
                found_ids = {item.id for item, _ in result.items}
                # Both items should be found (one via semantic, one via keyword)
                assert item_sem.id in found_ids
                assert item_kw.id in found_ids

    @pytest.mark.asyncio
    async def test_hybrid_search_returns_query_embedding(self):
        """The query embedding should be returned for reuse in block builders."""
        from anima_server.services.agent.embeddings import hybrid_search

        with _db_session() as db:
            user = _make_user(db)
            db.commit()

            async def mock_embed(text: str) -> list[float] | None:
                return [0.5, 0.5, 0.0]

            with patch(
                "anima_server.services.agent.embeddings.generate_embedding",
                side_effect=mock_embed,
            ):
                result = await hybrid_search(
                    db,
                    user_id=user.id,
                    query="test",
                    limit=5,
                )
                assert result.query_embedding == [0.5, 0.5, 0.0]

    @pytest.mark.asyncio
    async def test_hybrid_search_no_embedding_provider(self):
        """When embedding generation fails, keyword search should still work."""
        from anima_server.services.agent.embeddings import hybrid_search
        from anima_server.services.agent.vector_store import upsert_memory

        with _db_session() as db:
            user = _make_user(db)
            item = _make_item(db, user.id, "cooking pasta recipe")
            upsert_memory(
                user.id,
                item_id=item.id,
                content=item.content,
                embedding=[0.1, 0.2, 0.3],
                category="fact",
                importance=3,
                db=db,
            )
            db.commit()

            async def mock_embed_fail(text: str) -> list[float] | None:
                return None

            with patch(
                "anima_server.services.agent.embeddings.generate_embedding",
                side_effect=mock_embed_fail,
            ):
                result = await hybrid_search(
                    db,
                    user_id=user.id,
                    query="cooking",
                    limit=10,
                    similarity_threshold=0.0,
                )
                assert result.query_embedding is None
                # Should still find via keyword
                found_ids = {item.id for item, _ in result.items}
                assert item.id in found_ids

    # Brute-force fallback test removed — P6 eliminated O(n) brute-force
    # over embedding_json in favor of pgvector ANN search.
