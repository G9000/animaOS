from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.models.runtime import (
    RuntimeSource,
    RuntimeSourceArtifact,
    RuntimeSourceSpan,
)
from anima_server.services.ingestion.models import (
    SourceArtifactInput,
    SourceSpanInput,
)
from anima_server.services.ingestion.retrieval import (
    EmbeddingFn,
    upsert_source_span_embedding,
)


def replace_source_artifacts_and_spans(
    db: Session,
    *,
    source: RuntimeSource,
    artifacts: Sequence[SourceArtifactInput],
    spans: Sequence[SourceSpanInput],
    embedding_fn: EmbeddingFn | None = None,
    compile_knowledge: bool = False,
) -> tuple[list[RuntimeSourceArtifact], list[RuntimeSourceSpan]]:
    artifact_kinds = {artifact.artifact_kind for artifact in artifacts}
    missing_kinds = sorted({span.artifact_kind for span in spans} - artifact_kinds)
    if missing_kinds:
        joined = ", ".join(missing_kinds)
        raise ValueError(f"Spans reference missing artifact kinds: {joined}")

    now = datetime.now(UTC)
    existing_artifacts = list(
        db.scalars(
            select(RuntimeSourceArtifact).where(RuntimeSourceArtifact.source_id == source.id)
        ).all()
    )
    artifacts_by_identity = {
        (artifact.artifact_kind, artifact.content_hash): artifact
        for artifact in existing_artifacts
    }

    stored_artifacts: list[RuntimeSourceArtifact] = []
    for artifact_input in artifacts:
        artifact = artifacts_by_identity.get(
            (artifact_input.artifact_kind, artifact_input.content_hash)
        )
        if artifact is None:
            artifact = RuntimeSourceArtifact(
                user_id=source.user_id,
                source_id=source.id,
                artifact_kind=artifact_input.artifact_kind,
                content_hash=artifact_input.content_hash,
            )
        artifact.content_text = artifact_input.content_text
        artifact.metadata_json = _copy_metadata(artifact_input.metadata_json)
        artifact.updated_at = now
        db.add(artifact)
        stored_artifacts.append(artifact)
    db.flush()

    artifacts_by_kind = {
        artifact.artifact_kind: artifact for artifact in stored_artifacts
    }
    artifact_kind_by_id = {
        artifact.id: artifact.artifact_kind for artifact in existing_artifacts
    }
    existing_spans = list(
        db.scalars(
            select(RuntimeSourceSpan).where(RuntimeSourceSpan.source_id == source.id)
        ).all()
    )
    spans_by_identity = {
        (span.artifact_id, span.locator_hash, span.content_hash): span
        for span in existing_spans
    }
    spans_by_stable_identity = {
        (
            artifact_kind_by_id[span.artifact_id],
            span.locator_hash,
            span.content_hash,
        ): span
        for span in existing_spans
        if span.artifact_id in artifact_kind_by_id
    }

    stored_spans: list[RuntimeSourceSpan] = []
    for span_input in spans:
        artifact = artifacts_by_kind[span_input.artifact_kind]
        span = spans_by_identity.get(
            (artifact.id, span_input.locator_hash, span_input.content_hash)
        )
        if span is None:
            span = spans_by_stable_identity.get(
                (
                    span_input.artifact_kind,
                    span_input.locator_hash,
                    span_input.content_hash,
                )
            )
        if span is None:
            span = RuntimeSourceSpan(
                user_id=source.user_id,
                source_id=source.id,
                artifact_id=artifact.id,
                locator_hash=span_input.locator_hash,
                content_hash=span_input.content_hash,
            )
        span.artifact_id = artifact.id
        span.span_kind = span_input.span_kind
        span.locator_json = dict(span_input.locator_json)
        span.content_text = span_input.content_text
        span.metadata_json = _copy_metadata(span_input.metadata_json)
        span.updated_at = now
        db.add(span)
        stored_spans.append(span)
    db.flush()

    stored_span_ids = {span.id for span in stored_spans}
    for stale_span in existing_spans:
        if stale_span.id not in stored_span_ids:
            db.delete(stale_span)

    stored_artifact_ids = {artifact.id for artifact in stored_artifacts}
    for stale_artifact in existing_artifacts:
        if stale_artifact.id not in stored_artifact_ids:
            db.delete(stale_artifact)

    source.status = "indexed"
    source.indexed_at = now
    source.updated_at = now
    db.add(source)
    db.flush()
    _embed_source_spans(db, spans=stored_spans, embedding_fn=embedding_fn)
    if compile_knowledge:
        from anima_server.services.ingestion.document_compiler import (
            compile_source_knowledge,
        )

        compile_source_knowledge(
            db,
            source=source,
            spans=stored_spans,
            embedding_fn=embedding_fn,
        )

    return (
        _ordered_artifacts(db, source_id=source.id),
        _ordered_spans(db, source_id=source.id),
    )


def _embed_source_spans(
    db: Session,
    *,
    spans: Sequence[RuntimeSourceSpan],
    embedding_fn: EmbeddingFn | None,
) -> None:
    if embedding_fn is None:
        return
    for span in spans:
        # Section spans are parent read units, not retrieval evidence;
        # only chunk-level spans get embedded.
        if span.span_kind == "section":
            continue
        upsert_source_span_embedding(db, span=span, embedding_fn=embedding_fn)


def _ordered_artifacts(db: Session, *, source_id: int) -> list[RuntimeSourceArtifact]:
    return list(
        db.scalars(
            select(RuntimeSourceArtifact)
            .where(RuntimeSourceArtifact.source_id == source_id)
            .order_by(RuntimeSourceArtifact.id)
        ).all()
    )


def _ordered_spans(db: Session, *, source_id: int) -> list[RuntimeSourceSpan]:
    return list(
        db.scalars(
            select(RuntimeSourceSpan)
            .where(RuntimeSourceSpan.source_id == source_id)
            .order_by(RuntimeSourceSpan.id)
        ).all()
    )


def _copy_metadata(
    metadata_json: dict[str, object] | None,
) -> dict[str, object] | None:
    if metadata_json is None:
        return None
    return dict(metadata_json)
