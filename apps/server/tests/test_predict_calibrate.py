"""Tests for predict-calibrate consolidation -- F3."""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Quality Gates (heuristic-based)
# ---------------------------------------------------------------------------


class TestApplyQualityGates:
    """F3.3 — persistence, specificity, utility, independence filters."""

    def _gates(self, statements):
        from anima_server.services.agent.predict_calibrate import apply_quality_gates
        return apply_quality_gates(statements=statements)

    # -- Persistence: reject temporal markers --

    def test_reject_temporal_today(self):
        stmts = [{"content": "User is tired today", "category": "fact", "confidence": 0.8}]
        assert self._gates(stmts) == []

    def test_reject_temporal_right_now(self):
        stmts = [{"content": "User is working right now", "category": "fact", "confidence": 0.8}]
        assert self._gates(stmts) == []

    def test_reject_temporal_currently(self):
        stmts = [{"content": "User is currently reading a book", "category": "fact", "confidence": 0.7}]
        assert self._gates(stmts) == []

    def test_reject_temporal_yesterday(self):
        stmts = [{"content": "User went shopping yesterday", "category": "fact", "confidence": 0.7}]
        assert self._gates(stmts) == []

    def test_reject_temporal_this_morning(self):
        stmts = [{"content": "User had coffee this morning", "category": "fact", "confidence": 0.7}]
        assert self._gates(stmts) == []

    # -- Specificity: accept detailed statements --

    def test_accept_specific_restaurant(self):
        stmts = [{"content": "User's favorite restaurant is Sushi Dai in Tokyo", "category": "preference", "confidence": 0.9}]
        result = self._gates(stmts)
        assert len(result) == 1
        assert result[0]["content"] == "User's favorite restaurant is Sushi Dai in Tokyo"

    def test_reject_too_short(self):
        stmts = [{"content": "hi", "category": "fact", "confidence": 0.5}]
        assert self._gates(stmts) == []

    def test_reject_vague(self):
        stmts = [{"content": "Likes food", "category": "preference", "confidence": 0.6}]
        # 2 words is below the 3-word threshold
        assert self._gates(stmts) == []

    # -- Utility: reject "User said/asked" --

    def test_reject_user_said(self):
        stmts = [{"content": "User said hello to the assistant", "category": "fact", "confidence": 0.5}]
        assert self._gates(stmts) == []

    def test_reject_user_asked(self):
        stmts = [{"content": "User asked about the weather forecast", "category": "fact", "confidence": 0.5}]
        assert self._gates(stmts) == []

    def test_reject_user_mentioned(self):
        stmts = [{"content": "User mentioned something about work", "category": "fact", "confidence": 0.5}]
        assert self._gates(stmts) == []

    # -- Independence: reject context-dependent references --

    def test_reject_agreed_with_that(self):
        stmts = [{"content": "User agreed with that suggestion from assistant", "category": "fact", "confidence": 0.6}]
        assert self._gates(stmts) == []

    def test_reject_the_thing_we_discussed(self):
        stmts = [{"content": "The thing we discussed was interesting to user", "category": "fact", "confidence": 0.6}]
        assert self._gates(stmts) == []

    # -- Mixed: keep good, reject bad --

    def test_mixed_batch(self):
        stmts = [
            {"content": "User works at Google as a software engineer", "category": "fact", "confidence": 0.9},
            {"content": "User is tired today", "category": "fact", "confidence": 0.5},
            {"content": "User said hello", "category": "fact", "confidence": 0.3},
            {"content": "User lives in Berlin with their partner", "category": "fact", "confidence": 0.8},
        ]
        result = self._gates(stmts)
        contents = [r["content"] for r in result]
        assert "User works at Google as a software engineer" in contents
        assert "User lives in Berlin with their partner" in contents
        assert "User is tired today" not in contents
        assert "User said hello" not in contents

    # -- Passthrough: statements without content are skipped --

    def test_skip_empty_content(self):
        stmts = [{"content": "", "category": "fact"}, {"category": "fact"}]
        assert self._gates(stmts) == []


# ---------------------------------------------------------------------------
# ID Hallucination Protection (F3.11)
# ---------------------------------------------------------------------------


