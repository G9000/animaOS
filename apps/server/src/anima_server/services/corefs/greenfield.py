"""Native-only bootstrap for the first supported portable Core release."""

from __future__ import annotations

import hashlib
import json
from contextlib import suppress
from types import SimpleNamespace
from typing import Any

import anima_core

from anima_server.config import settings
from anima_server.services.core import get_core_id, get_owner_id
from anima_server.services.corefs.authority import (
    activate_content_authority,
    reconcile_content_authority,
)
from anima_server.services.corefs.catalog_reader import stable_opaque_id
from anima_server.services.corefs.formats import (
    ACCOUNT_PROFILE_CONTENT_TYPE,
    PREFERENCES_CONTENT_TYPE,
    encode_account_profile_document,
    encode_preferences_document,
)


class GreenfieldBootstrapError(RuntimeError):
    pass


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _cas(status: dict[str, Any]) -> dict[str, Any]:
    return {
        "pointerSha256": status["pointerSha256"],
        "snapshotSequence": status["snapshotSequence"],
    }


def bootstrap_account_content(
    *, user_id: int, corefs_keys: object, profile: Any, activate: bool = True
) -> None:
    """Commit the initial encrypted account objects before publishing registration."""
    native = anima_core.CorefsSession(str(settings.data_dir), get_core_id())
    try:
        session = SimpleNamespace(user_id=user_id, corefs_session=native, corefs_keys=corefs_keys)
        _publish_initial_content(session, profile, activate=activate)
    finally:
        with suppress(Exception):
            native.close()


def _publish_initial_content(session: Any, profile: Any, *, activate: bool) -> dict[str, object]:
    native, keys = session.corefs_session, session.corefs_keys
    if activate:
        existing = reconcile_content_authority(corefs_session=native, keys=keys)
        if existing is not None:
            return existing
    owner_id = get_owner_id()
    if not owner_id:
        raise GreenfieldBootstrapError("Initial Core owner binding is unavailable")
    root_id = stable_opaque_id("core-folder", "root")
    timestamp = profile.created_at.isoformat().replace("+00:00", "Z")
    folders = [
        {
            "stableId": root_id,
            "parentId": None,
            "name": "Core",
            "role": None,
            "policy": "user-write",
            "metadata": {},
        }
    ]
    for role, name in (
        ("core.journal", "Journal"),
        ("core.notes", "Notes"),
        ("core.conversations", "Conversations"),
        ("core.gallery", "Gallery"),
        ("core.trash", "Trash"),
    ):
        folders.append(
            {
                "stableId": stable_opaque_id("core-folder-role", role),
                "parentId": root_id,
                "name": name,
                "role": role,
                "policy": "shared-manage" if role == "core.conversations" else "user-write",
                "metadata": {},
            }
        )
    objects = [
        (
            "account-profile",
            "account-profile.json",
            ACCOUNT_PROFILE_CONTENT_TYPE,
            encode_account_profile_document(
                stable_id=stable_opaque_id("account-profile", owner_id),
                owner_id=owner_id,
                legacy_user_id=session.user_id,
                username=profile.username,
                display_name=profile.display_name,
                gender=profile.gender,
                age=profile.age,
                birthday=profile.birthday,
                setup_complete=False,
                created_at=timestamp,
                updated_at=timestamp,
            ),
        ),
        (
            "preferences",
            "preferences.json",
            PREFERENCES_CONTENT_TYPE,
            encode_preferences_document(
                stable_id=stable_opaque_id("preferences", owner_id),
                owner_id=owner_id,
                values={"presence": _initial_presence(session.user_id)},
                updated_at=timestamp,
            ),
        ),
    ]
    digest = hashlib.sha256(
        _json(
            {
                "folders": folders,
                "objects": [
                    (kind, hashlib.sha256(body).hexdigest()) for kind, _, _, body in objects
                ],
            }
        ).encode()
    ).hexdigest()
    status = dict(
        native.preparation_begin_or_resume_v1(
            keys,
            _json(
                {
                    "scope": "pcf004-writing-v1",
                    "expectedValidationGeneration": None,
                    "expectedValidationCatalogSha256": None,
                    "sourceOwnerId": stable_opaque_id("core-bootstrap-owner", owner_id),
                    "sourceSchemaVersion": 1,
                    "sourceMutationGeneration": 1,
                    "sourceInventorySha256": digest,
                }
            ),
        )
    )
    if status.get("state") != "ready":
        identities = []
        for kind, name, content_type, body in objects:
            object_id = stable_opaque_id(kind, owner_id)
            content_hash = hashlib.sha256(body).hexdigest()
            outcome = dict(
                native.preparation_prepare_object_v1(
                    keys,
                    _json(
                        {
                            "expected": _cas(status),
                            "object": {
                                "objectId": object_id,
                                "revision": 1,
                                "objectKeyEpoch": 1,
                                "kind": kind,
                                "parentId": root_id,
                                "name": name,
                                "contentType": content_type,
                                "bodyEncoding": "utf-8",
                                "bodyLength": len(body),
                                "contentSha256": content_hash,
                                "createdAt": timestamp,
                                "updatedAt": timestamp,
                                "sourceCharacterCount": None,
                                "references": [],
                                "policy": "inherit",
                                "stableRole": None,
                                "graphMetadata": {"ownerId": owner_id},
                                "sourceFingerprintSha256": content_hash,
                                "converterFormatVersion": 1,
                            },
                        }
                    ),
                    body,
                )
            )
            status = dict(outcome["status"])
            prepared = dict(outcome["prepared"])
            identities.append(
                {
                    key: prepared[key]
                    for key in ("objectId", "revision", "contentSha256", "preparationOrdinal")
                }
            )
        status = dict(
            native.preparation_seal_v1(
                keys,
                _json(
                    {
                        "expected": _cas(status),
                        "sourceMutationGeneration": 1,
                        "sourceInventorySha256": digest,
                        "folders": folders,
                        "objects": identities,
                    }
                ),
            )
        )
    receipt = dict(
        native.preparation_finalize_v1(
            keys,
            _json(
                {
                    "preparationId": status["preparationId"],
                    "expected": _cas(status),
                    "sourceMutationGeneration": 1,
                    "sourceInventorySha256": digest,
                }
            ),
        )
    )
    if not activate:
        return receipt
    return activate_content_authority(
        corefs_session=native,
        keys=keys,
        generation=int(receipt["validationGeneration"]),
        catalog_hash=str(receipt["validationCatalogSha256"]),
    )


