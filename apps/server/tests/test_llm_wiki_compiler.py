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
from anima_server.models.runtime_embedding import RuntimeEmbedding
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


def _embedding_for(text: str) -> list[float]:
    return [1.0, *([0.0] * 767)]


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


def test_compiler_embeds_completed_concepts(runtime_db) -> None:
    source, spans = _source_with_spans(runtime_db)

    result = compile_source_to_concepts(
        runtime_db,
        user_id=1,
        source_id=source.id,
        span_ids=[spans[0].id],
        model=lambda request: json.dumps(
            {
                "concepts": [
                    _concept_payload("topic", "topic-evidence", "Evidence", [spans[0].id])
                ],
                "links": [],
            }
        ),
        embedding_fn=_embedding_for,
    )

    concept = runtime_db.scalar(select(RuntimeKnowledgeConcept))
    embedding = runtime_db.scalar(select(RuntimeEmbedding))
    assert result.status == "completed"
    assert embedding.source_type == "knowledge_concept"
    assert embedding.source_id == concept.id
    assert embedding.category == "knowledge"
    assert "Compiled Evidence with citations." in embedding.content_preview


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


def test_compiler_resolves_links_using_payload_slugs_after_high_confidence_merge(
    runtime_db,
) -> None:
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
    merged_payload = _concept_payload(
        "topic",
        "topic-new-shared",
        "Shared Topic",
        [spans[0].id],
    )
    merged_payload["merge_confidence"] = 0.95
    notes_payload = _concept_payload(
        "source_summary",
        "source-notes",
        "Notes",
        [spans[0].id],
    )

    result = compile_source_to_concepts(
        runtime_db,
        user_id=1,
        source_id=source.id,
        span_ids=[spans[0].id],
        model=lambda request: json.dumps(
            {
                "concepts": [merged_payload, notes_payload],
                "links": [
                    {
                        "source_slug": "topic-new-shared",
                        "target_slug": "source-notes",
                        "link_type": "supports",
                        "confidence": 0.8,
                    }
                ],
            }
        ),
    )

    link = runtime_db.scalar(select(RuntimeKnowledgeLink))
    target = runtime_db.scalar(
        select(RuntimeKnowledgeConcept).where(RuntimeKnowledgeConcept.slug == "source-notes")
    )
    assert result.status == "completed"
    assert result.link_count == 1
    assert link.source_concept_id == existing.id
    assert link.target_concept_id == target.id


def test_compiler_resolves_links_to_existing_concepts_not_in_payload(runtime_db) -> None:
    source, spans = _source_with_spans(runtime_db)
    existing = RuntimeKnowledgeConcept(
        user_id=1,
        concept_type="topic",
        slug="topic-existing-shared",
        title="Shared Topic",
        body_markdown="Existing shared body.",
        frontmatter_json={"type": "topic", "title": "Shared Topic"},
        content_hash=_sha("Existing shared body."),
    )
    runtime_db.add(existing)
    runtime_db.commit()
    notes_payload = _concept_payload(
        "source_summary",
        "source-notes",
        "Notes",
        [spans[0].id],
    )

    result = compile_source_to_concepts(
        runtime_db,
        user_id=1,
        source_id=source.id,
        span_ids=[spans[0].id],
        model=lambda _request: json.dumps(
            {
                "concepts": [notes_payload],
                "links": [
                    {
                        "source_slug": "source-notes",
                        "target_slug": "topic-existing-shared",
                        "link_type": "supports",
                        "confidence": 0.8,
                    }
                ],
            }
        ),
    )

    link = runtime_db.scalar(select(RuntimeKnowledgeLink))
    source_concept = runtime_db.scalar(
        select(RuntimeKnowledgeConcept).where(
            RuntimeKnowledgeConcept.slug == "source-notes"
        )
    )
    assert result.status == "completed"
    assert result.link_count == 1
    assert link.source_concept_id == source_concept.id
    assert link.target_concept_id == existing.id


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


