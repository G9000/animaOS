"""User-authorized maintenance of the supported encrypted Portable Core.

Python authenticates users/backups and binds confirmations. Native CoreFS owns
all reachability, candidate selection, locking, quarantine, and deletion.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import tempfile
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from threading import RLock
from typing import Any

DEFAULT_RETENTION_DAYS = 30
_CONFIRMATION_TTL = 120
_MAX_CONFIRMATIONS = 128
_SHA256_LENGTH = 64


class MaintenanceError(RuntimeError):
    pass


class MaintenanceConfirmations:
    """Bounded ephemeral one-use grants; no passwords, keys, or content stored."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = RLock()
        self._grants: dict[str, tuple[float, bytes]] = {}

    def issue(self, binding: Mapping[str, object]) -> str:
        digest = _binding_digest(binding)
        with self._lock:
            now = self._clock()
            self._grants = {token: value for token, value in self._grants.items() if value[0] > now}
            if len(self._grants) >= _MAX_CONFIRMATIONS:
                raise MaintenanceError("Too many pending maintenance confirmations.")
            token = secrets.token_urlsafe(32)
            self._grants[token] = (now + _CONFIRMATION_TTL, digest)
            return token

    def consume(self, token: str, binding: Mapping[str, object]) -> None:
        expected = _binding_digest(binding)
        with self._lock:
            value = self._grants.pop(token, None)
            if (
                value is None
                or value[0] <= self._clock()
                or not hmac.compare_digest(value[1], expected)
            ):
                raise MaintenanceError(
                    "Maintenance confirmation expired, changed, or was already used."
                )


def _binding_digest(binding: Mapping[str, object]) -> bytes:
    encoded = json.dumps(
        dict(binding), sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()
    if len(encoded) > 64 * 1024:
        raise MaintenanceError("Maintenance confirmation inventory exceeds its bound.")
    return hashlib.sha256(encoded).digest()


def _is_digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == _SHA256_LENGTH
        and all(character in "0123456789abcdef" for character in value)
    )


def require_complete_inventory(
    value: object, *, allow_blockers: bool = False, continuation: str | None = None
) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("version") != 1:
        raise MaintenanceError("Native maintenance inventory is incomplete.")
    blockers = value.get("blockers")
    versions = value.get("requiredFrkVersions")
    if (
        not isinstance(blockers, list)
        or not all(isinstance(item, str) for item in blockers)
        or not isinstance(versions, list)
        or not versions
        or any(
            not isinstance(item, int) or isinstance(item, bool) or item <= 0 for item in versions
        )
        or not isinstance(value.get("headGeneration"), int)
        or isinstance(value["headGeneration"], bool)
        or value["headGeneration"] <= 0
        or not _is_digest(value.get("headCatalogHash"))
        or not _is_digest(value.get("inventoryDigest"))
    ):
        raise MaintenanceError("Native maintenance inventory is incomplete.")
    operation = value.get("operation")
    authorized_continuation = (
        continuation in {"resume", "cancel"}
        and value.get("resumeAuthorized" if continuation == "resume" else "cancelAuthorized")
        is True
        and isinstance(operation, dict)
        and operation.get("kind") in {"prune", "purge", "reanchor"}
        and _is_digest(operation.get("expectedInventoryDigest"))
        and _is_digest(operation.get("sourceHeadCatalogHash"))
        and isinstance(operation.get("sourceHeadGeneration"), int)
        and operation["sourceHeadGeneration"] > 0
        and set(blockers).issubset({"maintenance_operation_pending", "head_lineage_corrupt"})
    )
    if blockers and not allow_blockers and not authorized_continuation:
        raise MaintenanceError("Core maintenance is blocked: " + ", ".join(blockers))
    if continuation in {"resume", "cancel"} and not authorized_continuation:
        raise MaintenanceError("No authenticated maintenance operation permits this continuation.")
    return value


def public_inventory(value: Mapping[str, object]) -> dict[str, object]:
    allowed = {
        "version",
        "headGeneration",
        "headCatalogHash",
        "inventoryDigest",
        "retainedCatalogCount",
        "catalogCount",
        "objectCount",
        "candidateCatalogCount",
        "candidateObjectCount",
        "candidatePreparationCount",
        "candidateBytes",
        "requiredFrkVersions",
        "blockers",
        "operation",
        "retainedUnknownAgeCount",
        "targetStableId",
        "expectedRevision",
        "recursiveInventoryDigest",
        "waivedGenerations",
        "targetObjectCount",
        "warnings",
        "resumeAuthorized",
        "cancelAuthorized",
    }
    result = {key: value[key] for key in allowed if key in value}
    result.update(
        {
            "retentionDays": DEFAULT_RETENTION_DAYS,
            "trashExpiresAutomatically": False,
            "externalCopiesErased": False,
            "deletionLimits": "Local pruning cannot erase SSD remnants or exported archives and backups.",
        }
    )
    return result


