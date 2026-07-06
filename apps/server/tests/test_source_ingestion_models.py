from __future__ import annotations

import hashlib

import pytest
from anima_server.db.runtime_base import RuntimeBase
from anima_server.models.runtime import (
    RuntimeKnowledgeBundleRun,
    RuntimeKnowledgeConcept,
    RuntimeKnowledgeConceptSource,
    RuntimeKnowledgeLink,
    RuntimeSource,
    RuntimeSourceArtifact,
    RuntimeSourceSpan,
)
from sqlalchemy import ForeignKeyConstraint, inspect, select, text

pytest_plugins = ("conftest_runtime",)


@pytest.fixture(autouse=True)
def _enable_foreign_keys_for_source_model_tests(runtime_db) -> None:
    runtime_db.execute(text("PRAGMA foreign_keys = ON"))


def _sha(text_value: str) -> str:
    return hashlib.sha256(text_value.encode()).hexdigest()


def _constraint_columns(model: type, name: str) -> tuple[str, ...]:
    constraint = next(
        constraint for constraint in model.__table__.constraints if constraint.name == name
    )
    return tuple(column.name for column in constraint.columns)


def _foreign_key_constraint(model: type, name: str) -> ForeignKeyConstraint:
    constraint = next(
        constraint for constraint in model.__table__.constraints if constraint.name == name
    )
    assert isinstance(constraint, ForeignKeyConstraint)
    return constraint


def test_source_ingestion_tables_are_registered(runtime_engine) -> None:
    RuntimeBase.metadata.create_all(runtime_engine)

    names = set(inspect(runtime_engine).get_table_names())

    assert RuntimeSource.__tablename__ in names
    assert RuntimeSourceArtifact.__tablename__ in names
    assert RuntimeSourceSpan.__tablename__ in names
    assert RuntimeKnowledgeConcept.__tablename__ in names
    assert RuntimeKnowledgeConceptSource.__tablename__ in names
    assert RuntimeKnowledgeLink.__tablename__ in names
    assert RuntimeKnowledgeBundleRun.__tablename__ in names


def test_source_concept_constraints_and_indexes_are_registered() -> None:
    assert _constraint_columns(RuntimeSource, "uq_runtime_sources_user_uri_hash") == (
        "user_id",
        "source_uri",
        "content_hash",
    )
    assert _constraint_columns(
        RuntimeSourceArtifact,
        "uq_runtime_source_artifacts_source_kind_hash",
    ) == ("source_id", "artifact_kind", "content_hash")
    assert _constraint_columns(
        RuntimeSourceSpan,
        "uq_runtime_source_spans_artifact_locator_hash",
    ) == ("artifact_id", "locator_hash", "content_hash")
    assert _constraint_columns(
        RuntimeKnowledgeConcept,
        "uq_runtime_knowledge_concepts_user_slug",
    ) == ("user_id", "slug")
    assert _constraint_columns(
        RuntimeKnowledgeLink,
        "uq_runtime_knowledge_links_user_source_target_type",
    ) == ("user_id", "source_concept_id", "target_concept_id", "link_type")

    indexes = {
        index.name
        for model in (
            RuntimeSource,
            RuntimeSourceSpan,
            RuntimeKnowledgeConcept,
            RuntimeKnowledgeLink,
        )
        for index in model.__table__.indexes
    }
    assert "ix_runtime_sources_user_kind_status" in indexes
    assert "ix_runtime_source_spans_user_source" in indexes
    assert "ix_runtime_knowledge_concepts_user_type_status" in indexes
    assert "ix_runtime_knowledge_links_user_source" in indexes
    assert "ix_runtime_knowledge_links_user_target" in indexes


def test_source_artifacts_and_spans_are_user_scoped_by_composite_foreign_keys() -> None:
    artifact_fk = _foreign_key_constraint(
        RuntimeSourceArtifact,
        "fk_runtime_source_artifacts_source_user",
    )
    span_source_fk = _foreign_key_constraint(
        RuntimeSourceSpan,
        "fk_runtime_source_spans_source_user",
    )
    citation_span_fk = _foreign_key_constraint(
        RuntimeKnowledgeConceptSource,
        "fk_runtime_knowledge_concept_sources_span_user",
    )

    assert tuple(element.parent.name for element in artifact_fk.elements) == (
        "source_id",
        "user_id",
    )
    assert artifact_fk.ondelete == "CASCADE"
    assert tuple(element.parent.name for element in span_source_fk.elements) == (
        "source_id",
        "user_id",
    )
    assert span_source_fk.ondelete == "CASCADE"
    assert tuple(element.parent.name for element in citation_span_fk.elements) == (
        "span_id",
        "user_id",
    )
    assert citation_span_fk.ondelete == "CASCADE"


