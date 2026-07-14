from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import StrEnum
from uuid import uuid4

import anima_core
from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.models import SoulKeyslot, User, UserKey
from anima_server.services.auth import change_user_password, hash_password, verify_password
from anima_server.services.core import (
    get_manifest_path,
    get_sqlcipher_kdf_salt,
    get_wrapped_sqlcipher_key,
    store_wrapped_sqlcipher_key,
    update_core_manifest,
)
from anima_server.services.corefs.crypto import (
    manifest_keyslot_aad,
    soul_keyslot_aad,
    unwrap_keyslot_secret,
)
from anima_server.services.corefs.keyslots import (
    UnlockedKeyHierarchy,
    _build_soul_row,
    _manifest_secret_matches,
    _manifest_slot,
    _manifest_slots,
    _record_from_payload,
    _record_to_payload,
    _required_frk_versions_from_manifest,
    _soul_row_record,
    _unwrap_manifest_slot,
    get_active_manifest_scope,
    manifest_has_versioned_key_hierarchy,
    unlock_key_hierarchy,
    unlock_manifest_key_hierarchy,
    validate_scope_completeness,
    verify_legacy_soul_keys,
)
from anima_server.services.corefs.transactions import serialized_credential_transaction
from anima_server.services.corefs.types import (
    KeyPurpose,
    KeyslotStatus,
    PayloadScope,
    WrappingPath,
)
from anima_server.services.crypto import derive_sqlcipher_key, unwrap_dek, wrap_dek
from anima_server.services.data_crypto import ALL_DOMAINS
from anima_server.services.recovery import RECOVERY_DOMAIN_PREFIX, generate_recovery_phrase


class CredentialBoundary(StrEnum):
    SOUL_PENDING_DURABLE = "soul-pending-durable"
    MANIFEST_PENDING_DURABLE = "manifest-pending-durable"
    PENDING_REOPEN_VERIFIED = "pending-reopen-verified"
    MANIFEST_ACTIVATED = "manifest-activated"
    SOUL_PROMOTED = "soul-promoted"
    ACTIVE_REOPEN_VERIFIED = "active-reopen-verified"


FailureInjector = Callable[[CredentialBoundary], None]
_PENDING_RECOVERY_CREDENTIAL = "pending_recovery_credential"
_COORDINATOR_ID = uuid4().hex


def _inject(injector: FailureInjector | None, boundary: CredentialBoundary) -> None:
    if injector is not None:
        injector(boundary)


def _require_active_generation(
    manifest: dict[str, object],
    path: WrappingPath,
    expected: int,
) -> None:
    field = f"active_{path.value}_credential_generation"
    if int(manifest.get(field, 0)) != expected:
        raise ValueError(f"active {path.value} credential generation changed")


def _reject_live_pending_recovery(
    manifest: dict[str, object],
    *,
    replace_pending: bool = False,
) -> None:
    marker = manifest.get(_PENDING_RECOVERY_CREDENTIAL)
    if (
        isinstance(marker, dict)
        and marker.get("phase") == "ready"
        and not replace_pending
    ):
        raise ValueError("recovery credential preparation is in progress")


def _set_pending_recovery_marker(
    manifest: dict[str, object],
    *,
    generation: int,
    scope: PayloadScope,
    phase: str,
) -> None:
    manifest[_PENDING_RECOVERY_CREDENTIAL] = {
        "generation": generation,
        "scope": scope.value,
        "phase": phase,
        "coordinator_id": _COORDINATOR_ID,
    }


def _mark_pending_recovery_ready(
    *,
    generation: int,
    scope: PayloadScope,
    expected_active_generation: int,
) -> None:
    def mark_ready(value: dict[str, object]) -> None:
        _require_active_generation(value, WrappingPath.RECOVERY, expected_active_generation)
        marker = value.get(_PENDING_RECOVERY_CREDENTIAL)
        if not isinstance(marker, dict) or (
            marker.get("generation") != generation
            or marker.get("scope") != scope.value
            or marker.get("phase") != "preparing"
            or marker.get("coordinator_id") != _COORDINATOR_ID
        ):
            raise ValueError("pending recovery credential marker changed")
        _set_pending_recovery_marker(
            value,
            generation=generation,
            scope=scope,
            phase="ready",
        )

    update_core_manifest(mark_ready)


def _legacy_manifest_payload(record: object, user_id: int) -> dict[str, object]:
    payload = _record_to_payload(record)  # type: ignore[arg-type]
    payload["user_id"] = user_id
    return payload


def _verify_pending_password_generation(
    db: Session,
    *,
    password: str,
    generation: int,
    scope: PayloadScope,
    required_frk_versions: set[int],
    expected_sqlcipher: bytes | None,
    expected_frks: dict[int, object],
    expected_domains: dict[str, bytes],
    manifest_status: KeyslotStatus = KeyslotStatus.PENDING,
) -> None:
    manifest = json.loads(get_manifest_path().read_text(encoding="utf-8"))
    core_id = str(manifest["core_id"])
    owner_id = str(manifest["owner_id"])
    slots = [
        slot
        for slot in _manifest_slots(manifest)
        if slot.wrapping_path is WrappingPath.PASSWORD
        and slot.credential_generation == generation
        and slot.status is manifest_status
        and slot.scope is scope
    ]
    if len({(slot.purpose, slot.key_version) for slot in slots}) != len(slots):
        raise ValueError("duplicate pending manifest password keyslots")
    required_frks = (
        set(required_frk_versions) if scope in {PayloadScope.FULL, PayloadScope.FS} else set()
    )
    if set(expected_frks) != required_frks:
        raise ValueError("source filesystem password generation is incomplete")
    slot_frks = {
        int(slot.frk_version)
        for slot in slots
        if slot.purpose is KeyPurpose.FILESYSTEM_ROOT and slot.frk_version is not None
    }
    expected_root_count = len(required_frks) + (1 if expected_sqlcipher is not None else 0)
    if len(slots) != expected_root_count or slot_frks != required_frks:
        raise ValueError("pending manifest password generation is incomplete")
    purposes: set[KeyPurpose] = set()
    for slot in slots:
        aad = manifest_keyslot_aad(
            core_id=core_id,
            owner_id=owner_id,
            purpose=slot.purpose,
            key_version=slot.key_version,
            credential_generation=slot.credential_generation,
            scope=slot.scope,
            frk_version=slot.frk_version,
            object_key_epoch=slot.object_key_epoch,
            wrapping_path=WrappingPath.PASSWORD,
        )
        secret = _unwrap_manifest_slot(password, slot, aad)
        purposes.add(slot.purpose)
        expected = (
            expected_sqlcipher
            if slot.purpose is KeyPurpose.SOUL
            else expected_frks[int(slot.frk_version or 0)]
        )
        if not _manifest_secret_matches(secret, expected):
            raise ValueError("pending manifest password verification mismatch")

    rows = (
        list(
            db.scalars(
                select(SoulKeyslot).where(
                    SoulKeyslot.owner_id == owner_id,
                    SoulKeyslot.wrapping_path == WrappingPath.PASSWORD.value,
                    SoulKeyslot.credential_generation == generation,
                    SoulKeyslot.status.in_(
                        [KeyslotStatus.PENDING.value, KeyslotStatus.ACTIVE.value]
                    ),
                )
            ).all()
        )
        if scope in {PayloadScope.FULL, PayloadScope.SOUL}
        else []
    )
    if len({row.domain for row in rows}) != len(rows):
        raise ValueError("duplicate Soul password generation keyslots")
    if {row.domain for row in rows} != set(expected_domains):
        raise ValueError("pending Soul password generation is incomplete")
    for row in rows:
        aad = soul_keyslot_aad(
            core_id=core_id,
            owner_id=owner_id,
            domain=row.domain,
            key_version=row.key_version,
            credential_generation=row.credential_generation,
            wrapping_path=WrappingPath.PASSWORD,
        )
        if (
            unwrap_keyslot_secret(password, _soul_row_record(row), aad)
            != expected_domains[row.domain]
        ):
            raise ValueError("pending Soul password verification mismatch")
    validate_scope_completeness(
        scope,
        purposes=purposes,
        soul_domains={row.domain for row in rows},
        required_soul_domains=(
            set(ALL_DOMAINS) if scope in {PayloadScope.FULL, PayloadScope.SOUL} else set()
        ),
        frk_versions=slot_frks,
        required_frk_versions=required_frks,
    )