def test_compiler_rejects_slugs_that_are_not_okf_safe(runtime_db) -> None:
    source, spans = _source_with_spans(runtime_db)
    unsafe_payload = _concept_payload(
        "topic",
        "bad/topic",
        "Bad topic",
        [spans[0].id],
    )

    result = compile_source_to_concepts(
        runtime_db,
        user_id=1,
        source_id=source.id,
        span_ids=[spans[0].id],
        model=lambda request: json.dumps({"concepts": [unsafe_payload]}),
    )

    failed_run = runtime_db.scalar(select(RuntimeKnowledgeBundleRun))
    assert result.status == "failed"
    assert failed_run.status == "failed"
    assert failed_run.error_json["message"] == "Unsafe OKF concept slug: 'bad/topic'"
    assert runtime_db.scalar(select(func.count(RuntimeKnowledgeConcept.id))) == 0


def test_later_compiler_failure_rolls_back_partial_concept_writes(runtime_db) -> None:
    source, spans = _source_with_spans(runtime_db)

    result = compile_source_to_concepts(
        runtime_db,
        user_id=1,
        source_id=source.id,
        span_ids=[spans[0].id],
        model=lambda request: json.dumps(
            {
                "concepts": [
                    _concept_payload("topic", "partial-topic", "Partial", [spans[0].id])
                ],
                "links": ["not an object"],
            }
        ),
    )

    failed_run = runtime_db.scalar(select(RuntimeKnowledgeBundleRun))
    assert result.status == "failed"
    assert failed_run.status == "failed"
    assert failed_run.error_json["type"] == "ValueError"
    assert runtime_db.scalar(select(func.count(RuntimeKnowledgeConcept.id))) == 0
    assert runtime_db.scalar(select(func.count(RuntimeKnowledgeConceptSource.id))) == 0


def test_compiler_deduplicates_repeated_source_span_ids(runtime_db) -> None:
    source, spans = _source_with_spans(runtime_db)
    payload = _concept_payload(
        "claim",
        "claim-duplicate-evidence",
        "Duplicate evidence",
        [spans[0].id, spans[0].id],
    )

    result = compile_source_to_concepts(
        runtime_db,
        user_id=1,
        source_id=source.id,
        span_ids=[spans[0].id],
        model=lambda request: json.dumps({"concepts": [payload]}),
    )

    citations = list(runtime_db.scalars(select(RuntimeKnowledgeConceptSource)).all())
    assert result.status == "completed"
    assert len(citations) == 1
    assert citations[0].span_id == spans[0].id
    assert citations[0].citation_label == "S1"


def test_compiler_deduplicates_repeated_link_payloads(runtime_db) -> None:
    source, spans = _source_with_spans(runtime_db)
    payload = {
        "concepts": [
            _concept_payload("topic", "topic-evidence", "Evidence", [spans[0].id]),
            _concept_payload("source_summary", "source-notes", "Notes", [spans[0].id]),
        ],
        "links": [
            {
                "source_slug": "topic-evidence",
                "target_slug": "source-notes",
                "link_type": "supports",
                "confidence": 0.8,
            },
            {
                "source_slug": "topic-evidence",
                "target_slug": "source-notes",
                "link_type": "supports",
                "confidence": 0.8,
            },
        ],
    }

    result = compile_source_to_concepts(
        runtime_db,
        user_id=1,
        source_id=source.id,
        span_ids=[span.id for span in spans],
        model=lambda request: json.dumps(payload),
    )

    links = list(runtime_db.scalars(select(RuntimeKnowledgeLink)).all())
    assert result.status == "completed"
    assert result.link_count == 1
    assert len(links) == 1
    assert links[0].link_type == "supports"


