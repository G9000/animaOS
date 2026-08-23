"""Fail-closed CoreFS authority and bounded byte sources for original content."""

from __future__ import annotations

import base64
import binascii
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from anima_server.services.corefs import logical
from anima_server.services.corefs.content_authority import (
    CoreFsAuthorityUnavailable,
    authenticated_content_authority,
)

_SHA256_HEX = re.compile(r"[0-9a-f]{64}")
_CORE_OBJECT_URI = re.compile(r"corefs://object/([0-7][0-9A-HJKMNP-TV-Z]{25})")
_MAX_READ_CHUNK = 1024 * 1024
_MAX_ORIGINAL_BYTES = 100 * 1024 * 1024
_MAX_INVENTORY_ENTRIES = 50_000
_ORIGINAL_KINDS = frozenset({"attachment", "gallery-asset", "knowledge-source"})
_AUTHORITY_FAMILIES = frozenset({"assets", "documents", "knowledge"})


class CoreFsSourceError(RuntimeError):
    pass


class CoreFsAssetAuthorityLocked(CoreFsSourceError, CoreFsAuthorityUnavailable):
    """Canonical assets cannot be served to this session on an activated Core.

    Subclasses ``CoreFsSourceError`` so existing callers keep their handling,
    and carries the shared marker so anything unhandled becomes one stable 409
    instead of a 500.
    """


@dataclass(frozen=True, slots=True)
class AssetAuthoritySelection:
    generation: int
    catalog_hash: str

    @property
    def snapshot(self) -> logical.CoreFsValidationSnapshot:
        return logical.CoreFsValidationSnapshot(self.generation, self.catalog_hash)


@dataclass(frozen=True, slots=True)
class CoreFsByteSource:
    session: Any
    selection: AssetAuthoritySelection
    stable_id: str
    path: str
    size: int
    content_sha256: str
    content_type: str
    object_kind: str

    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[-1]

    def read_at(self, offset: int, max_bytes: int = _MAX_READ_CHUNK) -> bytes:
        if offset < 0 or max_bytes < 1 or max_bytes > _MAX_READ_CHUNK:
            raise ValueError("CoreFS source read is outside the bounded range.")
        raw = logical.read_chunk_v1(
            corefs_session=self.session.corefs_session,
            keys=self.session.corefs_keys,
            selected=self.selection.snapshot,
            path=self.path,
            offset=offset,
            max_bytes=max_bytes,
        )
        if raw is None:
            return b""
        payload = _wire(raw, expected_generation=self.selection.generation)
        encoded = payload.get("bytesBase64")
        if (
            not isinstance(encoded, str)
            or payload.get("offset") != offset
            or payload.get("path") != self.path
            or payload.get("stableId") != self.stable_id
            or payload.get("contentHash") != self.content_sha256
        ):
            raise CoreFsSourceError("Authenticated CoreFS source read changed identity.")
        try:
            chunk = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise CoreFsSourceError("Authenticated CoreFS source chunk is invalid.") from exc
        if len(chunk) > max_bytes or offset + len(chunk) > self.size:
            raise CoreFsSourceError("Authenticated CoreFS source exceeded its declared bounds.")
        return chunk

    def iter_chunks(self, *, chunk_bytes: int = _MAX_READ_CHUNK) -> Iterator[bytes]:
        offset = 0
        while offset < self.size:
            chunk = self.read_at(offset, min(chunk_bytes, self.size - offset))
            if not chunk:
                raise CoreFsSourceError("Authenticated CoreFS source was truncated.")
            yield chunk
            offset += len(chunk)

    def read_all(self, *, max_bytes: int = _MAX_ORIGINAL_BYTES) -> bytes:
        if self.size > max_bytes:
            raise CoreFsSourceError("Authenticated CoreFS source exceeds the caller limit.")
        body = b"".join(self.iter_chunks())
        if len(body) != self.size:
            raise CoreFsSourceError("Authenticated CoreFS source length is invalid.")
        return body


@dataclass(frozen=True, slots=True)
class CanonicalAssetRecord:
    stable_id: str
    path: str
    revision: int
    object_kind: str


@dataclass(frozen=True, slots=True)
class CanonicalAssetCatalog:
    selection: AssetAuthoritySelection
    gallery_path: str
    gallery_stable_id: str
    trash_stable_id: str
    assets: tuple[CanonicalAssetRecord, ...]


