from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import delete, select
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


def replace_source_artifacts_and_spans(
    db: Session,
    *,
    source: RuntimeSource,
    artifacts: Sequence[SourceArtifactInput],
    spans: Sequence[SourceSpanInput],
) -> tuple[list[RuntimeSourceArtifact], list[RuntimeSourceSpan]]:
    artifact_kinds = {artifact.artifact_kind for artifact in artifacts}
    missing_kinds = sorted({span.artifact_kind for span in spans} - artifact_kinds)
    if missing_kinds:
        joined = ", ".join(missing_kinds)
        raise ValueError(f"Spans reference missing artifact kinds: {joined}")

    db.execute(
        delete(RuntimeSourceSpan).where(RuntimeSourceSpan.source_id == source.id)
    )
    db.execute(
        delete(RuntimeSourceArtifact).where(RuntimeSourceArtifact.source_id == source.id)
    )
    db.flush()

    inserted_artifacts = [
        RuntimeSourceArtifact(
            user_id=source.user_id,
            source_id=source.id,
            artifact_kind=artifact.artifact_kind,
            content_text=artifact.content_text,
            content_hash=artifact.content_hash,
            metadata_json=_copy_metadata(artifact.metadata_json),
        )
        for artifact in artifacts
    ]
    db.add_all(inserted_artifacts)
    db.flush()

    artifacts_by_kind = {
        artifact.artifact_kind: artifact for artifact in inserted_artifacts
    }
    inserted_spans = [
        RuntimeSourceSpan(
            user_id=source.user_id,
            source_id=source.id,
            artifact_id=artifacts_by_kind[span.artifact_kind].id,
            span_kind=span.span_kind,
            locator_json=dict(span.locator_json),
            locator_hash=span.locator_hash,
            content_text=span.content_text,
            content_hash=span.content_hash,
            metadata_json=_copy_metadata(span.metadata_json),
        )
        for span in spans
    ]
    db.add_all(inserted_spans)

    now = datetime.now(UTC)
    source.status = "indexed"
    source.indexed_at = now
    source.updated_at = now
    db.add(source)
    db.flush()

    return (
        _ordered_artifacts(db, source_id=source.id),
        _ordered_spans(db, source_id=source.id),
    )


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
