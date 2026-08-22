from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from anima_server.services.corefs.diary_migration import (
    InactiveWritingCatalog,
    migration_opaque_id,
    read_prepared_writing_snapshot,
)


def publish_catalog_native(
    catalog: InactiveWritingCatalog,
    *,
    corefs_session: Any,
    keys: Any,
    expected_head: tuple[int, str] | None = None,
) -> dict[str, object]:
    """Test-only adapter for pure catalog fixtures over the lifecycle API."""
    session = SimpleNamespace(corefs_session=corefs_session, corefs_keys=keys)
    if expected_head is not None:
        current = read_prepared_writing_snapshot(session=session)
        expected = {
            item.stable_id: (item.content_hash, item.parent_id, item.name, item.kind)
            for item in catalog.objects
        }
        actual = {
            item.stable_id: (item.content_hash, item.parent_id, item.name, item.kind)
            for item in current.objects
        }
        expected_folders = {
            item.stable_id: (item.parent_id, item.name, item.role) for item in catalog.folders
        }
        actual_folders = {
            item.stable_id: (item.parent_id, item.name, item.role) for item in current.folders
        }
        if expected == actual and expected_folders == actual_folders:
            return {
                "generation": expected_head[0],
                "catalogHash": expected_head[1],
                "published": False,
            }

    source_generation = (expected_head[0] + 1) if expected_head is not None else 1
    status = dict(
        corefs_session.preparation_begin_or_resume_v1(
            keys,
            _json(
                {
                    "scope": "pcf004-writing-v1",
                    "expectedValidationGeneration": (
                        expected_head[0] if expected_head is not None else None
                    ),
                    "expectedValidationCatalogSha256": (
                        expected_head[1] if expected_head is not None else None
                    ),
                    "sourceOwnerId": migration_opaque_id(
                        "pcf004-test-source-owner", str(catalog.user_id)
                    ),
                    "sourceSchemaVersion": 1,
                    "sourceMutationGeneration": source_generation,
                    "sourceInventorySha256": catalog.catalog_hash,
                }
            ),
        )
    )
    identities: list[dict[str, object]] = []
    for item in catalog.objects:
        outcome = dict(
            corefs_session.preparation_prepare_object_v1(
                keys,
                _json(
                    {
                        "expected": _cas(status),
                        "object": {
                            "objectId": item.stable_id,
                            "revision": (item.expected_revision or 0) + 1,
                            "objectKeyEpoch": 1,
                            "kind": item.kind,
                            "parentId": item.parent_id,
                            "name": item.name,
                            "contentType": item.content_type,
                            "bodyEncoding": item.body_encoding,
                            "bodyLength": len(item.content),
                            "contentSha256": item.content_hash,
                            "createdAt": item.created_at,
                            "updatedAt": item.updated_at,
                            "sourceCharacterCount": item.source_character_count,
                            "references": list(item.references),
                            "policy": item.policy,
                            "stableRole": None,
                            "graphMetadata": item.metadata,
                            "sourceFingerprintSha256": item.source_hash,
                            "converterFormatVersion": 1,
                        },
                    }
                ),
                item.content,
            )
        )
        status = dict(outcome["status"])
        prepared = dict(outcome["prepared"])
        identities.append(
            {
                "objectId": prepared["objectId"],
                "revision": prepared["revision"],
                "contentSha256": prepared["contentSha256"],
                "preparationOrdinal": prepared["preparationOrdinal"],
            }
        )
    status = dict(
        corefs_session.preparation_seal_v1(
            keys,
            _json(
                {
                    "expected": _cas(status),
                    "sourceMutationGeneration": source_generation,
                    "sourceInventorySha256": catalog.catalog_hash,
                    "folders": [
                        {
                            "stableId": item.stable_id,
                            "parentId": item.parent_id,
                            "name": item.name,
                            "role": item.role,
                            "policy": item.policy,
                            "metadata": item.metadata,
                        }
                        for item in catalog.folders
                    ],
                    "objects": identities,
                }
            ),
        )
    )
    receipt = dict(
        corefs_session.preparation_finalize_v1(
            keys,
            _json(
                {
                    "preparationId": status["preparationId"],
                    "expected": _cas(status),
                    "sourceMutationGeneration": source_generation,
                    "sourceInventorySha256": catalog.catalog_hash,
                }
            ),
        )
    )
    return {
        "generation": receipt["validationGeneration"],
        "catalogHash": receipt["validationCatalogSha256"],
        "published": True,
    }


def _cas(status: dict[str, object]) -> dict[str, object]:
    return {
        "pointerSha256": status["pointerSha256"],
        "snapshotSequence": status["snapshotSequence"],
    }


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))