def asset_authority_selection(session: object) -> AssetAuthoritySelection | None:
    marker = getattr(session, "content_authority", None)
    if not isinstance(marker, dict):
        return None
    families = marker.get("families")
    generation = marker.get("generation")
    catalog_hash = marker.get("catalogHash")
    if (
        marker.get("version") != 1
        or marker.get("state") != "authoritative"
        or not isinstance(families, list)
        or not _AUTHORITY_FAMILIES.issubset(families)
        or isinstance(generation, bool)
        or not isinstance(generation, int)
        or generation < 1
        or not isinstance(catalog_hash, str)
        or _SHA256_HEX.fullmatch(catalog_hash) is None
        or getattr(session, "corefs_session", None) is None
        or getattr(session, "corefs_keys", None) is None
    ):
        return None
    return AssetAuthoritySelection(generation, catalog_hash)


def active_asset_authority_session(user_id: int) -> object | None:
    from anima_server.services.sessions import active_unlock_sessions

    return next(
        (
            session
            for session in reversed(active_unlock_sessions(user_id))
            if asset_authority_selection(session) is not None
        ),
        None,
    )


def require_legacy_asset_mutation_allowed(user_id: int) -> None:
    from anima_server.services.corefs.content_authority import core_content_authority_active

    if active_asset_authority_session(user_id) is not None or core_content_authority_active():
        raise CoreFsSourceError("Legacy asset mutation is disabled after CoreFS cutover.")


def canonical_asset_session_or_legacy(user_id: int) -> Any | None:
    """Return the canonical asset session, or None while legacy reads remain.

    Reads must fail closed rather than fall through to Runtime rows and
    plaintext files once the Core is activated (PR #148 review, P1): a session
    without canonical asset capability would otherwise resurface assets,
    documents, or knowledge sources that canonical state has superseded or
    deleted. None is returned only before activation, where legacy storage is
    still the authority.
    """
    from anima_server.services.corefs.content_authority import core_content_authority_active

    session = active_asset_authority_session(user_id)
    if session is not None:
        return session
    if core_content_authority_active():
        raise CoreFsAssetAuthorityLocked(
            "Canonical asset authority is unavailable for this session."
        )
    return None


def read_canonical_asset_catalog(*, session: Any) -> CanonicalAssetCatalog:
    try:
        marker = authenticated_content_authority(session, family="assets")
    except RuntimeError as exc:
        raise CoreFsSourceError("CoreFS asset authority could not be refreshed.") from exc
    selection = asset_authority_selection(session) if marker is not None else None
    if selection is None:
        raise CoreFsSourceError("Canonical asset authority is unavailable.")
    role = session.corefs_session.resolve_validation_role_v1(
        session.corefs_keys,
        "core.gallery",
    )
    if (
        not isinstance(role, dict)
        or role.get("generation") != selection.generation
        or role.get("catalogHash") != selection.catalog_hash
        or not isinstance(role.get("stableId"), str)
    ):
        raise CoreFsSourceError("core.gallery role is unavailable.")
    entries = _walk_all(session=session, selection=selection)
    gallery = next(
        (
            entry
            for entry in entries
            if entry.get("kind") == "directory" and entry.get("stableId") == role["stableId"]
        ),
        None,
    )
    trash_ids = [
        entry.get("stableId")
        for entry in entries
        if entry.get("kind") == "directory" and entry.get("role") == "core.trash"
    ]
    gallery_path = gallery.get("path") if isinstance(gallery, dict) else None
    if (
        not isinstance(gallery_path, str)
        or not gallery_path
        or len(trash_ids) != 1
        or not isinstance(trash_ids[0], str)
    ):
        raise CoreFsSourceError("Canonical asset folder authority is unavailable.")
    records: list[CanonicalAssetRecord] = []
    for entry in entries:
        path = entry.get("path")
        stable_id = entry.get("stableId")
        revision = entry.get("revision")
        object_kind = entry.get("objectKind")
        if (
            entry.get("kind") != "file"
            or not isinstance(path, str)
            or not path.startswith(f"{gallery_path}/")
        ):
            continue
        if (
            not isinstance(stable_id, str)
            or isinstance(revision, bool)
            or not isinstance(revision, int)
            or revision < 1
            or object_kind not in _ORIGINAL_KINDS
        ):
            raise CoreFsSourceError("Canonical asset identity is invalid.")
        records.append(
            CanonicalAssetRecord(
                stable_id=stable_id,
                path=path,
                revision=revision,
                object_kind=str(object_kind),
            )
        )
    return CanonicalAssetCatalog(
        selection=selection,
        gallery_path=gallery_path,
        gallery_stable_id=str(role["stableId"]),
        trash_stable_id=trash_ids[0],
        assets=tuple(records),
    )


