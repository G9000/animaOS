from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from threading import Lock
from typing import Any

import anima_core

from anima_server.services.core import get_manifest_path, update_core_manifest
from anima_server.services.corefs import keyslots, logical
from anima_server.services.corefs.types import (
    KeyPurpose,
    KeyslotStatus,
    ManifestKeyslot,
    WrappingPath,
)
from anima_server.services.sessions import UnlockSession

_rotation_operation_lock = Lock()


@dataclass(frozen=True, slots=True)
class CoreFSRotationResult:
    active_subkeys: object
    active_version: int
    committed_catalog_generation: int
    resumed: bool


def _manifest() -> dict[str, object]:
    value = json.loads(get_manifest_path().read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("invalid Core manifest")
    return value


def _rotation_state(manifest: dict[str, object]) -> dict[str, object]:
    state = manifest.get("frk_rotation")
    if not isinstance(state, dict):
        raise ValueError("invalid FRK rotation state")
    return state


def _slot_aad(
    manifest: dict[str, object],
    slot: ManifestKeyslot,
) -> bytes:
    return keyslots.manifest_keyslot_aad(
        core_id=str(manifest["core_id"]),
        owner_id=str(manifest["owner_id"]),
        purpose=slot.purpose,
        key_version=slot.key_version,
        credential_generation=slot.credential_generation,
        scope=slot.scope,
        frk_version=slot.frk_version,
        object_key_epoch=slot.object_key_epoch,
        wrapping_path=slot.wrapping_path,
    )


def _open_roots(
    manifest: dict[str, object],
    *,
    credential: str,
    wrapping_path: WrappingPath,
    statuses: set[KeyslotStatus],
    versions: set[int],
) -> dict[int, object]:
    generation_field = (
        "active_password_credential_generation"
        if wrapping_path is WrappingPath.PASSWORD
        else "active_recovery_credential_generation"
    )
    generation = int(manifest[generation_field])
    candidates = [
        slot
        for slot in keyslots._manifest_slots(manifest)
        if slot.purpose is KeyPurpose.FILESYSTEM_ROOT
        and slot.wrapping_path is wrapping_path
        and slot.credential_generation == generation
        and slot.status in statuses
        and slot.frk_version in versions
    ]
    if {slot.frk_version for slot in candidates} != versions:
        raise ValueError("filesystem credential generation is incomplete")
    roots: dict[int, object] = {}
    for slot in candidates:
        assert slot.frk_version is not None
        roots[slot.frk_version] = keyslots._unwrap_manifest_slot(
            credential,
            slot,
            _slot_aad(manifest, slot),
        )
    return roots


def _roots_match(first: dict[int, object], second: dict[int, object]) -> bool:
    return set(first) == set(second) and all(
        bool(first[version].matches(second[version])) for version in first
    )


def _prepare_rotation(
    *,
    manifest: dict[str, object],
    current_password: str,
    recovery_phrase: str,
    source_generation: int,
    source_catalog_hash: str,
) -> tuple[int, object]:
    rotation = _rotation_state(manifest)
    active_version = int(rotation["active_version"])
    retained_versions = {
        active_version,
        *(int(value) for value in rotation.get("decrypt_only_versions", [])),
    }
    password_roots = _open_roots(
        manifest,
        credential=current_password,
        wrapping_path=WrappingPath.PASSWORD,
        statuses={KeyslotStatus.ACTIVE, KeyslotStatus.DECRYPT_ONLY},
        versions=retained_versions,
    )
    recovery_roots = _open_roots(
        manifest,
        credential=recovery_phrase,
        wrapping_path=WrappingPath.RECOVERY,
        statuses={KeyslotStatus.ACTIVE, KeyslotStatus.DECRYPT_ONLY},
        versions=retained_versions,
    )
    if not _roots_match(password_roots, recovery_roots):
        raise ValueError("password and recovery filesystem roots do not match")
    if rotation.get("pending_version") is not None:
        raise ValueError("FRK rotation is already prepared")

    pending_version = active_version + 1
    pending_root = anima_core.corefs_generate_root_key()
    object_key_epoch = int(rotation.get("object_key_epoch", 1))
    active_slots = [
        slot
        for slot in keyslots._manifest_slots(manifest)
        if slot.purpose is KeyPurpose.FILESYSTEM_ROOT
        and slot.frk_version == active_version
        and slot.status is KeyslotStatus.ACTIVE
    ]
    by_path = {slot.wrapping_path: slot for slot in active_slots}
    if set(by_path) != {WrappingPath.PASSWORD, WrappingPath.RECOVERY}:
        raise ValueError("active filesystem keyslots are incomplete")
    credentials = {
        WrappingPath.PASSWORD: current_password,
        WrappingPath.RECOVERY: recovery_phrase,
    }
    pending_slots = [
        keyslots._manifest_slot(
            credentials[path],
            pending_root,
            core_id=str(manifest["core_id"]),
            owner_id=str(manifest["owner_id"]),
            purpose=KeyPurpose.FILESYSTEM_ROOT,
            wrapping_path=path,
            status=KeyslotStatus.PENDING,
            scope=by_path[path].scope,
            key_version=pending_version,
            credential_generation=by_path[path].credential_generation,
            frk_version=pending_version,
            object_key_epoch=object_key_epoch,
        )
        for path in (WrappingPath.PASSWORD, WrappingPath.RECOVERY)
    ]

    def write_pending(value: dict[str, object]) -> None:
        current = _rotation_state(value)
        if (
            int(current["active_version"]) != active_version
            or current.get("pending_version") is not None
        ):
            raise ValueError("FRK rotation state changed during preparation")
        value["keyslots"] = [
            *(slot.to_dict() for slot in keyslots._manifest_slots(value)),
            *(slot.to_dict() for slot in pending_slots),
        ]
        current.update(
            {
                "pending_version": pending_version,
                "phase": "prepared",
                "source_catalog_generation": source_generation,
                "source_catalog_hash": source_catalog_hash,
                "password_reopen_verified": False,
                "recovery_reopen_verified": False,
            }
        )

    update_core_manifest(write_pending)
    return pending_version, pending_root


def _resume_material(
    *,
    manifest: dict[str, object],
    current_password: str,
    recovery_phrase: str,
) -> tuple[int, object]:
    rotation = _rotation_state(manifest)
    pending_version = int(rotation["pending_version"])
    password = _open_roots(
        manifest,
        credential=current_password,
        wrapping_path=WrappingPath.PASSWORD,
        statuses={KeyslotStatus.PENDING},
        versions={pending_version},
    )
    recovery = _open_roots(
        manifest,
        credential=recovery_phrase,
        wrapping_path=WrappingPath.RECOVERY,
        statuses={KeyslotStatus.PENDING},
        versions={pending_version},
    )
    if not _roots_match(password, recovery):
        raise ValueError("pending password and recovery filesystem roots do not match")
    return pending_version, password[pending_version]


def _activate_rotation(
    *,
    pending_version: int,
    committed_generation: int,
) -> None:
    def activate(value: dict[str, object]) -> None:
        rotation = _rotation_state(value)
        if int(rotation["pending_version"]) != pending_version:
            raise ValueError("pending FRK version changed before activation")
        previous_active = int(rotation["active_version"])
        rewritten: list[ManifestKeyslot] = []
        for slot in keyslots._manifest_slots(value):
            if slot.purpose is not KeyPurpose.FILESYSTEM_ROOT:
                rewritten.append(slot)
            elif slot.frk_version == previous_active and slot.status is KeyslotStatus.ACTIVE:
                rewritten.append(replace(slot, status=KeyslotStatus.DECRYPT_ONLY))
            elif slot.frk_version == pending_version and slot.status is KeyslotStatus.PENDING:
                rewritten.append(replace(slot, status=KeyslotStatus.ACTIVE))
            else:
                rewritten.append(slot)
        value["keyslots"] = [slot.to_dict() for slot in rewritten]
        rotation.update(
            {
                "active_version": pending_version,
                "pending_version": None,
                "decrypt_only_versions": sorted(
                    {
                        previous_active,
                        *(
                            int(item)
                            for item in rotation.get(
                                "decrypt_only_versions",
                                [],
                            )
                        ),
                    }
                ),
                "phase": "idle",
                "committed_catalog_generation": committed_generation,
                "password_reopen_verified": True,
                "recovery_reopen_verified": True,
            }
        )
        rotation.pop("source_catalog_generation", None)
        rotation.pop("source_catalog_hash", None)

    update_core_manifest(activate)


def _mark_verifying(*, pending_version: int) -> None:
    def mark(value: dict[str, object]) -> None:
        rotation = _rotation_state(value)
        if int(rotation["pending_version"]) != pending_version:
            raise ValueError("pending FRK version changed before verification")
        rotation.update(
            {
                "phase": "verifying",
                "password_reopen_verified": True,
                "recovery_reopen_verified": True,
            }
        )

    update_core_manifest(mark)


def rotate_or_resume_frk(
    session: UnlockSession,
    *,
    current_password: str,
    recovery_phrase: str,
    before_activate: Callable[[CoreFSRotationResult], None] | None = None,
    require_pending: bool = False,
) -> CoreFSRotationResult:
    with _rotation_operation_lock:
        return _rotate_or_resume_frk_locked(
            session,
            current_password=current_password,
            recovery_phrase=recovery_phrase,
            before_activate=before_activate,
            require_pending=require_pending,
        )


def _rotate_or_resume_frk_locked(
    session: UnlockSession,
    *,
    current_password: str,
    recovery_phrase: str,
    before_activate: Callable[[CoreFSRotationResult], None] | None = None,
    require_pending: bool = False,
) -> CoreFSRotationResult:
    if (
        session.corefs_session is None
        or session.corefs_keys is None
        or not current_password
        or not recovery_phrase
    ):
        raise ValueError("active CoreFS unlock authority and both credentials are required")
    manifest = _manifest()
    rotation = _rotation_state(manifest)
    resumed = rotation.get("pending_version") is not None
    if require_pending and not resumed:
        raise ValueError("no FRK rotation is pending")

    if resumed:
        pending_version, pending_root = _resume_material(
            manifest=manifest,
            current_password=current_password,
            recovery_phrase=recovery_phrase,
        )
        source_generation = int(rotation["source_catalog_generation"])
        _mark_verifying(pending_version=pending_version)
        manifest = _manifest()
    else:
        selected = logical.select_validation_snapshot(
            corefs_session=session.corefs_session,
            keys=session.corefs_keys,
        )
        pending_version, pending_root = _prepare_rotation(
            manifest=manifest,
            current_password=current_password,
            recovery_phrase=recovery_phrase,
            source_generation=selected.generation,
            source_catalog_hash=selected.catalog_hash,
        )
        source_generation = selected.generation
        manifest = _manifest()
        reopened_version, reopened_root = _resume_material(
            manifest=manifest,
            current_password=current_password,
            recovery_phrase=recovery_phrase,
        )
        if reopened_version != pending_version or not bool(pending_root.matches(reopened_root)):
            raise ValueError("pending filesystem root failed independent reopen")
        pending_root = reopened_root
        _mark_verifying(pending_version=pending_version)
        manifest = _manifest()

    pending_subkeys = anima_core.corefs_derive_subkeys(
        pending_root,
        pending_version,
    )
    try:
        selected_pending = logical.select_validation_snapshot(
            corefs_session=session.corefs_session,
            keys=pending_subkeys,
        )
    except ValueError:
        selected_pending = None

    if selected_pending is not None and selected_pending.generation == source_generation + 1:
        committed_generation = selected_pending.generation
    else:
        state = _rotation_state(manifest)
        active_version = int(state["active_version"])
        retained_versions = {
            active_version,
            *(int(value) for value in state.get("decrypt_only_versions", [])),
        }
        retained_roots = _open_roots(
            manifest,
            credential=current_password,
            wrapping_path=WrappingPath.PASSWORD,
            statuses={KeyslotStatus.ACTIVE, KeyslotStatus.DECRYPT_ONLY},
            versions=retained_versions,
        )
        retained_subkeys = [
            anima_core.corefs_derive_subkeys(root, version)
            for version, root in sorted(retained_roots.items())
        ]
        outcome: Any = session.corefs_session.rotate_frk_v1(
            retained_subkeys,
            pending_subkeys,
            source_generation,
        )
        committed_generation = int(outcome["generation"])
        if bool(outcome.get("recoveryPending", False)):
            raise ValueError("CoreFS FRK rotation recovery is still pending")

    result = CoreFSRotationResult(
        active_subkeys=pending_subkeys,
        active_version=pending_version,
        committed_catalog_generation=committed_generation,
        resumed=resumed,
    )
    if before_activate is not None:
        before_activate(result)
    _activate_rotation(
        pending_version=pending_version,
        committed_generation=committed_generation,
    )
    return result