def _rewrap_legacy_password_rows(
    db: Session,
    *,
    user_id: int,
    password: str,
    domains: dict[str, bytes],
) -> None:
    rows = list(
        db.scalars(
            select(UserKey).where(
                UserKey.user_id == user_id,
                ~UserKey.domain.startswith(RECOVERY_DOMAIN_PREFIX),
            )
        ).all()
    )
    if {row.domain for row in rows} != set(domains):
        raise ValueError("legacy password key rollback rows are incomplete")
    for row in rows:
        wrapped = wrap_dek(password, domains[row.domain], user_id, row.domain)
        row.kdf_salt = wrapped.kdf_salt
        row.kdf_time_cost = wrapped.kdf_time_cost
        row.kdf_memory_cost_kib = wrapped.kdf_memory_cost_kib
        row.kdf_parallelism = wrapped.kdf_parallelism
        row.kdf_key_length = wrapped.kdf_key_length
        row.wrap_iv = wrapped.wrap_iv
        row.wrap_tag = wrapped.wrap_tag
        row.wrapped_dek = wrapped.wrapped_dek


def _rewrap_legacy_sqlcipher_root(new_password: str, user_id: int) -> None:
    from anima_server.config import settings
    from anima_server.services.sessions import get_sqlcipher_key

    if settings.core_passphrase.strip():
        return
    raw_key = get_sqlcipher_key()
    wrapped_data = get_wrapped_sqlcipher_key()
    if raw_key is None or wrapped_data is None:
        return
    store_wrapped_sqlcipher_key(
        _legacy_manifest_payload(
            wrap_dek(new_password, raw_key, user_id, "sqlcipher"),
            user_id,
        )
    )


@serialized_credential_transaction
def change_account_password_credential(
    db: Session,
    user: User,
    *,
    old_password: str,
    new_password: str,
    current_deks: dict[str, bytes],
    scope: PayloadScope = PayloadScope.FULL,
) -> None:
    """Rotate a versioned credential, retaining legacy compatibility pre-activation."""
    manifest = json.loads(get_manifest_path().read_text(encoding="utf-8"))
    if manifest_has_versioned_key_hierarchy(manifest):
        change_password_credential_generation(
            db,
            user,
            old_password=old_password,
            new_password=new_password,
            current_deks=current_deks,
            scope=scope,
        )
        return

    if scope is not PayloadScope.FULL:
        raise ValueError("legacy password change requires full scope")
    change_user_password(
        db,
        user,
        old_password=old_password,
        new_password=new_password,
        current_deks=current_deks,
    )
    _rewrap_legacy_sqlcipher_root(new_password, user.id)