def test_compiler_recompile_drops_stale_compiler_links(runtime_db) -> None:
    source, spans = _source_with_spans(runtime_db)
    concepts = [
        _concept_payload("topic", "topic-evidence", "Evidence", [spans[0].id]),
        _concept_payload("source_summary", "source-notes", "Notes", [spans[0].id]),
    ]

    compile_source_to_concepts(
        runtime_db,
        user_id=1,
        source_id=source.id,
        span_ids=[span.id for span in spans],
        model=lambda request: json.dumps(
            {
                "concepts": concepts,
                "links": [
                    {
                        "source_slug": "topic-evidence",
                        "target_slug": "source-notes",
                        "link_type": "supports",
                        "confidence": 0.8,
                    }
                ],
            }
        ),
    )
    topic = runtime_db.scalar(
        select(RuntimeKnowledgeConcept).where(RuntimeKnowledgeConcept.slug == "topic-evidence")
    )
    notes = runtime_db.scalar(
        select(RuntimeKnowledgeConcept).where(RuntimeKnowledgeConcept.slug == "source-notes")
    )
    runtime_db.add(
        RuntimeKnowledgeLink(
            user_id=1,
            source_concept_id=notes.id,
            target_concept_id=topic.id,
            link_type="related",
            metadata_json={"source": "manual"},
        )
    )
    runtime_db.flush()

    result = compile_source_to_concepts(
        runtime_db,
        user_id=1,
        source_id=source.id,
        span_ids=[span.id for span in spans],
        model=lambda request: json.dumps({"concepts": concepts, "links": []}),
    )

    links = list(runtime_db.scalars(select(RuntimeKnowledgeLink)).all())
    assert result.status == "completed"
    assert result.link_count == 0
    assert [(link.link_type, link.metadata_json) for link in links] == [
        ("related", {"source": "manual"})
    ]


def test_compiler_recompile_retires_stale_source_concepts(runtime_db) -> None:
    source, spans = _source_with_spans(runtime_db)

    compile_source_to_concepts(
        runtime_db,
        user_id=1,
        source_id=source.id,
        span_ids=[span.id for span in spans],
        model=lambda request: json.dumps(
            {
                "concepts": [
                    _concept_payload("source_summary", "source-notes", "Notes", [spans[0].id]),
                    _concept_payload("topic", "topic-current", "Current", [spans[0].id]),
                    _concept_payload("topic", "topic-stale", "Stale", [spans[1].id]),
                ],
                "links": [],
            }
        ),
    )

    result = compile_source_to_concepts(
        runtime_db,
        user_id=1,
        source_id=source.id,
        span_ids=[spans[0].id],
        model=lambda request: json.dumps(
            {
                "concepts": [
                    _concept_payload("source_summary", "source-notes", "Notes", [spans[0].id]),
                    _concept_payload("topic", "topic-current", "Current", [spans[0].id]),
                ],
                "links": [],
            }
        ),
    )

    current = runtime_db.scalar(
        select(RuntimeKnowledgeConcept).where(RuntimeKnowledgeConcept.slug == "topic-current")
    )
    stale = runtime_db.scalar(
        select(RuntimeKnowledgeConcept).where(RuntimeKnowledgeConcept.slug == "topic-stale")
    )
    assert result.status == "completed"
    assert current.status == "active"
    assert stale.status == "inactive"


