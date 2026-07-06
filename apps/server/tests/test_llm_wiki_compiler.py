from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest
from anima_server.models.runtime import (
    RuntimeKnowledgeBundleRun,
    RuntimeKnowledgeConcept,
    RuntimeKnowledgeConceptSource,
    RuntimeKnowledgeLink,
    RuntimeSource,
    RuntimeSourceSpan,
)
from anima_server.services.ingestion.artifacts import replace_source_artifacts_and_spans
from anima_server.services.ingestion.compiler import compile_source_to_concepts
from anima_server.services.ingestion.models import (
    SourceArtifactInput,
    SourceIdentity,
    SourceSpanInput,
)
from anima_server.services.ingestion.sources import register_source
from sqlalchemy import func, select, text

pytest_plugins = ("conftest_runtime",)


@pytest.fixture(autouse=True)
def _enable_foreign_keys_for_compiler_tests(runtime_db) -> None:
    runtime_db.execute(text("PRAGMA foreign_keys = ON"))


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _source_with_spans(runtime_db) -> tuple[RuntimeSource, list[RuntimeSourceSpan]]:
    source = register_source(
        runtime_db,
        SourceIdentity(
            user_id=1,
            kind="markdown",
            source_uri="file://notes.md",
            content_hash=_sha("raw source"),
            title="Notes",
            media_type="text/markdown",
        ),
    )
    _, spans = replace_source_artifacts_and_spans(
        runtime_db,
        source=source,
        artifacts=[
            SourceArtifactInput(
                artifact_kind="plain_text",
                content_text="Anima keeps source evidence.",
                content_hash=_sha("Anima keeps source evidence."),
            )
        ],
        spans=[
            SourceSpanInput(
                artifact_kind="plain_text",
                span_kind="paragraph",
                locator_json={"paragraph_index": 0},
                content_text="Anima keeps source evidence.",
                content_hash=_sha("Anima keeps source evidence."),
            ),
            SourceSpanInput(
                artifact_kind="plain_text",
                span_kind="paragraph",
                locator_json={"paragraph_index": 1},
                content_text="Open questions should stay visible.",
                content_hash=_sha("Open questions should stay visible."),
            ),
        ],
    )
    return source, spans


def test_compiler_creates_concepts_citations_links_and_completed_run(runtime_db) -> None:
    source, spans = _source_with_spans(runtime_db)
    payload = {
        "concepts": [
            _concept_payload("source_summary", "source-notes", "Notes", [spans[0].id]),
            _concept_payload("topic", "topic-evidence", "Evidence", [spans[0].id]),
            _concept_payload("entity", "entity-anima", "Anima", [spans[0].id]),
            _concept_payload("claim", "claim-source-evidence", "Source evidence", [spans[0].id]),
            _concept_payload("question", "question-open", "Open question", [spans[1].id]),
            _concept_payload("decision", "decision-keep-citations", "Keep citations", [spans[0].id]),
        ],
        "links": [
            {
                "source_slug": "topic-evidence",
                "target_slug": "source-notes",
                "link_type": "supports",
                "confidence": 0.8,
            }
        ],
    }

    result = compile_source_to_concepts(
        runtime_db,
        user_id=1,
        source_id=source.id,
        span_ids=[span.id for span in spans],
        model=lambda request: json.dumps(payload),
    )

    concept_types = set(
        runtime_db.scalars(select(RuntimeKnowledgeConcept.concept_type)).all()
    )
    assert result.concept_count == 6
    assert result.link_count == 1
    assert concept_types == {
        "source_summary",
        "topic",
        "entity",
        "claim",
        "question",
        "decision",
    }
    assert runtime_db.scalar(select(func.count(RuntimeKnowledgeConceptSource.id))) == 6
    assert runtime_db.scalar(select(RuntimeKnowledgeLink)).link_type == "supports"
    assert runtime_db.scalar(select(RuntimeKnowledgeBundleRun)).status == "completed"


