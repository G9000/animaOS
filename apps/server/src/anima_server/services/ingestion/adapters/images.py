from __future__ import annotations

import hashlib

from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.models.runtime import (
    RuntimeImageAnnotation,
    RuntimeImageAsset,
    RuntimeSource,
    RuntimeSourceArtifact,
    RuntimeSourceSpan,
)
from anima_server.services.ingestion.artifacts import replace_source_artifacts_and_spans
from anima_server.services.ingestion.models import (
    SourceArtifactInput,
    SourceIdentity,
    SourceSpanInput,
)
from anima_server.services.ingestion.retrieval import EmbeddingFn
from anima_server.services.ingestion.sources import register_source

IMAGE_ARTIFACT_KIND = "image_annotations"


def sync_image_source(
    db: Session,
    *,
    asset: RuntimeImageAsset,
    embedding_fn: EmbeddingFn | None = None,
) -> tuple[RuntimeSource, list[RuntimeSourceArtifact], list[RuntimeSourceSpan]]:
    annotations = list(
        db.scalars(
            select(RuntimeImageAnnotation)
            .where(
                RuntimeImageAnnotation.image_asset_id == asset.id,
                RuntimeImageAnnotation.user_id == asset.user_id,
                RuntimeImageAnnotation.status == "active",
            )
            .order_by(RuntimeImageAnnotation.id)
        ).all()
    )
    joined_text = "\n\n".join(annotation.content_text for annotation in annotations)
    source = register_source(
        db,
        SourceIdentity(
            user_id=asset.user_id,
            kind="image",
            source_uri=f"runtime-image://{asset.id}",
            content_hash=asset.sha256,
            title=asset.filename,
            media_type=asset.mime_type,
            metadata_json={
                "runtime_image_asset_id": asset.id,
                "storage_path": asset.storage_path,
                "size_bytes": asset.size_bytes,
                "width": asset.width,
                "height": asset.height,
                "retention_state": asset.retention_state,
                "source_metadata": dict(asset.metadata_json or {}),
            },
        ),
    )
    artifacts = [
        SourceArtifactInput(
            artifact_kind=IMAGE_ARTIFACT_KIND,
            content_text=joined_text,
            content_hash=_content_hash(joined_text or asset.sha256),
            metadata_json={
                "runtime_image_asset_id": asset.id,
                "annotation_count": len(annotations),
            },
        )
    ]
    spans = [
        SourceSpanInput(
            artifact_kind=IMAGE_ARTIFACT_KIND,
            span_kind="image_annotation",
            locator_json={
                "runtime_image_asset_id": asset.id,
                "runtime_image_annotation_id": annotation.id,
                "annotation_kind": annotation.annotation_kind,
            },
            content_text=annotation.content_text,
            content_hash=annotation.content_hash,
            metadata_json=_annotation_metadata(annotation),
        )
        for annotation in annotations
    ]
    return (
        source,
        *replace_source_artifacts_and_spans(
            db,
            source=source,
            artifacts=artifacts,
            spans=spans,
            embedding_fn=embedding_fn,
            compile_knowledge=True,
        ),
    )


def _annotation_metadata(annotation: RuntimeImageAnnotation) -> dict[str, object]:
    metadata: dict[str, object] = {
        "runtime_image_annotation_id": annotation.id,
        "annotation_kind": annotation.annotation_kind,
    }
    if annotation.source_model is not None:
        metadata["source_model"] = annotation.source_model
    if annotation.metadata_json:
        metadata["source_metadata"] = dict(annotation.metadata_json)
    return metadata


def _content_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()
