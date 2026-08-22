from __future__ import annotations

import base64
import binascii
import json
import os
import secrets
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID, uuid4

from anima_server.config import default_runtime_app_data_root, settings
from anima_server.services.core import get_core_id
from anima_server.services.corefs.instance_registry import (
    InstanceBindingCollision,
    RuntimeInstanceRegistry,
)
from anima_server.services.corefs.transfer import (
    ActiveCorePointer,
    ScheduledActivation,
    ScheduledRollback,
    TransferError,
    _exclusive_activation_lock,
    _fsync_directory,
    _read_authenticated_record,
    _write_authenticated_record,
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
_ACCOUNT_DELETE_REQUEST_AUTH_DOMAIN = b"anima-account-delete-request-v1\x00"
_ACCOUNT_DELETE_JOURNAL_AUTH_DOMAIN = b"anima-account-delete-journal-v1\x00"
_ACCOUNT_DELETE_KEYS = {
    "version",
    "deletionId",
    "userId",
    "pointerGeneration",
    "activeCoreId",
    "activeCorePath",
    "retainedCoreId",
    "retainedCorePath",
    "runtimeInstancePath",
    "replacementCorePath",
}


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


@dataclass(frozen=True, slots=True)
class ScheduledAccountDeletion:
    deletion_id: str
    request_path: Path
    restart_required: bool = True


def resolve_active_core_for_startup(
    *,
    store: CredentialStore | None = None,
) -> ActiveCoreStartup:
    """Select and recover the active Core before any Core-owned resource opens."""
    configured_core = settings.data_dir.expanduser().resolve(strict=False)
    registry = _registry_path(configured_core)
    selected_store = store or credential_store()
    authentication_key = _load_or_create_authentication_key(selected_store)
    replacement = consume_scheduled_account_deletion(
        registry,
        authentication_key=authentication_key,
        store=selected_store,
    )
    if replacement is not None:
        settings.data_dir = replacement
        return ActiveCoreStartup(registry, authentication_key, None, selected_store)
    if not registry.exists():
        return ActiveCoreStartup(registry, authentication_key, None, selected_store)

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
    return ActiveCoreStartup(registry, authentication_key, pointer, selected_store)


def schedule_active_core_account_deletion(
    *,
    user_id: int,
    store: CredentialStore | None = None,
) -> ScheduledAccountDeletion:
    """Schedule whole-Core deletion for the next pre-resource startup."""
    if user_id < 0:
        raise TransferError("account deletion owner is invalid")
    current_core = settings.data_dir.expanduser().resolve(strict=True)
    registry = _registry_path(current_core)
    selected_store = store or credential_store()
    authentication_key = _load_or_create_authentication_key(selected_store)
    request = registry.with_name(f"{registry.name}.delete-request")
    journal = registry.with_name(f"{registry.name}.delete-journal")
    activation_request = registry.with_name(f"{registry.name}.request")
    rollback_request = registry.with_name(f"{registry.name}.rollback-request")
    activation_journal = registry.with_name(f"{registry.name}.activation")

    with _exclusive_activation_lock(registry):
        pointer = read_active_core_pointer(
            registry,
            authentication_key=authentication_key,
        )
        if pointer.active_core_path != current_core:
            raise TransferError("running Core does not match the active-Core registry")
        if any(
            path.exists()
            for path in (activation_request, rollback_request, activation_journal, journal)
        ):
            raise TransferError("another active-Core restart operation is pending")
        _verify_account_owner(pointer.active_core_path, user_id=user_id)
        verify_registry_core_candidate(pointer.active_core_path)
        if pointer.retained_core_path is not None:
            verify_registry_core_candidate(pointer.retained_core_path)

        runtime_path = _scheduled_runtime_instance_path(pointer.core_id)
        if request.exists():
            body = _read_account_delete_record(
                request,
                authentication_key,
                domain=_ACCOUNT_DELETE_REQUEST_AUTH_DOMAIN,
            )
            if (
                body["userId"] != user_id
                or body["pointerGeneration"] != pointer.generation
                or body["activeCoreId"] != pointer.core_id
            ):
                raise TransferError("another whole-Core account deletion is scheduled")
            deletion_id = str(body["deletionId"])
        else:
            deletion_id = str(uuid4())
            body = {
                "version": 1,
                "deletionId": deletion_id,
                "userId": user_id,
                "pointerGeneration": pointer.generation,
                "activeCoreId": pointer.core_id,
                "activeCorePath": os.fspath(pointer.active_core_path),
                "retainedCoreId": pointer.retained_core_id,
                "retainedCorePath": (
                    os.fspath(pointer.retained_core_path)
                    if pointer.retained_core_path is not None
                    else None
                ),
                "runtimeInstancePath": os.fspath(runtime_path),
                "replacementCorePath": os.fspath(pointer.active_core_path),
            }
            _write_authenticated_record(
                request,
                body,
                authentication_key,
                _ACCOUNT_DELETE_REQUEST_AUTH_DOMAIN,
            )
    return ScheduledAccountDeletion(
        deletion_id=deletion_id,
        request_path=request,
    )


def consume_scheduled_account_deletion(
    registry_path: Path,
    *,
    authentication_key: bytes,
    store: CredentialStore,
    boundary_hook: Callable[[str], None] | None = None,
) -> Path | None:
    """Consume or resume an authenticated deletion before Core resources open."""
    registry = registry_path.expanduser().resolve(strict=False)
    request = registry.with_name(f"{registry.name}.delete-request")
    journal = registry.with_name(f"{registry.name}.delete-journal")
    if not request.exists() and not journal.exists():
        return None

    with _exclusive_activation_lock(registry):
        if journal.exists():
            body = _read_account_delete_record(
                journal,
                authentication_key,
                domain=_ACCOUNT_DELETE_JOURNAL_AUTH_DOMAIN,
            )
        else:
            body = _read_account_delete_record(
                request,
                authentication_key,
                domain=_ACCOUNT_DELETE_REQUEST_AUTH_DOMAIN,
            )
            pointer = read_active_core_pointer(
                registry,
                authentication_key=authentication_key,
            )
            _require_delete_request_matches_pointer(body, pointer)
            conflicts = (
                registry.with_name(f"{registry.name}.request"),
                registry.with_name(f"{registry.name}.rollback-request"),
                registry.with_name(f"{registry.name}.activation"),
            )
            if any(path.exists() for path in conflicts):
                raise TransferError("whole-Core deletion conflicts with a restart operation")
            _verify_account_owner(
                Path(str(body["activeCorePath"])),
                user_id=int(body["userId"]),
            )
            _validate_account_delete_targets(body)
            _write_authenticated_record(
                journal,
                body,
                authentication_key,
                _ACCOUNT_DELETE_JOURNAL_AUTH_DOMAIN,
            )
            _account_delete_boundary(boundary_hook, "account-delete:after-journal")

        _validate_account_delete_body(body)
        deletion_id = str(body["deletionId"])
        active = Path(str(body["activeCorePath"]))
        retained = (
            Path(str(body["retainedCorePath"]))
            if body["retainedCorePath"] is not None
            else None
        )
        runtime = Path(str(body["runtimeInstancePath"]))
        active_quarantine = _deletion_quarantine(active, deletion_id, "active")
        retained_quarantine = (
            _deletion_quarantine(retained, deletion_id, "retained")
            if retained is not None
            else None
        )
        runtime_quarantine = _deletion_quarantine(runtime, deletion_id, "runtime")

        runtime_registry = _runtime_instance_registry()
        try:
            runtime_registry.verify_account_deletion_binding(
                core_id=str(body["activeCoreId"]),
                instance_root=runtime,
                require_current_process=False,
                allow_missing=not runtime.exists(),
            )
        except InstanceBindingCollision as exc:
            raise TransferError("scheduled Runtime deletion target is not stopped") from exc

        _quarantine_core_for_deletion(
            active,
            active_quarantine,
            expected_core_id=str(body["activeCoreId"]),
        )
        _account_delete_boundary(boundary_hook, "account-delete:after-active-quarantine")
        if retained is not None and retained_quarantine is not None:
            _quarantine_core_for_deletion(
                retained,
                retained_quarantine,
                expected_core_id=str(body["retainedCoreId"]),
            )
        _account_delete_boundary(boundary_hook, "account-delete:after-retained-quarantine")
        _quarantine_directory(runtime, runtime_quarantine)
        try:
            runtime_registry.retire_account_deletion_binding(
                core_id=str(body["activeCoreId"]),
                instance_root=runtime,
                allow_missing=True,
            )
        except InstanceBindingCollision as exc:
            raise TransferError(
                "scheduled Runtime deletion binding could not be retired"
            ) from exc
        _account_delete_boundary(boundary_hook, "account-delete:after-runtime-quarantine")

        for path in _active_core_terminal_paths(registry):
            _unlink_regular_file(path)
        _fsync_directory(registry.parent)
        _account_delete_boundary(boundary_hook, "account-delete:after-registry-removal")

        for quarantine in (
            active_quarantine,
            retained_quarantine,
            runtime_quarantine,
        ):
            if quarantine is not None and quarantine.exists():
                shutil.rmtree(quarantine)
                _fsync_directory(quarantine.parent)
        _account_delete_boundary(boundary_hook, "account-delete:after-data-removal")

        request.unlink(missing_ok=True)
        _fsync_directory(request.parent)
        _account_delete_boundary(boundary_hook, "account-delete:after-request-removal")
        journal.unlink(missing_ok=True)
        _fsync_directory(journal.parent)
        store.delete(_ACTIVE_CORE_CREDENTIAL)
        replacement = Path(str(body["replacementCorePath"]))
        replacement.parent.mkdir(parents=True, exist_ok=True)
        return replacement


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


def _read_account_delete_record(
    path: Path,
    authentication_key: bytes,
    *,
    domain: bytes,
) -> dict[str, object]:
    body = _read_authenticated_record(
        path,
        authentication_key,
        domain,
        expected_keys=_ACCOUNT_DELETE_KEYS,
    )
    _validate_account_delete_body(body)
    return body


def _validate_account_delete_body(body: dict[str, object]) -> None:
    if set(body) != _ACCOUNT_DELETE_KEYS or body.get("version") != 1:
        raise TransferError("scheduled whole-Core account deletion is invalid")
    try:
        UUID(str(body["deletionId"]))
        UUID(str(body["activeCoreId"]))
        if body["retainedCoreId"] is not None:
            UUID(str(body["retainedCoreId"]))
    except (KeyError, ValueError, TypeError) as exc:
        raise TransferError("scheduled whole-Core account deletion is invalid") from exc
    if (
        not isinstance(body["userId"], int)
        or isinstance(body["userId"], bool)
        or int(body["userId"]) < 0
        or not isinstance(body["pointerGeneration"], int)
        or isinstance(body["pointerGeneration"], bool)
        or int(body["pointerGeneration"]) <= 0
    ):
        raise TransferError("scheduled whole-Core account deletion is invalid")
    for key in ("activeCorePath", "replacementCorePath"):
        if not isinstance(body[key], str) or not body[key]:
            raise TransferError("scheduled whole-Core account deletion is invalid")
    for key in ("retainedCorePath",):
        if body[key] is not None and (not isinstance(body[key], str) or not body[key]):
            raise TransferError("scheduled whole-Core account deletion is invalid")
    if not isinstance(body["runtimeInstancePath"], str) or not body["runtimeInstancePath"]:
        raise TransferError("scheduled whole-Core account deletion is invalid")
    if (body["retainedCoreId"] is None) != (body["retainedCorePath"] is None):
        raise TransferError("scheduled whole-Core account deletion is invalid")

    active = _validated_deletion_path(Path(str(body["activeCorePath"])))
    replacement = _validated_deletion_path(Path(str(body["replacementCorePath"])))
    if replacement != active:
        raise TransferError("scheduled whole-Core account deletion is invalid")
    retained = (
        _validated_deletion_path(Path(str(body["retainedCorePath"])))
        if body["retainedCorePath"] is not None
        else None
    )
    runtime = _validated_deletion_path(Path(str(body["runtimeInstancePath"])))
    paths = [active, runtime, *(item for item in (retained,) if item is not None)]
    if len(set(paths)) != len(paths):
        raise TransferError("scheduled whole-Core account deletion paths collide")
    for index, left in enumerate(paths):
        for right in paths[index + 1 :]:
            if left.is_relative_to(right) or right.is_relative_to(left):
                raise TransferError("scheduled whole-Core account deletion paths overlap")


def _validated_deletion_path(path: Path) -> Path:
    if not path.is_absolute():
        raise TransferError("scheduled whole-Core account deletion path is invalid")
    normalized = path.resolve(strict=False)
    anchor = Path(normalized.anchor)
    if normalized == anchor or normalized == Path.home().resolve():
        raise TransferError("scheduled whole-Core account deletion path is unsafe")
    return normalized


def _require_delete_request_matches_pointer(
    body: dict[str, object], pointer: ActiveCorePointer
) -> None:
    expected = {
        "pointerGeneration": pointer.generation,
        "activeCoreId": pointer.core_id,
        "activeCorePath": os.fspath(pointer.active_core_path),
        "retainedCoreId": pointer.retained_core_id,
        "retainedCorePath": (
            os.fspath(pointer.retained_core_path)
            if pointer.retained_core_path is not None
            else None
        ),
    }
    if any(body.get(key) != value for key, value in expected.items()):
        raise TransferError("scheduled whole-Core deletion no longer matches active Core")


def _validate_account_delete_targets(body: dict[str, object]) -> None:
    active = Path(str(body["activeCorePath"]))
    _verify_manifest_core_id(active, str(body["activeCoreId"]))
    verify_registry_core_candidate(active)
    if body["retainedCorePath"] is not None:
        retained = Path(str(body["retainedCorePath"]))
        _verify_manifest_core_id(retained, str(body["retainedCoreId"]))
        verify_registry_core_candidate(retained)
    candidate = Path(str(body["runtimeInstancePath"]))
    if candidate.is_symlink() or not candidate.is_dir():
        raise TransferError("scheduled Runtime deletion target is invalid")


def _verify_manifest_core_id(path: Path, expected_core_id: str) -> None:
    try:
        payload = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TransferError("scheduled deletion Core manifest is invalid") from exc
    if not isinstance(payload, dict) or payload.get("core_id") != expected_core_id:
        raise TransferError("scheduled deletion Core identity changed")


def _verify_account_owner(path: Path, *, user_id: int) -> None:
    try:
        payload = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TransferError("scheduled deletion Core manifest is invalid") from exc
    if not isinstance(payload, dict) or payload.get("owner_user_id") != user_id:
        raise TransferError("authenticated account does not own the active Core")


def _scheduled_runtime_instance_path(core_id: str) -> Path:
    if not settings.runtime_instance_data_dir:
        raise TransferError("Runtime instance deletion target is unavailable")
    candidate_input = Path(settings.runtime_instance_data_dir).expanduser()
    if candidate_input.is_symlink():
        raise TransferError("Runtime instance deletion target is invalid")
    candidate = candidate_input.resolve(strict=True)
    app_root = _runtime_app_root()
    expected_parent = app_root / "cores" / core_id / "instances"
    if candidate.parent != expected_parent or not candidate.is_dir():
        raise TransferError("Runtime instance deletion target is invalid")
    try:
        RuntimeInstanceRegistry(app_root).verify_account_deletion_binding(
            core_id=core_id,
            instance_root=candidate,
            require_current_process=True,
        )
    except InstanceBindingCollision as exc:
        raise TransferError("Runtime instance deletion target is not active") from exc
    return candidate


def _runtime_app_root() -> Path:
    return (
        Path(settings.runtime_app_data_dir)
        if settings.runtime_app_data_dir
        else default_runtime_app_data_root()
    ).expanduser().resolve(strict=False)


def _runtime_instance_registry() -> RuntimeInstanceRegistry:
    try:
        return RuntimeInstanceRegistry(_runtime_app_root())
    except InstanceBindingCollision as exc:
        raise TransferError("Runtime instance registry is unavailable") from exc


def _deletion_quarantine(path: Path, deletion_id: str, label: str) -> Path:
    token = str(UUID(deletion_id)).split("-", maxsplit=1)[0]
    return path.with_name(f".{path.name}.deleting-{token}-{label}")


def _quarantine_core_for_deletion(
    source: Path,
    quarantine: Path,
    *,
    expected_core_id: str,
) -> None:
    if source.exists() and quarantine.exists():
        raise TransferError("whole-Core deletion quarantine conflicts with its source")
    if source.exists():
        if source.is_symlink() or not source.is_dir():
            raise TransferError("whole-Core deletion source is invalid")
        _verify_manifest_core_id(source, expected_core_id)
        verify_registry_core_candidate(source)
        source.rename(quarantine)
        _fsync_directory(source.parent)
    elif quarantine.exists():
        if quarantine.is_symlink() or not quarantine.is_dir():
            raise TransferError("whole-Core deletion quarantine is invalid")
        _verify_manifest_core_id(quarantine, expected_core_id)
        verify_registry_core_candidate(quarantine)


def _quarantine_directory(source: Path, quarantine: Path) -> None:
    if source.exists() and quarantine.exists():
        raise TransferError("Runtime deletion quarantine conflicts with its source")
    if source.exists():
        if source.is_symlink() or not source.is_dir():
            raise TransferError("Runtime deletion source is invalid")
        source.rename(quarantine)
        _fsync_directory(source.parent)
    elif quarantine.exists() and (quarantine.is_symlink() or not quarantine.is_dir()):
        raise TransferError("Runtime deletion quarantine is invalid")


def _active_core_terminal_paths(registry: Path) -> tuple[Path, ...]:
    return (
        registry,
        registry.with_name(f"{registry.name}.activation"),
        registry.with_name(f"{registry.name}.completion"),
        registry.with_name(f"{registry.name}.request"),
        registry.with_name(f"{registry.name}.rollback-request"),
    )


def _unlink_regular_file(path: Path) -> None:
    if not path.exists():
        return
    if path.is_symlink() or not path.is_file():
        raise TransferError("active-Core terminal record is not a regular file")
    path.unlink()


def _account_delete_boundary(
    hook: Callable[[str], None] | None,
    boundary: str,
) -> None:
    if hook is not None:
        hook(boundary)


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
