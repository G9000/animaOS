from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.models.runtime import RuntimeImageAnnotation, RuntimeImageAsset
from anima_server.models.runtime_embedding import RuntimeEmbedding
from anima_server.services.agent.embedding_integrity import compute_embedding_checksum
from anima_server.services.agent.embeddings import generate_embedding
from anima_server.services.documents.indexing import _run_embedding
from anima_server.services.images.capabilities import ImageProcessingCapabilities
from anima_server.services.images.extractors import ImageCaptioner, ImageTextExtractor
from anima_server.services.images.store import resolve_image_storage_path

EmbeddingResult = list[float] | None
EmbeddingFn = Callable[[str], EmbeddingResult] | Callable[[str], Awaitable[EmbeddingResult]]


@dataclass(frozen=True, slots=True)
class ImageIndexingResult:
    image_asset_id: int
    annotations_indexed: int
    embedding_count: int


def index_image_asset(
    runtime_db: Session,
    *,
    user_id: int,
    image_asset_id: int,
    upload_context: str,
    embedding_fn: EmbeddingFn | None = None,
    capabilities: ImageProcessingCapabilities | None = None,
    caption_fn: ImageCaptioner | None = None,
    text_extraction_fn: ImageTextExtractor | None = None,
) -> ImageIndexingResult:
    asset = runtime_db.scalar(
        select(RuntimeImageAsset).where(
            RuntimeImageAsset.id == image_asset_id,
            RuntimeImageAsset.user_id == user_id,
        )
    )
    if asset is None:
        return ImageIndexingResult(
            image_asset_id=image_asset_id,
            annotations_indexed=0,
            embedding_count=0,
        )

    capabilities = capabilities or ImageProcessingCapabilities()
    image_path = resolve_image_storage_path(asset.storage_path, user_id=user_id)

    annotation_inputs = [
        (
            "upload_context",
            _upload_context_text(asset, upload_context),
            None,
        ),
        (
            "metadata",
            _metadata_text(asset),
            None,
        ),
    ]

    if capabilities.vision_caption and caption_fn is not None:
        caption = _safe_extract(caption_fn, image_path, asset)
        if caption:
            annotation_inputs.append(("vision_caption", caption.strip(), None))

    if capabilities.image_text_extraction and text_extraction_fn is not None:
        extracted_text = _safe_extract(text_extraction_fn, image_path, asset)
        if extracted_text:
            annotation_inputs.append(("ocr_text", extracted_text.strip(), None))

    annotations = [
        _upsert_active_annotation(
            runtime_db,
            user_id=user_id,
            image_asset_id=asset.id,
            annotation_kind=kind,
            content_text=content_text,
            source_model=source_model,
        )
        for kind, content_text, source_model in annotation_inputs
        if content_text.strip()
    ]
    embedding_count = embed_image_annotations(
        runtime_db,
        user_id=user_id,
        annotations=annotations,
        embedding_fn=embedding_fn or generate_embedding,
    )

    if _all_active_annotations_embedded(
        runtime_db,
        user_id=user_id,
        image_asset_id=asset.id,
    ):
        now = datetime.now(UTC)
        asset.status = "indexed"
        asset.indexed_at = now
        asset.updated_at = now
        runtime_db.add(asset)

    runtime_db.flush()
    return ImageIndexingResult(
        image_asset_id=asset.id,
        annotations_indexed=len(annotations),
        embedding_count=embedding_count,
    )


def index_image_attachments_for_message(
    runtime_db: Session,
    *,
    user_id: int,
    image_asset_ids: Sequence[int],
    upload_context: str,
    embedding_fn: EmbeddingFn | None = None,
    capabilities: ImageProcessingCapabilities | None = None,
) -> list[ImageIndexingResult]:
    results: list[ImageIndexingResult] = []
    seen: set[int] = set()
    for image_asset_id in image_asset_ids:
        if image_asset_id in seen:
            continue
        seen.add(image_asset_id)
        results.append(
            index_image_asset(
                runtime_db,
                user_id=user_id,
                image_asset_id=image_asset_id,
                upload_context=upload_context,
                embedding_fn=embedding_fn,
                capabilities=capabilities,
            )
        )
    return results


def embed_image_annotations(
    runtime_db: Session,
    *,
    user_id: int,
    annotations: Sequence[RuntimeImageAnnotation],
    embedding_fn: EmbeddingFn | None,
) -> int:
    if embedding_fn is None:
        return 0

    embedded = 0
    for annotation in annotations:
        if annotation.status != "active":
            continue
        embedding = _run_embedding(embedding_fn, annotation.content_text)
        if not embedding:
            continue
        _upsert_runtime_embedding(
            runtime_db,
            user_id=user_id,
            annotation=annotation,
            embedding=embedding,
        )
        embedded += 1
    runtime_db.flush()
    return embedded


