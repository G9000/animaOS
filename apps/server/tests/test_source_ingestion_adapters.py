from __future__ import annotations

import hashlib

import pytest
from anima_server.models.runtime import (
    RuntimeKnowledgeBundleRun,
    RuntimeSource,
    RuntimeSourceArtifact,
    RuntimeSourceSpan,
)
from anima_server.services.ingestion.adapters.base import IngestionAdapter
from anima_server.services.ingestion.artifacts import replace_source_artifacts_and_spans
from anima_server.services.ingestion.models import (
    IngestionAdapterResult,
    SourceArtifactInput,
    SourceIdentity,
    SourceSpanInput,
)
from anima_server.services.ingestion.sources import ingest_with_adapter, register_source
from sqlalchemy import func, select, text

pytest_plugins = ("conftest_runtime",)


@pytest.fixture(autouse=True)
def _enable_foreign_keys_for_adapter_tests(runtime_db) -> None:
    runtime_db.execute(text("PRAGMA foreign_keys = ON"))


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _identity(
    *,
    user_id: int = 1,
    source_uri: str = "file://notes.md",
    content_hash: str | None = None,
    kind: str = "markdown",
) -> SourceIdentity:
    return SourceIdentity(
        user_id=user_id,
        kind=kind,
        source_uri=source_uri,
        content_hash=content_hash or _sha("raw source"),
        title="Notes",
        media_type="text/markdown",
        metadata_json={"origin": "test"},
    )


def test_register_source_is_idempotent_by_user_content_hash_or_source_uri(runtime_db) -> None:
    original = register_source(runtime_db, _identity(source_uri="file://a.md"))

    same_hash = register_source(runtime_db, _identity(source_uri="file://renamed.md"))
    same_uri = register_source(
        runtime_db,
        _identity(source_uri="file://a.md", content_hash=_sha("changed content")),
    )
    other_user = register_source(
        runtime_db,
        _identity(user_id=2, source_uri="file://a.md"),
    )

    assert same_hash.id == original.id
    assert same_uri.id == original.id
    assert other_user.id != original.id
    assert runtime_db.scalar(select(func.count(RuntimeSource.id))) == 2


def test_adapter_result_stores_normalized_artifacts_and_spans(runtime_db) -> None:
    source = register_source(runtime_db, _identity())
    artifacts = [
        SourceArtifactInput(
            artifact_kind="page_text",
            content_text="page one text",
            content_hash=_sha("page one text"),
            metadata_json={"page": 1},
        ),
        SourceArtifactInput(
            artifact_kind="table",
            content_text="a,b\n1,2",
            content_hash=_sha("a,b\n1,2"),
        ),
    ]
    spans = [
        SourceSpanInput(
            artifact_kind="page_text",
            span_kind="page",
            locator_json={"page_start": 1, "page_end": 1},
            content_text="page one text",
            content_hash=_sha("page one text"),
        ),
        SourceSpanInput(
            artifact_kind="table",
            span_kind="cell",
            locator_json={"row_start": 1, "row_end": 1, "cell": "B1"},
            content_text="2",
            content_hash=_sha("2"),
        ),
    ]

    stored_artifacts, stored_spans = replace_source_artifacts_and_spans(
        runtime_db,
        source=source,
        artifacts=artifacts,
        spans=spans,
    )

    assert [artifact.artifact_kind for artifact in stored_artifacts] == ["page_text", "table"]
    assert [span.span_kind for span in stored_spans] == ["page", "cell"]
    assert stored_spans[0].locator_json == {"page_start": 1, "page_end": 1}
    assert source.status == "indexed"
    assert source.indexed_at is not None


