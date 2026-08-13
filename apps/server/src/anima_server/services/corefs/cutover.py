"""Authenticated CoreFS cutover state and crash reconciliation.

The portable manifest records operator progress, but only the encrypted
catalog selected by authoritative ``fs/HEAD`` can disable legacy rollback.
This module keeps that distinction explicit: manifest state may authorize a
future first mutation, while forward-only authority is always reconstructed
from the native authenticated cutover marker.
"""

from __future__ import annotations

import json
import re
import secrets
from dataclasses import dataclass
from enum import StrEnum
from threading import RLock
from typing import Any

from anima_server.services.core import get_manifest_path, update_core_manifest

_CUTOVER_FIELD = "corefs_cutover"
_CUTOVER_VERSION = 1
_SHA256_HEX = re.compile(r"[0-9a-f]{64}")
_cutover_lock = RLock()

# This is a binary-owned release contract, not a manifest-controlled grant.
# A cutover-capable release must implement every listed authority adapter
# before it is permitted to publish the first marked mutation.
CONTENT_AUTHORITY_FAMILIES = (
    "account",
    "assets",
    "conversations",
    "diary",
    "documents",
    "knowledge",
    "notes",
    "preferences",
    "tasks",
)


class CutoverState(StrEnum):
    LEGACY_AUTHORITATIVE = "legacy-authoritative"
    MIGRATING_WRITE_FROZEN = "migrating-write-frozen"
    CORE_FS_VALIDATION_READONLY = "corefs-validation-readonly"
    CORE_FS_APPROVED_PENDING_FIRST_WRITE = "corefs-approved-pending-first-write"
    CORE_FS_AUTHORITATIVE_FORWARD_ONLY = "corefs-authoritative-forward-only"


class CutoverStateError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class CutoverRecord:
    state: CutoverState
    validation_generation: int | None = None
    validation_catalog_hash: str | None = None
    cutover_epoch: int | None = None
    authoritative_generation: int | None = None
    authoritative_catalog_hash: str | None = None

    def to_manifest(self) -> dict[str, object]:
        value: dict[str, object] = {
            "version": _CUTOVER_VERSION,
            "state": self.state.value,
        }
        if self.validation_generation is not None:
            value["validationGeneration"] = self.validation_generation
            value["validationCatalogHash"] = self.validation_catalog_hash
        if self.cutover_epoch is not None:
            value["cutoverEpoch"] = self.cutover_epoch
        if self.authoritative_generation is not None:
            value["authoritativeGeneration"] = self.authoritative_generation
            value["authoritativeCatalogHash"] = self.authoritative_catalog_hash
        return value


def read_cutover_record() -> CutoverRecord:
    path = get_manifest_path()
    if not path.is_file():
        return CutoverRecord(CutoverState.LEGACY_AUTHORITATIVE)
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CutoverStateError("ANIMA CORE manifest is unavailable or invalid") from exc
    if not isinstance(manifest, dict):
        raise CutoverStateError("ANIMA CORE manifest is invalid")
    return _record_from_manifest(manifest)


def begin_migration() -> CutoverRecord:
    return _transition(
        expected={CutoverState.LEGACY_AUTHORITATIVE},
        next_record=CutoverRecord(CutoverState.MIGRATING_WRITE_FROZEN),
    )


def publish_validation_readonly(*, generation: int, catalog_hash: str) -> CutoverRecord:
    _validate_snapshot(generation, catalog_hash)
    return _transition(
        expected={CutoverState.MIGRATING_WRITE_FROZEN},
        next_record=CutoverRecord(
            CutoverState.CORE_FS_VALIDATION_READONLY,
            validation_generation=generation,
            validation_catalog_hash=catalog_hash,
        ),
    )


def approve_validation_cutover() -> CutoverRecord:
    with _cutover_lock:
        current = read_cutover_record()
        if current.state is not CutoverState.CORE_FS_VALIDATION_READONLY:
            raise CutoverStateError(f"cannot approve CoreFS cutover from {current.state.value}")
        epoch = secrets.randbits(63) or 1
        return _write_record(
            CutoverRecord(
                CutoverState.CORE_FS_APPROVED_PENDING_FIRST_WRITE,
                validation_generation=current.validation_generation,
                validation_catalog_hash=current.validation_catalog_hash,
                cutover_epoch=epoch,
            ),
            expected=current,
        )


