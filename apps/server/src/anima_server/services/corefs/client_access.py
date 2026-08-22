"""Device-local installed-client identities, grants, and short-lived capabilities."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any, Literal
from uuid import uuid4

from anima_server.config import settings
from anima_server.services.core import get_core_id

ClientScope = Literal["none", "read", "write", "manage"]
InstallationStatus = Literal[
    "pending",
    "approved",
    "reapproval_required",
    "collision",
    "revoked",
]

_VERSION = 1
_MAX_REGISTRY_BYTES = 4 * 1024 * 1024
_MAX_INSTALLATIONS = 256
_MAX_GRANTS = 10_000
_MAX_CAPABILITIES = 256
_MAX_CAPABILITY_TTL_SECONDS = 15
_MAX_FOLDER_ENTRIES = 25_000
_CLIENT_ID = re.compile(r"\A[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?\Z")
_PACKAGE_ID = re.compile(
    r"\A[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+\Z"
)
_DIGEST = re.compile(r"\Asha256:[0-9a-f]{64}\Z")
_SCOPE_ORDER = {"none": 0, "read": 1, "write": 2, "manage": 3}
_lock = RLock()


class ClientAccessError(RuntimeError):
    pass


class ClientGrantRequired(ClientAccessError):
    pass


class ClientReapprovalRequired(ClientAccessError):
    pass


@dataclass(frozen=True, slots=True)
class VerifiedClientInstallation:
    client_id: str
    package_id: str
    display_name: str
    version: str
    install_digest: str
    publisher_identity: str | None
    publisher_verified: bool
    declared_roles: tuple[str, ...] = ()
    declared_metadata_keys: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CoreFsFolderGrantTarget:
    stable_id: str
    path: str
    role: str | None


@dataclass(frozen=True, slots=True)
class ClientCapabilityIdentity:
    installation_id: str
    client_id: str
    package_id: str
    install_digest: str
    user_id: int


@dataclass(frozen=True, slots=True)
class _CapabilityRecord:
    installation_id: str
    client_id: str
    package_id: str
    install_digest: str
    user_id: int
    grant_generation: int
    session_ids: frozenset[int]
    expires_at: float


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _registry_path() -> Path:
    if not settings.runtime_instance_data_dir:
        raise ClientAccessError("Runtime instance is not bound.")
    return Path(settings.runtime_instance_data_dir) / "config" / "corefs-client-access.json"


def _local_instance_id() -> str:
    path = Path(settings.runtime_instance_data_dir)
    if not path.name:
        raise ClientAccessError("Runtime instance identity is unavailable.")
    return path.name


def _empty_registry() -> dict[str, Any]:
    return {
        "version": _VERSION,
        "coreId": get_core_id(),
        "localInstanceId": _local_instance_id(),
        "installations": [],
    }


def _validate_namespaced_values(
    values: object,
    *,
    client_id: str,
    field: str,
) -> list[str]:
    if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
        raise ClientAccessError(f"Client {field} are invalid.")
    expected = f"client:{client_id}:"
    canonical = sorted(set(values))
    if len(canonical) != len(values) or any(
        not item.startswith(expected)
        or item == expected
        or item.startswith("core.")
        or len(item.encode("utf-8")) > 255
        or any(char.isspace() or ord(char) < 32 for char in item)
        for item in canonical
    ):
        raise ClientAccessError(f"Client {field} escape the declared namespace.")
    return canonical


def _validate_grant(raw: object) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ClientAccessError("Client grant is invalid.")
    stable_id = raw.get("folderStableId")
    scope = raw.get("scope")
    approved_digest = raw.get("approvedDigest")
    generation = raw.get("generation")
    if (
        not isinstance(stable_id, str)
        or not stable_id
        or len(stable_id.encode("utf-8")) > 255
        or scope not in _SCOPE_ORDER
        or scope == "none"
        or not isinstance(approved_digest, str)
        or not _DIGEST.fullmatch(approved_digest)
        or isinstance(generation, bool)
        or not isinstance(generation, int)
        or generation < 1
        or not isinstance(raw.get("updatedAt"), str)
        or (raw.get("lastUsedAt") is not None and not isinstance(raw.get("lastUsedAt"), str))
    ):
        raise ClientAccessError("Client grant is invalid.")
    return raw


def _validate_installation(raw: object) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ClientAccessError("Client installation is invalid.")
    client_id = raw.get("clientId")
    package_id = raw.get("packageId")
    digest = raw.get("installDigest")
    publisher = raw.get("publisher")
    grants = raw.get("grants")
    if (
        not isinstance(raw.get("installationId"), str)
        or not raw["installationId"]
        or not isinstance(client_id, str)
        or not _CLIENT_ID.fullmatch(client_id)
        or not isinstance(package_id, str)
        or not _PACKAGE_ID.fullmatch(package_id)
        or not isinstance(raw.get("displayName"), str)
        or not raw["displayName"]
        or not isinstance(raw.get("packageVersion"), str)
        or not raw["packageVersion"]
        or not isinstance(digest, str)
        or not _DIGEST.fullmatch(digest)
        or raw.get("status")
        not in {"pending", "approved", "reapproval_required", "collision", "revoked"}
        or isinstance(raw.get("grantGeneration"), bool)
        or not isinstance(raw.get("grantGeneration"), int)
        or raw["grantGeneration"] < 1
        or not isinstance(grants, list)
    ):
        raise ClientAccessError("Client installation is invalid.")
    if publisher is not None and (
        not isinstance(publisher, dict)
        or not isinstance(publisher.get("identity"), str)
        or not publisher["identity"]
        or not isinstance(publisher.get("verified"), bool)
    ):
        raise ClientAccessError("Client publisher identity is invalid.")
    _validate_namespaced_values(raw.get("declaredRoles"), client_id=client_id, field="roles")
    _validate_namespaced_values(
        raw.get("declaredMetadataKeys"), client_id=client_id, field="metadata keys"
    )
    validated_grants = [_validate_grant(item) for item in grants]
    if len({item["folderStableId"] for item in validated_grants}) != len(validated_grants):
        raise ClientAccessError("Client installation contains duplicate grants.")
    approved_digest = raw.get("approvedDigest")
    if approved_digest is not None and (
        not isinstance(approved_digest, str) or not _DIGEST.fullmatch(approved_digest)
    ):
        raise ClientAccessError("Client approved digest is invalid.")
    for key in ("verifiedAt", "approvedAt", "lastUsedAt"):
        if raw.get(key) is not None and not isinstance(raw.get(key), str):
            raise ClientAccessError("Client installation audit metadata is invalid.")
    return raw


def _load(path: Path | None = None) -> dict[str, Any]:
    path = path or _registry_path()
    if not path.exists():
        return _empty_registry()
    try:
        if path.stat().st_size > _MAX_REGISTRY_BYTES:
            raise ClientAccessError("Client access registry exceeds its size bound.")
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ClientAccessError("Client access registry is unreadable.") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("version") != _VERSION
        or payload.get("coreId") != get_core_id()
        or payload.get("localInstanceId") != _local_instance_id()
        or not isinstance(payload.get("installations"), list)
        or len(payload["installations"]) > _MAX_INSTALLATIONS
    ):
        raise ClientAccessError("Client access registry binding is invalid.")
    installations = [_validate_installation(item) for item in payload["installations"]]
    if len({item["installationId"] for item in installations}) != len(installations):
        raise ClientAccessError("Client access registry contains duplicate installations.")
    if sum(len(item["grants"]) for item in installations) > _MAX_GRANTS:
        raise ClientAccessError("Client access registry exceeds its grant bound.")
    return payload


def _write(payload: dict[str, Any]) -> None:
    path = _registry_path()
    encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if len(encoded) > _MAX_REGISTRY_BYTES:
        raise ClientAccessError("Client access registry exceeds its size bound.")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    if _load(path) != payload:
        raise ClientAccessError("Client access registry verification failed.")


def _identity_key(raw: dict[str, Any]) -> tuple[str, str, str | None]:
    publisher = raw.get("publisher")
    return (
        str(raw["clientId"]),
        str(raw["packageId"]),
        str(publisher["identity"]) if isinstance(publisher, dict) else None,
    )


def _validate_verified_identity(identity: VerifiedClientInstallation) -> None:
    if not _CLIENT_ID.fullmatch(identity.client_id):
        raise ClientAccessError("Client ID is invalid.")
    if not _PACKAGE_ID.fullmatch(identity.package_id):
        raise ClientAccessError("Client package ID must use reverse-DNS form.")
    if not identity.display_name.strip() or len(identity.display_name.encode()) > 256:
        raise ClientAccessError("Client display name is invalid.")
    if not identity.version.strip() or len(identity.version.encode()) > 128:
        raise ClientAccessError("Client package version is invalid.")
    if not _DIGEST.fullmatch(identity.install_digest):
        raise ClientAccessError("Client install digest is invalid.")
    if identity.publisher_verified and not identity.publisher_identity:
        raise ClientAccessError("Verified publisher identity is missing.")
    if identity.publisher_identity and len(identity.publisher_identity.encode()) > 512:
        raise ClientAccessError("Client publisher identity is invalid.")
    _validate_namespaced_values(
        list(identity.declared_roles), client_id=identity.client_id, field="roles"
    )
    _validate_namespaced_values(
        list(identity.declared_metadata_keys),
        client_id=identity.client_id,
        field="metadata keys",
    )


def register_verified_installation(
    identity: VerifiedClientInstallation,
) -> str:
    """Record identity supplied by a trusted platform package verifier.

    There is deliberately no user/client HTTP endpoint for this operation.
    Registration establishes identity only; it never approves a package or a grant.
    """
    _validate_verified_identity(identity)
    publisher = (
        {
            "identity": identity.publisher_identity,
            "verified": identity.publisher_verified,
        }
        if identity.publisher_identity
        else None
    )
    key = (identity.client_id, identity.package_id, identity.publisher_identity)
    now = _now()
    with _lock:
        payload = _load()
        installations = payload["installations"]
        existing = next((item for item in installations if _identity_key(item) == key), None)
        if existing is not None:
            if existing["installDigest"] != identity.install_digest:
                existing["installDigest"] = identity.install_digest
                existing["status"] = "reapproval_required"
                existing["grantGeneration"] += 1
                existing["lastUsedAt"] = None
            existing.update(
                {
                    "displayName": identity.display_name.strip(),
                    "packageVersion": identity.version.strip(),
                    "publisher": publisher,
                    "declaredRoles": sorted(identity.declared_roles),
                    "declaredMetadataKeys": sorted(identity.declared_metadata_keys),
                    "verifiedAt": now,
                }
            )
            _write(payload)
            return str(existing["installationId"])

        installation_id = uuid4().hex
        record: dict[str, Any] = {
            "installationId": installation_id,
            "clientId": identity.client_id,
            "packageId": identity.package_id,
            "displayName": identity.display_name.strip(),
            "packageVersion": identity.version.strip(),
            "installDigest": identity.install_digest,
            "publisher": publisher,
            "declaredRoles": sorted(identity.declared_roles),
            "declaredMetadataKeys": sorted(identity.declared_metadata_keys),
            "status": "pending",
            "approvedDigest": None,
            "grantGeneration": 1,
            "verifiedAt": now,
            "approvedAt": None,
            "lastUsedAt": None,
            "grants": [],
        }
        conflicts = [
            item
            for item in installations
            if item["status"] != "revoked"
            and (item["clientId"] == identity.client_id or item["packageId"] == identity.package_id)
        ]
        if conflicts:
            record["status"] = "collision"
            for conflict in conflicts:
                conflict["status"] = "collision"
                conflict["grantGeneration"] += 1
        installations.append(record)
        if len(installations) > _MAX_INSTALLATIONS:
            raise ClientAccessError("Client installation capacity reached.")
        _write(payload)
        return installation_id


def approve_installation(installation_id: str, *, confirmed: bool) -> None:
    if not confirmed:
        raise ClientAccessError("Package identity approval requires explicit confirmation.")
    with _lock:
        payload = _load()
        record = _find_installation(payload, installation_id)
        if record["status"] in {"collision", "revoked"}:
            raise ClientAccessError("Conflicting or revoked package identity cannot be approved.")
        record["status"] = "approved"
        record["approvedDigest"] = record["installDigest"]
        record["approvedAt"] = _now()
        record["grantGeneration"] += 1
        for grant in record["grants"]:
            grant["approvedDigest"] = record["installDigest"]
            grant["generation"] = record["grantGeneration"]
            grant["updatedAt"] = record["approvedAt"]
        _write(payload)


def set_folder_grant(
    installation_id: str,
    *,
    folder_stable_id: str,
    scope: ClientScope,
    confirmed: bool = False,
) -> None:
    if scope not in _SCOPE_ORDER:
        raise ClientAccessError("Client grant scope is invalid.")
    if not folder_stable_id or len(folder_stable_id.encode()) > 255:
        raise ClientAccessError("Client grant folder is invalid.")
    with _lock:
        payload = _load()
        record = _find_installation(payload, installation_id)
        _require_current_approval(record)
        grants = record["grants"]
        existing = next(
            (item for item in grants if item["folderStableId"] == folder_stable_id), None
        )
        previous_scope = existing["scope"] if existing is not None else "none"
        if _SCOPE_ORDER[scope] > _SCOPE_ORDER[previous_scope] and not confirmed:
            raise ClientAccessError("Grant expansion requires explicit confirmation.")
        if scope == previous_scope:
            return
        record["grantGeneration"] += 1
        now = _now()
        if scope == "none":
            record["grants"] = [
                item for item in grants if item["folderStableId"] != folder_stable_id
            ]
        elif existing is None:
            record["grants"].append(
                {
                    "folderStableId": folder_stable_id,
                    "scope": scope,
                    "approvedDigest": record["installDigest"],
                    "generation": record["grantGeneration"],
                    "updatedAt": now,
                    "lastUsedAt": None,
                }
            )
        else:
            existing.update(
                {
                    "scope": scope,
                    "approvedDigest": record["installDigest"],
                    "generation": record["grantGeneration"],
                    "updatedAt": now,
                }
            )
        if sum(len(item["grants"]) for item in payload["installations"]) > _MAX_GRANTS:
            raise ClientAccessError("Client grant capacity reached.")
        _write(payload)


def revoke_installation(installation_id: str) -> None:
    with _lock:
        payload = _load()
        record = _find_installation(payload, installation_id)
        record["status"] = "revoked"
        record["grantGeneration"] += 1
        record["grants"] = []
        for candidate in payload["installations"]:
            if candidate["status"] != "collision":
                continue
            conflict_remains = any(
                other is not candidate
                and other["status"] != "revoked"
                and (
                    other["clientId"] == candidate["clientId"]
                    or other["packageId"] == candidate["packageId"]
                )
                for other in payload["installations"]
            )
            if not conflict_remains:
                candidate["status"] = (
                    "reapproval_required" if candidate.get("approvedDigest") else "pending"
                )
                candidate["grantGeneration"] += 1
        _write(payload)


def _find_installation(payload: dict[str, Any], installation_id: str) -> dict[str, Any]:
    record = next(
        (
            item
            for item in payload["installations"]
            if hmac.compare_digest(str(item["installationId"]), installation_id)
        ),
        None,
    )
    if record is None:
        raise ClientAccessError("Client installation was not found.")
    return record


def _require_current_approval(record: dict[str, Any]) -> None:
    if record["status"] != "approved" or record.get("approvedDigest") != record.get(
        "installDigest"
    ):
        raise ClientReapprovalRequired("Client package requires approval or reapproval.")


def public_registry() -> list[dict[str, Any]]:
    with _lock:
        payload = _load()
        return json.loads(json.dumps(payload["installations"]))


def list_corefs_grant_folders(
    session: object,
    *,
    selected: object | None = None,
) -> tuple[CoreFsFolderGrantTarget, ...]:
    """Read the authenticated folder inventory without returning body content."""
    from anima_server.services.corefs import logical

    keys = getattr(session, "corefs_keys", None)
    corefs_session = getattr(session, "corefs_session", None)
    if keys is None or corefs_session is None:
        raise ClientAccessError("CoreFS key material is unavailable.")
    try:
        selected = selected or logical.select_validation_snapshot(
            corefs_session=corefs_session, keys=keys
        )
        generation = getattr(selected, "generation", None)
        if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
            raise ClientAccessError("CoreFS validation snapshot is invalid.")
        cursor: str | None = None
        folders: list[CoreFsFolderGrantTarget] = []
        seen_cursors: set[str] = set()
        while True:
            raw = logical.walk_v1(
                corefs_session=corefs_session,
                keys=keys,
                selected=selected,
                root="",
                cursor_after=cursor,
                page_size=1000,
                include_directories=True,
                response_bytes=10 * 1024 * 1024,
            )
            payload = json.loads(raw.decode("utf-8"))
            result = payload.get("result") if isinstance(payload, dict) else None
            if (
                not isinstance(result, dict)
                or result.get("generation") != generation
                or not isinstance(result.get("entries"), list)
                or result.get("errors") not in (None, [])
            ):
                raise ClientAccessError("CoreFS folder inventory is invalid or degraded.")
            for entry in result["entries"]:
                if not isinstance(entry, dict) or entry.get("kind") != "directory":
                    continue
                stable_id = entry.get("stableId")
                path = entry.get("path")
                role = entry.get("role")
                if (
                    not isinstance(stable_id, str)
                    or not stable_id
                    or not isinstance(path, str)
                    or (role is not None and not isinstance(role, str))
                ):
                    raise ClientAccessError("CoreFS folder inventory entry is invalid.")
                folders.append(CoreFsFolderGrantTarget(stable_id=stable_id, path=path, role=role))
                if len(folders) > _MAX_FOLDER_ENTRIES:
                    raise ClientAccessError("CoreFS folder inventory exceeds its bound.")
            next_cursor = result.get("nextCursor")
            if next_cursor is None:
                break
            if (
                not isinstance(next_cursor, dict)
                or next_cursor.get("generation") != generation
                or not isinstance(next_cursor.get("after"), str)
                or not next_cursor["after"]
                or next_cursor["after"] in seen_cursors
            ):
                raise ClientAccessError("CoreFS folder cursor is invalid.")
            cursor = next_cursor["after"]
            seen_cursors.add(cursor)
        if len({folder.stable_id for folder in folders}) != len(folders):
            raise ClientAccessError("CoreFS folder inventory contains duplicate stable IDs.")
        return tuple(folders)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ClientAccessError("CoreFS folder inventory could not be authenticated.") from exc


class ClientCapabilityBroker:
    def __init__(self) -> None:
        self._records: dict[bytes, _CapabilityRecord] = {}
        self._lock = RLock()

    def issue(
        self,
        *,
        audience: str,
        client_id: str,
        install_digest: str,
        user_id: int,
        active_sessions: tuple[object, ...],
        ttl_seconds: int = 15,
    ) -> tuple[str, int]:
        if audience != f"anima-mod:{client_id}":
            raise ClientAccessError("Client capability audience mismatch.")
        if user_id < 0 or not active_sessions:
            raise ClientAccessError("Client capability owner is locked.")
        if ttl_seconds < 1 or ttl_seconds > _MAX_CAPABILITY_TTL_SECONDS:
            raise ClientAccessError("Client capability lifetime is invalid.")
        with _lock:
            payload = _load()
            candidates = [
                item
                for item in payload["installations"]
                if item["clientId"] == client_id
                and item["installDigest"] == install_digest
                and item["status"] != "revoked"
            ]
            if len(candidates) != 1:
                raise ClientAccessError("Client package identity is missing or ambiguous.")
            record = candidates[0]
            _require_current_approval(record)
            if not record["grants"]:
                raise ClientGrantRequired("Client has no approved folder grant.")
            token = secrets.token_urlsafe(32)
            now = time.monotonic()
            with self._lock:
                self._purge_locked(now)
                if len(self._records) >= _MAX_CAPABILITIES:
                    raise ClientAccessError("Client capability capacity reached.")
                self._records[self._digest(token)] = _CapabilityRecord(
                    installation_id=record["installationId"],
                    client_id=record["clientId"],
                    package_id=record["packageId"],
                    install_digest=record["installDigest"],
                    user_id=user_id,
                    grant_generation=record["grantGeneration"],
                    session_ids=frozenset(id(session) for session in active_sessions),
                    expires_at=now + ttl_seconds,
                )
            return token, ttl_seconds

    def consume(self, *, token: str, user_id: int, session: object) -> ClientCapabilityIdentity:
        digest = self._digest(token)
        now = time.monotonic()
        with self._lock:
            self._purge_locked(now)
            capability = self._records.pop(digest, None)
        if capability is None:
            raise ClientAccessError("Client capability is invalid, expired, or already used.")
        if capability.user_id != user_id or id(session) not in capability.session_ids:
            raise ClientAccessError("Client capability unlock session mismatch.")
        with _lock:
            payload = _load()
            record = _find_installation(payload, capability.installation_id)
            _require_current_approval(record)
            if (
                record["clientId"] != capability.client_id
                or record["packageId"] != capability.package_id
                or record["installDigest"] != capability.install_digest
                or record["grantGeneration"] != capability.grant_generation
            ):
                raise ClientAccessError("Client capability was revoked or superseded.")
        return ClientCapabilityIdentity(
            installation_id=capability.installation_id,
            client_id=capability.client_id,
            package_id=capability.package_id,
            install_digest=capability.install_digest,
            user_id=capability.user_id,
        )

    def revoke_all(self) -> None:
        with self._lock:
            self._records.clear()

    @staticmethod
    def _digest(token: str) -> bytes:
        if not isinstance(token, str) or len(token) < 32 or len(token) > 256:
            raise ClientAccessError("Client capability token is invalid.")
        return hashlib.sha256(b"anima-corefs-client-capability-v1\0" + token.encode()).digest()

    def _purge_locked(self, now: float) -> None:
        self._records = {
            digest: record for digest, record in self._records.items() if record.expires_at > now
        }


client_capability_broker = ClientCapabilityBroker()


def authorize_client_path(
    identity: ClientCapabilityIdentity,
    *,
    folders: tuple[CoreFsFolderGrantTarget, ...],
    logical_path: str | None,
    required_scope: ClientScope,
    record_use: bool = False,
) -> str:
    if required_scope not in {"read", "write", "manage"}:
        raise ClientAccessError("Client operation scope is invalid.")
    with _lock:
        payload = _load()
        record = _find_installation(payload, identity.installation_id)
        _require_current_approval(record)
        if (
            record["clientId"] != identity.client_id
            or record["packageId"] != identity.package_id
            or record["installDigest"] != identity.install_digest
        ):
            raise ClientAccessError("Client package identity changed.")
        folder_by_id = {folder.stable_id: folder for folder in folders}
        candidates: list[tuple[int, dict[str, Any], CoreFsFolderGrantTarget]] = []
        for grant in record["grants"]:
            folder = folder_by_id.get(grant["folderStableId"])
            if folder is None or grant["approvedDigest"] != identity.install_digest:
                continue
            if (
                logical_path is None
                or not folder.path
                or logical_path == folder.path
                or logical_path.startswith(f"{folder.path}/")
            ):
                candidates.append((len(folder.path), grant, folder))
        if not candidates:
            raise ClientGrantRequired("Client has no grant for this folder.")
        _, grant, folder = max(candidates, key=lambda item: item[0])
        if _SCOPE_ORDER[grant["scope"]] < _SCOPE_ORDER[required_scope]:
            raise ClientGrantRequired("Client grant scope does not allow this operation.")
        if record_use:
            used_at = _now()
            record["lastUsedAt"] = used_at
            grant["lastUsedAt"] = used_at
            _write(payload)
        return folder.stable_id
