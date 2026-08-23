from __future__ import annotations

import hashlib
import re
from pathlib import Path, PureWindowsPath
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.config import settings
from anima_server.models.runtime import RuntimeImageAsset
from anima_server.services.corefs.sealed_runtime import seal_runtime_fields
from anima_server.services.images.models import StoredImageAsset

if TYPE_CHECKING:
    from anima_server.services.corefs.asset_authority import CoreFsByteSource

ALLOWED_IMAGE_MIME_TYPES = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "image/gif": "gif",
}


class ImageAssetValidationError(ValueError):
    pass


class ImageStoragePathError(ValueError):
    pass


def register_image_asset(
    db: Session,
    *,
    user_id: int,
    data: bytes,
    mime_type: str,
    filename: str | None = None,
    metadata_json: dict[str, object] | None = None,
) -> StoredImageAsset:
    from anima_server.services.corefs.asset_authority import (
        require_legacy_asset_mutation_allowed,
    )

    require_legacy_asset_mutation_allowed(user_id)
    normalized_mime = _normalize_mime_type(mime_type)
    ext = ALLOWED_IMAGE_MIME_TYPES.get(normalized_mime)
    if ext is None:
        raise ImageAssetValidationError("Unsupported image type. Use PNG, JPEG, WebP, or GIF.")

    actual_mime = detect_image_mime(data)
    if actual_mime != normalized_mime:
        raise ImageAssetValidationError("Declared MIME type does not match image bytes.")

    digest = hashlib.sha256(data).hexdigest()
    existing = db.scalar(
        select(RuntimeImageAsset).where(
            RuntimeImageAsset.user_id == user_id,
            RuntimeImageAsset.sha256 == digest,
        )
    )
    if existing is not None:
        path = resolve_image_storage_path(existing.storage_path, user_id=user_id)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        return StoredImageAsset(asset=existing, path=path, created=False)

    storage_path = _storage_path_for(user_id=user_id, sha256=digest, ext=ext)
    path = resolve_image_storage_path(storage_path, user_id=user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(data)

    asset = RuntimeImageAsset(
        user_id=user_id,
        filename=None,
        mime_type="",
        storage_path="",
        sha256=digest,
        size_bytes=len(data),
        status="registered",
        retention_state="transient",
        metadata_json=None,
    )
    seal_runtime_fields(
        db,
        row=asset,
        row_type="runtime_image_asset",
        owner_id=user_id,
        payload={
            "filename": _sanitize_filename(filename),
            "mime_type": normalized_mime,
            "storage_path": storage_path,
            "metadata_json": dict(metadata_json) if metadata_json is not None else None,
        },
        placeholders={
            "filename": None,
            "mime_type": "",
            "storage_path": "",
            "metadata_json": None,
        },
    )
    return StoredImageAsset(asset=asset, path=path, created=True)


def resolve_image_storage_path(storage_path: str, *, user_id: int) -> Path:
    stripped = storage_path.strip()
    windows_path = PureWindowsPath(stripped)
    path = Path(stripped)
    if (
        not stripped
        or path.is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
        or windows_path.root
    ):
        raise ImageStoragePathError("Invalid image storage path.")

    try:
        data_root = settings.data_dir.resolve()
        resolved_path = (data_root / path).resolve()
        resolved_path.relative_to(data_root)
        resolved_path.relative_to(_user_image_root(data_root=data_root, user_id=user_id))
    except (OSError, ValueError) as exc:
        raise ImageStoragePathError("Invalid image storage path.") from exc

    return resolved_path


def resolve_image_byte_source(
    asset: RuntimeImageAsset,
    *,
    user_id: int,
) -> Path | CoreFsByteSource:
    from anima_server.services.corefs.asset_authority import (
        canonical_asset_session_or_legacy,
        open_corefs_byte_source,
    )
    from anima_server.services.corefs.diary_migration import migration_opaque_id

    session = canonical_asset_session_or_legacy(user_id)
    if session is not None:
        stable_id = migration_opaque_id("image-asset", str(asset.id))
        return open_corefs_byte_source(
            session=session,
            object_uri=f"corefs://object/{stable_id}",
            expected_kinds=frozenset({"gallery-asset"}),
        )
    return resolve_image_storage_path(asset.storage_path, user_id=user_id)


def resolve_projected_image_byte_source(
    *,
    user_id: int,
    image_asset_id: int,
) -> CoreFsByteSource | None:
    """Resolve an image from unlock memory without consulting Runtime rows."""
    from anima_server.services.corefs.asset_authority import (
        canonical_asset_session_or_legacy,
        open_corefs_byte_source,
    )
    from anima_server.services.corefs.image_authority import canonical_image_projection

    session = canonical_asset_session_or_legacy(user_id)
    if session is None:
        return None
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


def resolve_identity_avatar_byte_source(*, user_id: int) -> CoreFsByteSource | None:
    from anima_server.services.corefs.asset_authority import (
        canonical_asset_session_or_legacy,
        open_corefs_byte_source,
    )
    from anima_server.services.corefs.diary_migration import migration_opaque_id

    session = canonical_asset_session_or_legacy(user_id)
    if session is None:
        return None
    stable_id = migration_opaque_id("identity-avatar", "agent-profile")
    return open_corefs_byte_source(
        session=session,
        object_uri=f"corefs://object/{stable_id}",
        expected_kinds=frozenset({"gallery-asset"}),
    )


def delete_image_asset_file_if_safe(asset: RuntimeImageAsset) -> bool:
    path = resolve_image_storage_path(asset.storage_path, user_id=asset.user_id)
    if not path.exists():
        return False
    if not path.is_file():
        raise ImageStoragePathError("Image asset path is not a file.")
    path.unlink()
    return True


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


def _storage_path_for(*, user_id: int, sha256: str, ext: str) -> str:
    return f"users/{user_id}/media/images/{sha256[:2]}/{sha256}.{ext}"


def _user_image_root(*, data_root: Path, user_id: int) -> Path:
    return data_root / "users" / str(user_id) / "media" / "images"


def _normalize_mime_type(value: str) -> str:
    return value.split(";", maxsplit=1)[0].strip().lower()


_SAFE_FILENAME_RE = re.compile(r"[^a-zA-Z0-9._ -]+")


def _sanitize_filename(filename: str | None) -> str | None:
    if filename is None:
        return None
    name = Path(filename).name.strip()
    if not name:
        return None
    name = _SAFE_FILENAME_RE.sub("_", name)
    return name[:180] or None
