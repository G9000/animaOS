"""Ephemeral read-only access to an authenticated CoreFS recovery staging Core."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

import anima_core

from anima_server.services.corefs import logical
from anima_server.services.corefs.keyslots import (
    derive_active_corefs_subkeys,
    unlock_manifest_compartment_at,
)
from anima_server.services.corefs.types import PayloadScope, WrappingPath

RecoveryBrowseOperation = Literal["stat", "list", "read"]
StageIdentity = tuple[int, int]
ControlRecord = tuple[str, int, str]


class CoreFsRecoveryAccessError(RuntimeError):
    """Raised when recovery browsing cannot prove its staged authority."""


@dataclass(frozen=True, slots=True)
class CoreFsRecoveryBrowseResult:
    operation: RecoveryBrowseOperation
    generation: int
    catalog_hash: str
    payload: bytes | None


def staged_core_identity(path: Path) -> StageIdentity:
    candidate = path.expanduser()
    if candidate.is_symlink():
        raise CoreFsRecoveryAccessError("recovery staging Core must be a directory")
    resolved = candidate.resolve(strict=True)
    if not resolved.is_dir():
        raise CoreFsRecoveryAccessError("recovery staging Core must be a directory")
    stat = resolved.stat()
    return (stat.st_dev, stat.st_ino)


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


def _read_recovery_manifest(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CoreFsRecoveryAccessError("recovery staging manifest is invalid") from exc
    if not isinstance(value, dict):
        raise CoreFsRecoveryAccessError("recovery staging manifest is invalid")
    return value


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