@serialized_credential_transaction
def change_password_credential_generation(
    db: Session,
    user: User,
    *,
    old_password: str,
    new_password: str,
    current_deks: dict[str, bytes],
    scope: PayloadScope = PayloadScope.FULL,
    failure_injector: FailureInjector | None = None,
    _source_keys: UnlockedKeyHierarchy | None = None,
) -> None:
    if _source_keys is None:
        if not verify_password(old_password, user.password_hash).valid:
            raise ValueError("Invalid credentials")
        unlocked = unlock_key_hierarchy(
            db,
            credential=old_password,
            wrapping_path=WrappingPath.PASSWORD,
            scope=scope,
        )
    else:
        unlocked = _source_keys
    if scope in {PayloadScope.FULL, PayloadScope.SOUL} and (
        unlocked.sqlcipher_key is None or unlocked.soul_domains != current_deks
    ):
        raise ValueError("unlocked key hierarchy does not match the active session")

    manifest = json.loads(get_manifest_path().read_text(encoding="utf-8"))
    core_id = str(manifest["core_id"])
    owner_id = str(manifest["owner_id"])
    previous_generation = int(manifest["active_password_credential_generation"])
    generation = previous_generation + 1
    required_frk_versions = (
        _required_frk_versions_from_manifest(manifest)
        if scope in {PayloadScope.FULL, PayloadScope.FS}
        else set()
    )

    stale_pending = list(
        db.scalars(
            select(SoulKeyslot).where(
                SoulKeyslot.owner_id == owner_id,
                SoulKeyslot.wrapping_path == WrappingPath.PASSWORD.value,
                SoulKeyslot.credential_generation == generation,
                SoulKeyslot.status == KeyslotStatus.PENDING.value,
            )
        ).all()
    )
    if stale_pending:
        for row in stale_pending:
            db.delete(row)
        db.commit()

    if any(
        slot.wrapping_path is WrappingPath.PASSWORD
        and slot.credential_generation == generation
        and slot.status is KeyslotStatus.PENDING
        for slot in _manifest_slots(manifest)
    ):

        def _discard_stale_pending(value: dict[str, object]) -> None:
            _require_active_generation(value, WrappingPath.PASSWORD, previous_generation)
            value["keyslots"] = [
                slot.to_dict()
                for slot in _manifest_slots(value)
                if not (
                    slot.wrapping_path is WrappingPath.PASSWORD
                    and slot.credential_generation == generation
                    and slot.status is KeyslotStatus.PENDING
                )
            ]

        update_core_manifest(_discard_stale_pending)
        manifest = json.loads(get_manifest_path().read_text(encoding="utf-8"))

    if scope in {PayloadScope.FULL, PayloadScope.SOUL}:
        for domain, secret in unlocked.soul_domains.items():
            db.add(
                _build_soul_row(
                    new_password,
                    secret,
                    core_id=core_id,
                    owner_id=owner_id,
                    domain=domain,
                    wrapping_path=WrappingPath.PASSWORD,
                    status=KeyslotStatus.PENDING,
                    key_version=1,
                    credential_generation=generation,
                )
            )
        db.commit()
        _inject(failure_injector, CredentialBoundary.SOUL_PENDING_DURABLE)

    roots: list[tuple[bytes | object, KeyPurpose, int]] = []
    if unlocked.sqlcipher_key is not None:
        roots.append((unlocked.sqlcipher_key, KeyPurpose.SOUL, 1))
    roots.extend(
        (secret, KeyPurpose.FILESYSTEM_ROOT, version)
        for version, secret in sorted(unlocked.frks.items())
    )

    pending_slots = [
        _manifest_slot(
            new_password,
            secret,
            core_id=core_id,
            owner_id=owner_id,
            purpose=purpose,
            wrapping_path=WrappingPath.PASSWORD,
            status=KeyslotStatus.PENDING,
            scope=scope,
            key_version=version,
            credential_generation=generation,
            frk_version=version if purpose is KeyPurpose.FILESYSTEM_ROOT else None,
            object_key_epoch=(
                int(manifest["frk_rotation"].get("object_key_epoch", 1))
                if purpose is KeyPurpose.FILESYSTEM_ROOT
                else None
            ),
        )
        for secret, purpose, version in roots
    ]

    def _write_pending(value: dict[str, object]) -> None:
        _require_active_generation(value, WrappingPath.PASSWORD, previous_generation)
        value["keyslots"] = [*_manifest_slots(value), *pending_slots]
        value["keyslots"] = [slot.to_dict() for slot in value["keyslots"]]

    update_core_manifest(_write_pending)
    _inject(failure_injector, CredentialBoundary.MANIFEST_PENDING_DURABLE)

    _verify_pending_password_generation(
        db,
        password=new_password,
        generation=generation,
        scope=scope,
        required_frk_versions=required_frk_versions,
        expected_sqlcipher=unlocked.sqlcipher_key,
        expected_frks=unlocked.frks,
        expected_domains=unlocked.soul_domains,
    )
    _inject(failure_injector, CredentialBoundary.PENDING_REOPEN_VERIFIED)

    legacy_root = (
        wrap_dek(new_password, unlocked.sqlcipher_key, user.id, "sqlcipher")
        if unlocked.sqlcipher_key is not None
        else None
    )

    def _activate_manifest(value: dict[str, object]) -> None:
        _require_active_generation(value, WrappingPath.PASSWORD, previous_generation)
        activated = []
        for slot in _manifest_slots(value):
            if slot.wrapping_path is WrappingPath.PASSWORD and slot.scope is scope:
                if (
                    slot.credential_generation == previous_generation
                    and slot.status is KeyslotStatus.ACTIVE
                ):
                    slot = replace(slot, status=KeyslotStatus.DECRYPT_ONLY)
                elif (
                    slot.credential_generation == generation
                    and slot.status is KeyslotStatus.PENDING
                ):
                    slot = replace(slot, status=KeyslotStatus.ACTIVE)
            activated.append(slot.to_dict())
        value["keyslots"] = activated
        value["active_password_credential_generation"] = generation
        if legacy_root is not None:
            value["wrapped_sqlcipher_key"] = _legacy_manifest_payload(legacy_root, user.id)

    update_core_manifest(_activate_manifest)
    _inject(failure_injector, CredentialBoundary.MANIFEST_ACTIVATED)

    if scope in {PayloadScope.FULL, PayloadScope.SOUL}:
        rows = list(
            db.scalars(
                select(SoulKeyslot).where(
                    SoulKeyslot.owner_id == owner_id,
                    SoulKeyslot.wrapping_path == WrappingPath.PASSWORD.value,
                )
            ).all()
        )
        for row in rows:
            if (
                row.credential_generation == previous_generation
                and row.status == KeyslotStatus.ACTIVE
            ):
                row.status = KeyslotStatus.DECRYPT_ONLY.value
            elif row.credential_generation == generation and row.status == KeyslotStatus.PENDING:
                row.status = KeyslotStatus.ACTIVE.value
        _rewrap_legacy_password_rows(
            db,
            user_id=user.id,
            password=new_password,
            domains=unlocked.soul_domains,
        )
        user.password_hash = hash_password(new_password)
        db.commit()
        db.refresh(user)
        _inject(failure_injector, CredentialBoundary.SOUL_PROMOTED)

    verified = unlock_key_hierarchy(
        db,
        credential=new_password,
        wrapping_path=WrappingPath.PASSWORD,
        scope=scope,
    )
    if (
        verified.sqlcipher_key != unlocked.sqlcipher_key
        or verified.frks != unlocked.frks
        or verified.soul_domains != unlocked.soul_domains
    ):
        raise ValueError("active password credential generation verification failed")
    _inject(failure_injector, CredentialBoundary.ACTIVE_REOPEN_VERIFIED)


@serialized_credential_transaction
def recover_password_credential_generation(
    db: Session,
    user: User,
    *,
    recovery_phrase: str,
    new_password: str,
    scope: PayloadScope = PayloadScope.FULL,
    failure_injector: FailureInjector | None = None,
) -> dict[str, bytes]:
    source = unlock_key_hierarchy(
        db,
        credential=recovery_phrase,
        wrapping_path=WrappingPath.RECOVERY,
        scope=scope,
    )
    change_password_credential_generation(
        db,
        user,
        old_password="",
        new_password=new_password,
        current_deks=source.soul_domains,
        scope=scope,
        failure_injector=failure_injector,
        _source_keys=source,
    )
    return source.soul_domains