def _upsert_active_annotation(
    runtime_db: Session,
    *,
    user_id: int,
    image_asset_id: int,
    annotation_kind: str,
    content_text: str,
    source_model: str | None,
) -> RuntimeImageAnnotation:
    content_hash = RuntimeImageAnnotation.compute_content_hash(content_text)
    existing = runtime_db.scalar(
        select(RuntimeImageAnnotation).where(
            RuntimeImageAnnotation.user_id == user_id,
            RuntimeImageAnnotation.image_asset_id == image_asset_id,
            RuntimeImageAnnotation.annotation_kind == annotation_kind,
            RuntimeImageAnnotation.content_hash == content_hash,
        )
    )
    if existing is not None:
        return existing

    stale = list(
        runtime_db.scalars(
            select(RuntimeImageAnnotation).where(
                RuntimeImageAnnotation.user_id == user_id,
                RuntimeImageAnnotation.image_asset_id == image_asset_id,
                RuntimeImageAnnotation.annotation_kind == annotation_kind,
                RuntimeImageAnnotation.status == "active",
            )
        ).all()
    )
    now = datetime.now(UTC)
    for annotation in stale:
        annotation.status = "replaced"
        annotation.updated_at = now
        runtime_db.add(annotation)

    annotation = RuntimeImageAnnotation(
        user_id=user_id,
        image_asset_id=image_asset_id,
        annotation_kind=annotation_kind,
        content_text=content_text,
        content_hash=content_hash,
        source_model=source_model,
        status="active",
    )
    runtime_db.add(annotation)
    runtime_db.flush()
    return annotation


def _upsert_runtime_embedding(
    runtime_db: Session,
    *,
    user_id: int,
    annotation: RuntimeImageAnnotation,
    embedding: list[float],
) -> None:
    existing = runtime_db.scalar(
        select(RuntimeEmbedding).where(
            RuntimeEmbedding.user_id == user_id,
            RuntimeEmbedding.source_type == "image_annotation",
            RuntimeEmbedding.source_id == annotation.id,
        )
    )
    checksum = compute_embedding_checksum(embedding)
    if existing is None:
        runtime_db.add(
            RuntimeEmbedding(
                user_id=user_id,
                source_type="image_annotation",
                source_id=annotation.id,
                content_hash=annotation.content_hash,
                embedding_checksum=checksum,
                embedding=embedding,
                content_preview=annotation.content_text[:200],
                category="image",
                importance=3,
            )
        )
        return

    existing.content_hash = annotation.content_hash
    existing.embedding_checksum = checksum
    existing.embedding = embedding
    existing.content_preview = annotation.content_text[:200]
    existing.category = "image"
    existing.importance = 3
    existing.updated_at = datetime.now(UTC)
    runtime_db.add(existing)


def _all_active_annotations_embedded(
    runtime_db: Session,
    *,
    user_id: int,
    image_asset_id: int,
) -> bool:
    active_annotations = list(
        runtime_db.scalars(
            select(RuntimeImageAnnotation).where(
                RuntimeImageAnnotation.user_id == user_id,
                RuntimeImageAnnotation.image_asset_id == image_asset_id,
                RuntimeImageAnnotation.status == "active",
            )
        ).all()
    )
    if not active_annotations:
        return False

    for annotation in active_annotations:
        current_embedding = runtime_db.scalar(
            select(RuntimeEmbedding.id).where(
                RuntimeEmbedding.user_id == user_id,
                RuntimeEmbedding.source_type == "image_annotation",
                RuntimeEmbedding.source_id == annotation.id,
                RuntimeEmbedding.content_hash == annotation.content_hash,
            )
        )
        if current_embedding is None:
            return False
    return True


def _safe_extract(
    extractor: ImageCaptioner | ImageTextExtractor,
    image_path,
    asset: RuntimeImageAsset,
) -> str | None:
    try:
        return extractor(image_path, asset)
    except Exception:
        return None


def _upload_context_text(asset: RuntimeImageAsset, upload_context: str) -> str:
    parts = []
    if asset.filename:
        parts.append(f"Image filename: {asset.filename}.")
    if upload_context.strip():
        parts.append(f"Upload context: {upload_context.strip()}")
    return " ".join(parts).strip() or "Image uploaded in chat."


def _metadata_text(asset: RuntimeImageAsset) -> str:
    dimensions = (
        f", {asset.width}x{asset.height}px"
        if asset.width is not None and asset.height is not None
        else ""
    )
    return (
        f"Image metadata: {asset.mime_type}, {asset.size_bytes} bytes{dimensions}, "
        f"sha256 {asset.sha256}."
    )