def rollback_cutover(*, corefs_session: Any, keys: object) -> CutoverRecord:
    """Restore legacy authority only when authenticated ``fs/HEAD`` is unmarked."""
    with _cutover_lock:
        current = read_cutover_record()
        marker = _read_native_marker(
            corefs_session=corefs_session,
            keys=keys,
            required=(current.state is CutoverState.CORE_FS_APPROVED_PENDING_FIRST_WRITE),
        )
        if marker is not None:
            raise CutoverStateError(
                "legacy rollback is permanently disabled by authenticated fs/HEAD"
            )
        if current.state not in {
            CutoverState.MIGRATING_WRITE_FROZEN,
            CutoverState.CORE_FS_VALIDATION_READONLY,
            CutoverState.CORE_FS_APPROVED_PENDING_FIRST_WRITE,
        }:
            raise CutoverStateError(f"cannot roll back from {current.state.value}")
        return _write_record(
            CutoverRecord(CutoverState.LEGACY_AUTHORITATIVE),
            expected=current,
        )


def reconcile_cutover_authority(
    *,
    corefs_session: Any,
    keys: object,
) -> dict[str, object] | None:
    """Recover post-HEAD cutover and return the session authority marker.

    The native call authenticates committed ``fs/HEAD`` and completes any
    interrupted receipt/complete publication under the Core commit lock.
    Manifest finalization happens only after that call succeeds.
    """
    with _cutover_lock:
        current = read_cutover_record()
        marker = _read_native_marker(
            corefs_session=corefs_session,
            keys=keys,
            required=current.state
            in {
                CutoverState.CORE_FS_APPROVED_PENDING_FIRST_WRITE,
                CutoverState.CORE_FS_AUTHORITATIVE_FORWARD_ONLY,
            },
        )
        if marker is None:
            if current.state is CutoverState.CORE_FS_AUTHORITATIVE_FORWARD_ONLY:
                raise CutoverStateError(
                    "forward-only manifest state has no authenticated CoreFS cutover marker"
                )
            return None

        record = CutoverRecord(
            CutoverState.CORE_FS_AUTHORITATIVE_FORWARD_ONLY,
            validation_generation=current.validation_generation,
            validation_catalog_hash=current.validation_catalog_hash,
            cutover_epoch=int(marker["cutoverEpoch"]),
            authoritative_generation=int(marker["generation"]),
            authoritative_catalog_hash=str(marker["catalogHash"]),
        )
        if current != record:
            _write_record(record, expected=current)
        return {
            "version": 1,
            "state": "cutover_complete",
            "legacyRollbackDisabled": True,
            "cutoverEpoch": record.cutover_epoch,
            "generation": record.authoritative_generation,
            "catalogHash": record.authoritative_catalog_hash,
            "families": list(CONTENT_AUTHORITY_FAMILIES),
        }


def _transition(
    *,
    expected: set[CutoverState],
    next_record: CutoverRecord,
) -> CutoverRecord:
    with _cutover_lock:
        current = read_cutover_record()
        if current.state not in expected:
            raise CutoverStateError(
                f"cannot transition CoreFS cutover from {current.state.value} "
                f"to {next_record.state.value}"
            )
        return _write_record(next_record, expected=current)


def _write_record(record: CutoverRecord, *, expected: CutoverRecord) -> CutoverRecord:
    def update(manifest: dict[str, object]) -> None:
        observed = _record_from_manifest(manifest)
        if observed != expected:
            raise CutoverStateError("CoreFS cutover manifest changed concurrently")
        manifest[_CUTOVER_FIELD] = record.to_manifest()

    update_core_manifest(update)
    return record