def require_maintenance_session(session: object) -> None:
    if (
        getattr(session, "corefs_session", None) is None
        or getattr(session, "corefs_keys", None) is None
    ):
        raise MaintenanceError("A full CoreFS unlock session is required.")


def require_idle_operations(manifest: Mapping[str, object]) -> None:
    from anima_server.services.corefs import transfer_jobs

    rotation = manifest.get("frk_rotation")
    if (
        not isinstance(rotation, dict)
        or rotation.get("phase") != "idle"
        or rotation.get("pending_version") is not None
    ):
        raise MaintenanceError("CoreFS rotation must finish before maintenance.")
    if manifest.get("pending_password_credential_generation") or manifest.get(
        "pending_recovery_credential_generation"
    ):
        raise MaintenanceError("Credential replacement must finish before maintenance.")
    for manager in (transfer_jobs.core_transfer_operations, transfer_jobs.core_import_operations):
        with manager._lock:
            if any(
                str(operation.state) not in {"completed", "cancelled", "failed"}
                for operation in manager._operations.values()
            ):
                raise MaintenanceError("Active transfer or import pins block maintenance.")


maintenance_confirmations = MaintenanceConfirmations()


def _maintenance_directory() -> Path:
    from anima_server.config import (
        default_runtime_app_data_root,
        resolve_runtime_path_outside_core,
        settings,
    )
    from anima_server.services.core import get_core_id

    root = (
        Path(settings.runtime_app_data_dir)
        if settings.runtime_app_data_dir
        else default_runtime_app_data_root()
    )
    path = resolve_runtime_path_outside_core(
        root / get_core_id() / "maintenance", setting_name="Core maintenance"
    )
    path.mkdir(parents=True, exist_ok=True)
    return path