def test_compiler_updates_existing_concept_by_exact_slug(runtime_db) -> None:
    source, spans = _source_with_spans(runtime_db)
    existing = RuntimeKnowledgeConcept(
        user_id=1,
        concept_type="topic",
        slug="topic-evidence",
        title="Evidence",
        body_markdown="Old body.",
        frontmatter_json={"type": "topic", "title": "Evidence"},
        content_hash=_sha("Old body."),
    )
    runtime_db.add(existing)
    runtime_db.commit()

    compile_source_to_concepts(
        runtime_db,
        user_id=1,
        source_id=source.id,
        span_ids=[spans[0].id],
        model=lambda request: json.dumps(
            {"concepts": [_concept_payload("topic", "topic-evidence", "Evidence", [spans[0].id])]}
        ),
    )

    concepts = list(runtime_db.scalars(select(RuntimeKnowledgeConcept)).all())
    assert len(concepts) == 1
    assert concepts[0].id == existing.id
    assert concepts[0].body_markdown == "Compiled Evidence with citations."


def test_compiler_updates_existing_concept_by_high_confidence_title_type(runtime_db) -> None:
    source, spans = _source_with_spans(runtime_db)
    existing = RuntimeKnowledgeConcept(
        user_id=1,
        concept_type="topic",
        slug="topic-existing-shared",
        title="Shared Topic",
        body_markdown="Old shared body.",
        frontmatter_json={"type": "topic", "title": "Shared Topic"},
        content_hash=_sha("Old shared body."),
    )
    runtime_db.add(existing)
    runtime_db.commit()
    payload = _concept_payload("topic", "topic-new-shared", "Shared Topic", [spans[0].id])
    payload["merge_confidence"] = 0.95

    compile_source_to_concepts(
        runtime_db,
        user_id=1,
        source_id=source.id,
        span_ids=[spans[0].id],
        model=lambda request: json.dumps({"concepts": [payload]}),
    )

    concepts = list(runtime_db.scalars(select(RuntimeKnowledgeConcept)).all())
    assert len(concepts) == 1
    assert concepts[0].id == existing.id
    assert concepts[0].slug == "topic-existing-shared"
    assert concepts[0].body_markdown == "Compiled Shared Topic with citations."


def test_malformed_model_output_records_failed_run_without_corrupting_concepts(runtime_db) -> None:
    source, spans = _source_with_spans(runtime_db)
    existing = RuntimeKnowledgeConcept(
        user_id=1,
        concept_type="topic",
        slug="topic-stable",
        title="Stable",
        body_markdown="Do not change.",
        frontmatter_json={"type": "topic", "title": "Stable"},
        content_hash=_sha("Do not change."),
    )
    runtime_db.add(existing)
    runtime_db.commit()

    result = compile_source_to_concepts(
        runtime_db,
        user_id=1,
        source_id=source.id,
        span_ids=[spans[0].id],
        model=lambda request: "not json",
    )

    runtime_db.refresh(existing)
    failed_run = runtime_db.scalar(select(RuntimeKnowledgeBundleRun))
    assert result.status == "failed"
    assert failed_run.status == "failed"
    assert failed_run.error_json["type"] == "ValueError"
    assert existing.body_markdown == "Do not change."
    assert runtime_db.scalar(select(func.count(RuntimeKnowledgeConcept.id))) == 1


def test_compiler_rejects_spans_from_a_different_source(runtime_db) -> None:
    first_source, _first_spans = _source_with_spans(runtime_db)
    second_source = register_source(
        runtime_db,
        SourceIdentity(
            user_id=1,
            kind="markdown",
            source_uri="file://second.md",
            content_hash=_sha("second raw source"),
            title="Second",
            media_type="text/markdown",
        ),
    )
    _, second_spans = replace_source_artifacts_and_spans(
        runtime_db,
        source=second_source,
        artifacts=[
            SourceArtifactInput(
                artifact_kind="plain_text",
                content_text="Foreign source evidence.",
                content_hash=_sha("Foreign source evidence."),
            )
        ],
        spans=[
            SourceSpanInput(
                artifact_kind="plain_text",
                span_kind="paragraph",
                locator_json={"paragraph_index": 0},
                content_text="Foreign source evidence.",
                content_hash=_sha("Foreign source evidence."),
            )
        ],
    )

    with pytest.raises(ValueError, match="source spans do not exist"):
        compile_source_to_concepts(
            runtime_db,
            user_id=1,
            source_id=first_source.id,
            span_ids=[second_spans[0].id],
            model=lambda request: json.dumps(
                {
                    "concepts": [
                        _concept_payload(
                            "claim",
                            "claim-foreign",
                            "Foreign",
                            [second_spans[0].id],
                        )
                    ]
                }
            ),
        )