def test_compiler_does_not_take_ownership_of_existing_manual_links(runtime_db) -> None:
    source, spans = _source_with_spans(runtime_db)
    concepts = [
        _concept_payload("topic", "topic-evidence", "Evidence", [spans[0].id]),
        _concept_payload("source_summary", "source-notes", "Notes", [spans[0].id]),
    ]
    compile_source_to_concepts(
        runtime_db,
        user_id=1,
        source_id=source.id,
        span_ids=[span.id for span in spans],
        model=lambda request: json.dumps({"concepts": concepts, "links": []}),
    )
    topic = runtime_db.scalar(
        select(RuntimeKnowledgeConcept).where(RuntimeKnowledgeConcept.slug == "topic-evidence")
    )
    notes = runtime_db.scalar(
        select(RuntimeKnowledgeConcept).where(RuntimeKnowledgeConcept.slug == "source-notes")
    )
    runtime_db.add(
        RuntimeKnowledgeLink(
            user_id=1,
            source_concept_id=topic.id,
            target_concept_id=notes.id,
            link_type="supports",
            confidence=0.42,
            metadata_json={"source": "manual"},
        )
    )
    runtime_db.flush()

    emitted_result = compile_source_to_concepts(
        runtime_db,
        user_id=1,
        source_id=source.id,
        span_ids=[span.id for span in spans],
        model=lambda request: json.dumps(
            {
                "concepts": concepts,
                "links": [
                    {
                        "source_slug": "topic-evidence",
                        "target_slug": "source-notes",
                        "link_type": "supports",
                        "confidence": 0.8,
                    }
                ],
            }
        ),
    )
    emitted_link = runtime_db.scalar(select(RuntimeKnowledgeLink))
    assert emitted_result.link_count == 1
    assert emitted_link.metadata_json == {"source": "manual"}
    assert emitted_link.confidence == 0.42

    compile_source_to_concepts(
        runtime_db,
        user_id=1,
        source_id=source.id,
        span_ids=[span.id for span in spans],
        model=lambda request: json.dumps({"concepts": concepts, "links": []}),
    )

    links = list(runtime_db.scalars(select(RuntimeKnowledgeLink)).all())
    assert [(link.link_type, link.metadata_json, link.confidence) for link in links] == [
        ("supports", {"source": "manual"}, 0.42)
    ]


def test_source_refresh_preserves_citations_for_unchanged_spans(runtime_db) -> None:
    source, spans = _source_with_spans(runtime_db)
    concept = RuntimeKnowledgeConcept(
        user_id=1,
        concept_type="claim",
        slug="claim-source-evidence",
        title="Source evidence",
        description=None,
        body_markdown="Source evidence claim.",
        frontmatter_json={"type": "claim", "title": "Source evidence"},
        content_hash=_sha("Source evidence claim."),
        status="active",
    )
    runtime_db.add(concept)
    runtime_db.flush()
    citation = RuntimeKnowledgeConceptSource(
        user_id=1,
        concept_id=concept.id,
        source_id=source.id,
        span_id=spans[0].id,
        citation_label="S1",
        quote_text=spans[0].content_text,
    )
    runtime_db.add(citation)
    runtime_db.flush()

    _, refreshed_spans = replace_source_artifacts_and_spans(
        runtime_db,
        source=source,
        artifacts=[
            SourceArtifactInput(
                artifact_kind="plain_text",
                content_text="Anima keeps source evidence.",
                content_hash=_sha("Anima keeps source evidence."),
                metadata_json={"refreshed": True},
            )
        ],
        spans=[
            SourceSpanInput(
                artifact_kind="plain_text",
                span_kind="paragraph",
                locator_json={"paragraph_index": 0},
                content_text="Anima keeps source evidence.",
                content_hash=_sha("Anima keeps source evidence."),
                metadata_json={"refreshed": True},
            )
        ],
    )

    runtime_db.refresh(citation)
    assert [span.id for span in refreshed_spans] == [spans[0].id]
    assert citation.span_id == spans[0].id
    assert runtime_db.scalar(select(func.count(RuntimeKnowledgeConceptSource.id))) == 1


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