def test_span_inputs_support_page_time_line_row_cell_and_image_locators() -> None:
    locators = [
        {"page_start": 2, "page_end": 3},
        {"time_start_ms": 1000, "time_end_ms": 2500},
        {"line_start": 10, "line_end": 20},
        {"row_start": 5, "row_end": 8},
        {"cell": "C7"},
        {"image_asset_id": 9, "annotation_id": 11, "annotation_kind": "ocr_text"},
    ]

    hashes = {
        SourceSpanInput(
            artifact_kind="artifact",
            span_kind="evidence",
            locator_json=locator,
            content_text="evidence",
            content_hash=_sha("evidence"),
        ).locator_hash
        for locator in locators
    }

    assert len(hashes) == len(locators)


def test_replace_source_artifacts_and_spans_removes_stale_rows(runtime_db) -> None:
    source = register_source(runtime_db, _identity())
    old_artifacts, old_spans = replace_source_artifacts_and_spans(
        runtime_db,
        source=source,
        artifacts=[
            SourceArtifactInput(
                artifact_kind="plain_text",
                content_text="old",
                content_hash=_sha("old"),
            )
        ],
        spans=[
            SourceSpanInput(
                artifact_kind="plain_text",
                span_kind="paragraph",
                locator_json={"paragraph_index": 0},
                content_text="old",
                content_hash=_sha("old"),
            )
        ],
    )

    _new_artifacts, new_spans = replace_source_artifacts_and_spans(
        runtime_db,
        source=source,
        artifacts=[
            SourceArtifactInput(
                artifact_kind="plain_text",
                content_text="new",
                content_hash=_sha("new"),
            )
        ],
        spans=[
            SourceSpanInput(
                artifact_kind="plain_text",
                span_kind="paragraph",
                locator_json={"paragraph_index": 0},
                content_text="new",
                content_hash=_sha("new"),
            )
        ],
    )

    assert old_artifacts[0].content_text == "old"
    assert old_spans[0].content_text == "old"
    assert runtime_db.scalar(select(func.count(RuntimeSourceArtifact.id))) == 1
    assert runtime_db.scalar(select(func.count(RuntimeSourceSpan.id))) == 1
    assert new_spans[0].content_text == "new"


def test_failed_adapter_records_failed_run_without_half_written_spans(runtime_db) -> None:
    class FailingAdapter(IngestionAdapter):
        name = "failing"

        def extract(self, identity: SourceIdentity) -> IngestionAdapterResult:
            raise RuntimeError("extractor unavailable")

    source, run = ingest_with_adapter(runtime_db, adapter=FailingAdapter(), identity=_identity())

    assert source.status == "failed"
    assert run.status == "failed"
    assert run.run_type == "adapter:failing"
    assert run.error_json == {"message": "extractor unavailable", "type": "RuntimeError"}
    assert runtime_db.scalar(select(func.count(RuntimeSourceArtifact.id))) == 0
    assert runtime_db.scalar(select(func.count(RuntimeSourceSpan.id))) == 0


def test_successful_adapter_writes_completed_run(runtime_db) -> None:
    class TextAdapter(IngestionAdapter):
        name = "text"

        def extract(self, identity: SourceIdentity) -> IngestionAdapterResult:
            return IngestionAdapterResult(
                identity=identity,
                artifacts=[
                    SourceArtifactInput(
                        artifact_kind="plain_text",
                        content_text="hello",
                        content_hash=_sha("hello"),
                    )
                ],
                spans=[
                    SourceSpanInput(
                        artifact_kind="plain_text",
                        span_kind="paragraph",
                        locator_json={"paragraph_index": 0},
                        content_text="hello",
                        content_hash=_sha("hello"),
                    )
                ],
                metadata_json={"adapter": "text"},
            )

    source, run = ingest_with_adapter(runtime_db, adapter=TextAdapter(), identity=_identity())

    assert source.status == "indexed"
    assert run.status == "completed"
    assert run.result_json == {"artifacts": 1, "spans": 1, "adapter": "text"}
    assert runtime_db.scalar(select(RuntimeSourceArtifact)).source_id == source.id
    assert runtime_db.scalar(select(RuntimeSourceSpan)).source_id == source.id
    assert runtime_db.scalar(select(RuntimeKnowledgeBundleRun)).id == run.id