def test_lint_knowledge_bundle_reports_structured_findings(runtime_db) -> None:
    from anima_server.services.ingestion.lint import lint_knowledge_bundle

    source, _spans = _source_with_spans(runtime_db)
    first = RuntimeKnowledgeConcept(
        user_id=1,
        concept_type="claim",
        slug="portable-core",
        title="Portable Core",
        description=None,
        body_markdown="Portable core claim.",
        frontmatter_json={"type": "claim", "title": "Portable Core"},
        content_hash=_sha("stale"),
        status="active",
    )
    duplicate_title = RuntimeKnowledgeConcept(
        user_id=1,
        concept_type="claim",
        slug="portable-core-duplicate",
        title="Portable Core",
        description=None,
        body_markdown="Second portable core claim.",
        frontmatter_json={"type": "claim", "title": "Portable Core"},
        content_hash=_sha("Second portable core claim."),
        status="active",
    )
    deleted_target = RuntimeKnowledgeConcept(
        user_id=1,
        concept_type="claim",
        slug="deleted-target",
        title="Deleted Target",
        description=None,
        body_markdown="Deleted target.",
        frontmatter_json={"type": "claim", "title": "Deleted Target"},
        content_hash=_sha("Deleted target."),
        status="deleted",
    )
    runtime_db.add_all([first, duplicate_title, deleted_target])
    runtime_db.flush()
    runtime_db.add(
        RuntimeKnowledgeLink(
            user_id=1,
            source_concept_id=first.id,
            target_concept_id=deleted_target.id,
            link_type="supports",
        )
    )
    runtime_db.flush()

    findings = lint_knowledge_bundle(runtime_db, user_id=1)
    codes = {finding.code for finding in findings}

    assert {
        "uncited_claim",
        "duplicate_concept_title",
        "stale_concept_hash",
        "broken_concept_link",
        "orphan_source",
    }.issubset(codes)
    assert any(finding.source_id == source.id for finding in findings)


def test_lint_knowledge_bundle_supports_concept_scope(runtime_db) -> None:
    from anima_server.services.ingestion.lint import lint_knowledge_bundle

    first = RuntimeKnowledgeConcept(
        user_id=1,
        concept_type="claim",
        slug="first",
        title="First",
        description=None,
        body_markdown="First claim.",
        frontmatter_json={"type": "claim", "title": "First"},
        content_hash=_sha("First claim."),
        status="active",
    )
    second = RuntimeKnowledgeConcept(
        user_id=1,
        concept_type="claim",
        slug="second",
        title="Second",
        description=None,
        body_markdown="Second claim.",
        frontmatter_json={"type": "claim", "title": "Second"},
        content_hash=_sha("Second claim."),
        status="active",
    )
    runtime_db.add_all([first, second])
    runtime_db.flush()

    findings = lint_knowledge_bundle(runtime_db, user_id=1, concept_id=first.id)

    assert {finding.concept_id for finding in findings} == {first.id}


def _concept_payload(
    concept_type: str,
    slug: str,
    title: str,
    span_ids: list[int],
) -> dict[str, Any]:
    return {
        "type": concept_type,
        "title": title,
        "slug": slug,
        "description": f"{title} description",
        "body_markdown": f"Compiled {title} with citations.",
        "source_span_ids": span_ids,
        "tags": ["compiled"],
    }
