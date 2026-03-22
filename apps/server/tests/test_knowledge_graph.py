"""Tests for knowledge graph — F4."""
from __future__ import annotations

import asyncio
from collections.abc import Generator
from contextlib import contextmanager
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from anima_server.db.base import Base
from anima_server.models import KGEntity, KGRelation, User
from anima_server.services.agent.knowledge_graph import (
    _map_ids_back,
    _map_ids_to_sequential,
    _mention_boost,
    graph_context_for_query,
    normalize_entity_name,
    rerank_graph_results,
    search_graph,
    upsert_entity,
    upsert_relation,
)


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


def _create_user(session: Session, username: str = "kg-test") -> User:
    user = User(
        username=username,
        password_hash="not-used",
        display_name="KG Test User",
    )
    session.add(user)
    session.commit()
    return user


# ── T1: normalize_entity_name ────────────────────────────────────────


class TestNormalizeEntityName:
    def test_basic_spaces(self):
        assert normalize_entity_name("New York City") == "new_york_city"

    def test_with_period(self):
        assert normalize_entity_name("Dr. Alice Smith") == "dr._alice_smith"

    def test_single_word(self):
        assert normalize_entity_name("Alice") == "alice"

    def test_already_normalized(self):
        assert normalize_entity_name("google") == "google"

    def test_mixed_case(self):
        assert normalize_entity_name("Project Aurora") == "project_aurora"

    def test_strips_whitespace(self):
        assert normalize_entity_name("  Berlin  ") == "berlin"

    def test_multiple_spaces(self):
        assert normalize_entity_name("New   York") == "new_york"


# ── T2: upsert_entity ───────────────────────────────────────────────


class TestUpsertEntity:
    def test_create_entity(self):
        with _db_session() as db:
            user = _create_user(db)
            entity = upsert_entity(
                db, user_id=user.id, name="Alice",
                entity_type="person", description="User's sister",
            )
            assert entity.name == "Alice"
            assert entity.name_normalized == "alice"
            assert entity.entity_type == "person"
            assert entity.mentions == 1

    def test_upsert_same_name_increments_mentions(self):
        with _db_session() as db:
            user = _create_user(db)
            e1 = upsert_entity(db, user_id=user.id, name="Alice", entity_type="person")
            e2 = upsert_entity(db, user_id=user.id, name="Alice", entity_type="person")
            assert e1.id == e2.id
            assert e2.mentions == 2

    def test_upsert_updates_type_from_unknown(self):
        with _db_session() as db:
            user = _create_user(db)
            upsert_entity(db, user_id=user.id, name="X")
            e = upsert_entity(db, user_id=user.id, name="X", entity_type="person")
            assert e.entity_type == "person"

    def test_upsert_updates_description_if_longer(self):
        with _db_session() as db:
            user = _create_user(db)
            upsert_entity(db, user_id=user.id, name="Alice", description="Sister")
            e = upsert_entity(db, user_id=user.id, name="Alice", description="User's older sister, lives in Munich")
            assert "Munich" in e.description


# ── T3: upsert_relation ─────────────────────────────────────────────


class TestUpsertRelation:
    def test_create_relation(self):
        with _db_session() as db:
            user = _create_user(db)
            upsert_entity(db, user_id=user.id, name="User", entity_type="person")
            upsert_entity(db, user_id=user.id, name="Google", entity_type="organization")
            rel = upsert_relation(
                db, user_id=user.id,
                source_name="User", destination_name="Google",
                relation_type="works_at",
            )
            assert rel is not None
            assert rel.relation_type == "works_at"
            assert rel.mentions == 1

    def test_upsert_same_relation_increments_mentions(self):
        with _db_session() as db:
            user = _create_user(db)
            upsert_entity(db, user_id=user.id, name="User", entity_type="person")
            upsert_entity(db, user_id=user.id, name="Google", entity_type="organization")
            upsert_relation(
                db, user_id=user.id,
                source_name="User", destination_name="Google",
                relation_type="works_at",
            )
            rel2 = upsert_relation(
                db, user_id=user.id,
                source_name="User", destination_name="Google",
                relation_type="works_at",
            )
            assert rel2 is not None
            assert rel2.mentions == 2

    def test_returns_none_for_missing_entity(self):
        with _db_session() as db:
            user = _create_user(db)
            upsert_entity(db, user_id=user.id, name="User", entity_type="person")
            rel = upsert_relation(
                db, user_id=user.id,
                source_name="User", destination_name="Nonexistent",
                relation_type="knows",
            )
            assert rel is None


