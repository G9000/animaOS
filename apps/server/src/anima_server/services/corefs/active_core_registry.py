from __future__ import annotations

import base64
import binascii
import json
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID, uuid4

from anima_server.config import default_runtime_app_data_root, settings
from anima_server.services.core import get_core_id
from anima_server.services.corefs.transfer import (
    ActiveCorePointer,
    ScheduledActivation,
    ScheduledRollback,
    TransferError,
    consume_scheduled_core_activation,
    consume_scheduled_core_rollback,
    initialize_active_core_pointer,
    read_active_core_pointer,
    read_scheduled_core_rollback,
    recover_active_core_activation,
    schedule_retained_core_rollback,
    schedule_staged_core_activation,
)
from anima_server.services.credentials import (
    CredentialStore,
    credential_reference,
    credential_store,
)

_ACTIVE_CORE_KEY_BYTES = 32
_ACTIVE_CORE_REGISTRY_NAME = "active-core.json"
_ACTIVE_CORE_CREDENTIAL = credential_reference("core-transfer", "active-core-registry-v1")
_MAX_MANIFEST_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ActiveCoreStartup:
    registry_path: Path
    authentication_key: bytes
    pointer: ActiveCorePointer | None
    store: CredentialStore = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class ActiveCoreStatus:
    generation: int
    active_core_id: str
    retained_core_id: str | None
    activation_id: str
    rollback_scheduled: bool


def resolve_active_core_for_startup(
    *,
    store: CredentialStore | None = None,
) -> ActiveCoreStartup:
    """Select and recover the active Core before any Core-owned resource opens."""
    configured_core = settings.data_dir.expanduser().resolve(strict=False)
    registry = _registry_path(configured_core)
    authentication_key = _load_or_create_authentication_key(store or credential_store())
    if not registry.exists():
        return ActiveCoreStartup(registry, authentication_key, None, store or credential_store())

    consume_scheduled_core_rollback(
        registry,
        authentication_key=authentication_key,
        verifier=verify_registry_core_candidate,
    )
    consume_scheduled_core_activation(
        registry,
        authentication_key=authentication_key,
        verifier=verify_full_core_candidate,
    )
    recover_active_core_activation(
        registry,
        authentication_key=authentication_key,
        verifier=verify_full_core_candidate,
    )
    pointer = read_active_core_pointer(
        registry,
        authentication_key=authentication_key,
    )
    settings.data_dir = pointer.active_core_path
    return ActiveCoreStartup(registry, authentication_key, pointer, store or credential_store())


def initialize_active_core_after_manifest(startup: ActiveCoreStartup) -> ActiveCorePointer:
    """Create the first pointer only after the configured Core has a manifest."""
    active = settings.data_dir.expanduser().resolve(strict=True)
    core_id = get_core_id()
    authentication_key = _load_or_create_authentication_key(startup.store)
    if not startup.registry_path.exists():
        pointer = initialize_active_core_pointer(
            startup.registry_path,
            authentication_key=authentication_key,
            core_id=core_id,
            active_core_path=active,
        )
    else:
        pointer = read_active_core_pointer(
            startup.registry_path,
            authentication_key=authentication_key,
        )
        if pointer.active_core_path != active or pointer.core_id != core_id:
            raise TransferError("startup active-Core selection changed before initialization")
    return pointer


def schedule_full_restore_activation(
    staging_path: Path,
    *,
    core_id: str,
    store: CredentialStore | None = None,
) -> ScheduledActivation:
    """Schedule a verified full restore for the next pre-resource startup."""
    current_core = settings.data_dir.expanduser().resolve(strict=True)
    registry = _registry_path(current_core)
    authentication_key = _load_or_create_authentication_key(store or credential_store())
    pointer = read_active_core_pointer(registry, authentication_key=authentication_key)
    if pointer.active_core_path != current_core:
        raise TransferError("running Core does not match the active-Core registry")
    activation_id = str(uuid4())
    final_path = staging_path.parent / (
        f".anima-restored-{core_id.split('-')[0]}-{activation_id.split('-')[0]}"
    )
    return schedule_staged_core_activation(
        staging_path,
        final_path,
        registry,
        authentication_key=authentication_key,
        core_id=core_id,
        activation_id=activation_id,
        verifier=verify_full_core_candidate,
    )


def read_active_core_status(*, store: CredentialStore | None = None) -> ActiveCoreStatus:
    current_core = settings.data_dir.expanduser().resolve(strict=True)
    registry = _registry_path(current_core)
    authentication_key = _load_or_create_authentication_key(store or credential_store())
    pointer = read_active_core_pointer(registry, authentication_key=authentication_key)
    if pointer.active_core_path != current_core:
        raise TransferError("running Core does not match the active-Core registry")
    rollback_request = read_scheduled_core_rollback(
        registry,
        authentication_key=authentication_key,
    )
    return ActiveCoreStatus(
        generation=pointer.generation,
        active_core_id=pointer.core_id,
        retained_core_id=pointer.retained_core_id,
        activation_id=pointer.activation_id,
        rollback_scheduled=rollback_request is not None,
    )


