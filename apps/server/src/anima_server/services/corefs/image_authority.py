"""Canonical post-cutover image projections and retention mutations."""

from __future__ import annotations

import json
from contextlib import suppress
from typing import Any

from anima_server.services.corefs.asset_authority import open_corefs_byte_source
from anima_server.services.corefs.asset_mutations import (
    AssetMutationError,
    trash_canonical_asset,
    upsert_canonical_binary_asset,
)
from anima_server.services.corefs.diary_migration import (
    migration_opaque_id,
    read_prepared_writing_snapshot,
)
from anima_server.services.corefs.indexer import CoreFSImageProjection

_RETENTION_CONTENT_TYPE = "application/vnd.anima.image-retention+json"
_RETENTION_FORMAT = "anima-image-retention-v1"


def canonical_image_projection(
    *,
    session: Any,
    image_asset_id: int,
) -> CoreFSImageProjection | None:
    snapshot = read_prepared_writing_snapshot(session=session)
    for item in snapshot.objects:
        if item.kind != "gallery-asset":
            continue
        metadata = item.metadata
        legacy_id = metadata.get("legacyId")
        if metadata.get("origin") != "image-asset" or legacy_id != str(image_asset_id):
            continue
        filename = metadata.get("originalName")
        mime_type = metadata.get("mimeType")
        size = metadata.get("sizeBytes")
        if (
            not isinstance(filename, str)
            or not filename
            or not isinstance(mime_type, str)
            or not mime_type.startswith("image/")
            or isinstance(size, bool)
            or not isinstance(size, int)
            or size < 1
            or metadata.get("sha256") != item.content_hash
        ):
            raise ValueError("Canonical image metadata is invalid.")
        source = open_corefs_byte_source(
            session=session,
            object_uri=f"corefs://object/{item.stable_id}",
            expected_kinds=frozenset({"gallery-asset"}),
        )
        if (
            source.content_sha256 != item.content_hash
            or source.content_type != mime_type
            or source.size != size
        ):
            raise ValueError("Canonical image body changed identity.")
        return CoreFSImageProjection(
            image_asset_id=image_asset_id,
            stable_id=item.stable_id,
            filename=filename,
            mime_type=mime_type,
            content_sha256=item.content_hash,
            size=size,
        )
    return None


def set_canonical_image_retention(
    *,
    session: Any,
    image_asset_id: int,
    retention_state: str,
) -> str | None:
    normalized = retention_state.strip().lower()
    if normalized not in {"transient", "retained", "durable"}:
        raise ValueError("retention_state must be transient, retained, or durable")
    projection = canonical_image_projection(
        session=session,
        image_asset_id=image_asset_id,
    )
    if projection is None:
        return None
    body = json.dumps(
        {
            "format": _RETENTION_FORMAT,
            "imageStableId": projection.stable_id,
            "retentionState": normalized,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    upsert_canonical_binary_asset(
        session=session,
        stable_id=_retention_stable_id(projection.stable_id),
        name=f"{projection.filename}.retention.json",
        object_kind="attachment",
        content_type=_RETENTION_CONTENT_TYPE,
        data=body,
        replace_existing=True,
    )
    return normalized


def forget_canonical_image(*, session: Any, image_asset_id: int) -> bool:
    projection = canonical_image_projection(
        session=session,
        image_asset_id=image_asset_id,
    )
    if projection is None:
        return False
    if not trash_canonical_asset(session=session, stable_id=projection.stable_id):
        return False
    # The source image is already recoverably trashed. A policy sidecar is
    # non-authoritative and harmless if cleanup must resume later.
    with suppress(AssetMutationError, RuntimeError, ValueError):
        trash_canonical_asset(
            session=session,
            stable_id=_retention_stable_id(projection.stable_id),
        )
    return True


def _retention_stable_id(image_stable_id: str) -> str:
    return migration_opaque_id("image-retention", image_stable_id)