# ── T4: search_graph depth=1 ────────────────────────────────────────


class TestSearchGraphDepth1:
    def test_direct_relation(self):
        with _db_session() as db:
            user = _create_user(db)
            upsert_entity(db, user_id=user.id, name="A", entity_type="person")
            upsert_entity(db, user_id=user.id, name="B", entity_type="person")
            upsert_relation(
                db, user_id=user.id,
                source_name="A", destination_name="B",
                relation_type="knows",
            )
            db.flush()

            results = search_graph(
                db, user_id=user.id, entity_names=["A"], max_depth=1,
            )
            assert len(results) == 1
            assert results[0]["source"] == "A"
            assert results[0]["destination"] == "B"
            assert results[0]["relation"] == "knows"


# ── T5: search_graph depth=2 ────────────────────────────────────────


class TestSearchGraphDepth2:
    def test_two_hop_traversal(self):
        with _db_session() as db:
            user = _create_user(db)
            upsert_entity(db, user_id=user.id, name="A", entity_type="person")
            upsert_entity(db, user_id=user.id, name="B", entity_type="person")
            upsert_entity(db, user_id=user.id, name="C", entity_type="place")
            upsert_relation(
                db, user_id=user.id,
                source_name="A", destination_name="B",
                relation_type="knows",
            )
            upsert_relation(
                db, user_id=user.id,
                source_name="B", destination_name="C",
                relation_type="lives_in",
            )
            db.flush()

            results = search_graph(
                db, user_id=user.id, entity_names=["A"], max_depth=2,
            )
            # Should find both A->B and B->C
            assert len(results) == 2
            destinations = {r["destination"] for r in results}
            assert "B" in destinations
            assert "C" in destinations


# ── T6: search_graph bidirectional ───────────────────────────────────


class TestSearchGraphBidirectional:
    def test_reverse_traversal(self):
        with _db_session() as db:
            user = _create_user(db)
            upsert_entity(db, user_id=user.id, name="A", entity_type="person")
            upsert_entity(db, user_id=user.id, name="B", entity_type="person")
            upsert_relation(
                db, user_id=user.id,
                source_name="A", destination_name="B",
                relation_type="knows",
            )
            db.flush()

            # Search from B should find A (reverse direction)
            results = search_graph(
                db, user_id=user.id, entity_names=["B"], max_depth=1,
            )
            assert len(results) == 1
            assert results[0]["source"] == "A"
            assert results[0]["destination"] == "B"


# ── T14: rerank_graph_results ────────────────────────────────────────


class TestRerankGraphResults:
    def test_relevant_triples_rank_first(self):
        results = [
            {"source": "Cat", "relation": "is_a", "destination": "Animal", "source_type": "concept", "destination_type": "concept"},
            {"source": "Alice", "relation": "works_at", "destination": "Google", "source_type": "person", "destination_type": "organization"},
            {"source": "Berlin", "relation": "located_in", "destination": "Germany", "source_type": "place", "destination_type": "place"},
        ]
        ranked = rerank_graph_results(results, "where does Alice work", top_n=3)
        # Alice/Google/works_at triple should rank first for work-related query
        assert ranked[0]["source"] == "Alice"

    def test_empty_results(self):
        assert rerank_graph_results([], "test") == []

    def test_empty_query(self):
        results = [{"source": "A", "relation": "knows", "destination": "B", "source_type": "", "destination_type": ""}]
        ranked = rerank_graph_results(results, "", top_n=5)
        assert len(ranked) == 1


# ── T17: ID hallucination protection ────────────────────────────────


class TestIDHallucinationProtection:
    def test_map_and_round_trip(self):
        items = [
            {"id": 42, "source": "A", "relation": "knows", "destination": "B"},
            {"id": 99, "source": "B", "relation": "lives_in", "destination": "C"},
            {"id": 7, "source": "C", "relation": "part_of", "destination": "D"},
        ]
        mapped, reverse_map = _map_ids_to_sequential(items)

        # Mapped IDs should be sequential
        assert [m["id"] for m in mapped] == [1, 2, 3]

        # Round-trip: map back
        real_ids = _map_ids_back([1, 3], reverse_map)
        assert real_ids == [42, 7]

    def test_map_back_skips_unknown(self):
        _, reverse_map = _map_ids_to_sequential([{"id": 10}])
        result = _map_ids_back([1, 999], reverse_map)
        assert result == [10]  # 999 not in map, skipped


# ── T8: graph_context_for_query ──────────────────────────────────────


