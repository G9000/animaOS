"""Authenticated Core catalog and object readers for the supported format."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_MAX_PORTABLE_NAME_BYTES = 255


def _truncate_portable_name(value: str, suffix: str = "") -> str:
    """Bound a portable component without splitting a Unicode scalar."""
    available = _MAX_PORTABLE_NAME_BYTES - len(suffix.encode("utf-8"))
    if available <= 0:
        raise ValueError("Portable name suffix exceeds the CoreFS limit.")
    prefix = value
    while len(prefix.encode("utf-8")) > available:
        prefix = prefix[:-1]
    return f"{prefix}{suffix}"


def _portable_name_base(value: str, *, stable_id: str) -> str:
    """Encode a legacy display name into one valid deterministic CoreFS component."""
    try:
        import anima_core  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("Native CoreFS migration component mapping is unavailable.") from exc
    native = getattr(anima_core, "corefs_migration_component_v1", None)
    if native is None:
        raise RuntimeError("Native CoreFS migration component mapping is unavailable.")
    return str(native(value, stable_id))


def portable_catalog_component(value: str, *, stable_id: str) -> str:
    """Return one native portable component for post-cutover catalog writes."""
    return _portable_name_base(value, stable_id=stable_id)


def stable_opaque_id(domain: str, source_key: str | bytes) -> str:
    """Return the native converter's deterministic domain-separated ID."""
    source = source_key.encode() if isinstance(source_key, str) else source_key
    if not domain or not source:
        raise ValueError("Stable ID domain and source key are required.")
    try:
        import anima_core  # type: ignore[import-not-found]

        native = getattr(anima_core, "corefs_migration_id_v1", None)
        if native is not None:
            return str(native(domain, source))
    except ImportError:
        pass
    digest = hashlib.sha256(
        b"anima-corefs-migration-opaque-id-v1\0"
        + len(domain).to_bytes(8, "big")
        + domain.encode()
        + len(source).to_bytes(8, "big")
        + source
    ).digest()[:16]
    bits = int.from_bytes(digest, "big")
    return "".join(_CROCKFORD[(bits >> (125 - index * 5)) & 31] for index in range(26))


class CatalogReadError(RuntimeError):
    """Raised when the authenticated catalog cannot be read."""


@dataclass(frozen=True, slots=True)
class PreparedWritingObject:
    stable_id: str
    parent_id: str
    path: str
    name: str
    kind: str
    revision: int
    content_hash: str
    body_length: int
    created_at: str
    updated_at: str
    metadata: dict[str, Any]
    content_type: str
    body_encoding: str


@dataclass(frozen=True, slots=True)
class PreparedWritingFolder:
    stable_id: str
    parent_id: str | None
    path: str
    name: str
    role: str | None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PreparedWritingSnapshot:
    folders: tuple[PreparedWritingFolder, ...]
    objects: tuple[PreparedWritingObject, ...]


def resolve_prepared_role(*, session: Any, role: str) -> dict[str, object]:
    """Resolve an inactive stable role only through an authorized unlock session."""
    if session.corefs_session is None or session.corefs_keys is None:
        raise CatalogReadError("CoreFS prepared access requires an unlocked session.")
    value = session.corefs_session.resolve_validation_role_v1(session.corefs_keys, role)
    if not isinstance(value, dict):
        raise CatalogReadError(f"Prepared role {role} is unavailable.")
    return dict(value)


def read_prepared_writing_objects(*, session: Any) -> tuple[PreparedWritingObject, ...]:
    """Read authenticated inactive writing objects without changing route authority."""
    return read_prepared_writing_snapshot(session=session).objects