def open_corefs_byte_source(
    *,
    session: Any,
    object_uri: str,
    expected_kinds: frozenset[str] = _ORIGINAL_KINDS,
) -> CoreFsByteSource:
    try:
        marker = authenticated_content_authority(session, family="assets")
    except RuntimeError as exc:
        raise CoreFsSourceError("Canonical source authority could not be refreshed.") from exc
    selection = asset_authority_selection(session) if marker is not None else None
    match = _CORE_OBJECT_URI.fullmatch(object_uri)
    if selection is None or match is None:
        raise CoreFsSourceError("Canonical source authority is unavailable.")
    stable_id = match.group(1)
    role = session.corefs_session.resolve_validation_role_v1(
        session.corefs_keys,
        "core.gallery",
    )
    if (
        not isinstance(role, dict)
        or role.get("generation") != selection.generation
        or role.get("catalogHash") != selection.catalog_hash
        or not isinstance(role.get("stableId"), str)
    ):
        raise CoreFsSourceError("core.gallery role is unavailable.")
    entries = _walk_all(session=session, selection=selection)
    gallery_path = next(
        (
            entry.get("path")
            for entry in entries
            if entry.get("kind") == "directory" and entry.get("stableId") == role["stableId"]
        ),
        None,
    )
    if not isinstance(gallery_path, str) or not gallery_path:
        raise CoreFsSourceError("core.gallery role path is unavailable.")
    entry = next(
        (
            item
            for item in entries
            if item.get("kind") == "file" and item.get("stableId") == stable_id
        ),
        None,
    )
    if not isinstance(entry, dict):
        raise CoreFsSourceError("Canonical source object is unavailable.")
    path = entry.get("path")
    if not isinstance(path, str) or not path.startswith(f"{gallery_path}/"):
        raise CoreFsSourceError("Canonical source object escaped core.gallery.")
    stat = _wire(
        logical.stat_v1(
            corefs_session=session.corefs_session,
            keys=session.corefs_keys,
            selected=selection.snapshot,
            path=path,
        ),
        expected_generation=selection.generation,
    )
    kind = stat.get("objectKind")
    size = stat.get("size")
    content_hash = stat.get("contentHash")
    content_type = stat.get("contentType")
    if (
        stat.get("stableId") != stable_id
        or stat.get("path") != path
        or kind not in expected_kinds
        or isinstance(size, bool)
        or not isinstance(size, int)
        or size < 0
        or size > _MAX_ORIGINAL_BYTES
        or not isinstance(content_hash, str)
        or _SHA256_HEX.fullmatch(content_hash) is None
        or not isinstance(content_type, str)
        or not content_type
    ):
        raise CoreFsSourceError("Canonical source metadata is invalid.")
    return CoreFsByteSource(
        session=session,
        selection=selection,
        stable_id=stable_id,
        path=path,
        size=size,
        content_sha256=content_hash,
        content_type=content_type,
        object_kind=kind,
    )


def _walk_all(*, session: Any, selection: AssetAuthoritySelection) -> list[dict[str, object]]:
    cursor: str | None = None
    seen_cursors: set[str] = set()
    entries: list[dict[str, object]] = []
    while True:
        payload = _wire(
            logical.walk_v1(
                corefs_session=session.corefs_session,
                keys=session.corefs_keys,
                selected=selection.snapshot,
                root="",
                cursor_after=cursor,
                page_size=100,
                include_directories=True,
            ),
            expected_generation=selection.generation,
        )
        page = payload.get("entries")
        if not isinstance(page, list) or not all(isinstance(item, dict) for item in page):
            raise CoreFsSourceError("Canonical source inventory is invalid.")
        entries.extend(page)
        if len(entries) > _MAX_INVENTORY_ENTRIES:
            raise CoreFsSourceError("Canonical source inventory exceeds its bound.")
        next_cursor = payload.get("nextCursor")
        if next_cursor is None:
            return entries
        if not isinstance(next_cursor, dict) or not isinstance(next_cursor.get("after"), str):
            raise CoreFsSourceError("Canonical source cursor is invalid.")
        next_after = next_cursor["after"]
        if not next_after or next_after in seen_cursors:
            raise CoreFsSourceError("Canonical source cursor did not advance.")
        seen_cursors.add(next_after)
        cursor = next_after


def _wire(raw: bytes, *, expected_generation: int) -> dict[str, object]:
    try:
        envelope = json.loads(raw)
        value = envelope["result"]
    except (KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CoreFsSourceError("Canonical source response is invalid.") from exc
    if (
        not isinstance(envelope, dict)
        or envelope.get("version") != "corefs-logical-v1"
        or not isinstance(value, dict)
    ):
        raise CoreFsSourceError("Canonical source response is invalid.")
    generation = value.get("generation")
    if generation is not None and generation != expected_generation:
        raise CoreFsSourceError("Canonical source response changed generation.")
    return value