def activate_initial_account_content(*, user_id: int, corefs_keys: object) -> None:
    """Authenticate prepared initial account content before committing authority."""
    from anima_server.services.corefs.account_profile import _read_account_profile_record
    from anima_server.services.corefs.catalog_reader import (
        read_prepared_writing_body,
        read_prepared_writing_snapshot,
    )
    from anima_server.services.corefs.formats import decode_preferences_document

    native = anima_core.CorefsSession(str(settings.data_dir), get_core_id())
    try:
        session = SimpleNamespace(user_id=user_id, corefs_session=native, corefs_keys=corefs_keys)
        existing = reconcile_content_authority(corefs_session=native, keys=corefs_keys)
        if existing is not None:
            return
        snapshot = read_prepared_writing_snapshot(session=session)
        profile = _read_account_profile_record(session=session)
        if profile is None or sorted(item.kind for item in snapshot.objects) != [
            "account-profile",
            "preferences",
        ]:
            raise GreenfieldBootstrapError("Initial account catalog is incomplete or ambiguous")
        owner_id = profile.document.owner_id
        root_id = stable_opaque_id("core-folder", "root")
        expected_folders = {(root_id, None, "Core", None)} | {
            (stable_opaque_id("core-folder-role", role), root_id, name, role)
            for role, name in (
                ("core.journal", "Journal"),
                ("core.notes", "Notes"),
                ("core.conversations", "Conversations"),
                ("core.gallery", "Gallery"),
                ("core.trash", "Trash"),
            )
        }
        actual_folders = {
            (folder.stable_id, folder.parent_id, folder.name, folder.role)
            for folder in snapshot.folders
        }
        if (
            len(snapshot.folders) != 6
            or actual_folders != expected_folders
            or any(folder.metadata for folder in snapshot.folders)
        ):
            raise GreenfieldBootstrapError("Initial account folder topology is invalid")
        for item in snapshot.objects:
            expected_name = f"{item.kind}.json"
            expected_type = (
                ACCOUNT_PROFILE_CONTENT_TYPE
                if item.kind == "account-profile"
                else PREFERENCES_CONTENT_TYPE
            )
            if (
                item.stable_id != stable_opaque_id(item.kind, owner_id)
                or item.parent_id != root_id
                or item.name != expected_name
                or item.content_type != expected_type
                or item.revision != 1
                or item.metadata != {"ownerId": owner_id}
            ):
                raise GreenfieldBootstrapError("Initial account object binding is invalid")
        preferences = next(item for item in snapshot.objects if item.kind == "preferences")
        document = decode_preferences_document(
            read_prepared_writing_body(session=session, item=preferences)
        )
        if document.owner_id != profile.document.owner_id:
            raise GreenfieldBootstrapError("Initial preferences owner binding is invalid")
        selected = native.validation_snapshot(corefs_keys)
        activate_content_authority(
            corefs_session=native,
            keys=corefs_keys,
            generation=int(selected["generation"]),
            catalog_hash=str(selected["catalogHash"]),
        )
    finally:
        with suppress(Exception):
            native.close()


def bootstrap_greenfield_content(session: Any) -> dict[str, object] | None:
    """Authenticate already committed content before an unlock is published."""
    if session.corefs_session is None or session.corefs_keys is None:
        return None
    authority = reconcile_content_authority(
        corefs_session=session.corefs_session,
        keys=session.corefs_keys,
    )
    if authority is None:
        raise GreenfieldBootstrapError("Supported Core account catalog is unavailable")
    object.__setattr__(session, "content_authority", authority)
    return authority


def _initial_presence(user_id: int) -> dict[str, object]:
    from anima_server.services.presence_config import (
        PresenceConfigValues,
        presence_config_values_to_mapping,
    )

    return presence_config_values_to_mapping(PresenceConfigValues(user_id=user_id))