def _verify_full_core_reopen(
    core_root: Path, *, manifest: Mapping[str, Any], password: str, recovery_phrase: str
) -> Any:
    """Reopen both credentials at an explicit Core without publishing unlock state.

    Independent read-only SQLCipher handles authenticate the Soul owner/domain
    slots. The caller verifies CoreFS under its native lock using the same roots.
    No global settings, engine cache, sessions, or credential generations change.
    """
    import sqlcipher3
    from cryptography.exceptions import InvalidTag
    from sqlalchemy import create_engine, select
    from sqlalchemy.exc import SQLAlchemyError
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import NullPool

    from anima_server.db.base import Base
    from anima_server.models import SoulKeyslot, SoulOwner
    from anima_server.services.corefs import keyslots
    from anima_server.services.corefs.crypto import soul_keyslot_aad, unwrap_keyslot_secret
    from anima_server.services.corefs.types import KeyslotStatus, PayloadScope, WrappingPath
    from anima_server.services.data_crypto import ALL_DOMAINS

    source = core_root / "soul" / "soul.db"
    try:
        if source.is_symlink() or not source.is_file() or source.parent.is_symlink():
            raise ValueError("Soul database must be a regular encrypted file")
        with source.open("rb") as stream:
            if stream.read(16) == b"SQLite format 3\x00":
                raise ValueError("Plaintext Soul is unsupported")
        opened = []
        for path, credential in (
            (WrappingPath.PASSWORD, password),
            (WrappingPath.RECOVERY, recovery_phrase),
        ):
            roots = keyslots._unlock_manifest_key_hierarchy(
                dict(manifest),
                credential=credential,
                wrapping_path=path,
                expected_scope=PayloadScope.FULL,
                generation=None,
                status=KeyslotStatus.ACTIVE,
            )
            if roots.sqlcipher_key is None:
                raise ValueError("The full Core has no Soul root")

            def connect_readonly(raw_key=roots.sqlcipher_key):
                connection = sqlcipher3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)
                try:
                    connection.execute(f"PRAGMA key = \"x'{raw_key.hex()}'\"")
                    connection.execute("PRAGMA cipher_page_size = 4096")
                    connection.execute("PRAGMA query_only = ON")
                    connection.execute("PRAGMA busy_timeout = 30000")
                    return connection
                except BaseException:
                    connection.close()
                    raise

            engine = create_engine(
                "sqlite://",
                module=sqlcipher3,
                creator=connect_readonly,
                poolclass=NullPool,
            )
            try:
                with Session(engine, autoflush=False) as db:
                    connection = db.connection()
                    if connection.exec_driver_sql("PRAGMA integrity_check").fetchall() != [("ok",)]:
                        raise ValueError("Soul page integrity failed")
                    if (
                        connection.exec_driver_sql("PRAGMA foreign_key_check").fetchone()
                        is not None
                    ):
                        raise ValueError("Soul foreign-key integrity failed")
                    tables = {
                        row[0]
                        for row in connection.exec_driver_sql(
                            "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                        )
                    }
                    if tables != set(Base.metadata.tables) | {"alembic_version"}:
                        raise ValueError("Soul schema is not the current supported compartment")
                    if (
                        connection.exec_driver_sql(
                            "SELECT version_num FROM alembic_version"
                        ).scalar_one()
                        != "20261009_0001"
                    ):
                        raise ValueError("Soul schema is not the current supported head")
                    owners = list(db.scalars(select(SoulOwner).limit(2)))
                    if (
                        len(owners) != 1
                        or owners[0].core_owner_id != manifest["owner_id"]
                        or owners[0].id != manifest["owner_user_id"]
                    ):
                        raise ValueError("Soul owner does not match the authenticated Core")
                    rows = list(
                        db.scalars(
                            select(SoulKeyslot)
                            .where(
                                SoulKeyslot.owner_id == roots.owner_id,
                                SoulKeyslot.wrapping_path == path.value,
                                SoulKeyslot.credential_generation == roots.credential_generation,
                                SoulKeyslot.status.in_(
                                    [KeyslotStatus.ACTIVE.value, KeyslotStatus.PENDING.value]
                                ),
                            )
                            .limit(len(ALL_DOMAINS) + 1)
                        )
                    )
                    if len(rows) != len(ALL_DOMAINS) or {row.domain for row in rows} != set(
                        ALL_DOMAINS
                    ):
                        raise ValueError("Soul credential domains are incomplete or ambiguous")
                    domains = {}
                    for row in rows:
                        aad = soul_keyslot_aad(
                            core_id=str(manifest["core_id"]),
                            owner_id=roots.owner_id,
                            domain=row.domain,
                            key_version=row.key_version,
                            credential_generation=row.credential_generation,
                            wrapping_path=path,
                        )
                        domains[row.domain] = unwrap_keyslot_secret(
                            credential, keyslots._soul_row_record(row), aad
                        )
                opened.append(
                    keyslots.UnlockedKeyHierarchy(
                        scope=roots.scope,
                        owner_id=roots.owner_id,
                        credential_generation=roots.credential_generation,
                        sqlcipher_key=roots.sqlcipher_key,
                        soul_domains=domains,
                        frks=roots.frks,
                    )
                )
            finally:
                engine.dispose()
        password_keys, recovery_keys = opened
        if (
            not hmac.compare_digest(password_keys.sqlcipher_key, recovery_keys.sqlcipher_key)
            or password_keys.soul_domains != recovery_keys.soul_domains
            or set(password_keys.frks) != set(recovery_keys.frks)
            or any(
                not password_keys.frks[version].matches(recovery_keys.frks[version])
                for version in password_keys.frks
            )
        ):
            raise ValueError("Password and recovery opened different compartment keys")
        return password_keys
    except (
        OSError,
        ValueError,
        RuntimeError,
        KeyError,
        SQLAlchemyError,
        sqlcipher3.Error,
        InvalidTag,
    ) as exc:
        raise MaintenanceError(
            "Both credentials must independently reopen the full Core Soul and Filesystem hierarchy."
        ) from exc


def _reauthenticate(
    session: Any, *, password: str, recovery_phrase: str
) -> tuple[dict[str, Any], list[object]]:
    import anima_core

    from anima_server.services.core import get_owner_user_id
    from anima_server.services.corefs import keyslots, rotation
    from anima_server.services.corefs.authority import read_authority_record
    from anima_server.services.corefs.types import KeyslotStatus, PayloadScope, WrappingPath

    require_maintenance_session(session)
    read_authority_record()
    manifest = rotation._manifest()
    if get_owner_user_id() != session.user_id or not password or not recovery_phrase:
        raise MaintenanceError("Maintenance requires the current Core owner's credentials.")
    for path in (WrappingPath.PASSWORD, WrappingPath.RECOVERY):
        if keyslots.get_active_manifest_scope(path) is not PayloadScope.FULL:
            raise MaintenanceError("Maintenance requires full-compartment credentials.")
    require_idle_operations(manifest)
    from anima_server.services.core import get_core_dir

    reopened = _verify_full_core_reopen(
        get_core_dir(),
        manifest=manifest,
        password=password,
        recovery_phrase=recovery_phrase,
    )
    if reopened.soul_domains != session.deks:
        raise MaintenanceError("Reopened full Core does not match the current unlock session.")
    state = rotation._rotation_state(manifest)
    versions = {
        int(state["active_version"]),
        *(int(item) for item in state.get("decrypt_only_versions", [])),
    }
    try:
        password_roots = rotation._open_roots(
            manifest,
            credential=password,
            wrapping_path=WrappingPath.PASSWORD,
            statuses={KeyslotStatus.ACTIVE, KeyslotStatus.DECRYPT_ONLY},
            versions=versions,
        )
        recovery_roots = rotation._open_roots(
            manifest,
            credential=recovery_phrase,
            wrapping_path=WrappingPath.RECOVERY,
            statuses={KeyslotStatus.ACTIVE, KeyslotStatus.DECRYPT_ONLY},
            versions=versions,
        )
        if not rotation._roots_match(password_roots, recovery_roots):
            raise ValueError("Credential root mismatch")
        keys = [
            anima_core.corefs_derive_subkeys(root, version)
            for version, root in sorted(password_roots.items())
        ]
    except (ValueError, RuntimeError, KeyError) as exc:
        raise MaintenanceError(
            "Current password and recovery phrase could not authenticate the full Core."
        ) from exc
    return manifest, keys


def _native_inventory(
    session: Any,
    keys: list[object],
    request: dict[str, object],
    *,
    allow_blockers: bool = False,
    continuation: str | None = None,
) -> dict[str, Any]:
    purge = "targetStableId" in request
    function = getattr(
        session.corefs_session,
        "maintenance_purge_inventory_v1" if purge else "maintenance_inventory_v1",
        None,
    )
    if not callable(function):
        raise MaintenanceError("The native CoreFS maintenance implementation is unavailable.")
    try:
        value = function(
            keys,
            json.dumps(request, sort_keys=True, separators=(",", ":")),
            str(_maintenance_directory()),
        )
    except (RuntimeError, ValueError, OSError) as exc:
        raise MaintenanceError(
            "CoreFS inventory could not be authenticated; no deletion is authorized."
        ) from exc
    if purge:
        if not isinstance(value, dict) or not isinstance(value.get("inventory"), dict):
            raise MaintenanceError("Native purge inventory is incomplete.")
        summary = dict(value["inventory"])
        summary.update(
            {
                "targetStableId": value.get("targetStableId"),
                "expectedRevision": value.get("expectedRevision"),
                "recursiveInventoryDigest": value.get("recursiveInventoryDigest"),
                "waivedGenerations": value.get("requiredWaivedGenerations"),
                "targetObjectCount": value.get("objectCount"),
            }
        )
        if value.get("blockers"):
            summary["blockers"] = [*summary.get("blockers", []), *value["blockers"]]
        if not _is_digest(summary["recursiveInventoryDigest"]) or not isinstance(
            summary["waivedGenerations"], list
        ):
            raise MaintenanceError("Native purge inventory is incomplete.")
        if (
            summary["targetStableId"] != request["targetStableId"]
            or summary["expectedRevision"] != request["expectedRevision"]
            or any(
                not isinstance(item, int) or isinstance(item, bool) or item <= 0
                for item in summary["waivedGenerations"]
            )
            or len(set(summary["waivedGenerations"])) != len(summary["waivedGenerations"])
        ):
            raise MaintenanceError(
                "Native purge inventory does not match the selected trash revision."
            )
        value = summary
    return require_complete_inventory(
        value, allow_blockers=allow_blockers, continuation=continuation
    )


def _native_request(*, now_ms: int, parameters: Any | None = None) -> dict[str, object]:
    result: dict[str, object] = {
        "retentionDays": DEFAULT_RETENTION_DAYS,
        "nowMs": now_ms,
        "pins": [],
    }
    if parameters is not None and parameters.action == "purge":
        result.update(
            {
                "targetStableId": parameters.targetStableId,
                "expectedRevision": parameters.expectedRevision,
            }
        )
    return result


def _hash_archive(path: Path) -> str:
    from anima_server.services.corefs.archive_transfer import _is_multipart_controller
    from anima_server.services.corefs.transfer_jobs import _import_source_bytes

    if path.is_symlink() or not path.is_file():
        raise MaintenanceError("Backup must be a supported regular ANIMA CORE archive.")
    files = [path]
    if _is_multipart_controller(path):
        _import_source_bytes(path)
        files.extend(sorted(child for child in path.parent.iterdir() if child != path))
    digest = hashlib.sha256()
    for candidate in files:
        stat = candidate.lstat()
        if candidate.is_symlink() or getattr(stat, "st_file_attributes", 0) & 0x400:
            raise MaintenanceError("Backup may not use symbolic links or reparse points.")
        digest.update(candidate.name.encode())
        digest.update(stat.st_size.to_bytes(8, "big"))
        with candidate.open("rb") as source:
            for chunk in iter(lambda: source.read(8 * 1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def _verify_current_backup(
    *, manifest: dict[str, Any], inventory: Mapping[str, Any], payload: Any
) -> str:
    import anima_core

    from anima_server.services.core import get_core_dir
    from anima_server.services.corefs.archive_transfer import (
        CoreArchivePayloadKind,
        _filesystem_checkpoint,
        stage_core_archive_v2,
    )
    from anima_server.services.corefs.rotation import _open_roots, _roots_match
    from anima_server.services.corefs.types import KeyslotStatus, WrappingPath

    candidate = Path(payload.backupPath).expanduser()
    if not candidate.is_absolute() or candidate.is_symlink():
        raise MaintenanceError(
            "Choose an absolute path to a verified backup outside the live Core."
        )
    try:
        archive = candidate.resolve(strict=True)
        if archive.is_relative_to(get_core_dir().resolve()):
            raise MaintenanceError("A backup inside the live Core cannot authorize maintenance.")
        before = _hash_archive(archive)
        with tempfile.TemporaryDirectory(
            prefix="verify-backup-", dir=_maintenance_directory()
        ) as temporary:
            result = stage_core_archive_v2(
                archive,
                passphrase=payload.backupPassphrase.get_secret_value(),
                staging_path=Path(temporary) / "staged",
            )
            saved = result.inventory
            operation = inventory.get("operation")
            source_generation = inventory["headGeneration"]
            source_hash = inventory["headCatalogHash"]
            if isinstance(operation, dict) and operation.get("state") not in {
                "complete",
                "completed",
                "cancelled",
            }:
                source_generation = operation.get("sourceHeadGeneration", source_generation)
                source_hash = operation.get("sourceHeadCatalogHash", source_hash)
            if (
                saved.payload_kind is not CoreArchivePayloadKind.FULL
                or saved.core_id != manifest["core_id"]
                or saved.owner_id != manifest["owner_id"]
                or saved.filesystem_generation != source_generation
            ):
                raise MaintenanceError(
                    "A verified full backup of the current Core generation is required."
                )
            staged_manifest = json.loads(
                (result.staging_path / "manifest.json").read_text(encoding="utf-8")
            )
            live_soul = _verify_full_core_reopen(
                get_core_dir(),
                manifest=manifest,
                password=payload.currentPassword.get_secret_value(),
                recovery_phrase=payload.recoveryPhrase.get_secret_value(),
            )
            backup_soul = _verify_full_core_reopen(
                result.staging_path,
                manifest=staged_manifest,
                password=payload.currentPassword.get_secret_value(),
                recovery_phrase=payload.recoveryPhrase.get_secret_value(),
            )
            if (
                not hmac.compare_digest(live_soul.sqlcipher_key, backup_soul.sqlcipher_key)
                or live_soul.soul_domains != backup_soul.soul_domains
            ):
                raise MaintenanceError("Backup Soul keys do not match the current Core.")
            active_version = int(manifest["frk_rotation"]["active_version"])
            statuses = {KeyslotStatus.ACTIVE}
            current = _open_roots(
                manifest,
                credential=payload.currentPassword.get_secret_value(),
                wrapping_path=WrappingPath.PASSWORD,
                statuses=statuses,
                versions={active_version},
            )
            password = _open_roots(
                staged_manifest,
                credential=payload.currentPassword.get_secret_value(),
                wrapping_path=WrappingPath.PASSWORD,
                statuses=statuses,
                versions={active_version},
            )
            recovery = _open_roots(
                staged_manifest,
                credential=payload.recoveryPhrase.get_secret_value(),
                wrapping_path=WrappingPath.RECOVERY,
                statuses=statuses,
                versions={active_version},
            )
            if not _roots_match(current, password) or not _roots_match(password, recovery):
                raise MaintenanceError(
                    "Backup does not reopen with the current active-generation credentials."
                )
            staged_native = anima_core.CorefsSession(
                str(result.staging_path), str(staged_manifest["core_id"])
            )
            try:
                staged_keys = anima_core.corefs_derive_subkeys(
                    password[active_version], active_version
                )
                # The extraction summary does not prove a catalog hash. Native
                # inventory authenticates staged HEAD and catalog independently,
                # without publishing live authority. Maintenance inventory also
                # verifies every retained object body with the full keyring.
                staged_checkpoint = _filesystem_checkpoint(
                    staged_native.archive_inventory_v2(staged_keys)
                )
                if staged_checkpoint != (source_generation, source_hash):
                    raise MaintenanceError(
                        "A verified full backup of the current Core generation is required."
                    )
                staged_inventory = require_complete_inventory(
                    staged_native.maintenance_inventory_v1(
                        [
                            anima_core.corefs_derive_subkeys(root, version)
                            for version, root in sorted(backup_soul.frks.items())
                        ],
                        json.dumps(_native_request(now_ms=int(time.time() * 1000))),
                    )
                )
                if (
                    staged_inventory["headGeneration"],
                    staged_inventory["headCatalogHash"],
                ) != staged_checkpoint:
                    raise MaintenanceError("Backup filesystem changed during authentication.")
            finally:
                try:
                    staged_native.close()
                finally:
                    # Dropping the coordinator releases its Windows directory
                    # handles before TemporaryDirectory removes the staged Core.
                    del staged_native
        if not hmac.compare_digest(before, _hash_archive(archive)):
            raise MaintenanceError("Backup changed during verification.")
        return before
    except MaintenanceError:
        raise
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        raise MaintenanceError("Backup authentication or active-generation reopen failed.") from exc


def _confirmation_binding(
    *,
    session: Any,
    manifest: Mapping[str, Any],
    parameters: Any,
    inventory: Mapping[str, Any],
    backup_digest: str,
    observed_at_ms: int,
) -> dict[str, object]:
    return {
        "coreId": manifest["core_id"],
        "ownerId": manifest["owner_id"],
        "userId": session.user_id,
        "sessionId": id(session),
        "credentialGeneration": manifest["active_password_credential_generation"],
        "recoveryGeneration": manifest["active_recovery_credential_generation"],
        "action": parameters.action,
        "parameters": parameters.model_dump(),
        "inventoryDigest": inventory["inventoryDigest"],
        "backupDigest": backup_digest,
        "observedAtMs": observed_at_ms,
    }


class CoreMaintenanceService:
    def __init__(self) -> None:
        self._lock = RLock()
        self._last_run: dict[tuple[str, int], dict[str, object]] = {}

    def status(self, session: Any) -> dict[str, object]:
        from anima_server.services.core import get_core_id

        with self._lock:
            return {
                "retentionDays": DEFAULT_RETENTION_DAYS,
                "unlockRequired": getattr(session, "corefs_keys", None) is None,
                "lastRun": self._last_run.get((get_core_id(), session.user_id)),
            }

    def inspect(self, session: Any, payload: Any) -> dict[str, object]:
        from anima_server.services.corefs.maintenance_coordination import CORE_MAINTENANCE_ADMISSION

        with CORE_MAINTENANCE_ADMISSION:
            manifest, keys = _reauthenticate(
                session,
                password=payload.currentPassword.get_secret_value(),
                recovery_phrase=payload.recoveryPhrase.get_secret_value(),
            )
            inventory = _native_inventory(
                session, keys, _native_request(now_ms=int(time.time() * 1000)), allow_blockers=True
            )
            return self._remember(session, manifest, inventory)

    def prepare(self, session: Any, payload: Any) -> dict[str, object]:
        from anima_server.services.corefs.maintenance_coordination import CORE_MAINTENANCE_ADMISSION

        with CORE_MAINTENANCE_ADMISSION:
            manifest, keys = _reauthenticate(
                session,
                password=payload.currentPassword.get_secret_value(),
                recovery_phrase=payload.recoveryPhrase.get_secret_value(),
            )
            observed = int(time.time() * 1000)
            inventory = _native_inventory(
                session,
                keys,
                _native_request(now_ms=observed, parameters=payload.parameters),
                continuation=payload.parameters.action,
            )
            self._require_retirement_proof(manifest, inventory, payload.parameters)
            backup_digest = _verify_current_backup(
                manifest=manifest, inventory=inventory, payload=payload
            )
            binding = _confirmation_binding(
                session=session,
                manifest=manifest,
                parameters=payload.parameters,
                inventory=inventory,
                backup_digest=backup_digest,
                observed_at_ms=observed,
            )
            confirmation = maintenance_confirmations.issue(binding)
            return {
                "confirmation": confirmation,
                "expiresInSeconds": _CONFIRMATION_TTL,
                "expiresAtMs": int(time.time() * 1000) + _CONFIRMATION_TTL * 1000,
                "inventory": self._remember(session, manifest, inventory),
                "observedAtMs": observed,
                "parameters": payload.parameters.model_dump(),
                "backupVerified": True,
            }

    def execute(self, session: Any, payload: Any) -> dict[str, object]:
        from anima_server.services.corefs.maintenance_coordination import CORE_MAINTENANCE_ADMISSION

        with CORE_MAINTENANCE_ADMISSION:
            manifest, keys = _reauthenticate(
                session,
                password=payload.currentPassword.get_secret_value(),
                recovery_phrase=payload.recoveryPhrase.get_secret_value(),
            )
            request = _native_request(now_ms=payload.observedAtMs, parameters=payload.parameters)
            inventory = _native_inventory(
                session, keys, request, continuation=payload.parameters.action
            )
            if inventory["inventoryDigest"] != payload.inventoryDigest:
                raise MaintenanceError("Core inventory changed; inspect and confirm again.")
            self._require_retirement_proof(manifest, inventory, payload.parameters)
            backup_digest = _verify_current_backup(
                manifest=manifest, inventory=inventory, payload=payload
            )
            binding = _confirmation_binding(
                session=session,
                manifest=manifest,
                parameters=payload.parameters,
                inventory=inventory,
                backup_digest=backup_digest,
                observed_at_ms=payload.observedAtMs,
            )
            maintenance_confirmations.consume(payload.confirmation, binding)
            if payload.parameters.action == "retire-key":
                token = self._retire_key(session, manifest, keys, inventory, payload)
                return {
                    "success": True,
                    "inventory": self._remember(session, manifest, inventory),
                    "unlockToken": token,
                    "retiredFrkVersion": payload.parameters.retiringFrkVersion,
                }
            native = getattr(session.corefs_session, "maintenance_prune_v1", None)
            if payload.parameters.action == "refresh-checkpoint":
                native = getattr(session.corefs_session, "maintenance_reanchor_v1", None)
            if payload.parameters.action == "purge":
                native = getattr(session.corefs_session, "maintenance_purge_v1", None)
                request.update(
                    {
                        "recursiveInventoryDigest": inventory.get("recursiveInventoryDigest"),
                        "waivedGenerations": inventory.get("waivedGenerations", []),
                    }
                )
            if not callable(native):
                raise MaintenanceError("Native maintenance operation is unavailable.")
            if payload.parameters.action in {"resume", "cancel"}:
                operation = inventory.get("operation")
                if not isinstance(operation, dict):
                    raise MaintenanceError(
                        "No authenticated maintenance operation is available to resume or cancel."
                    )
                native = getattr(
                    session.corefs_session,
                    {
                        "prune": "maintenance_prune_v1",
                        "purge": "maintenance_purge_v1",
                        "reanchor": "maintenance_reanchor_v1",
                    }.get(operation.get("kind"), ""),
                    None,
                )
            if not callable(native):
                raise MaintenanceError("Native maintenance operation is unavailable.")
            request.update(
                {
                    "expectedInventoryDigest": inventory["inventoryDigest"],
                    "action": {
                        "prune": "begin",
                        "purge": "begin",
                        "refresh-checkpoint": "begin",
                    }.get(payload.parameters.action, payload.parameters.action),
                    "maxItems": 128,
                }
            )
            try:
                result = native(
                    keys,
                    str(_maintenance_directory()),
                    json.dumps(request, sort_keys=True, separators=(",", ":")),
                )
            except (OSError, RuntimeError, ValueError) as exc:
                raise MaintenanceError(
                    "Maintenance did not finish; inspect authenticated progress before resuming."
                ) from exc
            result = require_complete_inventory(result, allow_blockers=True)
            return {"success": True, "inventory": self._remember(session, manifest, result)}

    def _remember(
        self, session: Any, manifest: Mapping[str, Any], inventory: Mapping[str, object]
    ) -> dict[str, object]:
        result = public_inventory(inventory)
        with self._lock:
            self._last_run[(str(manifest["core_id"]), session.user_id)] = result
        return result

    @staticmethod
    def _require_retirement_proof(
        manifest: Mapping[str, Any], inventory: Mapping[str, Any], parameters: Any
    ) -> None:
        if parameters.action != "retire-key":
            return
        retiring = parameters.retiringFrkVersion
        state = manifest["frk_rotation"]
        if retiring == state["active_version"] or retiring not in state.get(
            "decrypt_only_versions", []
        ):
            raise MaintenanceError("Only an existing decrypt-only FRK can be retired.")
        if retiring in inventory["requiredFrkVersions"]:
            raise MaintenanceError(
                "Retained catalogs, objects, or operation pins still require this key."
            )

    @staticmethod
    def _retire_key(
        session: Any,
        manifest: dict[str, Any],
        keys: list[object],
        inventory: Mapping[str, Any],
        payload: Any,
    ) -> str:
        from copy import deepcopy

        from anima_server.services.core import update_core_manifest
        from anima_server.services.corefs import keyslots, rotation
        from anima_server.services.corefs.types import KeyPurpose, KeyslotStatus, WrappingPath
        from anima_server.services.sessions import unlock_session_store

        version = payload.parameters.retiringFrkVersion
        before = deepcopy(manifest)
        slots = keyslots._manifest_slots(manifest)
        removed = [
            slot
            for slot in slots
            if slot.purpose is KeyPurpose.FILESYSTEM_ROOT and slot.frk_version == version
        ]
        if not removed or any(slot.status is not KeyslotStatus.DECRYPT_ONLY for slot in removed):
            raise MaintenanceError("Retiring keyslots are incomplete or still active.")
        kept = [slot for slot in slots if slot not in removed]
        expected = _binding_digest({"manifest": manifest})

        def retire(value: dict[str, Any]) -> None:
            if not hmac.compare_digest(expected, _binding_digest({"manifest": value})):
                raise MaintenanceError("Manifest changed before key retirement.")
            value["keyslots"] = [slot.to_dict() for slot in kept]
            value["frk_rotation"]["decrypt_only_versions"] = [
                item
                for item in value["frk_rotation"].get("decrypt_only_versions", [])
                if item != version
            ]

        acquire_guard = getattr(session.corefs_session, "maintenance_lock_v1", None)
        if not callable(acquire_guard):
            raise MaintenanceError("Native Core-wide retirement locking is unavailable.")
        request = _native_request(now_ms=payload.observedAtMs)
        request["expectedInventoryDigest"] = inventory["inventoryDigest"]
        guard = acquire_guard(
            keys,
            json.dumps(request, sort_keys=True, separators=(",", ":")),
            str(_maintenance_directory()),
        )
        with guard:
            published = False
            try:
                update_core_manifest(retire)
                published = True
                updated = rotation._manifest()
                from anima_server.services.core import get_core_dir

                reopened = _verify_full_core_reopen(
                    get_core_dir(),
                    manifest=updated,
                    password=payload.currentPassword.get_secret_value(),
                    recovery_phrase=payload.recoveryPhrase.get_secret_value(),
                )
                if reopened.soul_domains != session.deks:
                    raise MaintenanceError("The full Core changed during key retirement.")
                state = rotation._rotation_state(updated)
                active = int(state["active_version"])
                roots_password = rotation._open_roots(
                    updated,
                    credential=payload.currentPassword.get_secret_value(),
                    wrapping_path=WrappingPath.PASSWORD,
                    statuses={KeyslotStatus.ACTIVE},
                    versions={active},
                )
                roots_recovery = rotation._open_roots(
                    updated,
                    credential=payload.recoveryPhrase.get_secret_value(),
                    wrapping_path=WrappingPath.RECOVERY,
                    statuses={KeyslotStatus.ACTIVE},
                    versions={active},
                )
                if not rotation._roots_match(roots_password, roots_recovery):
                    raise MaintenanceError("Active credentials did not reopen after retirement.")
                remaining = [key for key in keys if key.frk_version != version]
                checked = require_complete_inventory(guard.verify_inventory_v1(remaining))
                if (
                    checked["headGeneration"] != inventory["headGeneration"]
                    or checked["headCatalogHash"] != inventory["headCatalogHash"]
                    or version in checked["requiredFrkVersions"]
                ):
                    raise MaintenanceError("Core did not reopen without the retired key.")
            except BaseException:
                if published:

                    def restore(value: dict[str, Any]) -> None:
                        value.clear()
                        value.update(deepcopy(before))

                    update_core_manifest(restore)
                raise
        return unlock_session_store.replace_user(
            session.user_id,
            session.deks,
            corefs_keys=session.corefs_keys,
            preserve_existing_tokens=False,
        )


core_maintenance = CoreMaintenanceService()
