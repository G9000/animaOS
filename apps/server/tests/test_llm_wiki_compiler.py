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