def test_compiler_merging_existing_concept_retains_other_source_citations(runtime_db) -> None:
    first_source, first_spans = _source_with_spans(runtime_db)
    compile_source_to_concepts(
        runtime_db,
        user_id=1,
        source_id=first_source.id,
        span_ids=[first_spans[0].id],
        model=lambda request: json.dumps(
            {
                "concepts": [
                    _concept_payload("claim", "shared-claim", "Shared", [first_spans[0].id])
                ]
            }
        ),
    )
    second_source = register_source(
        runtime_db,
        SourceIdentity(
            user_id=1,
            kind="markdown",
            source_uri="file://second.md",
            content_hash=_sha("second source"),
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
                content_text="Second source evidence.",
                content_hash=_sha("Second source evidence."),
            )
        ],
        spans=[
            SourceSpanInput(
                artifact_kind="plain_text",
                span_kind="paragraph",
                locator_json={"paragraph_index": 0},
                content_text="Second source evidence.",
                content_hash=_sha("Second source evidence."),
            )
        ],
    )

    compile_source_to_concepts(
        runtime_db,
        user_id=1,
        source_id=second_source.id,
        span_ids=[second_spans[0].id],
        model=lambda request: json.dumps(
            {
                "concepts": [
                    _concept_payload("claim", "shared-claim", "Shared", [second_spans[0].id])
                ]
            }
        ),
    )

    concept = runtime_db.scalar(
        select(RuntimeKnowledgeConcept).where(RuntimeKnowledgeConcept.slug == "shared-claim")
    )
    citations = list(
        runtime_db.scalars(
            select(RuntimeKnowledgeConceptSource)
            .where(RuntimeKnowledgeConceptSource.concept_id == concept.id)
            .order_by(RuntimeKnowledgeConceptSource.source_id)
        ).all()
    )

    assert [citation.source_id for citation in citations] == [
        first_source.id,
        second_source.id,
    ]
    assert [citation.span_id for citation in citations] == [
        first_spans[0].id,
        second_spans[0].id,
    ]


def test_compiler_recompile_keeps_shared_concept_active_for_other_sources(
    runtime_db,
) -> None:
    first_source, first_spans = _source_with_spans(runtime_db)
    compile_source_to_concepts(
        runtime_db,
        user_id=1,
        source_id=first_source.id,
        span_ids=[first_spans[0].id],
        model=lambda request: json.dumps(
            {
                "concepts": [
                    _concept_payload("claim", "shared-claim", "Shared", [first_spans[0].id])
                ]
            }
        ),
    )
    second_source = register_source(
        runtime_db,
        SourceIdentity(
            user_id=1,
            kind="markdown",
            source_uri="file://second.md",
            content_hash=_sha("second source"),
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
                content_text="Second source evidence.",
                content_hash=_sha("Second source evidence."),
            )
        ],
        spans=[
            SourceSpanInput(
                artifact_kind="plain_text",
                span_kind="paragraph",
                locator_json={"paragraph_index": 0},
                content_text="Second source evidence.",
                content_hash=_sha("Second source evidence."),
            )
        ],
    )
    compile_source_to_concepts(
        runtime_db,
        user_id=1,
        source_id=second_source.id,
        span_ids=[second_spans[0].id],
        model=lambda request: json.dumps(
            {
                "concepts": [
                    _concept_payload("claim", "shared-claim", "Shared", [second_spans[0].id])
                ]
            }
        ),
    )

    result = compile_source_to_concepts(
        runtime_db,
        user_id=1,
        source_id=second_source.id,
        span_ids=[second_spans[0].id],
        model=lambda request: json.dumps({"concepts": [], "links": []}),
    )

    concept = runtime_db.scalar(
        select(RuntimeKnowledgeConcept).where(RuntimeKnowledgeConcept.slug == "shared-claim")
    )
    citations = list(
        runtime_db.scalars(
            select(RuntimeKnowledgeConceptSource)
            .where(RuntimeKnowledgeConceptSource.concept_id == concept.id)
            .order_by(RuntimeKnowledgeConceptSource.source_id)
        ).all()
    )

    assert result.status == "completed"
    assert concept.status == "active"
    assert concept.metadata_json["compiled_from_source_id"] == first_source.id
    assert [citation.source_id for citation in citations] == [first_source.id]