@serialized_credential_transaction
def finalize_pending_password_generation(
    db: Session,
    user: User,
    *,
    password: str,
) -> bool:
    """Finish the Soul half after a crash following manifest activation."""
    manifest = json.loads(get_manifest_path().read_text(encoding="utf-8"))
    owner_id = str(manifest.get("owner_id", ""))
    generation = int(manifest.get("active_password_credential_generation", 0))
    if not owner_id or generation <= 0:
        return False
    pending = list(
        db.scalars(
            select(SoulKeyslot).where(
                SoulKeyslot.owner_id == owner_id,
                SoulKeyslot.wrapping_path == WrappingPath.PASSWORD.value,
                SoulKeyslot.credential_generation == generation,
                SoulKeyslot.status == KeyslotStatus.PENDING.value,
            )
        ).all()
    )
    if not pending:
        return False

    scope = get_active_manifest_scope(WrappingPath.PASSWORD)
    if scope is PayloadScope.FS:
        raise ValueError("filesystem-only credentials cannot finalize Soul state")
    unlocked = unlock_key_hierarchy(
        db,
        credential=password,
        wrapping_path=WrappingPath.PASSWORD,
        scope=scope,
    )
    if {row.domain for row in pending} != set(unlocked.soul_domains):
        raise ValueError("pending Soul password generation is incomplete")
    active_rows = list(
        db.scalars(
            select(SoulKeyslot).where(
                SoulKeyslot.owner_id == owner_id,
                SoulKeyslot.wrapping_path == WrappingPath.PASSWORD.value,
            )
        ).all()
    )
    for row in active_rows:
        if row.status == KeyslotStatus.ACTIVE.value and row.credential_generation != generation:
            row.status = KeyslotStatus.DECRYPT_ONLY.value
        elif row.status == KeyslotStatus.PENDING.value and row.credential_generation == generation:
            row.status = KeyslotStatus.ACTIVE.value
    _rewrap_legacy_password_rows(
        db,
        user_id=user.id,
        password=password,
        domains=unlocked.soul_domains,
    )
    user.password_hash = hash_password(password)
    db.commit()
    db.refresh(user)
    return True


def _rewrap_legacy_recovery_rows(
    db: Session,
    *,
    user_id: int,
    recovery_phrase: str,
    domains: dict[str, bytes],
) -> None:
    rows = list(
        db.scalars(
            select(UserKey).where(
                UserKey.user_id == user_id,
                UserKey.domain.startswith(RECOVERY_DOMAIN_PREFIX),
            )
        ).all()
    )
    expected = {f"{RECOVERY_DOMAIN_PREFIX}{domain}" for domain in domains}
    if {row.domain for row in rows} != expected:
        raise ValueError("legacy recovery key rollback rows are incomplete")
    for row in rows:
        domain = row.domain[len(RECOVERY_DOMAIN_PREFIX) :]
        wrapped = wrap_dek(recovery_phrase, domains[domain], user_id, row.domain)
        row.kdf_salt = wrapped.kdf_salt
        row.kdf_time_cost = wrapped.kdf_time_cost
        row.kdf_memory_cost_kib = wrapped.kdf_memory_cost_kib
        row.kdf_parallelism = wrapped.kdf_parallelism
        row.kdf_key_length = wrapped.kdf_key_length
        row.wrap_iv = wrapped.wrap_iv
        row.wrap_tag = wrapped.wrap_tag
        row.wrapped_dek = wrapped.wrapped_dek


@dataclass(frozen=True, slots=True)
class PreparedRecoveryCredential:
    recovery_phrase: str
    pending_generation: int
    scope: PayloadScope


def _filesystem_roots_match(
    first: dict[int, object],
    second: dict[int, object],
) -> bool:
    return set(first) == set(second) and all(
        _manifest_secret_matches(first[version], second[version]) for version in first
    )


@serialized_credential_transaction
def change_filesystem_password_credential(
    *,
    current_password: str,
    new_password: str,
    failure_injector: FailureInjector | None = None,
) -> None:
    """Rotate a genuine FS-only password without opening the Soul database."""
    unlocked = unlock_manifest_key_hierarchy(
        credential=current_password,
        wrapping_path=WrappingPath.PASSWORD,
        expected_scope=PayloadScope.FS,
    )
    manifest = json.loads(get_manifest_path().read_text(encoding="utf-8"))
    core_id = str(manifest["core_id"])
    previous_generation = unlocked.credential_generation
    generation = previous_generation + 1
    object_key_epoch = int(dict(manifest["frk_rotation"]).get("object_key_epoch", 1))
    pending_slots = [
        _manifest_slot(
            new_password,
            root,
            core_id=core_id,
            owner_id=unlocked.owner_id,
            purpose=KeyPurpose.FILESYSTEM_ROOT,
            wrapping_path=WrappingPath.PASSWORD,
            status=KeyslotStatus.PENDING,
            scope=PayloadScope.FS,
            key_version=version,
            credential_generation=generation,
            frk_version=version,
            object_key_epoch=object_key_epoch,
        )
        for version, root in sorted(unlocked.frks.items())
    ]

    def write_pending(value: dict[str, object]) -> None:
        _require_active_generation(value, WrappingPath.PASSWORD, previous_generation)
        retained = [
            slot
            for slot in _manifest_slots(value)
            if not (
                slot.wrapping_path is WrappingPath.PASSWORD
                and slot.credential_generation == generation
                and slot.status is KeyslotStatus.PENDING
            )
        ]
        value["keyslots"] = [
            *(slot.to_dict() for slot in retained),
            *(slot.to_dict() for slot in pending_slots),
        ]

    update_core_manifest(write_pending)
    _inject(failure_injector, CredentialBoundary.MANIFEST_PENDING_DURABLE)
    pending = unlock_manifest_key_hierarchy(
        credential=new_password,
        wrapping_path=WrappingPath.PASSWORD,
        expected_scope=PayloadScope.FS,
        generation=generation,
        status=KeyslotStatus.PENDING,
    )
    if not _filesystem_roots_match(pending.frks, unlocked.frks):
        raise ValueError("pending filesystem password generation verification failed")
    _inject(failure_injector, CredentialBoundary.PENDING_REOPEN_VERIFIED)

    def activate(value: dict[str, object]) -> None:
        _require_active_generation(value, WrappingPath.PASSWORD, previous_generation)
        activated: list[dict[str, object]] = []
        for slot in _manifest_slots(value):
            if slot.wrapping_path is WrappingPath.PASSWORD and slot.scope is PayloadScope.FS:
                if (
                    slot.credential_generation == previous_generation
                    and slot.status is KeyslotStatus.ACTIVE
                ):
                    slot = replace(slot, status=KeyslotStatus.DECRYPT_ONLY)
                elif (
                    slot.credential_generation == generation
                    and slot.status is KeyslotStatus.PENDING
                ):
                    slot = replace(slot, status=KeyslotStatus.ACTIVE)
            activated.append(slot.to_dict())
        value["keyslots"] = activated
        value["active_password_credential_generation"] = generation

    update_core_manifest(activate)
    _inject(failure_injector, CredentialBoundary.MANIFEST_ACTIVATED)
    active = unlock_manifest_key_hierarchy(
        credential=new_password,
        wrapping_path=WrappingPath.PASSWORD,
        expected_scope=PayloadScope.FS,
    )
    if not _filesystem_roots_match(active.frks, unlocked.frks):
        raise ValueError("active filesystem password generation verification failed")
    _inject(failure_injector, CredentialBoundary.ACTIVE_REOPEN_VERIFIED)


