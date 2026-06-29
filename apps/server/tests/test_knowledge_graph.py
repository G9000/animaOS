"""Tests for knowledge graph — F4."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from anima_server.db.base import Base
from anima_server.models import KGEntity, KGRelation, MemoryItem, MemoryItemEvidence, User
from anima_server.services.agent import knowledge_graph as knowledge_graph_module
from anima_server.services.agent.embedding_integrity import compute_embedding_checksum
from anima_server.services.agent.knowledge_graph import (
    _map_ids_back,
    _map_ids_to_sequential,
    _mention_boost,
    extract_entities_and_relations,
    get_relation_history,
    graph_context_for_query,
    ingest_conversation_graph,
    normalize_entity_name,
    rerank_graph_results,
    resolve_latest_relation_belief,
    search_graph,
    upsert_entity,
    upsert_relation,
)
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


def _create_user(session: Session, username: str = "kg-test") -> User:
    user = User(
        username=username,
        password_hash="not-used",
        display_name="KG Test User",
    )
    session.add(user)
    session.commit()
    return user


def _create_memory_evidence(
    session: Session,
    *,
    user_id: int,
    text: str,
    observed_at: datetime,
    confidence: float = 1.0,
) -> MemoryItemEvidence:
    item = MemoryItem(
        user_id=user_id,
        content=text,
        category="relationship",
        importance=4,
        source="test",
    )
    session.add(item)
    session.flush()
    evidence = MemoryItemEvidence(
        user_id=user_id,
        memory_item_id=item.id,
        source_kind="test_fixture",
        observed_at=observed_at,
        confidence=confidence,
        evidence_text=text,
    )
    session.add(evidence)
    session.flush()
    return evidence


def test_temporal_knowledge_graph_model_metadata() -> None:
    relation_columns = set(KGRelation.__table__.c.keys())
    assert {
        "observed_at",
        "valid_from",
        "valid_to",
        "confidence",
        "status",
        "evidence_id",
        "supersedes_relation_id",
        "evolves_from_relation_id",
    }.issubset(relation_columns)
    assert "aliases_json" in KGEntity.__table__.c


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
                db,
                user_id=user.id,
                name="Alice",
                entity_type="person",
                description="User's sister",
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

    def test_upsert_same_exact_name_tolerates_type_drift(self):
        with _db_session() as db:
            user = _create_user(db)
            original = upsert_entity(db, user_id=user.id, name="Anima", entity_type="concept")

            updated = upsert_entity(db, user_id=user.id, name="Anima", entity_type="project")

            assert updated.id == original.id
            assert updated.mentions == 2
            entities = list(
                db.scalars(
                    select(KGEntity).where(
                        KGEntity.user_id == user.id,
                        KGEntity.name_normalized == "anima",
                    )
                ).all()
            )
            assert len(entities) == 1

    def test_upsert_updates_description_if_longer(self):
        with _db_session() as db:
            user = _create_user(db)
            upsert_entity(db, user_id=user.id, name="Alice", description="Sister")
            e = upsert_entity(
                db,
                user_id=user.id,
                name="Alice",
                description="User's older sister, lives in Munich",
            )
            assert "Munich" in e.description

    def test_upsert_stores_embedding_checksum_when_embedding_provided(self):
        with _db_session() as db:
            user = _create_user(db)
            embedding = [0.1, 0.2, 0.3]

            entity = upsert_entity(
                db,
                user_id=user.id,
                name="Alice",
                entity_type="person",
                embedding=embedding,
            )

            assert entity.embedding_json == embedding
            assert entity.embedding_checksum == compute_embedding_checksum(embedding)

    def test_upsert_updates_embedding_checksum_on_existing_entity(self):
        with _db_session() as db:
            user = _create_user(db)
            upsert_entity(db, user_id=user.id, name="Project Aurora", entity_type="project")
            updated_embedding = [0.9, 0.1]

            entity = upsert_entity(
                db,
                user_id=user.id,
                name="Project Aurora",
                entity_type="project",
                embedding=updated_embedding,
            )

            assert entity.embedding_json == updated_embedding
            assert entity.embedding_checksum == compute_embedding_checksum(updated_embedding)

    def test_upsert_rejects_invalid_embedding_payload(self):
        with _db_session() as db:
            user = _create_user(db)

            with pytest.raises(ValueError, match="KG entity embedding"):
                upsert_entity(
                    db,
                    user_id=user.id,
                    name="Bad Embedding",
                    entity_type="concept",
                    embedding=[1.0, float("nan")],
                )

    def test_upsert_entity_deduplicates_aliases_and_similar_embeddings(self):
        with _db_session() as db:
            user = _create_user(db)

            canonical = upsert_entity(
                db,
                user_id=user.id,
                name="Ari",
                entity_type="person",
                aliases=["Ariane"],
                embedding=[1.0, 0.0],
            )
            alias_match = upsert_entity(
                db,
                user_id=user.id,
                name="Ariane",
                entity_type="person",
                embedding=[1.0, 0.0],
            )
            semantic_match = upsert_entity(
                db,
                user_id=user.id,
                name="Anima collaborator",
                entity_type="person",
                aliases=["Ari"],
                embedding=[0.99, 0.01],
            )

            assert alias_match.id == canonical.id
            assert semantic_match.id == canonical.id
            assert set(semantic_match.aliases_json or []) >= {
                "Ari",
                "Ariane",
                "Anima collaborator",
            }


# ── T3: upsert_relation ─────────────────────────────────────────────


class TestUpsertRelation:
    def test_create_relation(self):
        with _db_session() as db:
            user = _create_user(db)
            upsert_entity(db, user_id=user.id, name="User", entity_type="person")
            upsert_entity(db, user_id=user.id, name="Google", entity_type="organization")
            rel = upsert_relation(
                db,
                user_id=user.id,
                source_name="User",
                destination_name="Google",
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
                db,
                user_id=user.id,
                source_name="User",
                destination_name="Google",
                relation_type="works_at",
            )
            rel2 = upsert_relation(
                db,
                user_id=user.id,
                source_name="User",
                destination_name="Google",
                relation_type="works_at",
            )
            assert rel2 is not None
            assert rel2.mentions == 2

    def test_returns_none_for_missing_entity(self):
        with _db_session() as db:
            user = _create_user(db)
            upsert_entity(db, user_id=user.id, name="User", entity_type="person")
            rel = upsert_relation(
                db,
                user_id=user.id,
                source_name="User",
                destination_name="Nonexistent",
                relation_type="knows",
            )
            assert rel is None

    def test_upsert_relation_records_temporal_evidence_fields(self):
        with _db_session() as db:
            user = _create_user(db)
            observed_at = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
            valid_from = datetime(2026, 5, 20, 9, 0, tzinfo=UTC)
            evidence = _create_memory_evidence(
                db,
                user_id=user.id,
                text="User said Ari collaborates on Temporal Memory.",
                observed_at=observed_at,
                confidence=0.82,
            )
            upsert_entity(db, user_id=user.id, name="Ari", entity_type="person")
            upsert_entity(db, user_id=user.id, name="Temporal Memory", entity_type="project")

            rel = upsert_relation(
                db,
                user_id=user.id,
                source_name="Ari",
                destination_name="Temporal Memory",
                relation_type="collaborates_on",
                source_memory_id=evidence.memory_item_id,
                evidence_id=evidence.id,
                observed_at=observed_at,
                valid_from=valid_from,
                confidence=0.82,
            )

            assert rel is not None
            assert rel.status == "active"
            assert rel.evidence_id == evidence.id
            assert rel.source_memory_id == evidence.memory_item_id
            assert rel.observed_at == observed_at
            assert rel.valid_from == valid_from
            assert rel.valid_to is None
            assert rel.confidence == pytest.approx(0.82)

    def test_relation_evolution_preserves_history_and_resolves_latest_belief(self):
        with _db_session() as db:
            user = _create_user(db)
            old_observed_at = datetime(2026, 1, 1, 8, 0, tzinfo=UTC)
            new_observed_at = datetime(2026, 6, 1, 8, 0, tzinfo=UTC)
            old_evidence = _create_memory_evidence(
                db,
                user_id=user.id,
                text="User said they work at Acme.",
                observed_at=old_observed_at,
                confidence=0.75,
            )
            new_evidence = _create_memory_evidence(
                db,
                user_id=user.id,
                text="User said they now work at Anthropic.",
                observed_at=new_observed_at,
                confidence=0.93,
            )
            upsert_entity(db, user_id=user.id, name="User", entity_type="person")
            upsert_entity(db, user_id=user.id, name="Acme", entity_type="organization")
            upsert_entity(db, user_id=user.id, name="Anthropic", entity_type="organization")
            old_relation = upsert_relation(
                db,
                user_id=user.id,
                source_name="User",
                destination_name="Acme",
                relation_type="works_at",
                evidence_id=old_evidence.id,
                observed_at=old_observed_at,
                valid_from=old_observed_at,
                confidence=0.75,
            )
            assert old_relation is not None

            new_relation = upsert_relation(
                db,
                user_id=user.id,
                source_name="User",
                destination_name="Anthropic",
                relation_type="works_at",
                evidence_id=new_evidence.id,
                observed_at=new_observed_at,
                valid_from=new_observed_at,
                confidence=0.93,
                supersedes_relation_id=old_relation.id,
                evolves_from_relation_id=old_relation.id,
            )
            db.flush()

            assert new_relation is not None
            assert old_relation.status == "superseded"
            assert old_relation.valid_to == new_observed_at
            assert new_relation.status == "active"
            assert new_relation.supersedes_relation_id == old_relation.id
            assert new_relation.evolves_from_relation_id == old_relation.id

            history = get_relation_history(
                db,
                user_id=user.id,
                source_name="User",
                relation_type="works_at",
            )
            assert [entry["destination"] for entry in history] == ["Acme", "Anthropic"]
            assert [entry["status"] for entry in history] == ["superseded", "active"]

            latest = resolve_latest_relation_belief(
                db,
                user_id=user.id,
                source_name="User",
                relation_type="works_at",
            )
            assert latest is not None
            assert latest["relation_id"] == new_relation.id
            assert latest["destination"] == "Anthropic"
            assert latest["evidence_ids"] == [new_evidence.id]
            assert latest["history_count"] == 2

            past = resolve_latest_relation_belief(
                db,
                user_id=user.id,
                source_name="User",
                relation_type="works_at",
                as_of=datetime(2026, 3, 1, 8, 0, tzinfo=UTC),
            )
            assert past is not None
            assert past["relation_id"] == old_relation.id
            assert past["destination"] == "Acme"

            active_results = search_graph(
                db,
                user_id=user.id,
                entity_names=["User"],
                max_depth=1,
            )
            assert {(r["relation"], r["destination"]) for r in active_results} == {
                ("works_at", "Anthropic")
            }


    # Additional relation lifecycle coverage.
    def test_readding_superseded_relation_creates_new_interval(self):
        with _db_session() as db:
            user = _create_user(db)
            old_observed_at = datetime(2026, 1, 1, 8, 0, tzinfo=UTC)
            new_observed_at = datetime(2026, 6, 1, 8, 0, tzinfo=UTC)
            return_observed_at = datetime(2026, 9, 1, 8, 0, tzinfo=UTC)
            old_evidence = _create_memory_evidence(
                db,
                user_id=user.id,
                text="User said they work at Acme.",
                observed_at=old_observed_at,
                confidence=0.75,
            )
            new_evidence = _create_memory_evidence(
                db,
                user_id=user.id,
                text="User said they now work at Anthropic.",
                observed_at=new_observed_at,
                confidence=0.93,
            )
            return_evidence = _create_memory_evidence(
                db,
                user_id=user.id,
                text="User said they returned to Acme.",
                observed_at=return_observed_at,
                confidence=0.88,
            )
            upsert_entity(db, user_id=user.id, name="User", entity_type="person")
            upsert_entity(db, user_id=user.id, name="Acme", entity_type="organization")
            upsert_entity(db, user_id=user.id, name="Anthropic", entity_type="organization")
            old_relation = upsert_relation(
                db,
                user_id=user.id,
                source_name="User",
                destination_name="Acme",
                relation_type="works_at",
                evidence_id=old_evidence.id,
                observed_at=old_observed_at,
                valid_from=old_observed_at,
                confidence=0.75,
            )
            assert old_relation is not None
            new_relation = upsert_relation(
                db,
                user_id=user.id,
                source_name="User",
                destination_name="Anthropic",
                relation_type="works_at",
                evidence_id=new_evidence.id,
                observed_at=new_observed_at,
                valid_from=new_observed_at,
                confidence=0.93,
                supersedes_relation_id=old_relation.id,
                evolves_from_relation_id=old_relation.id,
            )
            assert new_relation is not None

            returned_relation = upsert_relation(
                db,
                user_id=user.id,
                source_name="User",
                destination_name="Acme",
                relation_type="works_at",
                evidence_id=return_evidence.id,
                observed_at=return_observed_at,
                valid_from=return_observed_at,
                confidence=0.88,
            )
            db.flush()

            assert returned_relation is not None
            assert returned_relation.id != old_relation.id
            assert old_relation.status == "superseded"
            assert old_relation.valid_to == new_observed_at
            assert returned_relation.status == "active"
            assert returned_relation.evidence_id == return_evidence.id
            assert returned_relation.valid_from == return_observed_at

            history = get_relation_history(
                db,
                user_id=user.id,
                source_name="User",
                relation_type="works_at",
            )
            assert [entry["destination"] for entry in history] == ["Acme", "Anthropic", "Acme"]
            assert [entry["status"] for entry in history] == ["superseded", "active", "active"]

            latest = resolve_latest_relation_belief(
                db,
                user_id=user.id,
                source_name="User",
                relation_type="works_at",
            )
            assert latest is not None
            assert latest["relation_id"] == returned_relation.id
            assert latest["destination"] == "Acme"

    def test_duplicate_relation_without_confidence_preserves_existing_confidence(self):
        with _db_session() as db:
            user = _create_user(db)
            upsert_entity(db, user_id=user.id, name="User", entity_type="person")
            upsert_entity(db, user_id=user.id, name="Acme", entity_type="organization")
            relation = upsert_relation(
                db,
                user_id=user.id,
                source_name="User",
                destination_name="Acme",
                relation_type="works_at",
                confidence=0.42,
            )
            assert relation is not None

            duplicate = upsert_relation(
                db,
                user_id=user.id,
                source_name="User",
                destination_name="Acme",
                relation_type="works_at",
            )
            db.flush()

            assert duplicate is relation
            assert duplicate.mentions == 2
            assert duplicate.confidence == 0.42


# ── T4: search_graph depth=1 ────────────────────────────────────────


class TestSearchGraphDepth1:
    def test_direct_relation(self):
        with _db_session() as db:
            user = _create_user(db)
            upsert_entity(db, user_id=user.id, name="A", entity_type="person")
            upsert_entity(db, user_id=user.id, name="B", entity_type="person")
            upsert_relation(
                db,
                user_id=user.id,
                source_name="A",
                destination_name="B",
                relation_type="knows",
            )
            db.flush()

            results = search_graph(
                db,
                user_id=user.id,
                entity_names=["A"],
                max_depth=1,
            )
            assert len(results) == 1
            assert results[0]["source"] == "A"
            assert results[0]["destination"] == "B"
            assert results[0]["relation"] == "knows"

    def test_resolves_start_entity_by_alias(self):
        with _db_session() as db:
            user = _create_user(db)
            upsert_entity(
                db,
                user_id=user.id,
                name="Bob",
                entity_type="person",
                aliases=["Robert"],
            )
            upsert_entity(db, user_id=user.id, name="Acme", entity_type="organization")
            upsert_relation(
                db,
                user_id=user.id,
                source_name="Bob",
                destination_name="Acme",
                relation_type="works_at",
            )
            db.flush()

            results = search_graph(
                db,
                user_id=user.id,
                entity_names=["Robert"],
                max_depth=1,
            )

            assert len(results) == 1
            assert results[0]["source"] == "Bob"
            assert results[0]["destination"] == "Acme"
            assert results[0]["relation"] == "works_at"


# ── T5: search_graph depth=2 ────────────────────────────────────────


class TestSearchGraphDepth2:
    def test_two_hop_traversal(self):
        with _db_session() as db:
            user = _create_user(db)
            upsert_entity(db, user_id=user.id, name="A", entity_type="person")
            upsert_entity(db, user_id=user.id, name="B", entity_type="person")
            upsert_entity(db, user_id=user.id, name="C", entity_type="place")
            upsert_relation(
                db,
                user_id=user.id,
                source_name="A",
                destination_name="B",
                relation_type="knows",
            )
            upsert_relation(
                db,
                user_id=user.id,
                source_name="B",
                destination_name="C",
                relation_type="lives_in",
            )
            db.flush()

            results = search_graph(
                db,
                user_id=user.id,
                entity_names=["A"],
                max_depth=2,
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
                db,
                user_id=user.id,
                source_name="A",
                destination_name="B",
                relation_type="knows",
            )
            db.flush()

            # Search from B should find A (reverse direction)
            results = search_graph(
                db,
                user_id=user.id,
                entity_names=["B"],
                max_depth=1,
            )
            assert len(results) == 1
            assert results[0]["source"] == "A"
            assert results[0]["destination"] == "B"


# ── T14: rerank_graph_results ────────────────────────────────────────


class TestRerankGraphResults:
    def test_relevant_triples_rank_first(self):
        results = [
            {
                "source": "Cat",
                "relation": "is_a",
                "destination": "Animal",
                "source_type": "concept",
                "destination_type": "concept",
            },
            {
                "source": "Alice",
                "relation": "works_at",
                "destination": "Google",
                "source_type": "person",
                "destination_type": "organization",
            },
            {
                "source": "Berlin",
                "relation": "located_in",
                "destination": "Germany",
                "source_type": "place",
                "destination_type": "place",
            },
        ]
        ranked = rerank_graph_results(results, "where does Alice work", top_n=3)
        # Alice/Google/works_at triple should rank first for work-related query
        assert ranked[0]["source"] == "Alice"

    def test_empty_results(self):
        assert rerank_graph_results([], "test") == []

    def test_empty_query(self):
        results = [
            {
                "source": "A",
                "relation": "knows",
                "destination": "B",
                "source_type": "",
                "destination_type": "",
            }
        ]
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
                db,
                user_id=user.id,
                source_name="Alice",
                destination_name="Google",
                relation_type="works_at",
            )
            db.flush()

            lines = graph_context_for_query(
                db,
                user_id=user.id,
                query="tell me about Alice",
            )
            assert len(lines) >= 1
            assert "Alice" in lines[0]
            assert "works_at" in lines[0]
            assert "Google" in lines[0]

    def test_returns_empty_for_unknown_query(self):
        with _db_session() as db:
            user = _create_user(db)
            lines = graph_context_for_query(
                db,
                user_id=user.id,
                query="completely unrelated topic",
            )
            assert lines == []

    def test_semantic_fallback_uses_entity_embeddings(self, monkeypatch):
        with _db_session() as db:
            user = _create_user(db)
            alice_embedding = [1.0, 0.0]
            upsert_entity(
                db,
                user_id=user.id,
                name="Alice",
                entity_type="person",
                embedding=alice_embedding,
            )
            upsert_entity(db, user_id=user.id, name="Google", entity_type="organization")
            upsert_relation(
                db,
                user_id=user.id,
                source_name="Alice",
                destination_name="Google",
                relation_type="works_at",
            )
            db.flush()

            monkeypatch.setattr(
                knowledge_graph_module,
                "_generate_query_embedding_sync",
                lambda _query: [1.0, 0.0],
            )

            lines = graph_context_for_query(
                db,
                user_id=user.id,
                query="tell me about the person tied to the search company",
            )

            assert len(lines) >= 1
            assert "Alice" in lines[0]
            assert "Google" in lines[0]

    def test_semantic_fallback_repairs_missing_embedding_checksum(self, monkeypatch):
        with _db_session() as db:
            user = _create_user(db)
            bob_embedding = [0.7, 0.3]
            bob = upsert_entity(
                db,
                user_id=user.id,
                name="Bob",
                entity_type="person",
                embedding=bob_embedding,
            )
            bob.embedding_checksum = None
            upsert_entity(db, user_id=user.id, name="Berlin", entity_type="place")
            upsert_relation(
                db,
                user_id=user.id,
                source_name="Bob",
                destination_name="Berlin",
                relation_type="lives_in",
            )
            db.flush()

            monkeypatch.setattr(
                knowledge_graph_module,
                "_generate_query_embedding_sync",
                lambda _query: [0.7, 0.3],
            )

            lines = graph_context_for_query(
                db,
                user_id=user.id,
                query="who lives in that city",
            )

            assert len(lines) >= 1
            assert bob.embedding_checksum == compute_embedding_checksum(bob_embedding)


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

            upsert_relation(
                db,
                user_id=user.id,
                source_name="User",
                destination_name="Alice",
                relation_type="sister_of",
            )
            upsert_relation(
                db,
                user_id=user.id,
                source_name="Alice",
                destination_name="Bob",
                relation_type="married_to",
            )
            upsert_relation(
                db,
                user_id=user.id,
                source_name="Alice",
                destination_name="Munich",
                relation_type="lives_in",
            )
            db.flush()

            # Depth-2 from User should reach Bob and Munich through Alice
            results = search_graph(
                db,
                user_id=user.id,
                entity_names=["User"],
                max_depth=2,
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
                db,
                user_id=user.id,
                source_name="A",
                destination_name="B",
                relation_type="knows",
            )
            db.flush()

            results = search_graph(
                db,
                user_id=user.id,
                entity_names=["A"],
                max_depth=1,
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
                db,
                user_id=user.id,
                source_name="A",
                destination_name="B",
                relation_type="knows",
            )
            upsert_relation(
                db,
                user_id=user.id,
                source_name="A",
                destination_name="B",
                relation_type="knows",
            )
            db.flush()

            results = search_graph(
                db,
                user_id=user.id,
                entity_names=["A"],
                max_depth=1,
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
                "source": "Alice",
                "relation": "knows",
                "destination": "Berlin",
                "source_type": "person",
                "destination_type": "place",
                "source_mentions": 1,
                "destination_mentions": 1,
                "relation_mentions": 1,
            },
            {
                "source": "Alice",
                "relation": "knows",
                "destination": "Munich",
                "source_type": "person",
                "destination_type": "place",
                "source_mentions": 10,
                "destination_mentions": 5,
                "relation_mentions": 8,
            },
            {
                "source": "Cat",
                "relation": "is_a",
                "destination": "Animal",
                "source_type": "concept",
                "destination_type": "concept",
                "source_mentions": 1,
                "destination_mentions": 1,
                "relation_mentions": 1,
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
        import sys

        # Temporarily hide rank_bm25
        real_module = sys.modules.get("rank_bm25")
        sys.modules["rank_bm25"] = None  # type: ignore[assignment]
        try:
            # Force reimport so the try/except picks up the None
            results = [
                {
                    "source": "X",
                    "relation": "r",
                    "destination": "Y",
                    "source_type": "",
                    "destination_type": "",
                    "source_mentions": 1,
                    "destination_mentions": 1,
                    "relation_mentions": 1,
                },
                {
                    "source": "A",
                    "relation": "r",
                    "destination": "B",
                    "source_type": "",
                    "destination_type": "",
                    "source_mentions": 5,
                    "destination_mentions": 5,
                    "relation_mentions": 5,
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
                db,
                user_id=user.id,
                source_name="Alice",
                destination_name="Google",
                relation_type="works_at",
            )
            upsert_relation(
                db,
                user_id=user.id,
                source_name="Alice",
                destination_name="Google",
                relation_type="works_at",
            )
            upsert_relation(
                db,
                user_id=user.id,
                source_name="Alice",
                destination_name="Google",
                relation_type="works_at",
            )
            db.flush()

            lines = graph_context_for_query(
                db,
                user_id=user.id,
                query="tell me about Alice",
            )
            assert len(lines) >= 1
            assert "[mentioned 3x]" in lines[0]

    def test_low_mention_relation_not_annotated(self):
        with _db_session() as db:
            user = _create_user(db)
            upsert_entity(db, user_id=user.id, name="Bob", entity_type="person")
            upsert_entity(db, user_id=user.id, name="Berlin", entity_type="place")
            upsert_relation(
                db,
                user_id=user.id,
                source_name="Bob",
                destination_name="Berlin",
                relation_type="lives_in",
            )
            db.flush()

            lines = graph_context_for_query(
                db,
                user_id=user.id,
                query="tell me about Bob",
            )
            assert len(lines) >= 1
            assert "[mentioned" not in lines[0]


# ── T13: deterministic triplet extraction ───────────────────────────


class TestDeterministicTripletExtraction:
    @pytest.mark.asyncio()
    async def test_scaffold_mode_extracts_rule_triplets(self, monkeypatch):
        monkeypatch.setattr(
            knowledge_graph_module.settings,
            "agent_provider",
            "scaffold",
            raising=False,
        )

        entities, relations = await extract_entities_and_relations(
            text="",
            user_id=1,
            user_message="I work at Anthropic. My sister Alice lives in Munich.",
            assistant_response="",
        )

        entity_map = {entity["name"]: entity["type"] for entity in entities}
        relation_set = {
            (relation["source"], relation["relation"], relation["destination"])
            for relation in relations
        }

        assert entity_map["User"] == "person"
        assert entity_map["Anthropic"] == "organization"
        assert entity_map["Alice"] == "person"
        assert entity_map["Munich"] == "place"
        assert ("User", "works_at", "Anthropic") in relation_set
        assert ("User", "sister_of", "Alice") in relation_set
        assert ("Alice", "lives_in", "Munich") in relation_set


class TestIngestConversationGraphRules:
    @pytest.mark.asyncio()
    async def test_scaffold_mode_ingests_graph_from_rules(self, monkeypatch):
        monkeypatch.setattr(
            knowledge_graph_module.settings,
            "agent_provider",
            "scaffold",
            raising=False,
        )

        with _db_session() as db:
            user = _create_user(db)

            entities, relations, pruned = await ingest_conversation_graph(
                db,
                user_id=user.id,
                user_message="I work at Anthropic. My sister Alice lives in Munich.",
                assistant_response="",
            )

            assert entities >= 4
            assert relations >= 3
            assert pruned == 0

            stored_entities = {
                entity.name: entity.entity_type
                for entity in db.scalars(
                    select(KGEntity).where(KGEntity.user_id == user.id)
                ).all()
            }
            stored_relations = {
                (
                    db.get(KGEntity, relation.source_id).name,
                    relation.relation_type,
                    db.get(KGEntity, relation.destination_id).name,
                )
                for relation in db.scalars(
                    select(KGRelation).where(KGRelation.user_id == user.id)
                ).all()
            }

            assert stored_entities["User"] == "person"
            assert stored_entities["Anthropic"] == "organization"
            assert stored_entities["Alice"] == "person"
            assert stored_entities["Munich"] == "place"
            assert ("User", "works_at", "Anthropic") in stored_relations
            assert ("User", "sister_of", "Alice") in stored_relations
            assert ("Alice", "lives_in", "Munich") in stored_relations

    @pytest.mark.asyncio()
    async def test_scaffold_mode_populates_entity_embeddings(self, monkeypatch):
        monkeypatch.setattr(
            knowledge_graph_module.settings,
            "agent_provider",
            "scaffold",
            raising=False,
        )

        async def _fake_generate_embeddings_batch(texts: list[str], **_kwargs):
            return [[float(index), 0.5] for index, _text in enumerate(texts, start=1)]

        monkeypatch.setattr(
            knowledge_graph_module,
            "generate_embeddings_batch",
            _fake_generate_embeddings_batch,
        )

        with _db_session() as db:
            user = _create_user(db)

            entities, relations, pruned = await ingest_conversation_graph(
                db,
                user_id=user.id,
                user_message="I work at Anthropic. My sister Alice lives in Munich.",
                assistant_response="",
            )

            assert entities >= 4
            assert relations >= 3
            assert pruned == 0

            stored_entities = list(
                db.scalars(select(KGEntity).where(KGEntity.user_id == user.id)).all()
            )
            assert stored_entities
            assert all(entity.embedding_json is not None for entity in stored_entities)
            assert all(entity.embedding_checksum is not None for entity in stored_entities)