def read_prepared_writing_snapshot(
    *,
    session: Any,
    selected: Any | None = None,
) -> PreparedWritingSnapshot:
    """Read authenticated inactive folders and writing objects at one generation."""
    from anima_server.services.corefs import logical

    if session.corefs_session is None or session.corefs_keys is None:
        raise CatalogReadError("CoreFS prepared access requires an unlocked session.")
    if selected is None:
        selected = logical.select_validation_snapshot(
            corefs_session=session.corefs_session,
            keys=session.corefs_keys,
        )
    values: list[PreparedWritingObject] = []
    root = _wire_result(
        logical.stat_v1(
            corefs_session=session.corefs_session,
            keys=session.corefs_keys,
            selected=selected,
            path="",
        ),
        selected.generation,
    )
    root_id = root.get("stableId")
    if root.get("kind") != "directory" or not isinstance(root_id, str):
        raise CatalogReadError("Invalid prepared CoreFS root entry.")
    folders: list[PreparedWritingFolder] = [
        PreparedWritingFolder(
            stable_id=root_id,
            parent_id=None,
            path="",
            name="Core",
            role=None,
            metadata={},
        )
    ]
    cursor: str | None = None
    while True:
        raw = logical.walk_v1(
            corefs_session=session.corefs_session,
            keys=session.corefs_keys,
            selected=selected,
            root="",
            cursor_after=cursor,
            page_size=100,
            include_directories=True,
        )
        result = _wire_result(raw, selected.generation)
        entries = result.get("entries")
        if not isinstance(entries, list):
            raise CatalogReadError("Invalid prepared CoreFS walk response.")
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            path = entry.get("path")
            stable_id = entry.get("stableId")
            if entry.get("kind") == "directory":
                parent_id = entry.get("parentId")
                role = entry.get("role")
                metadata = entry.get("metadata")
                if metadata is None:
                    metadata = {}
                if (
                    not isinstance(path, str)
                    or not isinstance(stable_id, str)
                    or (parent_id is not None and not isinstance(parent_id, str))
                    or (role is not None and not isinstance(role, str))
                    or not isinstance(metadata, dict)
                ):
                    raise CatalogReadError("Invalid prepared CoreFS directory entry.")
                folders.append(
                    PreparedWritingFolder(
                        stable_id=stable_id,
                        parent_id=parent_id,
                        path=path,
                        name=path.rsplit("/", 1)[-1] if path else "Core",
                        role=role,
                        metadata=dict(metadata),
                    )
                )
                continue
            if entry.get("kind") != "file":
                continue
            kind = entry.get("objectKind")
            parent_id = entry.get("parentId")
            revision = entry.get("revision")
            content_hash = entry.get("contentHash")
            created_at = entry.get("createdAt")
            updated_at = entry.get("updatedAt")
            metadata = entry.get("metadata")
            if (
                not isinstance(path, str)
                or not isinstance(stable_id, str)
                or not isinstance(parent_id, str)
                or not isinstance(kind, str)
                or isinstance(revision, bool)
                or not isinstance(revision, int)
                or not isinstance(content_hash, str)
                or not isinstance(created_at, str)
                or not isinstance(updated_at, str)
                or not isinstance(metadata, dict)
            ):
                raise CatalogReadError("Invalid prepared CoreFS walk entry.")
            stat = _wire_result(
                logical.stat_v1(
                    corefs_session=session.corefs_session,
                    keys=session.corefs_keys,
                    selected=selected,
                    path=path,
                ),
                selected.generation,
            )
            content_type = stat.get("contentType")
            body_encoding = stat.get("bodyEncoding")
            body_length = stat.get("size")
            if (
                not isinstance(content_type, str)
                or body_encoding not in {"utf-8", "binary"}
                or isinstance(body_length, bool)
                or not isinstance(body_length, int)
                or body_length < 0
            ):
                raise CatalogReadError("Invalid prepared CoreFS envelope identity.")
            values.append(
                PreparedWritingObject(
                    stable_id=stable_id,
                    parent_id=parent_id,
                    path=path,
                    name=path.rsplit("/", 1)[-1],
                    kind=kind,
                    revision=revision,
                    content_hash=content_hash,
                    body_length=body_length,
                    created_at=created_at,
                    updated_at=updated_at,
                    metadata=dict(metadata),
                    content_type=content_type,
                    body_encoding=str(body_encoding),
                )
            )
        next_cursor = result.get("nextCursor")
        if next_cursor is None:
            break
        if not isinstance(next_cursor, dict) or not isinstance(next_cursor.get("after"), str):
            raise CatalogReadError("Invalid prepared CoreFS walk cursor.")
        cursor = str(next_cursor["after"])
    return PreparedWritingSnapshot(folders=tuple(folders), objects=tuple(values))


def read_prepared_writing_body(
    *,
    session: Any,
    item: PreparedWritingObject,
    selected: Any | None = None,
) -> bytes:
    """Read and verify exactly one inactive writing body."""
    from anima_server.services.corefs import logical

    if session.corefs_session is None or session.corefs_keys is None:
        raise CatalogReadError("CoreFS prepared access requires an unlocked session.")
    if selected is None:
        selected = logical.select_validation_snapshot(
            corefs_session=session.corefs_session,
            keys=session.corefs_keys,
        )
    content = _read_prepared_bytes(session=session, selected=selected, path=item.path)
    if len(content) != item.body_length or hashlib.sha256(content).hexdigest() != item.content_hash:
        raise CatalogReadError("Prepared object body did not verify.")
    return content


def _read_prepared_bytes(*, session: Any, selected: Any, path: str) -> bytes:
    from anima_server.services.corefs import logical

    chunks: list[bytes] = []
    offset = 0
    while True:
        raw = logical.read_chunk_v1(
            corefs_session=session.corefs_session,
            keys=session.corefs_keys,
            selected=selected,
            path=path,
            offset=offset,
            max_bytes=64 * 1024,
        )
        if raw is None:
            break
        result = _wire_result(raw, selected.generation)
        encoded = result.get("bytesBase64")
        if not isinstance(encoded, str) or result.get("offset") != offset:
            raise CatalogReadError("Invalid prepared CoreFS read response.")
        chunk = base64.b64decode(encoded, validate=True)
        if not chunk:
            break
        chunks.append(chunk)
        offset += len(chunk)
    return b"".join(chunks)


def _wire_result(raw: bytes, generation: int) -> dict[str, object]:
    try:
        payload = json.loads(raw)
        result = payload["result"]
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise CatalogReadError("Invalid prepared CoreFS response.") from exc
    if payload.get("version") != "corefs-logical-v1" or not isinstance(result, dict):
        raise CatalogReadError("Invalid prepared CoreFS response.")
    if result.get("generation") not in {None, generation}:
        raise CatalogReadError("Prepared CoreFS generation changed during verification.")
    return result