@serialized_credential_transaction
def prepare_filesystem_recovery_credential(
    *,
    current_password: str,
    current_recovery_phrase: str,
    replace_pending: bool = False,
    failure_injector: FailureInjector | None = None,
) -> PreparedRecoveryCredential:
    """Prepare a replacement FS-only recovery phrase using manifest roots only."""
    manifest = json.loads(get_manifest_path().read_text(encoding="utf-8"))
    active_generation = int(manifest["active_recovery_credential_generation"])
    generation = active_generation + 1
    _reject_live_pending_recovery(manifest, replace_pending=replace_pending)
    password_roots = unlock_manifest_key_hierarchy(
        credential=current_password,
        wrapping_path=WrappingPath.PASSWORD,
        expected_scope=PayloadScope.FS,
    )
    recovery_roots = unlock_manifest_key_hierarchy(
        credential=current_recovery_phrase,
        wrapping_path=WrappingPath.RECOVERY,
        expected_scope=PayloadScope.FS,
    )
    if password_roots.owner_id != recovery_roots.owner_id or not _filesystem_roots_match(
        password_roots.frks, recovery_roots.frks
    ):
        raise ValueError("filesystem password and recovery roots do not match")
    if recovery_roots.credential_generation != active_generation:
        raise ValueError("active recovery credential generation changed")

    core_id = str(manifest["core_id"])
    object_key_epoch = int(dict(manifest["frk_rotation"]).get("object_key_epoch", 1))
    phrase = generate_recovery_phrase()
    pending_slots = [
        _manifest_slot(
            phrase,
            root,
            core_id=core_id,
            owner_id=recovery_roots.owner_id,
            purpose=KeyPurpose.FILESYSTEM_ROOT,
            wrapping_path=WrappingPath.RECOVERY,
            status=KeyslotStatus.PENDING,
            scope=PayloadScope.FS,
            key_version=version,
            credential_generation=generation,
            frk_version=version,
            object_key_epoch=object_key_epoch,
        )
        for version, root in sorted(recovery_roots.frks.items())
    ]

    def write_pending(value: dict[str, object]) -> None:
        _require_active_generation(value, WrappingPath.RECOVERY, active_generation)
        retained = [
            slot
            for slot in _manifest_slots(value)
            if not (
                slot.wrapping_path is WrappingPath.RECOVERY
                and slot.credential_generation == generation
                and slot.status is KeyslotStatus.PENDING
            )
        ]
        value["keyslots"] = [
            *(slot.to_dict() for slot in retained),
            *(slot.to_dict() for slot in pending_slots),
        ]
        _set_pending_recovery_marker(
            value,
            generation=generation,
            scope=PayloadScope.FS,
            phase="preparing",
        )

    update_core_manifest(write_pending)
    _inject(failure_injector, CredentialBoundary.MANIFEST_PENDING_DURABLE)
    pending = unlock_manifest_key_hierarchy(
        credential=phrase,
        wrapping_path=WrappingPath.RECOVERY,
        expected_scope=PayloadScope.FS,
        generation=generation,
        status=KeyslotStatus.PENDING,
    )
    if not _filesystem_roots_match(pending.frks, recovery_roots.frks):
        raise ValueError("pending filesystem recovery generation verification failed")
    _inject(failure_injector, CredentialBoundary.PENDING_REOPEN_VERIFIED)
    _mark_pending_recovery_ready(
        generation=generation,
        scope=PayloadScope.FS,
        expected_active_generation=active_generation,
    )
    return PreparedRecoveryCredential(phrase, generation, PayloadScope.FS)


@serialized_credential_transaction
def confirm_filesystem_recovery_credential(
    *,
    recovery_phrase: str,
    pending_generation: int,
    failure_injector: FailureInjector | None = None,
) -> None:
    """Activate a prepared FS-only recovery phrase without Soul state."""
    manifest = json.loads(get_manifest_path().read_text(encoding="utf-8"))
    active_generation = int(manifest["active_recovery_credential_generation"])
    if active_generation not in {pending_generation - 1, pending_generation}:
        raise ValueError("pending filesystem recovery generation is stale")
    status = (
        KeyslotStatus.PENDING if active_generation < pending_generation else KeyslotStatus.ACTIVE
    )
    pending = unlock_manifest_key_hierarchy(
        credential=recovery_phrase,
        wrapping_path=WrappingPath.RECOVERY,
        expected_scope=PayloadScope.FS,
        generation=pending_generation,
        status=status,
    )
    _inject(failure_injector, CredentialBoundary.PENDING_REOPEN_VERIFIED)

    if active_generation < pending_generation:

        def activate(value: dict[str, object]) -> None:
            _require_active_generation(value, WrappingPath.RECOVERY, active_generation)
            activated: list[dict[str, object]] = []
            for slot in _manifest_slots(value):
                if slot.wrapping_path is WrappingPath.RECOVERY and slot.scope is PayloadScope.FS:
                    if (
                        slot.credential_generation == active_generation
                        and slot.status is KeyslotStatus.ACTIVE
                    ):
                        slot = replace(slot, status=KeyslotStatus.DECRYPT_ONLY)
                    elif (
                        slot.credential_generation == pending_generation
                        and slot.status is KeyslotStatus.PENDING
                    ):
                        slot = replace(slot, status=KeyslotStatus.ACTIVE)
                activated.append(slot.to_dict())
            value["keyslots"] = activated
            value["active_recovery_credential_generation"] = pending_generation
            value.pop(_PENDING_RECOVERY_CREDENTIAL, None)

        update_core_manifest(activate)
        _inject(failure_injector, CredentialBoundary.MANIFEST_ACTIVATED)

    active = unlock_manifest_key_hierarchy(
        credential=recovery_phrase,
        wrapping_path=WrappingPath.RECOVERY,
        expected_scope=PayloadScope.FS,
    )
    if not _filesystem_roots_match(active.frks, pending.frks):
        raise ValueError("active filesystem recovery generation verification failed")
    _inject(failure_injector, CredentialBoundary.ACTIVE_REOPEN_VERIFIED)


