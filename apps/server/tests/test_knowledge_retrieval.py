from __future__ import annotations

import hashlib

from anima_server.models.runtime import RuntimeKnowledgeConcept
from anima_server.models.runtime_embedding import RuntimeEmbedding
from anima_server.services.ingestion.adapters.text import ingest_text_content
from sqlalchemy import select

pytest_plugins = ("conftest_runtime",)

# Dim derived from the actual bound column rather than hardcoded: the pgvector
# column dimension is fixed once per process (baked in at first import of
# RuntimeEmbedding from the then-current default embedding provider), so a
# literal here would drift out of sync whenever that default changes.
_EMBED_DIM = RuntimeEmbedding.__table__.c.embedding.type.dim


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _embedding_for(text: str) -> list[float]:
    lowered = text.lower()
    if "portable" in lowered or "continuity" in lowered:
        return [1.0, *([0.0] * (_EMBED_DIM - 1))]
    if "citation" in lowered or "evidence" in lowered:
        return [0.5, 0.5, *([0.0] * (_EMBED_DIM - 2))]
    return [0.0, 0.0, 1.0, *([0.0] * (_EMBED_DIM - 3))]


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

    assert concept.id in [item.concept_id for item in result.concepts]
    assert [item.span_id for item in result.evidence_spans] == [spans[0].id]
    assert result.evidence_spans[0].source_id == spans[0].source_id
    assert result.concepts[0].score >= result.evidence_spans[0].score


def test_retrieve_knowledge_fills_missing_span_hits_from_text(runtime_db) -> None:
    from anima_server.services.ingestion.retrieval import (
        retrieve_knowledge,
        upsert_concept_embedding,
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
        content="Portable continuity appears only in span text.",
        filename="span-fallback.txt",
    )
    upsert_concept_embedding(runtime_db, concept=concept, embedding_fn=_embedding_for)

    result = retrieve_knowledge(
        runtime_db,
        user_id=1,
        query="portable continuity",
        embedding_fn=_embedding_for,
    )

    # The hybrid lexical arm now also surfaces the (unembedded) compiled
    # concepts of the ingested source; the embedded concept still ranks first.
    assert result.concepts[0].concept_id == concept.id
    # The unembedded span now surfaces through the hybrid lexical arm
    # directly (dense score 0.0) rather than the text-search fallback.
    assert [item.span_id for item in result.evidence_spans] == [spans[0].id]


def test_retrieve_knowledge_fills_missing_concept_hits_from_text(runtime_db) -> None:
    from anima_server.services.ingestion.retrieval import (
        retrieve_knowledge,
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
        content="Portable continuity evidence with a span embedding.",
        filename="concept-fallback.txt",
    )
    upsert_source_span_embedding(runtime_db, span=spans[0], embedding_fn=_embedding_for)

    result = retrieve_knowledge(
        runtime_db,
        user_id=1,
        query="portable continuity",
        embedding_fn=_embedding_for,
    )

    # The unembedded concept now surfaces through the hybrid lexical arm
    # directly (dense score 0.0) rather than the text-search fallback.
    assert any(item.concept_id == concept.id for item in result.concepts)
    assert [item.span_id for item in result.evidence_spans] == [spans[0].id]


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

def test_retrieve_knowledge_lexical_arm_promotes_exact_token_span(runtime_db) -> None:
    from anima_server.services.ingestion.retrieval import (
        retrieve_knowledge,
        upsert_source_span_embedding,
    )

    _source, _artifacts, spans = ingest_text_content(
        runtime_db,
        user_id=1,
        content=(
            "General portable continuity discussion without specifics."
            "\n\n"
            "The relay reports error code XK-9931 during boot."
        ),
        filename="hybrid-evidence.txt",
    )
    for span in spans:
        upsert_source_span_embedding(runtime_db, span=span, embedding_fn=_embedding_for)

    result = retrieve_knowledge(
        runtime_db,
        user_id=1,
        query="portable XK-9931",
        embedding_fn=_embedding_for,
    )

    # The query embeds onto the "portable" vector (see _embedding_for), so the
    # dense arm gives the exact-token span zero cosine and would drop it
    # entirely; only the lexical arm can surface it.
    span_ids = [item.span_id for item in result.evidence_spans]
    token_span = next(span for span in spans if "XK-9931" in span.content_text)
    generic_span = next(span for span in spans if "portable" in span.content_text)
    assert token_span.id in span_ids
    assert generic_span.id in span_ids