def test_compiler_recompile_clears_stale_links_for_shared_concepts(
    runtime_db,
) -> None:
    first_source, first_spans = _source_with_spans(runtime_db)
    second_source = register_source(
        runtime_db,
        SourceIdentity(
            user_id=1,
            kind="markdown",
            source_uri="file://second.md",
            content_hash=_sha("second source"),
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
                content_text="Second alpha.\n\nSecond beta.",
                content_hash=_sha("Second alpha.\n\nSecond beta."),
            )
        ],
        spans=[
            SourceSpanInput(
                artifact_kind="plain_text",
                span_kind="paragraph",
                locator_json={"paragraph_index": 0},
                content_text="Second alpha.",
                content_hash=_sha("Second alpha."),
            ),
            SourceSpanInput(
                artifact_kind="plain_text",
                span_kind="paragraph",
                locator_json={"paragraph_index": 1},
                content_text="Second beta.",
                content_hash=_sha("Second beta."),
            ),
        ],
    )
    compile_source_to_concepts(
        runtime_db,
        user_id=1,
        source_id=second_source.id,
        span_ids=[span.id for span in second_spans],
        model=lambda request: json.dumps(
            {
                "concepts": [
                    _concept_payload("claim", "shared-alpha", "Shared Alpha", [second_spans[0].id]),
                    _concept_payload("claim", "shared-beta", "Shared Beta", [second_spans[1].id]),
                ],
                "links": [],
            }
        ),
    )
    compile_source_to_concepts(
        runtime_db,
        user_id=1,
        source_id=first_source.id,
        span_ids=[span.id for span in first_spans],
        model=lambda request: json.dumps(
            {
                "concepts": [
                    _concept_payload("claim", "shared-alpha", "Shared Alpha", [first_spans[0].id]),
                    _concept_payload("claim", "shared-beta", "Shared Beta", [first_spans[1].id]),
                ],
                "links": [
                    {
                        "source_slug": "shared-alpha",
                        "target_slug": "shared-beta",
                        "link_type": "supports",
                        "confidence": 0.8,
                    }
                ],
            }
        ),
    )

    result = compile_source_to_concepts(
        runtime_db,
        user_id=1,
        source_id=first_source.id,
        span_ids=[span.id for span in first_spans],
        model=lambda request: json.dumps({"concepts": [], "links": []}),
    )

    concepts = list(
        runtime_db.scalars(
            select(RuntimeKnowledgeConcept)
            .where(RuntimeKnowledgeConcept.slug.in_(["shared-alpha", "shared-beta"]))
            .order_by(RuntimeKnowledgeConcept.slug)
        ).all()
    )
    links = list(runtime_db.scalars(select(RuntimeKnowledgeLink)).all())

    assert result.status == "completed"
    assert [concept.status for concept in concepts] == ["active", "active"]
    assert {
        concept.metadata_json["compiled_from_source_id"] for concept in concepts
    } == {second_source.id}
    assert links == []


def test_compiler_recompile_removes_stale_citation_when_other_source_owns_metadata(
    runtime_db,
) -> None:
    first_source, first_spans = _source_with_spans(runtime_db)
    compile_source_to_concepts(
        runtime_db,
        user_id=1,
        source_id=first_source.id,
        span_ids=[first_spans[0].id],
        model=lambda request: json.dumps(
            {
                "concepts": [
                    _concept_payload("claim", "shared-claim", "Shared", [first_spans[0].id])
                ]
            }
        ),
    )
    second_source = register_source(
        runtime_db,
        SourceIdentity(
            user_id=1,
            kind="markdown",
            source_uri="file://second.md",
            content_hash=_sha("second source"),
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
                content_text="Second source evidence.",
                content_hash=_sha("Second source evidence."),
            )
        ],
        spans=[
            SourceSpanInput(
                artifact_kind="plain_text",
                span_kind="paragraph",
                locator_json={"paragraph_index": 0},
                content_text="Second source evidence.",
                content_hash=_sha("Second source evidence."),
            )
        ],
    )
    compile_source_to_concepts(
        runtime_db,
        user_id=1,
        source_id=second_source.id,
        span_ids=[second_spans[0].id],
        model=lambda request: json.dumps(
            {
                "concepts": [
                    _concept_payload("claim", "shared-claim", "Shared", [second_spans[0].id])
                ]
            }
        ),
    )

    result = compile_source_to_concepts(
        runtime_db,
        user_id=1,
        source_id=first_source.id,
        span_ids=[first_spans[0].id],
        model=lambda request: json.dumps({"concepts": [], "links": []}),
    )

    concept = runtime_db.scalar(
        select(RuntimeKnowledgeConcept).where(RuntimeKnowledgeConcept.slug == "shared-claim")
    )
    citations = list(
        runtime_db.scalars(
            select(RuntimeKnowledgeConceptSource)
            .where(RuntimeKnowledgeConceptSource.concept_id == concept.id)
            .order_by(RuntimeKnowledgeConceptSource.source_id)
        ).all()
    )

    assert result.status == "completed"
    assert concept.status == "active"
    assert concept.metadata_json["compiled_from_source_id"] == second_source.id
    assert [citation.source_id for citation in citations] == [second_source.id]