class TestIDMapping:
    """F3.11 — map real memory IDs to sequential ints before LLM."""

    def test_build_id_map(self):
        from anima_server.services.agent.predict_calibrate import _build_id_map
        real_ids = [101, 205, 307]
        id_map, reverse_map = _build_id_map(real_ids)
        assert id_map == {101: 1, 205: 2, 307: 3}
        assert reverse_map == {1: 101, 2: 205, 3: 307}

    def test_round_trip(self):
        from anima_server.services.agent.predict_calibrate import _build_id_map
        real_ids = [42, 99, 7, 1001]
        id_map, reverse_map = _build_id_map(real_ids)
        for real_id in real_ids:
            mapped = id_map[real_id]
            assert reverse_map[mapped] == real_id

    def test_empty_ids(self):
        from anima_server.services.agent.predict_calibrate import _build_id_map
        id_map, reverse_map = _build_id_map([])
        assert id_map == {}
        assert reverse_map == {}


# ---------------------------------------------------------------------------
# Cold-Start Path (F3.5)
# ---------------------------------------------------------------------------


class TestColdStartPath:
    """F3.5 — when < 5 existing facts, fall back to direct extraction."""

    @pytest.mark.asyncio
    async def test_cold_start_uses_direct_extraction(self, monkeypatch):
        """With fewer than 5 facts, predict_calibrate_extraction should
        fall back to extract_memories_via_llm."""
        from anima_server.services.agent import predict_calibrate

        # Mock hybrid_search to return < 5 results
        async def mock_hybrid_search(db, *, user_id, query, limit=15, **kw):
            from anima_server.services.agent.embeddings import HybridSearchResult
            return HybridSearchResult(items=[], query_embedding=None)

        # Track whether direct extraction was called
        direct_called = []

        async def mock_extract_direct(*, user_message, assistant_response):
            from anima_server.services.agent.consolidation import LLMExtractionResult
            direct_called.append(True)
            return LLMExtractionResult(
                memories=[{"content": "User likes pizza very much", "category": "preference", "importance": 3}],
                emotion=None,
            )

        monkeypatch.setattr(predict_calibrate, "hybrid_search", mock_hybrid_search)
        monkeypatch.setattr(predict_calibrate, "extract_memories_via_llm", mock_extract_direct)

        result, _emotion = await predict_calibrate.predict_calibrate_extraction(
            user_id=1,
            user_message="I love pizza",
            assistant_response="That's great!",
            db=None,  # type: ignore[arg-type]
        )
        assert len(direct_called) == 1
        assert len(result) >= 1


# ---------------------------------------------------------------------------
# Full Pipeline (mocked LLM)
# ---------------------------------------------------------------------------