def _legacy_record(payload: object):
    if not isinstance(payload, dict):
        raise ValueError("legacy SQLCipher wrapper is missing")
    try:
        return _record_from_payload(
            {
                key: payload[key]
                for key in (
                    "kdf_salt",
                    "kdf_time_cost",
                    "kdf_memory_cost_kib",
                    "kdf_parallelism",
                    "kdf_key_length",
                    "wrap_iv",
                    "wrap_tag",
                    "wrapped_key",
                )
            }
        )
    except KeyError as exc:
        raise ValueError("legacy SQLCipher wrapper is incomplete") from exc


def _legacy_sqlcipher_root(
    manifest: dict[str, object],
    *,
    user_id: int,
    password: str,
    recovery_phrase: str,
) -> bytes:
    from anima_server.config import settings
    from anima_server.services.sessions import get_sqlcipher_key

    password_payload = manifest.get("wrapped_sqlcipher_key")
    if isinstance(password_payload, dict):
        password_root = unwrap_dek(password, _legacy_record(password_payload), user_id, "sqlcipher")
    elif settings.core_passphrase.strip():
        password_root = derive_sqlcipher_key(
            settings.core_passphrase.strip(),
            get_sqlcipher_kdf_salt(),
        )
    else:
        password_root = get_sqlcipher_key()
        if password_root is None:
            raise ValueError("legacy SQLCipher root is not unlocked")

    recovery_payload = manifest.get("recovery_sqlcipher_key")
    if isinstance(recovery_payload, dict):
        recovery_root = unwrap_dek(
            recovery_phrase,
            _legacy_record(recovery_payload),
            user_id,
            "recovery:sqlcipher",
        )
        if recovery_root != password_root:
            raise ValueError("legacy password/recovery SQLCipher roots do not match")
    return password_root


def _discard_pending_recovery(
    db: Session,
    *,
    owner_id: str,
    generation: int,
    include_password: bool,
    expected_active_generation: int,
) -> dict[str, object]:
    paths = [WrappingPath.RECOVERY.value]
    if include_password:
        paths.append(WrappingPath.PASSWORD.value)
    stale = list(
        db.scalars(
            select(SoulKeyslot).where(
                SoulKeyslot.owner_id == owner_id,
                SoulKeyslot.wrapping_path.in_(paths),
                SoulKeyslot.credential_generation == generation,
                SoulKeyslot.status == KeyslotStatus.PENDING.value,
            )
        ).all()
    )
    for row in stale:
        db.delete(row)
    if stale:
        db.commit()

    def discard(value: dict[str, object]) -> None:
        _require_active_generation(value, WrappingPath.RECOVERY, expected_active_generation)
        value["keyslots"] = [
            slot.to_dict()
            for slot in _manifest_slots(value)
            if not (
                slot.wrapping_path.value in paths
                and slot.credential_generation == generation
                and slot.status is KeyslotStatus.PENDING
            )
        ]
        marker = value.get(_PENDING_RECOVERY_CREDENTIAL)
        if isinstance(marker, dict) and marker.get("generation") == generation:
            value.pop(_PENDING_RECOVERY_CREDENTIAL, None)

    return update_core_manifest(discard)


def _unlock_recovery_generation(
    db: Session,
    *,
    credential: str,
    generation: int,
    scope: PayloadScope,
    manifest_status: KeyslotStatus,
    required_frk_versions: set[int],
) -> UnlockedKeyHierarchy:
    manifest = json.loads(get_manifest_path().read_text(encoding="utf-8"))
    core_id = str(manifest["core_id"])
    owner_id = str(manifest["owner_id"])
    slots = [
        slot
        for slot in _manifest_slots(manifest)
        if slot.wrapping_path is WrappingPath.RECOVERY
        and slot.credential_generation == generation
        and slot.status is manifest_status
        and slot.scope is scope
    ]
    if len({(slot.purpose, slot.key_version) for slot in slots}) != len(slots):
        raise ValueError("duplicate recovery generation root slots")
    sqlcipher_key: bytes | None = None
    frks: dict[int, object] = {}
    purposes: set[KeyPurpose] = set()
    for slot in slots:
        aad = manifest_keyslot_aad(
            core_id=core_id,
            owner_id=owner_id,
            purpose=slot.purpose,
            key_version=slot.key_version,
            credential_generation=slot.credential_generation,
            scope=slot.scope,
            frk_version=slot.frk_version,
            object_key_epoch=slot.object_key_epoch,
            wrapping_path=WrappingPath.RECOVERY,
        )
        secret = _unwrap_manifest_slot(credential, slot, aad)
        purposes.add(slot.purpose)
        if slot.purpose is KeyPurpose.SOUL:
            if not isinstance(secret, bytes):
                raise ValueError("Soul keyslot produced an invalid key type")
            sqlcipher_key = secret
        else:
            if slot.frk_version is None:
                raise ValueError("recovery FRK slot is missing its version")
            frks[slot.frk_version] = secret

    soul_domains: dict[str, bytes] = {}
    if scope in {PayloadScope.FULL, PayloadScope.SOUL}:
        rows = list(
            db.scalars(
                select(SoulKeyslot).where(
                    SoulKeyslot.owner_id == owner_id,
                    SoulKeyslot.wrapping_path == WrappingPath.RECOVERY.value,
                    SoulKeyslot.credential_generation == generation,
                    SoulKeyslot.status.in_(
                        [KeyslotStatus.PENDING.value, KeyslotStatus.ACTIVE.value]
                    ),
                )
            ).all()
        )
        if len({row.domain for row in rows}) != len(rows):
            raise ValueError("duplicate recovery generation Soul slots")
        for row in rows:
            aad = soul_keyslot_aad(
                core_id=core_id,
                owner_id=owner_id,
                domain=row.domain,
                key_version=row.key_version,
                credential_generation=row.credential_generation,
                wrapping_path=WrappingPath.RECOVERY,
            )
            soul_domains[row.domain] = unwrap_keyslot_secret(
                credential,
                _soul_row_record(row),
                aad,
            )

    validate_scope_completeness(
        scope,
        purposes=purposes,
        soul_domains=set(soul_domains),
        required_soul_domains=set(ALL_DOMAINS) if scope is not PayloadScope.FS else set(),
        frk_versions=set(frks),
        required_frk_versions=required_frk_versions,
    )
    return UnlockedKeyHierarchy(
        scope=scope,
        owner_id=owner_id,
        credential_generation=generation,
        sqlcipher_key=sqlcipher_key,
        soul_domains=soul_domains,
        frks=frks,
    )


