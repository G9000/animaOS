"""Post-cutover original-byte mutations committed only through CoreFS."""

from __future__ import annotations

import hashlib
from threading import RLock
from typing import Any

from anima_server.services.corefs import logical
from anima_server.services.corefs.asset_authority import (
    CanonicalAssetCatalog,
    CanonicalAssetRecord,
    open_corefs_byte_source,
    read_canonical_asset_catalog,
)
from anima_server.services.corefs.asset_migration import MAX_PORTABLE_ASSET_BYTES
from anima_server.services.corefs.content_authority import (
    invalidate_active_catalog_indexes,
    publish_content_authority_after_mutation,
)
from anima_server.services.corefs.diary_migration import portable_catalog_component

_locks_guard = RLock()
_locks: dict[int, RLock] = {}


class AssetMutationError(RuntimeError):
    pass


def upsert_canonical_binary_asset(
    *,
    session: Any,
    stable_id: str,
    name: str,
    object_kind: str,
    content_type: str,
    data: bytes,
    replace_existing: bool = False,
) -> CanonicalAssetRecord:
    if not data:
        raise AssetMutationError("Canonical asset body is empty.")
    if len(data) > MAX_PORTABLE_ASSET_BYTES:
        raise AssetMutationError("Canonical asset exceeds the portable size limit.")
    if object_kind not in {"attachment", "gallery-asset", "knowledge-source"}:
        raise AssetMutationError("Canonical asset kind is invalid.")
    body_encoding = "utf-8" if object_kind == "knowledge-source" else "binary"
    if body_encoding == "utf-8":
        try:
            data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AssetMutationError("Canonical knowledge source must be UTF-8.") from exc
    with _asset_lock(int(session.user_id)):
        catalog = read_canonical_asset_catalog(session=session)
        existing = _find_asset(catalog, stable_id)
        if existing is not None:
            source = open_corefs_byte_source(
                session=session,
                object_uri=f"corefs://object/{stable_id}",
                expected_kinds=frozenset({object_kind}),
            )
            digest = hashlib.sha256(data).hexdigest()
            if (
                source.content_sha256 == digest
                and source.size == len(data)
                and source.content_type == content_type
            ):
                return existing
            if not replace_existing:
                raise AssetMutationError("Canonical asset identity already exists.")
            mutation: dict[str, object] = {
                "operation": "write_file",
                "target": {"stableId": stable_id},
                "expectedRevision": existing.revision,
                "contentType": content_type,
                "bodyEncoding": body_encoding,
            }
        else:
            component = portable_catalog_component(name, stable_id=stable_id)
            mutation = {
                "operation": "create_file",
                "path": f"{catalog.gallery_path}/{component}",
                "stableId": stable_id,
                "kind": object_kind,
                "contentType": content_type,
                "bodyEncoding": body_encoding,
            }
        _execute(session=session, catalog=catalog, mutation=mutation, body=data)
        refreshed = read_canonical_asset_catalog(session=session)
        record = _find_asset(refreshed, stable_id)
        if record is None:
            raise AssetMutationError("Canonical asset publication did not verify.")
        source = open_corefs_byte_source(
            session=session,
            object_uri=f"corefs://object/{stable_id}",
            expected_kinds=frozenset({object_kind}),
        )
        if (
            source.content_sha256 != hashlib.sha256(data).hexdigest()
            or source.size != len(data)
            or source.content_type != content_type
        ):
            raise AssetMutationError("Canonical asset body did not verify.")
        return record


def trash_canonical_asset(*, session: Any, stable_id: str) -> bool:
    with _asset_lock(int(session.user_id)):
        catalog = read_canonical_asset_catalog(session=session)
        record = _find_asset(catalog, stable_id)
        if record is None:
            return False
        _execute(
            session=session,
            catalog=catalog,
            mutation={
                "operation": "trash",
                "target": {"stableId": stable_id},
                "trashFolder": {"stableId": catalog.trash_stable_id},
                "expectedRevision": record.revision,
            },
            body=None,
        )
        return _find_asset(read_canonical_asset_catalog(session=session), stable_id) is None


def _find_asset(
    catalog: CanonicalAssetCatalog,
    stable_id: str,
) -> CanonicalAssetRecord | None:
    return next((record for record in catalog.assets if record.stable_id == stable_id), None)


def _execute(
    *,
    session: Any,
    catalog: CanonicalAssetCatalog,
    mutation: dict[str, object],
    body: bytes | None,
) -> None:
    try:
        result = logical.execute_mutation_v1(
            corefs_session=session.corefs_session,
            keys=session.corefs_keys,
            selected=catalog.selection.snapshot,
            principal="user",
            mutation=mutation,
            body=body,
            invalidate=lambda _generation, _catalog_hash: invalidate_active_catalog_indexes(
                int(session.user_id)
            ),
        )
        changes = result.get("changes")
        if (
            not isinstance(changes, list)
            or len(changes) != 1
            or not isinstance(changes[0], dict)
        ):
            raise AssetMutationError("Native CoreFS asset mutation result is invalid.")
        publish_content_authority_after_mutation(
            session,
            generation=int(result["generation"]),
            catalog_hash=str(result["catalogHash"]),
        )
    except AssetMutationError:
        raise
    except (RuntimeError, ValueError) as exc:
        raise AssetMutationError("Canonical asset mutation failed.") from exc


def _asset_lock(user_id: int) -> RLock:
    with _locks_guard:
        return _locks.setdefault(user_id, RLock())
