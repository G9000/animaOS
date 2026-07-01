from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

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
class ImageAnnotationSource:
    message_id: int | None
    thread_id: int | None
    attachment_id: str | None
    attachment_url: str | None
    created_at: datetime | None = None


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
    related_sources: tuple[ImageAnnotationSource, ...]


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

    seen_assets: set[int] = set()
    scored_candidates: list[tuple[float, RuntimeImageAnnotation, RuntimeImageAsset]] = []
    for similarity, annotation, asset in scored:
        if (
            not math.isfinite(similarity)
            or similarity <= MIN_RELEVANT_IMAGE_ANNOTATION_SIMILARITY
        ):
            continue
        if asset.id in seen_assets:
            continue
        seen_assets.add(asset.id)
        scored_candidates.append((similarity, annotation, asset))

    asset_sources = _all_image_sources_by_asset(
        runtime_db,
        user_id=user_id,
        image_asset_ids=[asset.id for _, _, asset in scored_candidates],
    )

    results: list[ImageAnnotationSearchResult] = []
    for similarity, annotation, asset in scored_candidates:
        sources = asset_sources.get(asset.id, ())
        source_message_id: int | None = None
        source_thread_id: int | None = None
        attachment_id: str | None = None
        attachment_url: str | None = None
        if sources:
            latest = sources[0]
            source_message_id = latest.message_id
            source_thread_id = latest.thread_id
            attachment_id = latest.attachment_id
            attachment_url = latest.attachment_url
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
                related_sources=sources,
            )
        )
        if len(results) >= limit:
            break
    return results


def _all_image_sources_by_asset(
    runtime_db: Session,
    *,
    user_id: int,
    image_asset_ids: Sequence[int],
) -> dict[int, tuple[ImageAnnotationSource, ...]]:
    if not image_asset_ids:
        return {}

    rows = runtime_db.execute(
        select(RuntimeImageMessageLink, RuntimeMessage)
        .join(RuntimeMessage, RuntimeImageMessageLink.message_id == RuntimeMessage.id)
        .where(
            RuntimeImageMessageLink.user_id == user_id,
            RuntimeImageMessageLink.image_asset_id.in_(image_asset_ids),
            RuntimeMessage.user_id == user_id,
        )
        .order_by(
            RuntimeImageMessageLink.image_asset_id,
            RuntimeImageMessageLink.created_at.desc(),
            RuntimeImageMessageLink.id.desc(),
        )
    ).all()
    if not rows:
        return {}

    grouped: dict[int, list[ImageAnnotationSource]] = {}
    for link, message in rows:
        grouped.setdefault(link.image_asset_id, []).append(
            ImageAnnotationSource(
                message_id=message.id,
                thread_id=message.thread_id,
                attachment_id=link.attachment_id,
                attachment_url=(
                    f"/api/chat/messages/{message.id}/attachments/{link.attachment_id}"
                    if link.attachment_id
                    else None
                ),
                created_at=link.created_at,
            )
        )

    return {asset_id: tuple(sources) for asset_id, sources in grouped.items()}


def _cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)