@serialized_credential_transaction
def prepare_recovery_credential(
    db: Session,
    user: User,
    *,
    current_recovery_phrase: str,
    current_password: str,
    scope: PayloadScope = PayloadScope.FULL,
    replace_pending: bool = False,
    failure_injector: FailureInjector | None = None,
) -> PreparedRecoveryCredential:
    """Write and verify pending recovery material without activating it."""
    if not verify_password(current_password, user.password_hash).valid:
        raise ValueError("Invalid credentials")
    manifest = json.loads(get_manifest_path().read_text(encoding="utf-8"))
    core_id = str(manifest["core_id"])
    owner_id = str(manifest["owner_id"])
    active_generation = int(manifest.get("active_recovery_credential_generation", 0))
    _reject_live_pending_recovery(manifest, replace_pending=replace_pending)
    upgrading_legacy = active_generation == 0
    if upgrading_legacy and scope is not PayloadScope.FULL:
        raise ValueError("legacy hierarchy upgrade requires full scope")

    if upgrading_legacy:
        domains = verify_legacy_soul_keys(
            db,
            user_id=user.id,
            password=current_password,
            recovery_phrase=current_recovery_phrase,
        )
        unlocked = UnlockedKeyHierarchy(
            scope=PayloadScope.FULL,
            owner_id=owner_id,
            credential_generation=0,
            sqlcipher_key=_legacy_sqlcipher_root(
                manifest,
                user_id=user.id,
                password=current_password,
                recovery_phrase=current_recovery_phrase,
            ),
            soul_domains=domains,
            frks={1: anima_core.corefs_generate_root_key()},
        )
    else:
        unlocked = unlock_key_hierarchy(
            db,
            credential=current_recovery_phrase,
            wrapping_path=WrappingPath.RECOVERY,
            scope=scope,
        )

    generation = active_generation + 1
    required_frk_versions = (
        {1}
        if upgrading_legacy
        else (
            _required_frk_versions_from_manifest(manifest)
            if scope in {PayloadScope.FULL, PayloadScope.FS}
            else set()
        )
    )
    manifest = _discard_pending_recovery(
        db,
        owner_id=owner_id,
        generation=generation,
        include_password=upgrading_legacy,
        expected_active_generation=active_generation,
    )

    def mark_preparing(value: dict[str, object]) -> None:
        _require_active_generation(value, WrappingPath.RECOVERY, active_generation)
        _set_pending_recovery_marker(
            value,
            generation=generation,
            scope=scope,
            phase="preparing",
        )

    manifest = update_core_manifest(mark_preparing)
    new_phrase = generate_recovery_phrase()
    soul_credentials = [(new_phrase, WrappingPath.RECOVERY)]
    if upgrading_legacy:
        soul_credentials.append((current_password, WrappingPath.PASSWORD))
    if scope in {PayloadScope.FULL, PayloadScope.SOUL}:
        for credential, path in soul_credentials:
            for domain, secret in unlocked.soul_domains.items():
                db.add(
                    _build_soul_row(
                        credential,
                        secret,
                        core_id=core_id,
                        owner_id=owner_id,
                        domain=domain,
                        wrapping_path=path,
                        status=KeyslotStatus.PENDING,
                        key_version=1,
                        credential_generation=generation,
                    )
                )
        db.commit()
        _inject(failure_injector, CredentialBoundary.SOUL_PENDING_DURABLE)

    roots: list[tuple[bytes | object, KeyPurpose, int]] = []
    if unlocked.sqlcipher_key is not None:
        roots.append((unlocked.sqlcipher_key, KeyPurpose.SOUL, 1))
    roots.extend(
        (secret, KeyPurpose.FILESYSTEM_ROOT, version)
        for version, secret in sorted(unlocked.frks.items())
    )
    credentials = [(new_phrase, WrappingPath.RECOVERY)]
    if upgrading_legacy:
        credentials.append((current_password, WrappingPath.PASSWORD))
    pending_slots = [
        _manifest_slot(
            credential,
            secret,
            core_id=core_id,
            owner_id=owner_id,
            purpose=purpose,
            wrapping_path=path,
            status=KeyslotStatus.PENDING,
            scope=scope,
            key_version=version,
            credential_generation=generation,
            frk_version=version if purpose is KeyPurpose.FILESYSTEM_ROOT else None,
            object_key_epoch=(
                int(dict(manifest.get("frk_rotation", {})).get("object_key_epoch", 1))
                if purpose is KeyPurpose.FILESYSTEM_ROOT
                else None
            ),
        )
        for credential, path in credentials
        for secret, purpose, version in roots
    ]

    def write_pending(value: dict[str, object]) -> None:
        _require_active_generation(value, WrappingPath.RECOVERY, active_generation)
        value["keyslots"] = [
            *(slot.to_dict() for slot in _manifest_slots(value)),
            *(slot.to_dict() for slot in pending_slots),
        ]

    update_core_manifest(write_pending)
    _inject(failure_injector, CredentialBoundary.MANIFEST_PENDING_DURABLE)
    reopened = _unlock_recovery_generation(
        db,
        credential=new_phrase,
        generation=generation,
        scope=scope,
        manifest_status=KeyslotStatus.PENDING,
        required_frk_versions=required_frk_versions,
    )
    if (
        reopened.sqlcipher_key != unlocked.sqlcipher_key
        or reopened.frks != unlocked.frks
        or reopened.soul_domains != unlocked.soul_domains
    ):
        raise ValueError("pending recovery generation verification failed")
    if upgrading_legacy:
        if unlocked.sqlcipher_key is None:
            raise ValueError("legacy SQLCipher root is missing")
        _verify_pending_password_generation(
            db,
            password=current_password,
            generation=generation,
            scope=PayloadScope.FULL,
            required_frk_versions=required_frk_versions,
            expected_sqlcipher=unlocked.sqlcipher_key,
            expected_frks=unlocked.frks,
            expected_domains=unlocked.soul_domains,
        )
    _inject(failure_injector, CredentialBoundary.PENDING_REOPEN_VERIFIED)
    _mark_pending_recovery_ready(
        generation=generation,
        scope=scope,
        expected_active_generation=active_generation,
    )
    return PreparedRecoveryCredential(new_phrase, generation, scope)


