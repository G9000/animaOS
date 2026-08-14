"""Canonical post-cutover knowledge-source documents and projections."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from anima_server.services.corefs.asset_mutations import upsert_canonical_binary_asset
from anima_server.services.corefs.diary_migration import (
    migration_opaque_id,
    read_prepared_writing_body,
    read_prepared_writing_snapshot,
)
from anima_server.services.corefs.indexer import (
    CoreFSKnowledgeConceptProjection,
    CoreFSKnowledgeSourceProjection,
)

KNOWLEDGE_DOCUMENT_CONTENT_TYPE = "application/vnd.anima.knowledge-source+json"
_FORMAT = "anima-knowledge-source-v1"


@dataclass(frozen=True, slots=True)
class CanonicalKnowledgeDocument:
    source_kind: str
    source_uri: str
    source_title: str | None
    source_media_type: str
    filename: str
    artifact_kind: str
    content: str
    original_content: str


class CanonicalKnowledgeProjectionView:
    def __init__(self, projections: tuple[CoreFSKnowledgeSourceProjection, ...]) -> None:
        self._sources = projections
        self._concepts = tuple(_concept(projection) for projection in projections)

    def knowledge_source_projections(self) -> tuple[CoreFSKnowledgeSourceProjection, ...]:
        return self._sources

    def knowledge_concept_projections(self) -> tuple[CoreFSKnowledgeConceptProjection, ...]:
        return self._concepts

    def knowledge_concept_projection(
        self,
        concept_id: int,
    ) -> CoreFSKnowledgeConceptProjection | None:
        return next(
            (item for item in self._concepts if item.concept_id == concept_id),
            None,
        )

    def search_knowledge_source_projections(
        self,
        query: str,
        *,
        limit: int,
    ) -> tuple[CoreFSKnowledgeSourceProjection, ...]:
        terms = tuple(dict.fromkeys(query.casefold().split()))
        ranked = [
            (sum(item.content_text.casefold().count(term) for term in terms), item)
            for item in self._sources
        ]
        ranked.sort(key=lambda value: (-value[0], value[1].source_id))
        return tuple(item for score, item in ranked if score > 0)[:limit]

    def search_knowledge_concept_projections(
        self,
        query: str,
        *,
        limit: int,
    ) -> tuple[CoreFSKnowledgeConceptProjection, ...]:
        terms = tuple(dict.fromkeys(query.casefold().split()))
        ranked = [
            (
                sum(
                    f"{item.title}\n{item.body_markdown}".casefold().count(term)
                    for term in terms
                ),
                item,
            )
            for item in self._concepts
        ]
        ranked.sort(key=lambda value: (-value[0], value[1].concept_id))
        return tuple(item for score, item in ranked if score > 0)[:limit]


def publish_canonical_knowledge_source(
    *,
    session: Any,
    document: CanonicalKnowledgeDocument,
    stable_id: str | None = None,
    replace_existing: bool = False,
) -> CoreFSKnowledgeSourceProjection:
    body = encode_knowledge_document(document)
    resolved_stable_id = stable_id or migration_opaque_id(
        "knowledge-live-source",
        _document_identity(document),
    )
    upsert_canonical_binary_asset(
        session=session,
        stable_id=resolved_stable_id,
        name=document.filename,
        object_kind="knowledge-source",
        content_type=KNOWLEDGE_DOCUMENT_CONTENT_TYPE,
        data=body,
        replace_existing=replace_existing,
    )
    return knowledge_projection_from_document(
        stable_id=resolved_stable_id,
        filename=document.filename,
        document=document,
        content_sha256=hashlib.sha256(body).hexdigest(),
    )


def canonical_knowledge_document(
    *,
    session: Any,
    source_id: int,
) -> tuple[str, CanonicalKnowledgeDocument] | None:
    snapshot = read_prepared_writing_snapshot(session=session)
    for item in snapshot.objects:
        if (
            item.kind != "knowledge-source"
            or item.content_type != KNOWLEDGE_DOCUMENT_CONTENT_TYPE
            or _numeric_id("source", item.stable_id) != source_id
        ):
            continue
        body = read_prepared_writing_body(session=session, item=item)
        return item.stable_id, decode_knowledge_document(body)
    return None


def canonical_knowledge_view(*, session: Any) -> CanonicalKnowledgeProjectionView:
    snapshot = read_prepared_writing_snapshot(session=session)
    projections: list[CoreFSKnowledgeSourceProjection] = []
    for item in snapshot.objects:
        if item.kind != "knowledge-source":
            continue
        body = read_prepared_writing_body(session=session, item=item)
        if item.content_type == KNOWLEDGE_DOCUMENT_CONTENT_TYPE:
            projections.append(
                knowledge_projection_from_document(
                    stable_id=item.stable_id,
                    filename=item.name,
                    document=decode_knowledge_document(body),
                    content_sha256=item.content_hash,
                )
            )
            continue
        metadata = item.metadata
        source_id = metadata.get("sourceId")
        artifact_id = metadata.get("artifactId")
        artifact_kind = metadata.get("artifactKind")
        source_kind = metadata.get("sourceKind")
        source_uri = metadata.get("sourceUri")
        if (
            isinstance(source_id, bool)
            or not isinstance(source_id, int)
            or source_id < 1
            or isinstance(artifact_id, bool)
            or not isinstance(artifact_id, int)
            or artifact_id < 1
            or not isinstance(artifact_kind, str)
            or not isinstance(source_kind, str)
            or not isinstance(source_uri, str)
        ):
            raise ValueError("Canonical knowledge-source metadata is invalid.")
        projections.append(
            CoreFSKnowledgeSourceProjection(
                stable_id=item.stable_id,
                source_id=source_id,
                artifact_id=artifact_id,
                artifact_kind=artifact_kind,
                source_kind=source_kind,
                source_uri=source_uri,
                source_title=metadata.get("sourceTitle")
                if isinstance(metadata.get("sourceTitle"), str)
                else None,
                source_media_type=metadata.get("sourceMediaType")
                if isinstance(metadata.get("sourceMediaType"), str)
                else item.content_type,
                filename=metadata.get("originalName")
                if isinstance(metadata.get("originalName"), str)
                else item.name,
                content_text=body.decode("utf-8"),
                content_sha256=item.content_hash,
            )
        )
    projections.sort(key=lambda item: (item.source_id, item.artifact_id, item.stable_id))
    return CanonicalKnowledgeProjectionView(tuple(projections))


def encode_knowledge_document(document: CanonicalKnowledgeDocument) -> bytes:
    if (
        not document.source_kind
        or not document.source_uri
        or not document.source_media_type
        or not document.filename
        or not document.artifact_kind
    ):
        raise ValueError("Canonical knowledge-source document is incomplete.")
    return json.dumps(
        {
            "artifactKind": document.artifact_kind,
            "content": document.content,
            "filename": document.filename,
            "format": _FORMAT,
            "originalContent": document.original_content,
            "sourceKind": document.source_kind,
            "sourceMediaType": document.source_media_type,
            "sourceTitle": document.source_title,
            "sourceUri": document.source_uri,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def decode_knowledge_document(body: bytes) -> CanonicalKnowledgeDocument:
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Canonical knowledge-source document is invalid.") from exc
    if not isinstance(payload, dict) or set(payload) != {
        "artifactKind",
        "content",
        "filename",
        "format",
        "originalContent",
        "sourceKind",
        "sourceMediaType",
        "sourceTitle",
        "sourceUri",
    }:
        raise ValueError("Canonical knowledge-source document is invalid.")
    title = payload["sourceTitle"]
    if title is not None and not isinstance(title, str):
        raise ValueError("Canonical knowledge-source title is invalid.")
    values = (
        payload["artifactKind"],
        payload["content"],
        payload["filename"],
        payload["format"],
        payload["originalContent"],
        payload["sourceKind"],
        payload["sourceMediaType"],
        payload["sourceUri"],
    )
    if not all(isinstance(value, str) and value for value in values) or payload["format"] != _FORMAT:
        raise ValueError("Canonical knowledge-source document is invalid.")
    return CanonicalKnowledgeDocument(
        source_kind=payload["sourceKind"],
        source_uri=payload["sourceUri"],
        source_title=title,
        source_media_type=payload["sourceMediaType"],
        filename=payload["filename"],
        artifact_kind=payload["artifactKind"],
        content=payload["content"],
        original_content=payload["originalContent"],
    )


def knowledge_projection_from_document(
    *,
    stable_id: str,
    filename: str,
    document: CanonicalKnowledgeDocument,
    content_sha256: str,
) -> CoreFSKnowledgeSourceProjection:
    return CoreFSKnowledgeSourceProjection(
        stable_id=stable_id,
        source_id=_numeric_id("source", stable_id),
        artifact_id=_numeric_id("artifact", stable_id),
        artifact_kind=document.artifact_kind,
        source_kind=document.source_kind,
        source_uri=document.source_uri,
        source_title=document.source_title,
        source_media_type=document.source_media_type,
        filename=filename,
        content_text=document.content,
        content_sha256=content_sha256,
    )


def _concept(source: CoreFSKnowledgeSourceProjection) -> CoreFSKnowledgeConceptProjection:
    compact = " ".join(source.content_text.split())
    title = source.source_title or source.filename
    slug = f"corefs-source-{source.source_id}"
    if source.source_kind == "okf" and source.source_uri.startswith("okf://"):
        candidate = source.source_uri.removeprefix("okf://")
        if candidate.endswith(".md") and "/" not in candidate:
            slug = candidate[:-3]
    return CoreFSKnowledgeConceptProjection(
        concept_id=source.source_id,
        source_id=source.source_id,
        stable_id=source.stable_id,
        artifact_id=source.artifact_id,
        artifact_kind=source.artifact_kind,
        slug=slug,
        title=title,
        description=compact[:240],
        concept_type="source_summary",
        body_markdown=source.content_text,
        source_uri=source.source_uri,
        content_sha256=source.content_sha256,
    )


def _numeric_id(namespace: str, stable_id: str) -> int:
    value = int.from_bytes(
        hashlib.sha256(f"{namespace}:{stable_id}".encode()).digest()[:8],
        "big",
    ) & ((1 << 63) - 1)
    return value or 1


def _document_identity(document: CanonicalKnowledgeDocument) -> str:
    digest = hashlib.sha256()
    for value in (
        document.source_kind,
        document.source_uri,
        document.original_content,
    ):
        encoded = value.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()