class TestGraphContextForQuery:
    def test_returns_formatted_strings(self):
        with _db_session() as db:
            user = _create_user(db)
            upsert_entity(db, user_id=user.id, name="Alice", entity_type="person")
            upsert_entity(db, user_id=user.id, name="Google", entity_type="organization")
            upsert_relation(
                db, user_id=user.id,
                source_name="Alice", destination_name="Google",
                relation_type="works_at",
            )
            db.flush()

            lines = graph_context_for_query(
                db, user_id=user.id, query="tell me about Alice",
            )
            assert len(lines) >= 1
            assert "Alice" in lines[0]
            assert "works_at" in lines[0]
            assert "Google" in lines[0]

    def test_returns_empty_for_unknown_query(self):
        with _db_session() as db:
            user = _create_user(db)
            lines = graph_context_for_query(
                db, user_id=user.id, query="completely unrelated topic",
            )
            assert lines == []


# ── Integration: full graph scenario ─────────────────────────────────


class TestFullGraphScenario:
    def test_family_graph(self):
        """Build a small family graph and verify traversal."""
        with _db_session() as db:
            user = _create_user(db)
            # Build graph
            for name, etype in [
                ("User", "person"),
                ("Alice", "person"),
                ("Bob", "person"),
                ("Munich", "place"),
            ]:
                upsert_entity(db, user_id=user.id, name=name, entity_type=etype)

            upsert_relation(db, user_id=user.id, source_name="User", destination_name="Alice", relation_type="sister_of")
            upsert_relation(db, user_id=user.id, source_name="Alice", destination_name="Bob", relation_type="married_to")
            upsert_relation(db, user_id=user.id, source_name="Alice", destination_name="Munich", relation_type="lives_in")
            db.flush()

            # Depth-2 from User should reach Bob and Munich through Alice
            results = search_graph(
                db, user_id=user.id, entity_names=["User"], max_depth=2,
            )
            names_found = set()
            for r in results:
                names_found.add(r["source"])
                names_found.add(r["destination"])

            assert "Alice" in names_found
            assert "Bob" in names_found
            assert "Munich" in names_found


# ── T9: mention counting in search results ────────────────────────────


class TestMentionCountsInSearchResults:
    def test_search_results_include_mention_counts(self):
        with _db_session() as db:
            user = _create_user(db)
            upsert_entity(db, user_id=user.id, name="A", entity_type="person")
            upsert_entity(db, user_id=user.id, name="B", entity_type="person")
            upsert_relation(
                db, user_id=user.id,
                source_name="A", destination_name="B",
                relation_type="knows",
            )
            db.flush()

            results = search_graph(
                db, user_id=user.id, entity_names=["A"], max_depth=1,
            )
            assert len(results) == 1
            assert results[0]["source_mentions"] == 1
            assert results[0]["destination_mentions"] == 1
            assert results[0]["relation_mentions"] == 1

    def test_mention_counts_reflect_upserts(self):
        with _db_session() as db:
            user = _create_user(db)
            upsert_entity(db, user_id=user.id, name="A", entity_type="person")
            upsert_entity(db, user_id=user.id, name="B", entity_type="person")
            # Upsert A again to bump mentions
            upsert_entity(db, user_id=user.id, name="A", entity_type="person")
            upsert_entity(db, user_id=user.id, name="A", entity_type="person")
            # Upsert relation twice
            upsert_relation(
                db, user_id=user.id,
                source_name="A", destination_name="B",
                relation_type="knows",
            )
            upsert_relation(
                db, user_id=user.id,
                source_name="A", destination_name="B",
                relation_type="knows",
            )
            db.flush()

            results = search_graph(
                db, user_id=user.id, entity_names=["A"], max_depth=1,
            )
            assert len(results) == 1
            assert results[0]["source_mentions"] == 3  # upserted 3 times
            assert results[0]["destination_mentions"] == 1
            assert results[0]["relation_mentions"] == 2  # upserted 2 times


# ── T10: mention boost function ───────────────────────────────────────


class TestMentionBoost:
    def test_baseline_mentions_give_neutral_boost(self):
        r = {"source_mentions": 1, "destination_mentions": 1, "relation_mentions": 1}
        assert _mention_boost(r) == 1.0

    def test_higher_mentions_give_positive_boost(self):
        r = {"source_mentions": 5, "destination_mentions": 3, "relation_mentions": 2}
        boost = _mention_boost(r)
        assert boost > 1.0

    def test_missing_mention_fields_default_to_neutral(self):
        r = {"source": "A", "relation": "knows", "destination": "B"}
        assert _mention_boost(r) == 1.0

    def test_boost_is_monotonically_increasing(self):
        boosts = []
        for extra in range(10):
            r = {"source_mentions": 1 + extra, "destination_mentions": 1, "relation_mentions": 1}
            boosts.append(_mention_boost(r))
        for i in range(1, len(boosts)):
            assert boosts[i] >= boosts[i - 1]