@serialized_credential_transaction
def confirm_recovery_credential(
    db: Session,
    user: User,
    *,
    recovery_phrase: str,
    pending_generation: int,
    scope: PayloadScope,
    current_password: str,
    failure_injector: FailureInjector | None = None,
) -> None:
    """Activate only after the generated phrase is typed back and reopened."""
    if not verify_password(current_password, user.password_hash).valid:
        raise ValueError("Invalid credentials")
    manifest = json.loads(get_manifest_path().read_text(encoding="utf-8"))
    active_generation = int(manifest.get("active_recovery_credential_generation", 0))
    upgrading_legacy = pending_generation == 1
    required_frk_versions = (
        {1}
        if upgrading_legacy
        else (
            _required_frk_versions_from_manifest(manifest)
            if scope in {PayloadScope.FULL, PayloadScope.FS}
            else set()
        )
    )
    if active_generation not in {pending_generation - 1, pending_generation}:
        raise ValueError("pending recovery generation is stale")
    status = (
        KeyslotStatus.PENDING if active_generation < pending_generation else KeyslotStatus.ACTIVE
    )
    pending = _unlock_recovery_generation(
        db,
        credential=recovery_phrase,
        generation=pending_generation,
        scope=scope,
        manifest_status=status,
        required_frk_versions=required_frk_versions,
    )
    if upgrading_legacy:
        _verify_pending_password_generation(
            db,
            password=current_password,
            generation=pending_generation,
            scope=PayloadScope.FULL,
            required_frk_versions=required_frk_versions,
            expected_sqlcipher=pending.sqlcipher_key,
            expected_frks=pending.frks,
            expected_domains=pending.soul_domains,
            manifest_status=status,
        )
    _inject(failure_injector, CredentialBoundary.PENDING_REOPEN_VERIFIED)

    if active_generation < pending_generation:
        legacy_root = (
            wrap_dek(recovery_phrase, pending.sqlcipher_key, user.id, "recovery:sqlcipher")
            if pending.sqlcipher_key is not None
            else None
        )

        def activate(value: dict[str, object]) -> None:
            _require_active_generation(value, WrappingPath.RECOVERY, active_generation)
            activated = []
            for slot in _manifest_slots(value):
                selected_recovery = (
                    slot.wrapping_path is WrappingPath.RECOVERY and slot.scope is scope
                )
                selected_upgrade_password = (
                    upgrading_legacy
                    and slot.wrapping_path is WrappingPath.PASSWORD
                    and slot.scope is PayloadScope.FULL
                )
                if selected_recovery or selected_upgrade_password:
                    if (
                        slot.credential_generation == active_generation
                        and slot.status is KeyslotStatus.ACTIVE
                    ):
                        slot = replace(slot, status=KeyslotStatus.DECRYPT_ONLY)
                    elif (
                        slot.credential_generation == pending_generation
                        and slot.status is KeyslotStatus.PENDING
                    ):
                        slot = replace(slot, status=KeyslotStatus.ACTIVE)
                activated.append(slot.to_dict())
            value["keyslots"] = activated
            value["active_recovery_credential_generation"] = pending_generation
            value.pop(_PENDING_RECOVERY_CREDENTIAL, None)
            if upgrading_legacy:
                value["active_password_credential_generation"] = pending_generation
                value["frk_rotation"] = {
                    "active_version": 1,
                    "pending_version": None,
                    "decrypt_only_versions": [],
                    "phase": "idle",
                    "object_key_epoch": 1,
                }
            if legacy_root is not None:
                value["recovery_sqlcipher_key"] = _legacy_manifest_payload(legacy_root, user.id)

        update_core_manifest(activate)
        _inject(failure_injector, CredentialBoundary.MANIFEST_ACTIVATED)

    if scope in {PayloadScope.FULL, PayloadScope.SOUL}:
        rows = list(
            db.scalars(select(SoulKeyslot).where(SoulKeyslot.owner_id == pending.owner_id)).all()
        )
        for row in rows:
            selected = row.wrapping_path == WrappingPath.RECOVERY.value or (
                upgrading_legacy and row.wrapping_path == WrappingPath.PASSWORD.value
            )
            if not selected:
                continue
            if (
                row.credential_generation == pending_generation - 1
                and row.status == KeyslotStatus.ACTIVE.value
            ):
                row.status = KeyslotStatus.DECRYPT_ONLY.value
            elif (
                row.credential_generation == pending_generation
                and row.status == KeyslotStatus.PENDING.value
            ):
                row.status = KeyslotStatus.ACTIVE.value
        _rewrap_legacy_recovery_rows(
            db,
            user_id=user.id,
            recovery_phrase=recovery_phrase,
            domains=pending.soul_domains,
        )
        db.commit()
    _inject(failure_injector, CredentialBoundary.SOUL_PROMOTED)

    verified = unlock_key_hierarchy(
        db,
        credential=recovery_phrase,
        wrapping_path=WrappingPath.RECOVERY,
        scope=scope,
    )
    if (
        verified.sqlcipher_key != pending.sqlcipher_key
        or verified.frks != pending.frks
        or verified.soul_domains != pending.soul_domains
    ):
        raise ValueError("active recovery credential generation verification failed")
    if upgrading_legacy:
        verified_password = unlock_key_hierarchy(
            db,
            credential=current_password,
            wrapping_path=WrappingPath.PASSWORD,
            scope=PayloadScope.FULL,
        )
        if (
            verified_password.sqlcipher_key != pending.sqlcipher_key
            or not _filesystem_roots_match(verified_password.frks, pending.frks)
            or verified_password.soul_domains != pending.soul_domains
        ):
            raise ValueError("active password credential generation verification failed")
    _inject(failure_injector, CredentialBoundary.ACTIVE_REOPEN_VERIFIED)
