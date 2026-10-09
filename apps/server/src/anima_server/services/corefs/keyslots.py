from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Callable, Set
from dataclasses import dataclass, field, replace
from pathlib import Path

import anima_core
from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.models import SoulKeyslot
from anima_server.services.core import (
    ensure_core_manifest,
    get_manifest_path,
    update_core_manifest,
)
from anima_server.services.corefs.crypto import (
    manifest_keyslot_aad,
    soul_keyslot_aad,
    unwrap_keyslot_secret,
    wrap_keyslot_secret,
)
from anima_server.services.corefs.types import (
    KEYSLOT_ENVELOPE_VERSION,
    SUPPORTED_KDF_ALGORITHM,
    SUPPORTED_WRAP_ALGORITHM,
    KeyPurpose,
    KeyslotStatus,
    ManifestKeyslot,
    PayloadScope,
    WrappingPath,
)
from anima_server.services.crypto import (
    ARGON2_MEMORY_COST_KIB,
    ARGON2_PARALLELISM,
    ARGON2_TIME_COST,
    AUTH_TAG_LENGTH,
    IV_LENGTH,
    KEY_LENGTH,
    SALT_LENGTH,
    WrappedDekRecord,
)
from anima_server.services.data_crypto import ALL_DOMAINS

_MAX_MANIFEST_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class UnlockedKeyHierarchy:
    scope: PayloadScope
    owner_id: str
    credential_generation: int
    sqlcipher_key: bytes | None = field(repr=False)
    soul_domains: dict[str, bytes] = field(repr=False)
    frks: dict[int, object] = field(repr=False)


@dataclass(frozen=True, slots=True)
class UnlockedManifestRoots:
    scope: PayloadScope
    owner_id: str
    credential_generation: int
    sqlcipher_key: bytes | None = field(repr=False)
    frks: dict[int, object] = field(repr=False)


def validate_scope_completeness(
    scope: PayloadScope,
    *,
    purposes: Set[KeyPurpose],
    soul_domains: Set[str],
    required_soul_domains: Set[str],
    frk_versions: Set[int],
    required_frk_versions: Set[int],
) -> None:
    has_soul = KeyPurpose.SOUL in purposes
    has_fs = KeyPurpose.FILESYSTEM_ROOT in purposes

    if scope is PayloadScope.SOUL:
        if has_fs or frk_versions:
            raise ValueError("soul scope forbids Filesystem Root key material")
        if not has_soul:
            raise ValueError("soul scope is missing the SQLCipher Soul root")
    elif scope is PayloadScope.FS:
        if has_soul or soul_domains:
            raise ValueError("fs scope forbids Soul key material")
        if not has_fs:
            raise ValueError("fs scope is missing Filesystem Root key material")
    elif scope is PayloadScope.FULL:
        if not has_soul or not has_fs:
            raise ValueError("full scope requires both Soul and Filesystem Root keys")
    else:
        raise ValueError(f"unsupported payload scope: {scope}")

    if scope in {PayloadScope.FULL, PayloadScope.SOUL} and soul_domains != required_soul_domains:
        raise ValueError("incomplete Soul domain set")
    if scope in {PayloadScope.FULL, PayloadScope.FS} and frk_versions != required_frk_versions:
        raise ValueError("incomplete Filesystem Root key set")


