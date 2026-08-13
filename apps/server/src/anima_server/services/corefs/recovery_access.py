"""Ephemeral read-only access to an authenticated CoreFS recovery staging Core."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from collections.abc import Callable
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal
from uuid import uuid4

import anima_core

from anima_server.services.corefs import logical
from anima_server.services.corefs.archive_transfer import keyslot_snapshot_bytes
from anima_server.services.corefs.keyslots import (
    UnlockedManifestRoots,
    _manifest_secret_matches,
    _manifest_slot,
    derive_active_corefs_subkeys,
    unlock_manifest_compartment_at,
)
from anima_server.services.corefs.types import (
    KeyPurpose,
    KeyslotStatus,
    PayloadScope,
    WrappingPath,
)
from anima_server.services.recovery import generate_recovery_phrase

RecoveryBrowseOperation = Literal["stat", "list", "read"]
StageIdentity = tuple[int, int]
ControlRecord = tuple[str, int, str]
CredentialBoundaryHook = Callable[[str], None]


class CoreFsRecoveryAccessError(RuntimeError):
    """Raised when recovery browsing cannot prove its staged authority."""


@dataclass(frozen=True, slots=True)
class CoreFsRecoveryBrowseResult:
    operation: RecoveryBrowseOperation
    generation: int
    catalog_hash: str
    payload: bytes | None


@dataclass(frozen=True, slots=True)
class CoreFsRecoveryCredentialResult:
    recovery_phrase: str
    password_generation: int
    recovery_generation: int
    control_records: tuple[ControlRecord, ...]


@dataclass(slots=True)
class CoreFsRecoveryExportContext:
    core_root: Path
    manifest: dict[str, object]
    corefs_keys: object
    corefs_session: object
    expected_stage_identity: StageIdentity
    control_records: tuple[ControlRecord, ...]
    closed: bool = False

    def verify_authority(self) -> None:
        if self.closed:
            raise CoreFsRecoveryAccessError("CoreFS recovery export context is closed")
        if staged_core_identity(self.core_root) != self.expected_stage_identity:
            raise CoreFsRecoveryAccessError("recovery staging Core changed during export")
        _verify_control_records(self.core_root, self.control_records)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        begin_close = getattr(self.corefs_session, "begin_close", None)
        if callable(begin_close):
            begin_close()
        close = getattr(self.corefs_session, "close", None)
        if callable(close):
            close()


def staged_core_identity(path: Path) -> StageIdentity:
    candidate = path.expanduser()
    if candidate.is_symlink():
        raise CoreFsRecoveryAccessError("recovery staging Core must be a directory")
    resolved = candidate.resolve(strict=True)
    if not resolved.is_dir():
        raise CoreFsRecoveryAccessError("recovery staging Core must be a directory")
    stat = resolved.stat()
    return (stat.st_dev, stat.st_ino)


def open_staged_corefs_export(
    *,
    staging_path: Path,
    expected_core_id: str,
    expected_stage_identity: StageIdentity,
    control_records: tuple[ControlRecord, ...],
    credential: str,
    wrapping_path: WrappingPath,
    session_factory: Callable[[str, str], object] | None = None,
) -> CoreFsRecoveryExportContext:
    """Open one credential-bound, non-activatable staged export context."""
    if not 1 <= len(credential) <= 1024:
        raise CoreFsRecoveryAccessError("CoreFS recovery export credential is invalid")
    root = staging_path.expanduser().resolve(strict=True)
    if staged_core_identity(root) != expected_stage_identity:
        raise CoreFsRecoveryAccessError("recovery staging Core changed after import")
    _verify_control_records(root, control_records)
    manifest_bytes = (root / "manifest.json").read_bytes()
    _verify_control_payload(control_records, "manifest.json", manifest_bytes)
    manifest = _decode_recovery_manifest(manifest_bytes)
    if (
        manifest.get("core_id") != expected_core_id
        or manifest.get("archive_payload_scope") != PayloadScope.FS.value
        or manifest.get("degraded_state") != "recovery_only"
    ):
        raise CoreFsRecoveryAccessError("recovery staging manifest is not FS-scoped")

    native: object | None = None
    try:
        roots = _unlock_manifest_snapshot(
            root=root,
            manifest_bytes=manifest_bytes,
            credential=credential,
            wrapping_path=wrapping_path,
        )
        if roots.sqlcipher_key is not None or not roots.frks:
            raise CoreFsRecoveryAccessError(
                "recovery staging credential contains invalid key material"
            )
        keys = derive_active_corefs_subkeys(manifest, roots.frks)
        factory = session_factory or anima_core.CorefsSession
        native = factory(str(root), expected_core_id)
        context = CoreFsRecoveryExportContext(
            core_root=root,
            manifest=manifest,
            corefs_keys=keys,
            corefs_session=native,
            expected_stage_identity=expected_stage_identity,
            control_records=control_records,
        )
        context.verify_authority()
        return context
    except CoreFsRecoveryAccessError:
        if native is not None:
            _close_native(native)
        raise
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        if native is not None:
            _close_native(native)
        raise CoreFsRecoveryAccessError("CoreFS recovery export credential is invalid") from exc


def replace_staged_corefs_credentials(
    *,
    staging_path: Path,
    expected_core_id: str,
    expected_stage_identity: StageIdentity,
    control_records: tuple[ControlRecord, ...],
    source_credential: str,
    source_wrapping_path: WrappingPath,
    new_password: str,
    boundary_hook: CredentialBoundaryHook | None = None,
) -> CoreFsRecoveryCredentialResult:
    """Replace a staged FS-only Core's wrappers without promoting its scope.

    The source credential and unwrapped roots remain request-local.  Candidate
    password and recovery wrappers are independently reopened before either
    authenticated control file is published.  The keyslot inventory is
    replaced before the manifest authority; ordinary failures roll both files
    back byte-for-byte, while a process crash can invalidate only this
    disposable, non-activatable staging Core.
    """
    if not 8 <= len(new_password) <= 1024:
        raise CoreFsRecoveryAccessError(
            "replacement password must be between 8 and 1024 characters"
        )
    if not 1 <= len(source_credential) <= 1024:
        raise CoreFsRecoveryAccessError("source recovery credential is invalid")
    root = staging_path.expanduser().resolve(strict=True)
    if staged_core_identity(root) != expected_stage_identity:
        raise CoreFsRecoveryAccessError("recovery staging Core changed after import")
    _verify_control_records(root, control_records)

    manifest_path = root / "manifest.json"
    keyslot_path = root / "keyslots" / "root-keyslots.json"
    original_manifest = manifest_path.read_bytes()
    original_keyslots = keyslot_path.read_bytes()
    _verify_control_payload(control_records, "manifest.json", original_manifest)
    _verify_control_payload(
        control_records,
        "keyslots/root-keyslots.json",
        original_keyslots,
    )
    manifest = _decode_recovery_manifest(original_manifest)
    if (
        manifest.get("core_id") != expected_core_id
        or manifest.get("archive_payload_scope") != PayloadScope.FS.value
        or manifest.get("degraded_state") != "recovery_only"
    ):
        raise CoreFsRecoveryAccessError("recovery staging manifest is not FS-scoped")

    try:
        source = _unlock_manifest_snapshot(
            root=root,
            manifest_bytes=original_manifest,
            credential=source_credential,
            wrapping_path=source_wrapping_path,
        )
        if source.sqlcipher_key is not None or not source.frks:
            raise CoreFsRecoveryAccessError(
                "recovery staging credential contains invalid key material"
            )
        password_generation = _next_credential_generation(manifest, WrappingPath.PASSWORD)
        recovery_generation = _next_credential_generation(manifest, WrappingPath.RECOVERY)
        object_key_epoch = _stable_object_key_epoch(manifest)
        recovery_phrase = generate_recovery_phrase()
        core_id = str(manifest["core_id"])
        owner_id = str(manifest["owner_id"])
        candidate = deepcopy(manifest)
        candidate["keyslots"] = [
            _manifest_slot(
                credential,
                root_key,
                core_id=core_id,
                owner_id=owner_id,
                purpose=KeyPurpose.FILESYSTEM_ROOT,
                wrapping_path=wrapping_path,
                status=KeyslotStatus.ACTIVE,
                scope=PayloadScope.FS,
                key_version=frk_version,
                credential_generation=generation,
                frk_version=frk_version,
                object_key_epoch=object_key_epoch,
            ).to_dict()
            for credential, wrapping_path, generation in (
                (new_password, WrappingPath.PASSWORD, password_generation),
                (recovery_phrase, WrappingPath.RECOVERY, recovery_generation),
            )
            for frk_version, root_key in sorted(source.frks.items())
        ]
        candidate["active_password_credential_generation"] = password_generation
        candidate["active_recovery_credential_generation"] = recovery_generation
        candidate["archive_payload_scope"] = PayloadScope.FS.value
        candidate["degraded_state"] = "recovery_only"
        candidate.pop("pending_recovery_credential", None)

        candidate_manifest = _canonical_json(candidate)
        candidate_keyslots = keyslot_snapshot_bytes(candidate)
        _verify_candidate_credentials(
            root=root,
            manifest_bytes=candidate_manifest,
            password=new_password,
            recovery_phrase=recovery_phrase,
            expected_roots=source.frks,
        )

        try:
            _atomic_replace_file(keyslot_path, candidate_keyslots)
            _credential_boundary(boundary_hook, "keyslots_durable")
            _atomic_replace_file(manifest_path, candidate_manifest)
            _credential_boundary(boundary_hook, "manifest_durable")
            reopened_password = unlock_manifest_compartment_at(
                manifest_path,
                credential=new_password,
                wrapping_path=WrappingPath.PASSWORD,
                compartment=PayloadScope.FS,
            )
            reopened_recovery = unlock_manifest_compartment_at(
                manifest_path,
                credential=recovery_phrase,
                wrapping_path=WrappingPath.RECOVERY,
                compartment=PayloadScope.FS,
            )
            if not _roots_match(source.frks, reopened_password.frks) or not _roots_match(
                source.frks, reopened_recovery.frks
            ):
                raise CoreFsRecoveryAccessError(
                    "published recovery credentials failed independent verification"
                )
        except BaseException:
            _restore_control_files(
                manifest_path=manifest_path,
                manifest_bytes=original_manifest,
                keyslot_path=keyslot_path,
                keyslot_bytes=original_keyslots,
            )
            raise
    except CoreFsRecoveryAccessError:
        raise
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise CoreFsRecoveryAccessError("CoreFS recovery credential replacement failed") from exc

    try:
        if staged_core_identity(root) != expected_stage_identity:
            raise CoreFsRecoveryAccessError("recovery staging Core changed during replacement")
        updated_records = _updated_control_records(
            control_records,
            {
                "manifest.json": candidate_manifest,
                "keyslots/root-keyslots.json": candidate_keyslots,
            },
        )
        _verify_control_records(root, updated_records)
    except BaseException:
        _restore_control_files(
            manifest_path=manifest_path,
            manifest_bytes=original_manifest,
            keyslot_path=keyslot_path,
            keyslot_bytes=original_keyslots,
        )
        raise
    return CoreFsRecoveryCredentialResult(
        recovery_phrase=recovery_phrase,
        password_generation=password_generation,
        recovery_generation=recovery_generation,
        control_records=updated_records,
    )


def browse_staged_corefs(
    *,
    staging_path: Path,
    expected_core_id: str,
    expected_generation: int,
    expected_stage_identity: StageIdentity,
    control_records: tuple[ControlRecord, ...],
    credential: str,
    wrapping_path: WrappingPath,
    operation: RecoveryBrowseOperation,
    logical_path: str,
    cursor_after: str | None = None,
    cursor_generation: int | None = None,
    limit: int = 100,
    offset: int = 0,
    max_bytes: int = 65_536,
    response_bytes: int | None = None,
    session_factory: Callable[[str, str], object] | None = None,
) -> CoreFsRecoveryBrowseResult:
    """Open one FS-scoped recovery credential for one bounded read request.

    Nothing is cached: the credential, unwrapped FRKs, derived subkeys, and
    native session live only for this call.  Authenticated control-record
    hashes bind the browse to the exact archive extraction retained by the
    import operation rather than to an interchangeable path.
    """
    root = staging_path.expanduser().resolve(strict=True)
    if staged_core_identity(root) != expected_stage_identity:
        raise CoreFsRecoveryAccessError("recovery staging Core changed after import")
    _verify_control_records(root, control_records)
    manifest_path = root / "manifest.json"
    manifest = _read_recovery_manifest(manifest_path)
    if (
        manifest.get("core_id") != expected_core_id
        or manifest.get("archive_payload_scope") != PayloadScope.FS.value
        or manifest.get("degraded_state") != "recovery_only"
    ):
        raise CoreFsRecoveryAccessError("recovery staging manifest is not FS-scoped")

    try:
        roots = unlock_manifest_compartment_at(
            manifest_path,
            credential=credential,
            wrapping_path=wrapping_path,
            compartment=PayloadScope.FS,
        )
        keys = derive_active_corefs_subkeys(manifest, roots.frks)
        factory = session_factory or anima_core.CorefsSession
        with _validation_pointer_alias(root, control_records):
            native = factory(str(root), expected_core_id)
            try:
                selected = logical.select_validation_snapshot(
                    corefs_session=native,
                    keys=keys,
                )
                if selected.generation != expected_generation:
                    raise CoreFsRecoveryAccessError(
                        "recovery staging filesystem generation changed after import"
                    )
                if cursor_generation is not None and cursor_generation != selected.generation:
                    raise CoreFsRecoveryAccessError("recovery browse cursor generation is stale")
                common = {
                    "corefs_session": native,
                    "keys": keys,
                    "selected": selected,
                }
                if operation == "stat":
                    payload = logical.stat_v1(**common, path=logical_path)
                elif operation == "list":
                    payload = logical.list_v1(
                        **common,
                        path=logical_path,
                        cursor_after=cursor_after,
                        limit=limit,
                        response_bytes=response_bytes,
                    )
                elif operation == "read":
                    payload = logical.read_chunk_v1(
                        **common,
                        path=logical_path,
                        offset=offset,
                        max_bytes=max_bytes,
                        response_bytes=response_bytes,
                    )
                else:
                    raise CoreFsRecoveryAccessError("unsupported recovery browse operation")
            finally:
                begin_close = getattr(native, "begin_close", None)
                if callable(begin_close):
                    begin_close()
                close = getattr(native, "close", None)
                if callable(close):
                    close()
    except CoreFsRecoveryAccessError:
        raise
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise CoreFsRecoveryAccessError("CoreFS recovery credential or catalog is invalid") from exc

    if staged_core_identity(root) != expected_stage_identity:
        raise CoreFsRecoveryAccessError("recovery staging Core changed during browse")
    _verify_control_records(root, control_records)
    return CoreFsRecoveryBrowseResult(
        operation=operation,
        generation=selected.generation,
        catalog_hash=selected.catalog_hash,
        payload=payload,
    )


def _next_credential_generation(
    manifest: dict[str, object],
    wrapping_path: WrappingPath,
) -> int:
    field = (
        "active_password_credential_generation"
        if wrapping_path is WrappingPath.PASSWORD
        else "active_recovery_credential_generation"
    )
    generations: list[int] = []
    active = manifest.get(field)
    if active is not None:
        if isinstance(active, bool) or not isinstance(active, int) or active <= 0:
            raise CoreFsRecoveryAccessError("recovery credential generation is invalid")
        generations.append(active)
    slots = manifest.get("keyslots")
    if not isinstance(slots, list):
        raise CoreFsRecoveryAccessError("recovery staging keyslots are invalid")
    for raw in slots:
        if not isinstance(raw, dict):
            raise CoreFsRecoveryAccessError("recovery staging keyslots are invalid")
        if raw.get("wrapping_path") != wrapping_path.value:
            continue
        generation = raw.get("credential_generation")
        if isinstance(generation, bool) or not isinstance(generation, int) or generation <= 0:
            raise CoreFsRecoveryAccessError("recovery credential generation is invalid")
        generations.append(generation)
    next_generation = max(generations, default=0) + 1
    if next_generation > (1 << 63) - 1:
        raise CoreFsRecoveryAccessError("recovery credential generation is exhausted")
    return next_generation


def _stable_object_key_epoch(manifest: dict[str, object]) -> int:
    rotation = manifest.get("frk_rotation")
    if not isinstance(rotation, dict):
        raise CoreFsRecoveryAccessError("recovery FRK rotation state is invalid")
    if rotation.get("pending_version") is not None or rotation.get("phase", "idle") != "idle":
        raise CoreFsRecoveryAccessError("recovery credentials cannot change during an FRK rotation")
    epoch = rotation.get("object_key_epoch", 1)
    if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch <= 0:
        raise CoreFsRecoveryAccessError("recovery object-key epoch is invalid")
    return epoch


def _canonical_json(value: dict[str, object]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _unlock_manifest_snapshot(
    *,
    root: Path,
    manifest_bytes: bytes,
    credential: str,
    wrapping_path: WrappingPath,
) -> UnlockedManifestRoots:
    snapshot_path = root / f".manifest-credential-source-{uuid4().hex}.json"
    descriptor = os.open(snapshot_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(manifest_bytes)
            handle.flush()
            os.fsync(handle.fileno())
        _fsync_directory(root)
        return unlock_manifest_compartment_at(
            snapshot_path,
            credential=credential,
            wrapping_path=wrapping_path,
            compartment=PayloadScope.FS,
        )
    finally:
        snapshot_path.unlink(missing_ok=True)
        _fsync_directory(root)


def _verify_candidate_credentials(
    *,
    root: Path,
    manifest_bytes: bytes,
    password: str,
    recovery_phrase: str,
    expected_roots: dict[int, object],
) -> None:
    candidate_path = root / f".manifest-credential-check-{uuid4().hex}.json"
    descriptor = os.open(candidate_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(manifest_bytes)
            handle.flush()
            os.fsync(handle.fileno())
        _fsync_directory(root)
        password_roots = unlock_manifest_compartment_at(
            candidate_path,
            credential=password,
            wrapping_path=WrappingPath.PASSWORD,
            compartment=PayloadScope.FS,
        )
        recovery_roots = unlock_manifest_compartment_at(
            candidate_path,
            credential=recovery_phrase,
            wrapping_path=WrappingPath.RECOVERY,
            compartment=PayloadScope.FS,
        )
        if not _roots_match(expected_roots, password_roots.frks) or not _roots_match(
            expected_roots, recovery_roots.frks
        ):
            raise CoreFsRecoveryAccessError(
                "candidate recovery credentials failed independent verification"
            )
    finally:
        candidate_path.unlink(missing_ok=True)
        _fsync_directory(root)


def _roots_match(first: dict[int, object], second: dict[int, object]) -> bool:
    return set(first) == set(second) and all(
        _manifest_secret_matches(first[version], second[version]) for version in first
    )


def _close_native(native: object) -> None:
    begin_close = getattr(native, "begin_close", None)
    if callable(begin_close):
        begin_close()
    close = getattr(native, "close", None)
    if callable(close):
        close()


def _atomic_replace_file(path: Path, payload: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _restore_control_files(
    *,
    manifest_path: Path,
    manifest_bytes: bytes,
    keyslot_path: Path,
    keyslot_bytes: bytes,
) -> None:
    _atomic_replace_file(manifest_path, manifest_bytes)
    _atomic_replace_file(keyslot_path, keyslot_bytes)


def _credential_boundary(hook: CredentialBoundaryHook | None, boundary: str) -> None:
    if hook is not None:
        hook(boundary)


def _updated_control_records(
    records: tuple[ControlRecord, ...],
    replacements: dict[str, bytes],
) -> tuple[ControlRecord, ...]:
    remaining = set(replacements)
    updated: list[ControlRecord] = []
    for relative, length, digest in records:
        payload = replacements.get(relative)
        if payload is None:
            updated.append((relative, length, digest))
            continue
        updated.append((relative, len(payload), hashlib.sha256(payload).hexdigest()))
        remaining.discard(relative)
    if remaining:
        raise CoreFsRecoveryAccessError("recovery staging control inventory is incomplete")
    return tuple(updated)


def _verify_control_payload(
    records: tuple[ControlRecord, ...],
    relative: str,
    payload: bytes,
) -> None:
    candidates = [(length, digest) for path, length, digest in records if path == relative]
    if len(candidates) != 1:
        raise CoreFsRecoveryAccessError("recovery staging control inventory is incomplete")
    expected_length, expected_digest = candidates[0]
    if len(payload) != expected_length or not hmac.compare_digest(
        hashlib.sha256(payload).hexdigest(), expected_digest
    ):
        raise CoreFsRecoveryAccessError("recovery staging control record changed")


def _decode_recovery_manifest(payload: bytes) -> dict[str, object]:
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CoreFsRecoveryAccessError("recovery staging manifest is invalid") from exc
    if not isinstance(value, dict):
        raise CoreFsRecoveryAccessError("recovery staging manifest is invalid")
    return value


def _read_recovery_manifest(path: Path) -> dict[str, object]:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise CoreFsRecoveryAccessError("recovery staging manifest is invalid") from exc
    return _decode_recovery_manifest(payload)


def _verify_control_records(root: Path, records: tuple[ControlRecord, ...]) -> None:
    if not records:
        raise CoreFsRecoveryAccessError("recovery staging control inventory is missing")
    for relative, expected_length, expected_digest in records:
        pure = PurePosixPath(relative)
        if (
            pure.is_absolute()
            or not pure.parts
            or any(part in {"", ".", ".."} for part in pure.parts)
        ):
            raise CoreFsRecoveryAccessError("recovery staging control path is invalid")
        path = root
        for component in pure.parts:
            path = path / component
            if path.is_symlink():
                raise CoreFsRecoveryAccessError("recovery staging control path contains a link")
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(root) or not resolved.is_file():
            raise CoreFsRecoveryAccessError("recovery staging control record is invalid")
        digest = hashlib.sha256()
        length = 0
        with resolved.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                length += len(chunk)
                digest.update(chunk)
        if length != expected_length or not hmac.compare_digest(
            digest.hexdigest(), expected_digest
        ):
            raise CoreFsRecoveryAccessError("recovery staging control record changed")


@contextmanager
def _validation_pointer_alias(
    root: Path,
    records: tuple[ControlRecord, ...],
):
    """Make an authenticated HEAD readable through the pre-cutover selector."""
    control = {path: (length, digest) for path, length, digest in records}
    head_record = control.get("fs/HEAD")
    if head_record is None:
        raise CoreFsRecoveryAccessError("recovery staging HEAD is missing")
    head = root / "fs" / "HEAD"
    alias = root / "fs" / "VALIDATION_HEAD"
    remove_alias = False
    if alias.exists() or alias.is_symlink():
        authenticated_alias = control.get("fs/VALIDATION_HEAD")
        if authenticated_alias is None:
            _verify_one_control_record(root, "fs/VALIDATION_HEAD", *head_record)
            remove_alias = True
    else:
        descriptor = os.open(alias, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            with head.open("rb") as source, os.fdopen(descriptor, "wb") as target:
                while chunk := source.read(1024 * 1024):
                    target.write(chunk)
                target.flush()
                os.fsync(target.fileno())
            _fsync_directory(alias.parent)
            _verify_one_control_record(root, "fs/VALIDATION_HEAD", *head_record)
            remove_alias = True
        except BaseException:
            alias.unlink(missing_ok=True)
            raise
    try:
        yield
    finally:
        if remove_alias:
            alias.unlink(missing_ok=True)
            _fsync_directory(alias.parent)


def _verify_one_control_record(
    root: Path,
    relative: str,
    expected_length: int,
    expected_digest: str,
) -> None:
    path = root / PurePosixPath(relative)
    if path.is_symlink() or not path.is_file():
        raise CoreFsRecoveryAccessError("recovery staging derived pointer is invalid")
    digest = hashlib.sha256()
    length = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            length += len(chunk)
            digest.update(chunk)
    if length != expected_length or not hmac.compare_digest(digest.hexdigest(), expected_digest):
        raise CoreFsRecoveryAccessError("recovery staging derived pointer changed")


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
