from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.models.runtime import (
    RuntimeImageAnnotation,
    RuntimeImageAsset,
    RuntimeImageMessageLink,
    RuntimeMessage,
)
from anima_server.models.runtime_embedding import RuntimeEmbedding
from anima_server.services.documents.indexing import _run_embedding
from anima_server.services.images.indexing import EmbeddingFn

MIN_RELEVANT_IMAGE_ANNOTATION_SIMILARITY = 0.0


@dataclass(frozen=True, slots=True)
class ImageAnnotationSearchResult:
    image_asset_id: int
    user_id: int
    filename: str | None
    mime_type: str
    source_message_id: int | None
    source_thread_id: int | None
    attachment_id: str | None
    attachment_url: str | None
    annotation_id: int
    annotation_kind: str
    snippet: str
    similarity: float


def search_image_annotations(
    runtime_db: Session,
    *,
    user_id: int,
    query: str,
    embedding_fn: EmbeddingFn,
    limit: int = 5,
) -> list[ImageAnnotationSearchResult]:
    if limit <= 0:
        return []
    query_embedding = _run_embedding(embedding_fn, query)
    return search_image_annotations_by_embedding(
        runtime_db,
        user_id=user_id,
        query_embedding=query_embedding,
        limit=limit,
    )


def search_image_annotations_by_embedding(
    runtime_db: Session,
    *,
    user_id: int,
    query_embedding: Sequence[float] | None,
    limit: int = 5,
) -> list[ImageAnnotationSearchResult]:
    if limit <= 0:
        return []
    if not query_embedding:
        return []

    rows = runtime_db.execute(
        select(RuntimeImageAnnotation, RuntimeImageAsset, RuntimeEmbedding)
        .join(RuntimeImageAsset, RuntimeImageAnnotation.image_asset_id == RuntimeImageAsset.id)
        .join(
            RuntimeEmbedding,
            (RuntimeEmbedding.user_id == RuntimeImageAnnotation.user_id)
            & (RuntimeEmbedding.source_type == "image_annotation")
            & (RuntimeEmbedding.source_id == RuntimeImageAnnotation.id)
            & (RuntimeEmbedding.content_hash == RuntimeImageAnnotation.content_hash),
        )
        .where(
            RuntimeImageAnnotation.user_id == user_id,
            RuntimeImageAnnotation.status == "active",
            RuntimeImageAsset.user_id == user_id,
            RuntimeImageAsset.status != "deleted",
        )
    ).all()

    scored: list[tuple[float, RuntimeImageAnnotation, RuntimeImageAsset]] = []
    for annotation, asset, embedding in rows:
        similarity = _cosine_similarity(query_embedding, list(embedding.embedding))
        scored.append((similarity, annotation, asset))
    scored.sort(key=lambda item: item[0], reverse=True)

    results: list[ImageAnnotationSearchResult] = []
    seen_assets: set[int] = set()
    for similarity, annotation, asset in scored:
        if (
            not math.isfinite(similarity)
            or similarity <= MIN_RELEVANT_IMAGE_ANNOTATION_SIMILARITY
        ):
            continue
        if asset.id in seen_assets:
            continue
        seen_assets.add(asset.id)
        source = _latest_image_source(
            runtime_db, user_id=user_id, image_asset_id=asset.id)
        source_message_id: int | None = None
        source_thread_id: int | None = None
        attachment_id: str | None = None
        attachment_url: str | None = None
        if source is not None:
            link, message = source
            source_message_id = message.id
            source_thread_id = message.thread_id
            attachment_id = link.attachment_id
            if attachment_id:
                attachment_url = (
                    f"/api/chat/messages/{message.id}/attachments/{attachment_id}"
                )
        results.append(
            ImageAnnotationSearchResult(
                image_asset_id=asset.id,
                user_id=asset.user_id,
                filename=asset.filename,
                mime_type=asset.mime_type,
                source_message_id=source_message_id,
                source_thread_id=source_thread_id,
                attachment_id=attachment_id,
                attachment_url=attachment_url,
                annotation_id=annotation.id,
                annotation_kind=annotation.annotation_kind,
                snippet=annotation.content_text,
                similarity=round(similarity, 4),
            )
        )
        if len(results) >= limit:
            break
    return results


def _latest_image_source(
    runtime_db: Session,
    *,
    user_id: int,
    image_asset_id: int,
) -> tuple[RuntimeImageMessageLink, RuntimeMessage] | None:
    row = runtime_db.execute(
        select(RuntimeImageMessageLink, RuntimeMessage)
        .join(RuntimeMessage, RuntimeImageMessageLink.message_id == RuntimeMessage.id)
        .where(
            RuntimeImageMessageLink.user_id == user_id,
            RuntimeImageMessageLink.image_asset_id == image_asset_id,
            RuntimeMessage.user_id == user_id,
        )
        .order_by(RuntimeImageMessageLink.created_at.desc())
        .limit(1)
    ).first()
    if row is None:
        return None
    link, message = row
    return link, message


def _cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)
