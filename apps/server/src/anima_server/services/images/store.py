from __future__ import annotations

from typing import TYPE_CHECKING

from anima_server.models.runtime import RuntimeImageAsset

if TYPE_CHECKING:
    from anima_server.services.corefs.asset_authority import CoreFsByteSource

ALLOWED_IMAGE_MIME_TYPES = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "image/gif": "gif",
}


def resolve_image_byte_source(
    asset: RuntimeImageAsset,
    *,
    user_id: int,
) -> CoreFsByteSource:
    from anima_server.services.corefs.asset_authority import (
        open_corefs_byte_source,
        require_canonical_asset_session,
    )
    from anima_server.services.corefs.catalog_reader import stable_opaque_id

    session = require_canonical_asset_session(user_id)
    stable_id = stable_opaque_id("image-asset", str(asset.id))
    return open_corefs_byte_source(
        session=session,
        object_uri=f"corefs://object/{stable_id}",
        expected_kinds=frozenset({"gallery-asset"}),
    )


def resolve_projected_image_byte_source(
    *,
    user_id: int,
    image_asset_id: int,
) -> CoreFsByteSource:
    """Resolve an image from unlock memory without consulting Runtime rows."""
    from anima_server.services.corefs.asset_authority import (
        open_corefs_byte_source,
        require_canonical_asset_session,
    )
    from anima_server.services.corefs.image_authority import canonical_image_projection

    session = require_canonical_asset_session(user_id)
    projection = canonical_image_projection(
        session=session,
        image_asset_id=image_asset_id,
    )
    if projection is None:
        raise LookupError("Canonical image projection is unavailable.")
    source = open_corefs_byte_source(
        session=session,
        object_uri=f"corefs://object/{projection.stable_id}",
        expected_kinds=frozenset({"gallery-asset"}),
    )
    if (
        source.content_sha256 != projection.content_sha256
        or source.content_type != projection.mime_type
        or source.size != projection.size
    ):
        raise ValueError("Canonical image projection changed identity.")
    return source


def resolve_identity_avatar_byte_source(*, user_id: int) -> CoreFsByteSource:
    from anima_server.services.corefs.asset_authority import (
        open_corefs_byte_source,
        require_canonical_asset_session,
    )
    from anima_server.services.corefs.catalog_reader import stable_opaque_id

    session = require_canonical_asset_session(user_id)
    stable_id = stable_opaque_id("identity-avatar", "agent-profile")
    return open_corefs_byte_source(
        session=session,
        object_uri=f"corefs://object/{stable_id}",
        expected_kinds=frozenset({"gallery-asset"}),
    )


def detect_image_mime(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return None