def _required_frk_versions_from_manifest(manifest: dict[str, object]) -> set[int]:
    rotation = manifest.get("frk_rotation", {})
    if not isinstance(rotation, dict):
        raise ValueError("invalid FRK rotation state")
    try:
        required = {
            int(rotation["active_version"]),
            *(int(version) for version in rotation.get("decrypt_only_versions", [])),
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("invalid FRK rotation state") from exc
    if any(version <= 0 for version in required):
        raise ValueError("invalid FRK rotation state")
    return required


def _record_to_payload(record: WrappedDekRecord) -> dict[str, object]:
    return {
        "kdf_salt": record.kdf_salt,
        "kdf_time_cost": record.kdf_time_cost,
        "kdf_memory_cost_kib": record.kdf_memory_cost_kib,
        "kdf_parallelism": record.kdf_parallelism,
        "kdf_key_length": record.kdf_key_length,
        "wrap_iv": record.wrap_iv,
        "wrap_tag": record.wrap_tag,
        "wrapped_key": record.wrapped_dek,
    }


def _native_root_to_payload(wrapped: object) -> dict[str, object]:
    return {
        "kdf_salt": wrapped.kdf_salt,
        "kdf_time_cost": ARGON2_TIME_COST,
        "kdf_memory_cost_kib": ARGON2_MEMORY_COST_KIB,
        "kdf_parallelism": ARGON2_PARALLELISM,
        "kdf_key_length": KEY_LENGTH,
        "wrap_iv": wrapped.wrap_iv,
        "wrap_tag": wrapped.wrap_tag,
        "wrapped_key": wrapped.wrapped_key,
    }


def _record_from_payload(payload: dict[str, object]) -> WrappedDekRecord:
    expected = {
        "kdf_salt",
        "kdf_time_cost",
        "kdf_memory_cost_kib",
        "kdf_parallelism",
        "kdf_key_length",
        "wrap_iv",
        "wrap_tag",
        "wrapped_key",
    }
    if set(payload) != expected:
        raise ValueError("invalid wrapped keyslot fields")
    record = WrappedDekRecord(
        kdf_salt=str(payload["kdf_salt"]),
        kdf_time_cost=int(payload["kdf_time_cost"]),
        kdf_memory_cost_kib=int(payload["kdf_memory_cost_kib"]),
        kdf_parallelism=int(payload["kdf_parallelism"]),
        kdf_key_length=int(payload["kdf_key_length"]),
        wrap_iv=str(payload["wrap_iv"]),
        wrap_tag=str(payload["wrap_tag"]),
        wrapped_dek=str(payload["wrapped_key"]),
    )
    try:
        salt = base64.b64decode(record.kdf_salt, validate=True)
        iv = base64.b64decode(record.wrap_iv, validate=True)
        tag = base64.b64decode(record.wrap_tag, validate=True)
        ciphertext = base64.b64decode(record.wrapped_dek, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("unsupported keyslot KDF/wrap profile") from exc
    if (
        record.kdf_time_cost != ARGON2_TIME_COST
        or record.kdf_memory_cost_kib != ARGON2_MEMORY_COST_KIB
        or record.kdf_parallelism != ARGON2_PARALLELISM
        or record.kdf_key_length != KEY_LENGTH
        or len(salt) != SALT_LENGTH
        or len(iv) != IV_LENGTH
        or len(tag) != AUTH_TAG_LENGTH
        or len(ciphertext) != KEY_LENGTH
    ):
        raise ValueError("unsupported keyslot KDF/wrap profile")
    return record


def _native_root_from_payload(payload: dict[str, object]) -> object:
    record = _record_from_payload(payload)
    return anima_core.CorefsWrappedRootKey(
        record.kdf_salt,
        record.wrap_iv,
        record.wrap_tag,
        record.wrapped_dek,
    )


def _unwrap_manifest_slot(
    credential: str,
    slot: ManifestKeyslot,
    aad: bytes,
) -> bytes | object:
    if slot.purpose is KeyPurpose.FILESYSTEM_ROOT:
        return anima_core.corefs_unwrap_root_key(
            credential,
            _native_root_from_payload(slot.wrapped),
            aad,
        )
    return unwrap_keyslot_secret(credential, _record_from_payload(slot.wrapped), aad)


def _manifest_secret_matches(actual: bytes | object, expected: bytes | object) -> bool:
    if isinstance(actual, bytes) or isinstance(expected, bytes):
        return isinstance(actual, bytes) and isinstance(expected, bytes) and actual == expected
    return bool(actual.matches(expected))


def _manifest_slot(
    credential: str,
    secret: bytes | object,
    *,
    core_id: str,
    owner_id: str,
    purpose: KeyPurpose,
    wrapping_path: WrappingPath,
    status: KeyslotStatus,
    scope: PayloadScope,
    key_version: int,
    credential_generation: int,
    frk_version: int | None = None,
    object_key_epoch: int | None = None,
) -> ManifestKeyslot:
    aad = manifest_keyslot_aad(
        core_id=core_id,
        owner_id=owner_id,
        purpose=purpose,
        key_version=key_version,
        credential_generation=credential_generation,
        scope=scope,
        frk_version=frk_version,
        object_key_epoch=object_key_epoch,
        wrapping_path=wrapping_path,
    )
    if purpose is KeyPurpose.FILESYSTEM_ROOT:
        wrapped = anima_core.corefs_wrap_root_key(credential, secret, aad)
        wrapped_payload = _native_root_to_payload(wrapped)
    else:
        if not isinstance(secret, bytes):
            raise TypeError("Soul key material must be bytes")
        wrapped = wrap_keyslot_secret(credential, secret, aad)
        wrapped_payload = _record_to_payload(wrapped)
    slot = ManifestKeyslot(
        purpose=purpose,
        wrapping_path=wrapping_path,
        status=status,
        scope=scope,
        key_version=key_version,
        credential_generation=credential_generation,
        frk_version=frk_version,
        object_key_epoch=object_key_epoch,
        kdf_algorithm=SUPPORTED_KDF_ALGORITHM,
        wrap_algorithm=SUPPORTED_WRAP_ALGORITHM,
        envelope_version=KEYSLOT_ENVELOPE_VERSION,
        wrapped=wrapped_payload,
    )
    reopened = _unwrap_manifest_slot(credential, slot, aad)
    if not _manifest_secret_matches(reopened, secret):
        raise ValueError("manifest keyslot independent verification failed")
    return slot


def _soul_row_record(row: SoulKeyslot) -> WrappedDekRecord:
    if row.kdf_algorithm != SUPPORTED_KDF_ALGORITHM:
        raise ValueError(f"unsupported KDF algorithm: {row.kdf_algorithm}")
    if row.wrap_algorithm != SUPPORTED_WRAP_ALGORITHM:
        raise ValueError(f"unsupported wrapping algorithm: {row.wrap_algorithm}")
    if row.envelope_version != KEYSLOT_ENVELOPE_VERSION:
        raise ValueError(f"unsupported keyslot envelope version: {row.envelope_version}")
    return WrappedDekRecord(
        kdf_salt=row.kdf_salt,
        kdf_time_cost=row.kdf_time_cost,
        kdf_memory_cost_kib=row.kdf_memory_cost_kib,
        kdf_parallelism=row.kdf_parallelism,
        kdf_key_length=row.kdf_key_length,
        wrap_iv=row.wrap_iv,
        wrap_tag=row.wrap_tag,
        wrapped_dek=row.wrapped_dek,
    )


def _build_soul_row(
    credential: str,
    secret: bytes,
    *,
    core_id: str,
    owner_id: str,
    domain: str,
    wrapping_path: WrappingPath,
    status: KeyslotStatus,
    key_version: int,
    credential_generation: int,
) -> SoulKeyslot:
    aad = soul_keyslot_aad(
        core_id=core_id,
        owner_id=owner_id,
        domain=domain,
        key_version=key_version,
        credential_generation=credential_generation,
        wrapping_path=wrapping_path,
    )
    wrapped = wrap_keyslot_secret(credential, secret, aad)
    if unwrap_keyslot_secret(credential, wrapped, aad) != secret:
        raise ValueError("Soul keyslot independent verification failed")
    return SoulKeyslot(
        owner_id=owner_id,
        domain=domain,
        wrapping_path=wrapping_path.value,
        key_version=key_version,
        credential_generation=credential_generation,
        status=status.value,
        kdf_algorithm=SUPPORTED_KDF_ALGORITHM,
        wrap_algorithm=SUPPORTED_WRAP_ALGORITHM,
        envelope_version=KEYSLOT_ENVELOPE_VERSION,
        kdf_salt=wrapped.kdf_salt,
        kdf_time_cost=wrapped.kdf_time_cost,
        kdf_memory_cost_kib=wrapped.kdf_memory_cost_kib,
        kdf_parallelism=wrapped.kdf_parallelism,
        kdf_key_length=wrapped.kdf_key_length,
        wrap_iv=wrapped.wrap_iv,
        wrap_tag=wrapped.wrap_tag,
        wrapped_dek=wrapped.wrapped_dek,
    )


def prepare_initial_manifest_roots(
    *,
    user_id: int,
    password: str,
    recovery_phrase: str,
    sqlcipher_key: bytes,
    frk: object | None = None,
) -> object:
    """Durably authenticate pending roots before the first encrypted Soul write."""
    from datetime import UTC, datetime

    from anima_server.services.corefs.authority import PORTABLE_CORE_RELEASE

    manifest = ensure_core_manifest()
    if manifest.get("keyslots") and manifest.get("initial_registration_state") != "pending":
        raise ValueError("Core key hierarchy is already provisioned")
    protected_fields = (
        "core_id",
        "owner_id",
        "owner_user_id",
        "owner_binding",
        "keyslots",
        "initial_registration_state",
        "initial_registration_created_at",
        "active_password_credential_generation",
        "active_recovery_credential_generation",
        "frk_rotation",
    )
    expected = json.dumps(
        {field: manifest.get(field) for field in protected_fields}, sort_keys=True
    )
    if manifest.get("initial_registration_state") == "pending":
        roots = unlock_manifest_key_hierarchy(
            credential=password,
            wrapping_path=WrappingPath.PASSWORD,
            generation=1,
            status=KeyslotStatus.PENDING,
        )
        if (
            manifest.get("owner_user_id") != user_id
            or roots.sqlcipher_key != sqlcipher_key
            or frk is None
            or set(roots.frks) != {1}
            or not roots.frks[1].matches(frk)
        ):
            raise ValueError("Initial pending registration roots or owner changed")
    frk = frk if frk is not None else anima_core.corefs_generate_root_key()
    slots = [
        _manifest_slot(
            credential,
            secret,
            core_id=str(manifest["core_id"]),
            owner_id=str(manifest["owner_id"]),
            purpose=purpose,
            wrapping_path=path,
            status=KeyslotStatus.PENDING,
            scope=PayloadScope.FULL,
            key_version=1,
            credential_generation=1,
            frk_version=version,
            object_key_epoch=epoch,
        )
        for credential, path in (
            (password, WrappingPath.PASSWORD),
            (recovery_phrase, WrappingPath.RECOVERY),
        )
        for secret, purpose, version, epoch in (
            (sqlcipher_key, KeyPurpose.SOUL, None, None),
            (frk, KeyPurpose.FILESYSTEM_ROOT, 1, 1),
        )
    ]

    def persist(value):
        actual = json.dumps({field: value.get(field) for field in protected_fields}, sort_keys=True)
        if actual != expected:
            raise ValueError("Initial registration manifest changed before root publication")
        value["portable_core_release"] = PORTABLE_CORE_RELEASE
        value["keyslots_version"] = 1
        value["keyslots"] = [slot.to_dict() for slot in slots]
        value["owner_user_id"] = user_id
        value["owner_binding"] = {"legacy_user_id": user_id}
        value["encryption_mode"] = "sqlcipher+field"
        value["initial_registration_state"] = "pending"
        value.setdefault("initial_registration_created_at", datetime.now(UTC).isoformat())
        value["active_password_credential_generation"] = 1
        value["active_recovery_credential_generation"] = 1
        value["frk_rotation"] = {
            "active_version": 1,
            "pending_version": None,
            "decrypt_only_versions": [],
            "phase": "idle",
            "object_key_epoch": 1,
        }

    update_core_manifest(persist)
    return frk


def activate_initial_manifest_roots() -> None:
    def activate(value):
        if value.get("initial_registration_state") != "pending":
            return
        slots = _manifest_slots(value)
        if len(slots) != 4 or any(
            slot.status is not KeyslotStatus.PENDING or slot.credential_generation != 1
            for slot in slots
        ):
            raise ValueError("Initial pending root hierarchy is incomplete")
        value["keyslots"] = [replace(slot, status=KeyslotStatus.ACTIVE).to_dict() for slot in slots]
        value.pop("initial_registration_state", None)
        value.pop("initial_registration_created_at", None)

    update_core_manifest(activate)


def provision_initial_key_hierarchy(
    db: Session,
    *,
    user_id: int,
    password: str,
    recovery_phrase: str,
    sqlcipher_key: bytes,
    deks: dict[str, bytes],
    prepare_content: Callable[[object], None],
    activate_content: Callable[[object], None],
    initial_frk: object,
) -> object:
    manifest = ensure_core_manifest()
    core_id = str(manifest["core_id"])
    owner_id = str(manifest["owner_id"])
    if manifest.get("initial_registration_state") != "pending":
        raise ValueError("Initial authenticated pending roots are unavailable")

    if set(deks) != set(ALL_DOMAINS):
        raise ValueError("Initial Soul domains are incomplete")
    if db.scalar(select(SoulKeyslot.id).limit(1)) is not None:
        raise ValueError("Initial Soul keyslots are already provisioned")
    for domain, secret in deks.items():
        for credential, path in (
            (password, WrappingPath.PASSWORD),
            (recovery_phrase, WrappingPath.RECOVERY),
        ):
            db.add(
                _build_soul_row(
                    credential,
                    secret,
                    core_id=core_id,
                    owner_id=owner_id,
                    domain=domain,
                    wrapping_path=path,
                    status=KeyslotStatus.ACTIVE,
                    key_version=1,
                    credential_generation=1,
                )
            )
    db.flush()
    subkeys = anima_core.corefs_derive_subkeys(initial_frk, 1)
    prepare_content(subkeys)
    db.commit()
    activate_initial_manifest_roots()
    activate_content(subkeys)
    return subkeys


def derive_active_corefs_subkeys(
    manifest: dict[str, object],
    frks: dict[int, object],
) -> object:
    rotation = manifest.get("frk_rotation")
    if not isinstance(rotation, dict):
        raise ValueError("invalid FRK rotation state")
    try:
        active_version = int(rotation["active_version"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("invalid FRK rotation state") from exc
    if active_version <= 0:
        raise ValueError("invalid FRK rotation state")
    active_frk = frks.get(active_version)
    if active_frk is None:
        raise ValueError(f"active FRK version {active_version} is unavailable")
    return anima_core.corefs_derive_subkeys(active_frk, active_version)


def _manifest_slots(manifest: dict[str, object]) -> list[ManifestKeyslot]:
    raw_slots = manifest.get("keyslots", [])
    if not isinstance(raw_slots, list):
        raise ValueError("manifest keyslots must be a list")
    return [ManifestKeyslot.from_dict(dict(value)) for value in raw_slots]


def manifest_has_versioned_key_hierarchy(manifest: dict[str, object]) -> bool:
    """Return whether the manifest declares any versioned credential state.

    Generation/rotation markers and activated slot evidence are authoritative
    even if an interrupted or malicious edit removed the other representation.
    A valid PENDING-only slot set remains non-authoritative so an unconfirmed
    legacy upgrade can still use its legacy credential wrappers.
    """
    if any(
        field in manifest
        for field in (
            "active_password_credential_generation",
            "active_recovery_credential_generation",
            "frk_rotation",
        )
    ):
        return True

    raw_slots = manifest.get("keyslots", [])
    if not isinstance(raw_slots, list):
        return "keyslots" in manifest
    for raw_slot in raw_slots:
        if not isinstance(raw_slot, dict):
            return True
        status = raw_slot.get("status")
        if status in {KeyslotStatus.ACTIVE.value, KeyslotStatus.DECRYPT_ONLY.value}:
            return True
        if status != KeyslotStatus.PENDING.value:
            return True
    return False


def get_active_manifest_scope(wrapping_path: WrappingPath) -> PayloadScope:
    manifest = json.loads(get_manifest_path().read_text(encoding="utf-8"))
    generation_field = (
        "active_password_credential_generation"
        if wrapping_path is WrappingPath.PASSWORD
        else "active_recovery_credential_generation"
    )
    generation = int(manifest[generation_field])
    if generation <= 0:
        raise ValueError("active credential generation must be positive")
    scopes = {
        slot.scope
        for slot in _manifest_slots(manifest)
        if slot.wrapping_path is wrapping_path
        and slot.credential_generation == generation
        and slot.status is KeyslotStatus.ACTIVE
    }
    if len(scopes) != 1:
        raise ValueError("active credential generation has an ambiguous scope")
    return next(iter(scopes))


def unlock_manifest_key_hierarchy(
    *,
    credential: str,
    wrapping_path: WrappingPath,
    expected_scope: PayloadScope | None = None,
    generation: int | None = None,
    status: KeyslotStatus = KeyslotStatus.ACTIVE,
) -> UnlockedManifestRoots:
    """Authenticate and open only the roots stored in the public manifest."""
    manifest = json.loads(get_manifest_path().read_text(encoding="utf-8"))
    if (
        status is KeyslotStatus.ACTIVE
        and generation is None
        and manifest.get("initial_registration_state") == "pending"
    ):
        status = KeyslotStatus.PENDING
        generation = 1
    return _unlock_manifest_key_hierarchy(
        manifest,
        credential=credential,
        wrapping_path=wrapping_path,
        expected_scope=expected_scope,
        generation=generation,
        status=status,
    )


def unlock_manifest_key_hierarchy_at(
    manifest_path: Path,
    *,
    credential: str,
    wrapping_path: WrappingPath,
    expected_scope: PayloadScope | None = None,
    generation: int | None = None,
    status: KeyslotStatus = KeyslotStatus.ACTIVE,
) -> UnlockedManifestRoots:
    """Open manifest roots at an explicit, already-staged Core boundary.

    Recovery-only transfer sessions cannot consult the process-global active
    Core.  The caller supplies the exact staged manifest and this helper keeps
    the same scope/completeness checks as the active-Core unlock path.
    """
    manifest = _read_manifest_at(manifest_path)
    return _unlock_manifest_key_hierarchy(
        manifest,
        credential=credential,
        wrapping_path=wrapping_path,
        expected_scope=expected_scope,
        generation=generation,
        status=status,
    )


def unlock_manifest_compartment_at(
    manifest_path: Path,
    *,
    credential: str,
    wrapping_path: WrappingPath,
    compartment: PayloadScope,
) -> UnlockedManifestRoots:
    """Recover only one declared partial-archive compartment.

    A scoped archive filters out the other compartment but cannot rewrite its
    surviving wrapper AAD without the user's credential.  Therefore source
    slots may still declare ``full`` until the dedicated replacement flow.
    This helper authenticates those original AAD fields, while completeness is
    checked against the archive-declared compartment and every foreign-purpose
    slot remains forbidden.
    """
    if compartment is PayloadScope.FULL:
        raise ValueError("full scope is not a partial archive compartment")
    manifest = _read_manifest_at(manifest_path)
    if manifest.get("archive_payload_scope") != compartment.value:
        raise ValueError("manifest does not declare the requested archive compartment")
    slots = _manifest_slots(manifest)
    expected_purpose = (
        KeyPurpose.SOUL if compartment is PayloadScope.SOUL else KeyPurpose.FILESYSTEM_ROOT
    )
    if not slots or any(
        slot.purpose is not expected_purpose or slot.scope not in {PayloadScope.FULL, compartment}
        for slot in slots
    ):
        raise ValueError("partial archive contains foreign key material")
    return _unlock_manifest_key_hierarchy(
        manifest,
        credential=credential,
        wrapping_path=wrapping_path,
        expected_scope=None,
        generation=None,
        status=KeyslotStatus.ACTIVE,
        validation_scope=compartment,
    )


def _read_manifest_at(manifest_path: Path) -> dict[str, object]:
    candidate = manifest_path.expanduser()
    if candidate.is_symlink():
        raise ValueError("Core manifest must be a regular file")
    resolved = candidate.resolve(strict=True)
    if not resolved.is_file():
        raise ValueError("Core manifest must be a regular file")
    size = resolved.stat().st_size
    if size <= 0 or size > _MAX_MANIFEST_BYTES:
        raise ValueError("Core manifest exceeds its size bound")
    try:
        manifest = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Core manifest is unavailable or invalid") from exc
    if not isinstance(manifest, dict):
        raise ValueError("Core manifest is invalid")
    return manifest


def _unlock_manifest_key_hierarchy(
    manifest: dict[str, object],
    *,
    credential: str,
    wrapping_path: WrappingPath,
    expected_scope: PayloadScope | None,
    generation: int | None,
    status: KeyslotStatus,
    validation_scope: PayloadScope | None = None,
) -> UnlockedManifestRoots:
    if not manifest_has_versioned_key_hierarchy(manifest):
        raise ValueError("versioned key hierarchy is absent")
    core_id = str(manifest["core_id"])
    owner_id = str(manifest["owner_id"])
    generation_field = (
        "active_password_credential_generation"
        if wrapping_path is WrappingPath.PASSWORD
        else "active_recovery_credential_generation"
    )
    selected_generation = int(manifest[generation_field]) if generation is None else generation
    if selected_generation <= 0:
        raise ValueError("active credential generation must be positive")
    allowed_statuses = (
        {KeyslotStatus.ACTIVE, KeyslotStatus.DECRYPT_ONLY}
        if status is KeyslotStatus.ACTIVE
        else {status}
    )
    candidates = [
        slot
        for slot in _manifest_slots(manifest)
        if slot.wrapping_path is wrapping_path
        and slot.credential_generation == selected_generation
        and slot.status in allowed_statuses
    ]
    scopes = {slot.scope for slot in candidates}
    if len(scopes) != 1:
        raise ValueError("credential generation has an ambiguous scope")
    scope = next(iter(scopes))
    if expected_scope is not None and scope is not expected_scope:
        raise ValueError("credential generation scope does not match the requested scope")
    effective_scope = validation_scope or scope
    if validation_scope is not None and scope not in {PayloadScope.FULL, validation_scope}:
        raise ValueError("source credential scope cannot recover the requested compartment")
    if len({(slot.purpose, slot.key_version) for slot in candidates}) != len(candidates):
        raise ValueError("duplicate manifest keyslots")

    sqlcipher_key: bytes | None = None
    frks: dict[int, object] = {}
    purposes: set[KeyPurpose] = set()
    for slot in candidates:
        aad = manifest_keyslot_aad(
            core_id=core_id,
            owner_id=owner_id,
            purpose=slot.purpose,
            key_version=slot.key_version,
            credential_generation=slot.credential_generation,
            scope=slot.scope,
            frk_version=slot.frk_version,
            object_key_epoch=slot.object_key_epoch,
            wrapping_path=wrapping_path,
        )
        secret = _unwrap_manifest_slot(credential, slot, aad)
        purposes.add(slot.purpose)
        if slot.purpose is KeyPurpose.SOUL:
            if not isinstance(secret, bytes):
                raise ValueError("Soul keyslot produced an invalid key type")
            sqlcipher_key = secret
        else:
            if slot.frk_version is None:
                raise ValueError("Filesystem Root keyslot is missing its FRK version")
            frks[slot.frk_version] = secret

    required_frks: set[int] = set()
    if effective_scope in {PayloadScope.FULL, PayloadScope.FS}:
        required_frks = _required_frk_versions_from_manifest(manifest)
    validate_scope_completeness(
        effective_scope,
        purposes=purposes,
        soul_domains=set(),
        required_soul_domains=set(),
        frk_versions=set(frks),
        required_frk_versions=required_frks,
    )
    return UnlockedManifestRoots(
        scope=effective_scope,
        owner_id=owner_id,
        credential_generation=selected_generation,
        sqlcipher_key=sqlcipher_key,
        frks=frks,
    )


def unlock_key_hierarchy(
    db: Session,
    *,
    credential: str,
    wrapping_path: WrappingPath,
    scope: PayloadScope,
) -> UnlockedKeyHierarchy:
    manifest = json.loads(get_manifest_path().read_text(encoding="utf-8"))
    core_id = str(manifest["core_id"])
    roots = unlock_manifest_key_hierarchy(
        credential=credential,
        wrapping_path=wrapping_path,
        expected_scope=scope,
    )
    owner_id = roots.owner_id
    generation = roots.credential_generation

    soul_domains: dict[str, bytes] = {}
    if scope in {PayloadScope.FULL, PayloadScope.SOUL}:
        rows = list(
            db.scalars(
                select(SoulKeyslot).where(
                    SoulKeyslot.owner_id == owner_id,
                    SoulKeyslot.wrapping_path == wrapping_path.value,
                    SoulKeyslot.credential_generation == generation,
                    SoulKeyslot.status.in_(
                        [KeyslotStatus.ACTIVE.value, KeyslotStatus.PENDING.value]
                    ),
                )
            ).all()
        )
        if len({row.domain for row in rows}) != len(rows):
            raise ValueError("duplicate active Soul keyslots")
        for row in rows:
            aad = soul_keyslot_aad(
                core_id=core_id,
                owner_id=owner_id,
                domain=row.domain,
                key_version=row.key_version,
                credential_generation=row.credential_generation,
                wrapping_path=wrapping_path,
            )
            soul_domains[row.domain] = unwrap_keyslot_secret(
                credential,
                _soul_row_record(row),
                aad,
            )

    validate_scope_completeness(
        scope,
        purposes=(
            ({KeyPurpose.SOUL} if roots.sqlcipher_key is not None else set())
            | ({KeyPurpose.FILESYSTEM_ROOT} if roots.frks else set())
        ),
        soul_domains=set(soul_domains),
        required_soul_domains=set(ALL_DOMAINS) if scope is not PayloadScope.FS else set(),
        frk_versions=set(roots.frks),
        required_frk_versions=(
            _required_frk_versions_from_manifest(manifest)
            if scope in {PayloadScope.FULL, PayloadScope.FS}
            else set()
        ),
    )
    return UnlockedKeyHierarchy(
        scope=scope,
        owner_id=owner_id,
        credential_generation=generation,
        sqlcipher_key=roots.sqlcipher_key,
        soul_domains=soul_domains,
        frks=roots.frks,
    )