class TestPredictCalibratePipeline:
    """Integration test for the full predict-calibrate pipeline with mocked LLM."""

    @pytest.mark.asyncio
    async def test_full_pipeline_with_mocked_llm(self, monkeypatch):
        from unittest.mock import MagicMock
        from anima_server.services.agent import predict_calibrate
        from anima_server.services.agent.embeddings import HybridSearchResult

        # Create mock memory items with enough facts (>= 5)
        mock_items = []
        facts = [
            "User works at Google",
            "User lives in Munich",
            "User is 30 years old",
            "User likes hiking in the mountains",
            "User has a dog named Max",
            "User prefers dark coffee in morning",
        ]
        for i, content in enumerate(facts, start=1):
            item = MagicMock()
            item.id = i * 100
            item.content = content
            item.category = "fact"
            item.importance = 3
            mock_items.append(item)

        async def mock_hybrid_search(db, *, user_id, query, limit=15, **kw):
            return HybridSearchResult(
                items=[(item, 0.5) for item in mock_items],
                query_embedding=[0.1] * 10,
            )

        # Mock predict: return a prediction string
        async def mock_predict(*, existing_facts, conversation_summary):
            return "User will likely mention their job at Google."

        # Mock delta extraction: return a new fact not in existing
        async def mock_extract_delta(*, user_message, assistant_response, prediction):
            return [
                {
                    "content": "User is moving from Munich to Berlin next month",
                    "category": "fact",
                    "confidence": 0.9,
                    "reason": "contradictory",
                    "detected_emotion": None,
                },
            ]

        monkeypatch.setattr(predict_calibrate, "hybrid_search", mock_hybrid_search)
        monkeypatch.setattr(predict_calibrate, "predict_episode_knowledge", mock_predict)
        monkeypatch.setattr(predict_calibrate, "extract_knowledge_delta", mock_extract_delta)
        # Mock df to return content as-is (no encryption in tests)
        monkeypatch.setattr(predict_calibrate, "df", lambda uid, content, **kw: content)

        result, _emotion = await predict_calibrate.predict_calibrate_extraction(
            user_id=1,
            user_message="Actually, I'm moving to Berlin next month.",
            assistant_response="That's exciting! Berlin is a great city.",
            db=None,  # type: ignore[arg-type]
        )

        assert len(result) >= 1
        assert any("Berlin" in r["content"] for r in result)

    @pytest.mark.asyncio
    async def test_fallback_on_predict_failure(self, monkeypatch):
        """F3.9 — if predict-calibrate fails, fall back to direct extraction."""
        from anima_server.services.agent import predict_calibrate
        from anima_server.services.agent.embeddings import HybridSearchResult
        from unittest.mock import MagicMock

        mock_items = []
        for i in range(6):
            item = MagicMock()
            item.id = i + 1
            item.content = f"Fact number {i}"
            item.category = "fact"
            item.importance = 3
            mock_items.append(item)

        async def mock_hybrid_search(db, *, user_id, query, limit=15, **kw):
            return HybridSearchResult(
                items=[(item, 0.5) for item in mock_items],
                query_embedding=[0.1] * 10,
            )

        async def mock_predict(*, existing_facts, conversation_summary):
            raise RuntimeError("LLM unavailable")

        direct_called = []

        async def mock_extract_direct(*, user_message, assistant_response):
            from anima_server.services.agent.consolidation import LLMExtractionResult
            direct_called.append(True)
            return LLMExtractionResult(
                memories=[{"content": "User enjoys swimming at the beach", "category": "preference", "importance": 3}],
                emotion=None,
            )

        monkeypatch.setattr(predict_calibrate, "hybrid_search", mock_hybrid_search)
        monkeypatch.setattr(predict_calibrate, "predict_episode_knowledge", mock_predict)
        monkeypatch.setattr(predict_calibrate, "extract_memories_via_llm", mock_extract_direct)
        monkeypatch.setattr(predict_calibrate, "df", lambda uid, content, **kw: content)

        result, _emotion = await predict_calibrate.predict_calibrate_extraction(
            user_id=1,
            user_message="I like swimming",
            assistant_response="That's nice!",
            db=None,  # type: ignore[arg-type]
        )

        assert len(direct_called) == 1
        assert len(result) >= 1

    @pytest.mark.asyncio
    async def test_emotion_preserved_in_output(self, monkeypatch):
        """F3.12 — detected_emotion must be passed through."""
        from anima_server.services.agent import predict_calibrate
        from anima_server.services.agent.embeddings import HybridSearchResult
        from unittest.mock import MagicMock

        mock_items = []
        for i in range(6):
            item = MagicMock()
            item.id = i + 1
            item.content = f"Fact number {i}"
            item.category = "fact"
            item.importance = 3
            mock_items.append(item)

        async def mock_hybrid_search(db, *, user_id, query, limit=15, **kw):
            return HybridSearchResult(
                items=[(item, 0.5) for item in mock_items],
                query_embedding=[0.1] * 10,
            )

        async def mock_predict(*, existing_facts, conversation_summary):
            return "User will mention their work."

        async def mock_extract_delta(*, user_message, assistant_response, prediction):
            return [
                {
                    "content": "User is frustrated with their manager at work",
                    "category": "fact",
                    "confidence": 0.9,
                    "reason": "surprising",
                    "detected_emotion": {
                        "emotion": "frustrated",
                        "confidence": 0.8,
                        "trajectory": "escalating",
                        "evidence_type": "explicit",
                        "evidence": "User said they are frustrated",
                    },
                },
            ]

        monkeypatch.setattr(predict_calibrate, "hybrid_search", mock_hybrid_search)
        monkeypatch.setattr(predict_calibrate, "predict_episode_knowledge", mock_predict)
        monkeypatch.setattr(predict_calibrate, "extract_knowledge_delta", mock_extract_delta)
        monkeypatch.setattr(predict_calibrate, "df", lambda uid, content, **kw: content)

        result, pc_emotion = await predict_calibrate.predict_calibrate_extraction(
            user_id=1,
            user_message="My manager is driving me crazy",
            assistant_response="I'm sorry to hear that.",
            db=None,  # type: ignore[arg-type]
        )

        assert len(result) >= 1
        # Bug 3 fix: emotion_data should be returned even when items exist
        assert pc_emotion is not None
        assert pc_emotion["emotion"] == "frustrated"
        emotion_item = [r for r in result if "frustrated" in r["content"]]
        assert len(emotion_item) == 1
        assert emotion_item[0].get("detected_emotion") is not None
        assert emotion_item[0]["detected_emotion"]["emotion"] == "frustrated"
