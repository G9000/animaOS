from __future__ import annotations

import hashlib

from anima_server.models.runtime import RuntimeKnowledgeConcept
from anima_server.services.ingestion.adapters.text import ingest_text_content
from sqlalchemy import select

pytest_plugins = ("conftest_runtime",)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _embedding_for(text: str) -> list[float]:
    lowered = text.lower()
    if "portable" in lowered or "continuity" in lowered:
        return [1.0, *([0.0] * 767)]
    if "citation" in lowered or "evidence" in lowered:
        return [0.5, 0.5, *([0.0] * 766)]
    return [0.0, 0.0, 1.0, *([0.0] * 765)]


def _concept(runtime_db, *, user_id: int, slug: str, title: str, body: str):
    concept = RuntimeKnowledgeConcept(
        user_id=user_id,
        concept_type="claim",
        slug=slug,
        title=title,
        description=f"{title} description",
        body_markdown=body,
        frontmatter_json={"type": "claim", "title": title},
        content_hash=_sha(body),
        status="active",
    )
    runtime_db.add(concept)
    runtime_db.flush()
    return concept


def test_retrieve_knowledge_returns_concepts_and_evidence_spans(runtime_db) -> None:
    from anima_server.services.ingestion.retrieval import (
        retrieve_knowledge,
        upsert_concept_embedding,
        upsert_source_span_embedding,
    )

    concept = _concept(
        runtime_db,
        user_id=1,
        slug="portable-core",
        title="Portable Core",
        body="Portable continuity keeps the local core coherent.",
    )
    _source, _artifacts, spans = ingest_text_content(
        runtime_db,
        user_id=1,
        content="Citation evidence for portable continuity.",
        filename="evidence.txt",
    )
    upsert_concept_embedding(runtime_db, concept=concept, embedding_fn=_embedding_for)
    upsert_source_span_embedding(runtime_db, span=spans[0], embedding_fn=_embedding_for)

    result = retrieve_knowledge(
        runtime_db,
        user_id=1,
        query="portable continuity",
        embedding_fn=_embedding_for,
    )

    assert [item.concept_id for item in result.concepts] == [concept.id]
    assert [item.span_id for item in result.evidence_spans] == [spans[0].id]
    assert result.evidence_spans[0].source_id == spans[0].source_id
    assert result.concepts[0].score >= result.evidence_spans[0].score


def test_retrieve_knowledge_is_user_scoped(runtime_db) -> None:
    from anima_server.services.ingestion.retrieval import (
        retrieve_knowledge,
        upsert_concept_embedding,
    )

    owner = _concept(
        runtime_db,
        user_id=1,
        slug="owner-core",
        title="Owner Core",
        body="Portable continuity for owner.",
    )
    other = _concept(
        runtime_db,
        user_id=2,
        slug="other-core",
        title="Other Core",
        body="Portable continuity for other user.",
    )
    upsert_concept_embedding(runtime_db, concept=owner, embedding_fn=_embedding_for)
    upsert_concept_embedding(runtime_db, concept=other, embedding_fn=_embedding_for)

    result = retrieve_knowledge(
        runtime_db,
        user_id=1,
        query="portable continuity",
        embedding_fn=_embedding_for,
    )

    assert [item.concept_id for item in result.concepts] == [owner.id]
    assert runtime_db.scalar(
        select(RuntimeKnowledgeConcept).where(RuntimeKnowledgeConcept.id == other.id)
    )