# ── T11: rerank with mention boost ────────────────────────────────────


class TestRerankWithMentionBoost:
    def test_mention_boost_promotes_high_mention_result(self):
        """Among results with similar BM25 relevance, higher mention counts
        should rank higher thanks to the mention boost multiplier.

        A third unrelated document is included so that BM25 IDF is non-zero
        for the matching terms (IDF is zero when all docs contain the term).
        """
        results = [
            {
                "source": "Alice", "relation": "knows", "destination": "Berlin",
                "source_type": "person", "destination_type": "place",
                "source_mentions": 1, "destination_mentions": 1, "relation_mentions": 1,
            },
            {
                "source": "Alice", "relation": "knows", "destination": "Munich",
                "source_type": "person", "destination_type": "place",
                "source_mentions": 10, "destination_mentions": 5, "relation_mentions": 8,
            },
            {
                "source": "Cat", "relation": "is_a", "destination": "Animal",
                "source_type": "concept", "destination_type": "concept",
                "source_mentions": 1, "destination_mentions": 1, "relation_mentions": 1,
            },
        ]
        ranked = rerank_graph_results(results, "Alice knows", top_n=3)
        # Both Alice triples match "Alice knows" equally via BM25, but the
        # Munich triple has far more mentions and should be boosted ahead.
        alice_results = [r for r in ranked if r["source"] == "Alice"]
        assert len(alice_results) == 2
        assert alice_results[0]["destination"] == "Munich"

    def test_fallback_mention_ordering_without_bm25(self):
        """When rank_bm25 is unavailable, results should be sorted purely
        by mention boost (higher mentions first)."""
        import importlib
        import sys

        # Temporarily hide rank_bm25
        real_module = sys.modules.get("rank_bm25")
        sys.modules["rank_bm25"] = None  # type: ignore[assignment]
        try:
            # Force reimport so the try/except picks up the None
            results = [
                {
                    "source": "X", "relation": "r", "destination": "Y",
                    "source_type": "", "destination_type": "",
                    "source_mentions": 1, "destination_mentions": 1, "relation_mentions": 1,
                },
                {
                    "source": "A", "relation": "r", "destination": "B",
                    "source_type": "", "destination_type": "",
                    "source_mentions": 5, "destination_mentions": 5, "relation_mentions": 5,
                },
            ]
            ranked = rerank_graph_results(results, "some query", top_n=2)
            assert ranked[0]["source"] == "A"
        finally:
            if real_module is not None:
                sys.modules["rank_bm25"] = real_module
            else:
                sys.modules.pop("rank_bm25", None)


# ── T12: graph context annotation ─────────────────────────────────────


class TestGraphContextAnnotation:
    def test_high_mention_relation_is_annotated(self):
        with _db_session() as db:
            user = _create_user(db)
            upsert_entity(db, user_id=user.id, name="Alice", entity_type="person")
            upsert_entity(db, user_id=user.id, name="Google", entity_type="organization")
            # Create the relation 3 times to reach the annotation threshold
            upsert_relation(
                db, user_id=user.id,
                source_name="Alice", destination_name="Google",
                relation_type="works_at",
            )
            upsert_relation(
                db, user_id=user.id,
                source_name="Alice", destination_name="Google",
                relation_type="works_at",
            )
            upsert_relation(
                db, user_id=user.id,
                source_name="Alice", destination_name="Google",
                relation_type="works_at",
            )
            db.flush()

            lines = graph_context_for_query(
                db, user_id=user.id, query="tell me about Alice",
            )
            assert len(lines) >= 1
            assert "[mentioned 3x]" in lines[0]

    def test_low_mention_relation_not_annotated(self):
        with _db_session() as db:
            user = _create_user(db)
            upsert_entity(db, user_id=user.id, name="Bob", entity_type="person")
            upsert_entity(db, user_id=user.id, name="Berlin", entity_type="place")
            upsert_relation(
                db, user_id=user.id,
                source_name="Bob", destination_name="Berlin",
                relation_type="lives_in",
            )
            db.flush()

            lines = graph_context_for_query(
                db, user_id=user.id, query="tell me about Bob",
            )
            assert len(lines) >= 1
            assert "[mentioned" not in lines[0]