def schedule_active_core_rollback(
    *,
    store: CredentialStore | None = None,
) -> ScheduledRollback:
    current_core = settings.data_dir.expanduser().resolve(strict=True)
    registry = _registry_path(current_core)
    authentication_key = _load_or_create_authentication_key(store or credential_store())
    pointer = read_active_core_pointer(registry, authentication_key=authentication_key)
    if pointer.active_core_path != current_core:
        raise TransferError("running Core does not match the active-Core registry")
    return schedule_retained_core_rollback(
        registry,
        authentication_key=authentication_key,
        rollback_id=str(uuid4()),
    )


def verify_full_core_candidate(path: Path) -> None:
    """Verify the minimum complete-Core shape required for registry activation."""
    candidate_input = path.expanduser()
    if candidate_input.is_symlink():
        raise TransferError("restore activation candidate must be a regular directory")
    candidate = candidate_input.resolve(strict=True)
    if not candidate.is_dir():
        raise TransferError("restore activation candidate must be a regular directory")
    manifest_path = candidate / "manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise TransferError("restore activation candidate has no regular manifest")
    try:
        if manifest_path.stat().st_size > _MAX_MANIFEST_BYTES:
            raise TransferError("restore activation manifest exceeds its bound")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TransferError("restore activation manifest is invalid") from exc
    if not isinstance(manifest, dict):
        raise TransferError("restore activation manifest is invalid")
    for identity_field in ("core_id", "owner_id"):
        value = manifest.get(identity_field)
        if not isinstance(value, str):
            raise TransferError("restore activation manifest identity is invalid")
        try:
            UUID(value)
        except ValueError as exc:
            raise TransferError("restore activation manifest identity is invalid") from exc
    if manifest.get("archive_payload_scope") != "full" or manifest.get("degraded_state") in {
        "filesystem_missing",
        "recovery_only",
    }:
        raise TransferError("only a complete full archive can activate as ANIMA CORE")
    _verify_complete_core_shape(candidate, label="restore activation candidate")


def _verify_complete_core_shape(candidate: Path, *, label: str) -> None:
    for required in (candidate / "soul" / "soul.db", candidate / "fs" / "HEAD"):
        if required.is_symlink() or not required.is_file():
            raise TransferError(f"{label} is incomplete")
    catalogs = candidate / "fs" / "catalogs"
    if (
        catalogs.is_symlink()
        or not catalogs.is_dir()
        or not any(
            child.is_file() and not child.is_symlink() and child.name.endswith(".acore")
            for child in catalogs.iterdir()
        )
    ):
        raise TransferError(f"{label} has no committed catalog")


def verify_registry_core_candidate(path: Path) -> None:
    """Verify a pointer-selected Core without imposing restored-full metadata."""
    candidate_input = path.expanduser()
    if candidate_input.is_symlink():
        raise TransferError("registry Core candidate must be a regular directory")
    candidate = candidate_input.resolve(strict=True)
    manifest_path = candidate / "manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise TransferError("registry Core candidate has no regular manifest")
    try:
        if manifest_path.stat().st_size > _MAX_MANIFEST_BYTES:
            raise TransferError("registry Core manifest exceeds its bound")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TransferError("registry Core manifest is invalid") from exc
    if not isinstance(manifest, dict):
        raise TransferError("registry Core manifest is invalid")
    for identity_field in ("core_id", "owner_id"):
        value = manifest.get(identity_field)
        if not isinstance(value, str):
            raise TransferError("registry Core identity is invalid")
        try:
            UUID(value)
        except ValueError as exc:
            raise TransferError("registry Core identity is invalid") from exc
    _verify_complete_core_shape(candidate, label="registry Core candidate")


def _registry_path(configured_core: Path) -> Path:
    app_data_root = (
        (
            Path(settings.runtime_app_data_dir)
            if settings.runtime_app_data_dir
            else default_runtime_app_data_root()
        )
        .expanduser()
        .resolve(strict=False)
    )
    if app_data_root.is_relative_to(configured_core) or configured_core.is_relative_to(
        app_data_root
    ):
        raise TransferError("active-Core registry must not overlap the portable Core")
    registry_root = app_data_root / "core-selection"
    registry_root.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        os.chmod(registry_root, 0o700)
    return registry_root / _ACTIVE_CORE_REGISTRY_NAME


def _load_or_create_authentication_key(store: CredentialStore) -> bytes:
    encoded = store.get(_ACTIVE_CORE_CREDENTIAL)
    if encoded is None:
        encoded = base64.b64encode(secrets.token_bytes(_ACTIVE_CORE_KEY_BYTES)).decode("ascii")
        store.put(_ACTIVE_CORE_CREDENTIAL, encoded)
    try:
        key = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise TransferError("active-Core registry credential is invalid") from exc
    if len(key) != _ACTIVE_CORE_KEY_BYTES or base64.b64encode(key).decode("ascii") != encoded:
        raise TransferError("active-Core registry credential is invalid")
    return key