def test_insert_source_artifacts_spans_concepts_links_and_run(runtime_db) -> None:
    source = RuntimeSource(
        user_id=1,
        kind="web_capture",
        source_uri="https://example.test/article",
        content_hash=_sha("raw html"),
        title="Example article",
        media_type="text/html",
        status="registered",
        metadata_json={"connector": "test"},
    )
    runtime_db.add(source)
    runtime_db.flush()

    artifact = RuntimeSourceArtifact(
        user_id=1,
        source_id=source.id,
        artifact_kind="readable_text",
        content_text="Heading\nEvidence paragraph.",
        content_hash=_sha("Heading\nEvidence paragraph."),
        metadata_json={"language": "en"},
    )
    runtime_db.add(artifact)
    runtime_db.flush()

    span = RuntimeSourceSpan(
        user_id=1,
        source_id=source.id,
        artifact_id=artifact.id,
        span_kind="paragraph",
        locator_json={"char_start": 8, "char_end": 27},
        locator_hash=RuntimeSourceSpan.compute_locator_hash(
            {"char_start": 8, "char_end": 27}
        ),
        content_text="Evidence paragraph.",
        content_hash=_sha("Evidence paragraph."),
        metadata_json={"heading": "Heading"},
    )
    runtime_db.add(span)
    runtime_db.flush()

    source_concept = RuntimeKnowledgeConcept(
        user_id=1,
        concept_type="source_summary",
        slug="source-example-article",
        title="Example article",
        description="Summary of the captured article.",
        body_markdown="Compiled notes.",
        frontmatter_json={"type": "source_summary", "tags": ["example"]},
        metadata_json={"source_id": source.id},
        content_hash=_sha("Compiled notes."),
        status="active",
    )
    topic_concept = RuntimeKnowledgeConcept(
        user_id=1,
        concept_type="topic",
        slug="topic-evidence",
        title="Evidence",
        body_markdown="A topic page with a citation.",
        frontmatter_json={"type": "topic"},
        content_hash=_sha("A topic page with a citation."),
        status="active",
    )
    runtime_db.add_all([source_concept, topic_concept])
    runtime_db.flush()

    citation = RuntimeKnowledgeConceptSource(
        user_id=1,
        concept_id=topic_concept.id,
        source_id=source.id,
        span_id=span.id,
        citation_label="S1",
        quote_text="Evidence paragraph.",
        metadata_json={"reason": "supports"},
    )
    link = RuntimeKnowledgeLink(
        user_id=1,
        source_concept_id=topic_concept.id,
        target_concept_id=source_concept.id,
        link_type="supports",
        confidence=0.8,
        metadata_json={"created_by": "test"},
    )
    run = RuntimeKnowledgeBundleRun(
        user_id=1,
        run_type="compile",
        status="completed",
        source_id=source.id,
        result_json={"concepts": 2},
    )
    runtime_db.add_all([citation, link, run])
    runtime_db.commit()

    assert runtime_db.get(RuntimeSource, source.id).kind == "web_capture"
    assert runtime_db.get(RuntimeSourceArtifact, artifact.id).source_id == source.id
    assert runtime_db.get(RuntimeSourceSpan, span.id).artifact_id == artifact.id
    assert runtime_db.get(RuntimeKnowledgeConcept, topic_concept.id).slug == "topic-evidence"
    assert runtime_db.scalar(select(RuntimeKnowledgeConceptSource)).span_id == span.id
    assert runtime_db.scalar(select(RuntimeKnowledgeLink)).link_type == "supports"
    assert runtime_db.scalar(select(RuntimeKnowledgeBundleRun)).status == "completed"


def test_deleting_source_cascades_derived_evidence_but_preserves_concepts(runtime_db) -> None:
    source = RuntimeSource(
        user_id=1,
        kind="text",
        source_uri="file://notes.txt",
        content_hash=_sha("raw"),
    )
    runtime_db.add(source)
    runtime_db.flush()

    artifact = RuntimeSourceArtifact(
        user_id=1,
        source_id=source.id,
        artifact_kind="plain_text",
        content_text="source text",
        content_hash=_sha("source text"),
    )
    runtime_db.add(artifact)
    runtime_db.flush()

    span = RuntimeSourceSpan(
        user_id=1,
        source_id=source.id,
        artifact_id=artifact.id,
        span_kind="paragraph",
        locator_json={"paragraph_index": 0},
        locator_hash=RuntimeSourceSpan.compute_locator_hash({"paragraph_index": 0}),
        content_text="source text",
        content_hash=_sha("source text"),
    )
    concept = RuntimeKnowledgeConcept(
        user_id=1,
        concept_type="topic",
        slug="topic-source-text",
        title="Source text",
        body_markdown="Compiled source text.",
        frontmatter_json={"type": "topic"},
        content_hash=_sha("Compiled source text."),
    )
    runtime_db.add_all([span, concept])
    runtime_db.flush()
    citation = RuntimeKnowledgeConceptSource(
        user_id=1,
        concept_id=concept.id,
        source_id=source.id,
        span_id=span.id,
    )
    runtime_db.add(citation)
    runtime_db.flush()

    runtime_db.delete(source)
    runtime_db.flush()

    assert runtime_db.scalar(select(RuntimeSourceArtifact)) is None
    assert runtime_db.scalar(select(RuntimeSourceSpan)) is None
    assert runtime_db.scalar(select(RuntimeKnowledgeConceptSource)) is None
    assert runtime_db.get(RuntimeKnowledgeConcept, concept.id) is not None