def test_unembedded_spans_stay_keyword_searchable_in_hybrid(runtime_db) -> None:
    import hashlib

    from anima_server.models.runtime import (
        RuntimeSource,
        RuntimeSourceArtifact,
        RuntimeSourceSpan,
    )
    from anima_server.models.runtime_embedding import RuntimeEmbedding
    from anima_server.services.agent.embedding_integrity import (
        compute_embedding_checksum,
    )
    from anima_server.services.ingestion.retrieval import retrieve_knowledge

    def _sha(value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()

    source = RuntimeSource(
        user_id=7,
        kind="markdown",
        source_uri="markdown://mixed.md",
        content_hash=_sha("mixed"),
        title="Mixed Coverage",
        media_type="text/markdown",
        status="indexed",
    )
    runtime_db.add(source)
    runtime_db.flush()
    artifact = RuntimeSourceArtifact(
        user_id=7,
        source_id=source.id,
        artifact_kind="markdown",
        content_text="Embedded body.\n\nE-17 fault body.",
        content_hash=_sha("artifact"),
    )
    runtime_db.add(artifact)
    runtime_db.flush()
    embedded_span = RuntimeSourceSpan(
        user_id=7,
        source_id=source.id,
        artifact_id=artifact.id,
        span_kind="paragraph",
        locator_json={"paragraph_index": 0},
        locator_hash=RuntimeSourceSpan.compute_locator_hash({"paragraph_index": 0}),
        content_text="Embedded body about gardens.",
        content_hash=_sha("embedded"),
    )
    unembedded_span = RuntimeSourceSpan(
        user_id=7,
        source_id=source.id,
        artifact_id=artifact.id,
        span_kind="paragraph",
        locator_json={"paragraph_index": 1},
        locator_hash=RuntimeSourceSpan.compute_locator_hash({"paragraph_index": 1}),
        content_text="The E-17 fault body ingested during an embedding outage.",
        content_hash=_sha("unembedded"),
    )
    runtime_db.add_all([embedded_span, unembedded_span])
    runtime_db.flush()
    vector = [1.0] + [0.0] * (_EMBED_DIM - 1)
    runtime_db.add(
        RuntimeEmbedding(
            user_id=7,
            source_type="source_span",
            source_id=embedded_span.id,
            content_hash=embedded_span.content_hash,
            embedding_checksum=compute_embedding_checksum(vector),
            embedding=vector,
            content_preview=embedded_span.content_text[:200],
            category="knowledge",
            importance=3,
        )
    )
    runtime_db.flush()

    result = retrieve_knowledge(
        runtime_db,
        user_id=7,
        query="E-17",
        embedding_fn=lambda text: [1.0] + [0.0] * (_EMBED_DIM - 1),
        limit_concepts=0,
        limit_spans=5,
    )

    # Dense succeeded (the embedded span ranks), but the exact-token match
    # in the unembedded span must still surface through the lexical arm.
    hit_ids = {hit.span_id for hit in result.evidence_spans}
    assert unembedded_span.id in hit_ids


def test_text_fallback_excludes_section_spans(runtime_db) -> None:
    from anima_server.services.ingestion.adapters.text import ingest_markdown_content
    from anima_server.services.ingestion.retrieval import retrieve_knowledge_text

    _source, _artifacts, spans = ingest_markdown_content(
        runtime_db,
        user_id=1,
        content="# Relay Guide\n\nUnique zephyrblade paragraph body.",
        filename="sections.md",
        compile_knowledge=False,
    )
    assert any(span.span_kind == "section" for span in spans)

    result = retrieve_knowledge_text(
        runtime_db,
        user_id=1,
        query="zephyrblade",
        limit_concepts=0,
        limit_spans=5,
    )

    # The paragraph evidence span matches; the parent section span (which
    # duplicates the same text) must not displace it.
    kinds = {hit.span_kind for hit in result.evidence_spans}
    assert "paragraph" in kinds
    assert "section" not in kinds
