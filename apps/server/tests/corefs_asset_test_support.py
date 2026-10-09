"""Canonical original-asset/source projection for inactive CoreFS validation."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from corefs_catalog_test_support import (
    InactiveFolder,
    migration_opaque_id,
)
from corefs_writing_fixture_support import (
    WritingSourceBody,
    WritingSourceObjectDescriptor,
)

MAX_PORTABLE_ASSET_BYTES = 100 * 1024 * 1024
_BINARY_KINDS = frozenset({"attachment", "gallery-asset"})
_FORBIDDEN_METADATA_KEYS = frozenset(
    {"path", "hostPath", "host_path", "sourcePath", "source_path", "storagePath", "storage_path"}
)


@dataclass(frozen=True, slots=True)
class PortableBinaryAssetSource:
    namespace: str
    legacy_id: str
    name: str
    kind: Literal["attachment", "gallery-asset"] | str
    content_type: str
    path: Path
    size: int
    sha256: str
    created_at: str
    updated_at: str
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PortableKnowledgeSource:
    legacy_id: str
    name: str
    content_type: str
    data: bytes
    created_at: str
    updated_at: str
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PortableAssetShadow:
    folders: tuple[InactiveFolder, ...]
    objects: tuple[WritingSourceBody, ...]
    _asset_uri_by_legacy_id: Mapping[str, str] = field(repr=False)
    _document_uri_by_legacy_id: Mapping[str, str] = field(repr=False)
    _asset_uri_by_attachment_id: Mapping[str, str] = field(repr=False)
    _asset_uri_by_sha256: Mapping[str, str] = field(repr=False)

    def resolve_reference(self, value: object) -> str | None:
        if not isinstance(value, Mapping):
            return None
        asset_id = value.get("assetId", value.get("asset_id"))
        if not isinstance(asset_id, bool) and isinstance(asset_id, (int, str)):
            resolved = self._asset_uri_by_legacy_id.get(str(asset_id))
            if resolved is not None:
                return resolved
        attachment_id = value.get("id")
        if isinstance(attachment_id, str):
            resolved = self._asset_uri_by_attachment_id.get(attachment_id)
            if resolved is not None:
                return resolved
        digest = value.get("sha256")
        if isinstance(digest, str):
            return self._asset_uri_by_sha256.get(digest)
        return None

    def resolve_document(self, legacy_id: int | str) -> str | None:
        return self._document_uri_by_legacy_id.get(str(legacy_id))


def build_portable_asset_shadow(
    *,
    user_id: int,
    binary_assets: Iterable[PortableBinaryAssetSource] = (),
    knowledge_sources: Iterable[PortableKnowledgeSource] = (),
    attachment_links: Mapping[str, str] | None = None,
    preserved_gallery_folder: InactiveFolder | None = None,
) -> PortableAssetShadow:
    """Build a deterministic encrypted-original inventory without derived text."""
    del user_id
    root_id = migration_opaque_id("core-folder", "root")
    folder_id = migration_opaque_id("core-folder-role", "core.gallery")
    folder = preserved_gallery_folder or InactiveFolder(
        stable_id=folder_id,
        parent_id=root_id,
        name="Gallery",
        order=3,
        role="core.gallery",
        owner="user",
        agent_access="write",
        policy="user-write",
        metadata={"formatVersion": 1},
    )
    return _assemble_portable_asset_shadow(
        folder=folder,
        binary_assets=binary_assets,
        knowledge_sources=knowledge_sources,
        attachment_links=attachment_links,
    )


def _assemble_portable_asset_shadow(
    *,
    folder: InactiveFolder,
    binary_assets: Iterable[PortableBinaryAssetSource],
    knowledge_sources: Iterable[PortableKnowledgeSource],
    attachment_links: Mapping[str, str] | None,
) -> PortableAssetShadow:
    if (
        folder.role != "core.gallery"
        or folder.owner != "user"
        or folder.agent_access != "write"
        or folder.policy != "user-write"
    ):
        raise ValueError("Preserved gallery root violates the user/write contract.")

    objects: dict[str, WritingSourceBody] = {}
    asset_uri_by_id: dict[str, str] = {}
    document_uri_by_id: dict[str, str] = {}
    digest_candidates: dict[str, set[str]] = {}
    for source in binary_assets:
        _validate_binary_source(source)
        stable_id = migration_opaque_id(source.namespace, source.legacy_id)
        uri = f"corefs://object/{stable_id}"
        metadata = _private_metadata(source.metadata)
        metadata.update(
            {
                "legacyId": source.legacy_id,
                "originalName": source.name,
                "mimeType": source.content_type,
                "sizeBytes": source.size,
                "sha256": source.sha256,
                "origin": source.namespace,
            }
        )
        body = WritingSourceBody(
            descriptor=WritingSourceObjectDescriptor(
                stable_id=stable_id,
                parent_id=folder.stable_id,
                name=source.name,
                kind=source.kind,
                content_type=source.content_type,
                body_encoding="binary",
                body_length=source.size,
                content_sha256=source.sha256,
                source_fingerprint_sha256=source.sha256,
                created_at=source.created_at,
                updated_at=source.updated_at,
                revision=1,
                metadata=metadata,
                body_source="supplemental_path",
                source_key=str(source.path),
            ),
            body=None,
        )
        _insert_exact(objects, body)
        if source.namespace == "image-asset":
            asset_uri_by_id[source.legacy_id] = uri
            digest_candidates.setdefault(source.sha256, set()).add(uri)
        elif source.namespace == "document":
            document_uri_by_id[source.legacy_id] = uri

    for source in knowledge_sources:
        _validate_knowledge_source(source)
        stable_id = migration_opaque_id("knowledge-source", source.legacy_id)
        digest = hashlib.sha256(source.data).hexdigest()
        metadata = _private_metadata(source.metadata)
        metadata.update(
            {
                "legacyId": source.legacy_id,
                "originalName": source.name,
                "contentType": source.content_type,
                "sizeBytes": len(source.data),
                "sha256": digest,
            }
        )
        body = WritingSourceBody(
            descriptor=WritingSourceObjectDescriptor(
                stable_id=stable_id,
                parent_id=folder.stable_id,
                name=source.name,
                kind="knowledge-source",
                content_type=source.content_type,
                body_encoding="utf-8",
                body_length=len(source.data),
                content_sha256=digest,
                source_fingerprint_sha256=digest,
                created_at=source.created_at,
                updated_at=source.updated_at,
                revision=1,
                metadata=metadata,
                body_source="supplemental",
                source_key=stable_id,
            ),
            body=source.data,
        )
        _insert_exact(objects, body)

    asset_uri_by_attachment_id = {
        attachment_id: asset_uri_by_id[legacy_id]
        for attachment_id, legacy_id in (attachment_links or {}).items()
        if legacy_id in asset_uri_by_id
    }
    asset_uri_by_sha256 = {
        digest: next(iter(uris)) for digest, uris in digest_candidates.items() if len(uris) == 1
    }
    return PortableAssetShadow(
        folders=(folder,),
        objects=tuple(objects[key] for key in sorted(objects)),
        _asset_uri_by_legacy_id=asset_uri_by_id,
        _document_uri_by_legacy_id=document_uri_by_id,
        _asset_uri_by_attachment_id=asset_uri_by_attachment_id,
        _asset_uri_by_sha256=asset_uri_by_sha256,
    )


def _insert_exact(
    objects: dict[str, WritingSourceBody],
    candidate: WritingSourceBody,
) -> None:
    stable_id = candidate.descriptor.stable_id
    existing = objects.get(stable_id)
    if existing is None:
        objects[stable_id] = candidate
        return
    if existing != candidate:
        raise ValueError("Portable source contains a conflicting stable object identity.")


def _validate_binary_source(source: PortableBinaryAssetSource) -> None:
    if not source.namespace or not source.legacy_id or not source.name:
        raise ValueError("Portable binary source identity is invalid.")
    if source.kind not in _BINARY_KINDS:
        raise ValueError("Portable binary source kind is invalid.")
    if not source.content_type or len(source.content_type) > 255:
        raise ValueError("Portable binary content type is invalid.")
    if source.size < 0 or source.size > MAX_PORTABLE_ASSET_BYTES:
        raise ValueError("Portable binary source exceeds the object size limit.")
    if len(source.sha256) != 64 or any(char not in "0123456789abcdef" for char in source.sha256):
        raise ValueError("Portable binary source hash is invalid.")
    actual_size, actual_hash = _file_identity(source.path)
    if actual_size != source.size or actual_hash != source.sha256:
        raise ValueError("Portable binary source identity changed during collection.")
    _private_metadata(source.metadata)


def _validate_knowledge_source(source: PortableKnowledgeSource) -> None:
    if not source.legacy_id or not source.name:
        raise ValueError("Portable knowledge source identity is invalid.")
    if not source.content_type or len(source.content_type) > 255:
        raise ValueError("Portable knowledge source content type is invalid.")
    if not source.data or len(source.data) > MAX_PORTABLE_ASSET_BYTES:
        raise ValueError("Portable knowledge source byte length is invalid.")
    try:
        source.data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Portable knowledge source must be valid UTF-8.") from exc
    _private_metadata(source.metadata)


def _private_metadata(value: Mapping[str, object]) -> dict[str, object]:
    result = dict(value)
    if _contains_forbidden_key(result):
        raise ValueError("Canonical private metadata cannot retain a host path.")
    try:
        json.dumps(result, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ValueError("Canonical private metadata is not JSON-safe.") from exc
    return result


def _contains_forbidden_key(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(
            key in _FORBIDDEN_METADATA_KEYS or _contains_forbidden_key(child)
            for key, child in value.items()
            if isinstance(key, str)
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_forbidden_key(child) for child in value)
    return False


def _file_identity(path: Path) -> tuple[int, str]:
    hasher = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as source:
            while chunk := source.read(64 * 1024):
                size += len(chunk)
                if size > MAX_PORTABLE_ASSET_BYTES:
                    raise ValueError("Portable binary source exceeds the object size limit.")
                hasher.update(chunk)
    except OSError as exc:
        raise ValueError("Portable binary source is unavailable.") from exc
    return size, hasher.hexdigest()


def _timestamp(value: datetime | str | None) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        raise ValueError("Portable source timestamp is missing.")
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _without_host_paths(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): _without_host_paths(child)
            for key, child in value.items()
            if isinstance(key, str) and key not in _FORBIDDEN_METADATA_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_without_host_paths(child) for child in value]
    return value


def _avatar_content_type(path: Path) -> str:
    content_type = {
        ".gif": "image/gif",
        ".jpeg": "image/jpeg",
        ".jpg": "image/jpeg",
        ".png": "image/png",
        ".svg": "image/svg+xml",
        ".webp": "image/webp",
    }.get(path.suffix.lower())
    if content_type is None:
        raise ValueError("Identity avatar content type is unsupported.")
    return content_type


def _is_canonical_source_artifact(source_kind: str, artifact_kind: str) -> bool:
    allowed = {
        "text": {"plain_text"},
        "markdown": {"markdown", "structured_markdown"},
        "web_capture": {"raw_html", "structured_markdown", "readable_text"},
        "html": {"raw_html", "structured_markdown"},
    }
    return artifact_kind in allowed.get(source_kind, set())


def _knowledge_content_type(artifact_kind: str) -> tuple[str, str]:
    if artifact_kind == "raw_html":
        return "text/html; charset=utf-8", ".html"
    if artifact_kind in {"markdown", "structured_markdown"}:
        return "text/markdown; charset=utf-8", ".md"
    return "text/plain; charset=utf-8", ".txt"


def _canonical_source_uri(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"html", "http", "https", "markdown", "text"}:
        raise ValueError("Canonical knowledge source URI is not portable.")
    if parsed.scheme in {"http", "https"} and not parsed.netloc:
        raise ValueError("Canonical knowledge source URI is invalid.")
    if "\\" in value or value.startswith(("/", "~")):
        raise ValueError("Canonical knowledge source URI contains a host path.")
    return value


def portable_asset_shadow_digest(shadow: PortableAssetShadow) -> str:
    payload: list[dict[str, Any]] = [
        {
            "id": item.descriptor.stable_id,
            "sha256": item.descriptor.content_sha256,
        }
        for item in shadow.objects
    ]
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