def test_compiler_recompile_preserves_user_concept_citing_refreshed_source(
    runtime_db,
) -> None:
    source, spans = _source_with_spans(runtime_db)
    user_concept = RuntimeKnowledgeConcept(
        user_id=1,
        concept_type="claim",
        slug="user-claim",
        title="User claim",
        description=None,
        body_markdown="User-authored claim.",
        frontmatter_json={"type": "claim", "title": "User claim"},
        content_hash=_sha("User-authored claim."),
        status="active",
    )
    runtime_db.add(user_concept)
    runtime_db.flush()
    runtime_db.add(
        RuntimeKnowledgeConceptSource(
            user_id=1,
            concept_id=user_concept.id,
            source_id=source.id,
            span_id=spans[0].id,
            citation_label="U1",
            quote_text=spans[0].content_text,
            metadata_json={"origin": "import"},
        )
    )
    runtime_db.flush()

    result = compile_source_to_concepts(
        runtime_db,
        user_id=1,
        source_id=source.id,
        span_ids=[spans[0].id],
        model=lambda request: json.dumps({"concepts": [], "links": []}),
    )

    concept = runtime_db.get(RuntimeKnowledgeConcept, user_concept.id)
    citations = list(
        runtime_db.scalars(
            select(RuntimeKnowledgeConceptSource).where(
                RuntimeKnowledgeConceptSource.concept_id == user_concept.id
            )
        ).all()
    )

    assert result.status == "completed"
    assert concept.status == "active"
    assert len(citations) == 1
    assert citations[0].metadata_json == {"origin": "import"}


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


def test_lint_concept_scope_does_not_mark_other_sources_orphaned(runtime_db) -> None:
    from anima_server.services.ingestion.lint import lint_knowledge_bundle

    first_source, first_spans = _source_with_spans(runtime_db)
    second_source = register_source(
        runtime_db,
        SourceIdentity(
            user_id=1,
            kind="markdown",
            source_uri="file://second.md",
            content_hash=_sha("second source"),
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
                content_text="Second source evidence.",
                content_hash=_sha("Second source evidence."),
            )
        ],
        spans=[
            SourceSpanInput(
                artifact_kind="plain_text",
                span_kind="paragraph",
                locator_json={"paragraph_index": 0},
                content_text="Second source evidence.",
                content_hash=_sha("Second source evidence."),
            )
        ],
    )
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
    runtime_db.add_all(
        [
            RuntimeKnowledgeConceptSource(
                user_id=1,
                concept_id=first.id,
                source_id=first_source.id,
                span_id=first_spans[0].id,
                citation_label="S1",
                quote_text=first_spans[0].content_text,
            ),
            RuntimeKnowledgeConceptSource(
                user_id=1,
                concept_id=second.id,
                source_id=second_source.id,
                span_id=second_spans[0].id,
                citation_label="S1",
                quote_text=second_spans[0].content_text,
            ),
        ]
    )
    runtime_db.flush()

    findings = lint_knowledge_bundle(runtime_db, user_id=1, concept_id=first.id)

    assert all(finding.code != "orphan_source" for finding in findings)


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