def _record_from_manifest(manifest: dict[str, object]) -> CutoverRecord:
    raw = manifest.get(_CUTOVER_FIELD)
    if raw is None:
        return CutoverRecord(CutoverState.LEGACY_AUTHORITATIVE)
    if not isinstance(raw, dict) or raw.get("version") != _CUTOVER_VERSION:
        raise CutoverStateError("CoreFS cutover manifest record is invalid")
    try:
        state = CutoverState(raw["state"])
    except (KeyError, TypeError, ValueError) as exc:
        raise CutoverStateError("CoreFS cutover manifest state is invalid") from exc

    base_fields = {"version", "state"}
    allowed_fields = {
        CutoverState.LEGACY_AUTHORITATIVE: base_fields,
        CutoverState.MIGRATING_WRITE_FROZEN: base_fields,
        CutoverState.CORE_FS_VALIDATION_READONLY: base_fields
        | {"validationGeneration", "validationCatalogHash"},
        CutoverState.CORE_FS_APPROVED_PENDING_FIRST_WRITE: base_fields
        | {"validationGeneration", "validationCatalogHash", "cutoverEpoch"},
        CutoverState.CORE_FS_AUTHORITATIVE_FORWARD_ONLY: base_fields
        | {
            "validationGeneration",
            "validationCatalogHash",
            "cutoverEpoch",
            "authoritativeGeneration",
            "authoritativeCatalogHash",
        },
    }[state]
    if set(raw) != allowed_fields and not (
        state is CutoverState.CORE_FS_AUTHORITATIVE_FORWARD_ONLY
        and set(raw) == allowed_fields - {"validationGeneration", "validationCatalogHash"}
    ):
        raise CutoverStateError("CoreFS cutover manifest record has an invalid shape")

    validation_generation = _optional_positive_int(raw, "validationGeneration")
    validation_hash = _optional_hash(raw, "validationCatalogHash")
    cutover_epoch = _optional_positive_int(raw, "cutoverEpoch")
    authoritative_generation = _optional_positive_int(raw, "authoritativeGeneration")
    authoritative_hash = _optional_hash(raw, "authoritativeCatalogHash")

    if (validation_generation is None) != (validation_hash is None):
        raise CutoverStateError("CoreFS validation identity is incomplete")
    if (authoritative_generation is None) != (authoritative_hash is None):
        raise CutoverStateError("CoreFS authoritative identity is incomplete")
    if state in {
        CutoverState.LEGACY_AUTHORITATIVE,
        CutoverState.MIGRATING_WRITE_FROZEN,
    } and any(
        value is not None
        for value in (
            validation_generation,
            validation_hash,
            cutover_epoch,
            authoritative_generation,
            authoritative_hash,
        )
    ):
        raise CutoverStateError("CoreFS cutover manifest has fields invalid for its state")
    if state is CutoverState.CORE_FS_VALIDATION_READONLY and (
        validation_generation is None
        or cutover_epoch is not None
        or authoritative_generation is not None
    ):
        raise CutoverStateError("CoreFS validation-readonly state is incomplete")
    if state is CutoverState.CORE_FS_APPROVED_PENDING_FIRST_WRITE and (
        validation_generation is None
        or cutover_epoch is None
        or authoritative_generation is not None
    ):
        raise CutoverStateError("CoreFS pending-first-write state is incomplete")
    if state is CutoverState.CORE_FS_AUTHORITATIVE_FORWARD_ONLY and (
        cutover_epoch is None or authoritative_generation is None
    ):
        raise CutoverStateError("CoreFS forward-only state is incomplete")
    return CutoverRecord(
        state=state,
        validation_generation=validation_generation,
        validation_catalog_hash=validation_hash,
        cutover_epoch=cutover_epoch,
        authoritative_generation=authoritative_generation,
        authoritative_catalog_hash=authoritative_hash,
    )


def _read_native_marker(
    *,
    corefs_session: Any,
    keys: object,
    required: bool = False,
) -> dict[str, object] | None:
    reader = getattr(corefs_session, "authoritative_cutover_v1", None)
    if not callable(reader):
        if required:
            raise CutoverStateError("native CoreFS cutover authentication is unavailable")
        # Pre-approval compatibility test doubles cannot have consumed the
        # first-mutation authorization. Pending/forward states require the
        # current native authority method above and fail closed without it.
        return None
    raw = reader(keys)
    if raw is None:
        return None
    if not isinstance(raw, dict) or set(raw) != {
        "version",
        "legacyRollbackDisabled",
        "cutoverEpoch",
        "generation",
        "catalogHash",
    }:
        raise CutoverStateError("native CoreFS cutover marker has an invalid shape")
    if raw.get("version") != 1 or raw.get("legacyRollbackDisabled") is not True:
        raise CutoverStateError("native CoreFS cutover marker is invalid")
    epoch = raw.get("cutoverEpoch")
    generation = raw.get("generation")
    catalog_hash = raw.get("catalogHash")
    if (
        isinstance(epoch, bool)
        or not isinstance(epoch, int)
        or epoch <= 0
        or isinstance(generation, bool)
        or not isinstance(generation, int)
        or generation <= 0
        or not isinstance(catalog_hash, str)
        or _SHA256_HEX.fullmatch(catalog_hash) is None
    ):
        raise CutoverStateError("native CoreFS cutover marker is invalid")
    return dict(raw)


def _validate_snapshot(generation: int, catalog_hash: str) -> None:
    if (
        isinstance(generation, bool)
        or not isinstance(generation, int)
        or generation <= 0
        or not isinstance(catalog_hash, str)
        or _SHA256_HEX.fullmatch(catalog_hash) is None
    ):
        raise CutoverStateError("CoreFS validation snapshot identity is invalid")


def _optional_positive_int(raw: dict[str, object], key: str) -> int | None:
    value = raw.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise CutoverStateError(f"CoreFS cutover {key} is invalid")
    return value


def _optional_hash(raw: dict[str, object], key: str) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or _SHA256_HEX.fullmatch(value) is None:
        raise CutoverStateError(f"CoreFS cutover {key} is invalid")
    return value
