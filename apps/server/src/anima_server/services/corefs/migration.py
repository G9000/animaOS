from __future__ import annotations

import base64
import json
from collections import Counter
from collections.abc import Callable
from typing import Any

from anima_server.services.corefs import logical
from anima_server.services.corefs.indexer import ReadinessState
from anima_server.services.sessions import UnlockSession

_INDEX_READ_CHUNK_BYTES = 64 * 1024
_MAX_INDEXABLE_OBJECT_BYTES = 16 * 1024 * 1024


def reconcile_authenticated_catalog(
    session: UnlockSession,
) -> logical.CoreFsValidationSnapshot:
    """Publish navigation readiness from an authenticated native catalog."""
    if (
        session.runtime_index is None
        or session.corefs_session is None
        or session.corefs_keys is None
    ):
        raise ValueError("CoreFS reconciliation requires an unlocked session")
    selected = logical.select_validation_snapshot(
        corefs_session=session.corefs_session,
        keys=session.corefs_keys,
    )
    snapshot = session.runtime_index.snapshot()
    if snapshot.catalog_generation != selected.generation or snapshot.state in {
        ReadinessState.OPENING_CORE,
        ReadinessState.VALIDATING_CORE,
    }:
        session.runtime_index.begin_catalog()
        session.runtime_index.publish_catalog(
            catalog_generation=selected.generation,
            families={},
        )
    return selected


def rebuild_unlocked_search(
    session: UnlockSession,
    *,
    embedder: Callable[[str], tuple[float, ...]] | None = None,
) -> logical.CoreFsValidationSnapshot:
    """Rebuild unlock-scoped search state from one authenticated catalog snapshot."""
    if (
        session.runtime_index is None
        or session.corefs_session is None
        or session.corefs_keys is None
    ):
        raise ValueError("CoreFS reconciliation requires an unlocked session")

    selected = logical.select_validation_snapshot(
        corefs_session=session.corefs_session,
        keys=session.corefs_keys,
    )
    entries, walk_failures = _walk_authenticated_files(
        corefs_session=session.corefs_session,
        keys=session.corefs_keys,
        selected=selected,
    )
    family_counts = Counter(entry["family"] for entry in entries)

    index = session.runtime_index
    index.begin_catalog()
    index.publish_catalog(
        catalog_generation=selected.generation,
        families=dict(family_counts),
    )
    for failure in walk_failures:
        family = failure.get("family")
        object_id = failure.get("object_id")
        if isinstance(family, str) and family in family_counts and isinstance(object_id, str):
            index.mark_family_failure(family=family, object_id=object_id)

    index.begin_blind_generation(
        generation=selected.generation,
        expected_count=len(entries),
    )
    for entry in entries:
        index.add_blind_token(
            generation=selected.generation,
            value=entry["path"],
            object_id=entry["stable_id"],
        )
    index.commit_blind_generation(selected.generation)

    index.begin_text_indexing()
    indexed: list[tuple[str, str]] = []
    for entry in entries:
        try:
            text = _read_authenticated_text(
                corefs_session=session.corefs_session,
                keys=session.corefs_keys,
                selected=selected,
                path=entry["path"],
            )
        except (UnicodeDecodeError, ValueError):
            index.mark_family_failure(
                family=entry["family"],
                object_id=entry["stable_id"],
            )
            continue
        index.index_text(
            family=entry["family"],
            object_id=entry["stable_id"],
            revision=entry["revision"],
            text=text,
        )
        indexed.append((entry["stable_id"], text))

    if embedder is not None:
        index.begin_semantic_indexing()
        for object_id, text in indexed:
            try:
                vector = embedder(text)
                index.index_vector(object_id=object_id, vector=vector)
            except (TypeError, ValueError):
                family = next(
                    entry["family"] for entry in entries if entry["stable_id"] == object_id
                )
                index.mark_family_failure(family=family, object_id=object_id)
    index.finish()
    return selected


def _walk_authenticated_files(
    *,
    corefs_session: Any,
    keys: object,
    selected: logical.CoreFsValidationSnapshot,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    entries: list[dict[str, str]] = []
    failures: list[dict[str, str]] = []
    cursor: str | None = None
    while True:
        raw = logical.walk_v1(
            corefs_session=corefs_session,
            keys=keys,
            selected=selected,
            root="",
            cursor_after=cursor,
            page_size=100,
            include_directories=False,
        )
        result = _wire_result(raw, selected.generation)
        page_entries = result.get("entries")
        page_errors = result.get("errors")
        if not isinstance(page_entries, list) or not isinstance(page_errors, list):
            raise ValueError("invalid CoreFS walk response")
        for value in page_entries:
            if not isinstance(value, dict) or value.get("kind") != "file":
                continue
            path = value.get("path")
            stable_id = value.get("stableId")
            revision = value.get("revision")
            family = value.get("objectKind")
            if (
                not isinstance(path, str)
                or not path
                or not isinstance(stable_id, str)
                or not stable_id
                or isinstance(revision, bool)
                or not isinstance(revision, int)
                or revision <= 0
                or not isinstance(family, str)
                or not family
            ):
                raise ValueError("invalid CoreFS walk entry")
            entries.append(
                {
                    "path": path,
                    "stable_id": stable_id,
                    "revision": str(revision),
                    "family": family,
                }
            )
        for value in page_errors:
            if not isinstance(value, dict):
                raise ValueError("invalid CoreFS walk error")
            path = value.get("path")
            if isinstance(path, str) and path:
                failures.append(
                    {
                        "family": "unknown",
                        "object_id": path,
                    }
                )
        next_cursor = result.get("nextCursor")
        if next_cursor is None:
            break
        if not isinstance(next_cursor, dict):
            raise ValueError("invalid CoreFS walk cursor")
        after = next_cursor.get("after")
        if not isinstance(after, str) or not after or after == cursor:
            raise ValueError("invalid CoreFS walk cursor")
        cursor = after
    return entries, failures


def _read_authenticated_text(
    *,
    corefs_session: Any,
    keys: object,
    selected: logical.CoreFsValidationSnapshot,
    path: str,
) -> str:
    chunks: list[bytes] = []
    offset = 0
    while offset <= _MAX_INDEXABLE_OBJECT_BYTES:
        raw = logical.read_chunk_v1(
            corefs_session=corefs_session,
            keys=keys,
            selected=selected,
            path=path,
            offset=offset,
            max_bytes=_INDEX_READ_CHUNK_BYTES,
        )
        if raw is None:
            break
        result = _wire_result(raw, selected.generation)
        encoded = result.get("bytesBase64")
        response_offset = result.get("offset")
        if (
            not isinstance(encoded, str)
            or isinstance(response_offset, bool)
            or response_offset != offset
        ):
            raise ValueError("invalid CoreFS read response")
        try:
            chunk = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError) as exc:
            raise ValueError("invalid CoreFS read response") from exc
        if not chunk:
            break
        chunks.append(chunk)
        offset += len(chunk)
    if offset > _MAX_INDEXABLE_OBJECT_BYTES:
        raise ValueError("CoreFS object exceeds the in-memory indexing limit")
    return b"".join(chunks).decode("utf-8")


def _wire_result(raw: bytes, generation: int) -> dict[str, object]:
    try:
        payload = json.loads(raw)
        result = payload["result"]
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid CoreFS logical response") from exc
    if payload.get("version") != "corefs-logical-v1" or not isinstance(result, dict):
        raise ValueError("invalid CoreFS logical response")
    result_generation = result.get("generation")
    if result_generation is not None and result_generation != generation:
        raise ValueError("CoreFS logical response generation changed")
    return result
