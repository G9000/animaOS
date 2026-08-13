from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import secrets
import shutil
import struct
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO, cast
from uuid import UUID, uuid4

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from anima_server.services.corefs.cutover import CutoverState, read_cutover_record
from anima_server.services.corefs.instance_registry import RuntimeInstanceBinding
from anima_server.services.credentials import (
    CredentialStore,
    credential_reference,
    credential_store,
)

_MAGIC = b"ANIMART1"
_TRAILER = b"ANIMAREND"
_VERSION = 1
_HEADER = struct.Struct(">8sHH16s4s")
_CHUNK_BYTES = 1024 * 1024
_TAG_BYTES = 16
_KEY_BYTES = 32
_MAX_MANIFEST_BYTES = 8 * 1024 * 1024
_MAX_FILES = 1_000_000
_MAX_PATH_BYTES = 32 * 1024
_MAX_LENGTH_PREFIX = _MAX_MANIFEST_BYTES + _TAG_BYTES
_CREDENTIAL_DOMAIN = b"anima-legacy-runtime-recovery-credential-v1\x00"
_INVENTORY_DOMAIN = b"anima-legacy-runtime-recovery-inventory-v1\x00"
_MANIFEST_AAD_DOMAIN = b"anima-legacy-runtime-recovery-manifest-v1\x00"
_CHUNK_AAD_DOMAIN = b"anima-legacy-runtime-recovery-chunk-v1\x00"
_FOOTER_AAD_DOMAIN = b"anima-legacy-runtime-recovery-footer-v1\x00"


class LegacyRuntimeRecoveryError(RuntimeError):
    """Raised when legacy Runtime recovery cannot be proven safe."""


@dataclass(frozen=True, slots=True)
class LegacyRuntimeRecoveryBundle:
    path: Path
    bundle_id: str
    file_count: int
    plaintext_bytes: int
    inventory_digest: str


@dataclass(frozen=True, slots=True)
class _SourceRecord:
    ordinal: int
    relative_path: str
    source_path: Path
    plaintext_length: int
    chunk_count: int
    sha256: str
    identity: tuple[int, int, int, int]


BoundaryHook = Callable[[str], None]


def prepare_legacy_runtime_recovery_bundle(
    binding: RuntimeInstanceBinding,
    *,
    postgres_running: bool,
    store: CredentialStore | None = None,
    boundary_hook: BoundaryHook | None = None,
) -> LegacyRuntimeRecoveryBundle:
    """Create and re-verify an encrypted recovery bundle without deleting source."""
    if postgres_running:
        raise LegacyRuntimeRecoveryError("legacy Runtime recovery requires PostgreSQL is stopped")
    source = binding.legacy_pg_data_dir.expanduser()
    _require_instance_path(binding, source)
    _reject_link_chain(source, boundary=binding.instance_root)
    if not source.is_dir():
        raise LegacyRuntimeRecoveryError("legacy Runtime source is unavailable")

    target = _bundle_path(binding)
    target.parent.mkdir(parents=True, exist_ok=True)
    _reject_link_chain(target, boundary=binding.instance_root)
    partial = target.with_name(f".{target.name}.partial")
    records, inventory_digest, plaintext_bytes = _inventory(source)
    key = _load_or_create_key(
        binding,
        store or credential_store(),
        allow_create=not target.exists() and not partial.exists(),
    )

    if target.exists():
        verified = verify_legacy_runtime_recovery_bundle(binding, store=store)
        _require_expected_inventory(
            verified,
            records=records,
            plaintext_bytes=plaintext_bytes,
            inventory_digest=inventory_digest,
        )
        return verified

    if partial.exists():
        verified = _verify_bundle_file(binding, partial, key=key)
        _require_expected_inventory(
            verified,
            records=records,
            plaintext_bytes=plaintext_bytes,
            inventory_digest=inventory_digest,
        )
        _publish_create_only(partial, target)
        published = verify_legacy_runtime_recovery_bundle(binding, store=store)
        _require_expected_inventory(
            published,
            records=records,
            plaintext_bytes=plaintext_bytes,
            inventory_digest=inventory_digest,
        )
        partial.unlink(missing_ok=True)
        _fsync_directory(target.parent)
        return published

    bundle_id = uuid4()
    nonce_prefix = secrets.token_bytes(4)
    header = _HEADER.pack(
        _MAGIC,
        _VERSION,
        _HEADER.size,
        bundle_id.bytes,
        nonce_prefix,
    )
    header_hash = hashlib.sha256(header).digest()
    manifest = {
        "version": 1,
        "bundleId": str(bundle_id),
        "coreIdentityHash": _identity_hash("core", binding.core_id),
        "instanceIdentityHash": _identity_hash("instance", binding.local_instance_id),
        "fileCount": len(records),
        "plaintextBytes": plaintext_bytes,
        "inventoryDigest": inventory_digest,
        "records": [
            {
                "ordinal": record.ordinal,
                "path": record.relative_path,
                "plaintextLength": record.plaintext_length,
                "chunkCount": record.chunk_count,
                "sha256": record.sha256,
            }
            for record in records
        ],
    }
    manifest_bytes = _canonical_json(manifest)
    if len(manifest_bytes) > _MAX_MANIFEST_BYTES:
        raise LegacyRuntimeRecoveryError("legacy Runtime recovery manifest exceeds its bound")

    descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(header)
            encrypted_manifest = AESGCM(key).encrypt(
                _nonce(nonce_prefix, 0),
                manifest_bytes,
                _envelope_aad(_MANIFEST_AAD_DOMAIN, header_hash),
            )
            _write_prefixed(output, encrypted_manifest)
            _boundary(boundary_hook, "legacy-runtime-recovery:after_manifest")
            ordinal = 1
            for record in records:
                streamed_hash = hashlib.sha256()
                offset = 0
                _reject_link(record.source_path)
                if _file_identity(record.source_path) != record.identity:
                    raise LegacyRuntimeRecoveryError(
                        "legacy Runtime source changed after recovery inventory"
                    )
                with record.source_path.open("rb") as source_handle:
                    for chunk_index in range(record.chunk_count):
                        plaintext = source_handle.read(_CHUNK_BYTES)
                        if not plaintext and record.plaintext_length != 0:
                            raise LegacyRuntimeRecoveryError(
                                "legacy Runtime source changed while bundling"
                            )
                        streamed_hash.update(plaintext)
                        ciphertext = AESGCM(key).encrypt(
                            _nonce(nonce_prefix, ordinal),
                            plaintext,
                            _chunk_aad(
                                header_hash=header_hash,
                                bundle_id=bundle_id,
                                record=record,
                                chunk_index=chunk_index,
                                offset=offset,
                                plaintext_length=len(plaintext),
                            ),
                        )
                        _write_prefixed(output, ciphertext)
                        offset += len(plaintext)
                        ordinal += 1
                    if source_handle.read(1):
                        raise LegacyRuntimeRecoveryError(
                            "legacy Runtime source changed while bundling"
                        )
                if (
                    offset != record.plaintext_length
                    or streamed_hash.hexdigest() != record.sha256
                    or _file_identity(record.source_path) != record.identity
                ):
                    raise LegacyRuntimeRecoveryError("legacy Runtime source changed while bundling")
            footer = _canonical_json(
                {
                    "version": 1,
                    "bundleId": str(bundle_id),
                    "fileCount": len(records),
                    "plaintextBytes": plaintext_bytes,
                    "inventoryDigest": inventory_digest,
                    "manifestSha256": hashlib.sha256(manifest_bytes).hexdigest(),
                }
            )
            encrypted_footer = AESGCM(key).encrypt(
                _nonce(nonce_prefix, ordinal),
                footer,
                _envelope_aad(
                    _FOOTER_AAD_DOMAIN,
                    header_hash,
                    hashlib.sha256(manifest_bytes).digest(),
                ),
            )
            _write_prefixed(output, encrypted_footer)
            output.write(_TRAILER)
            output.flush()
            os.fsync(output.fileno())
        _boundary(boundary_hook, "legacy-runtime-recovery:after_file_fsync")
        _publish_create_only(partial, target)
        _boundary(boundary_hook, "legacy-runtime-recovery:after_publish")
    except BaseException:
        partial.unlink(missing_ok=True)
        raise

    verified = verify_legacy_runtime_recovery_bundle(binding, store=store)
    _require_expected_inventory(
        verified,
        records=records,
        plaintext_bytes=plaintext_bytes,
        inventory_digest=inventory_digest,
    )
    partial.unlink(missing_ok=True)
    _fsync_directory(target.parent)
    return verified


def verify_legacy_runtime_recovery_bundle(
    binding: RuntimeInstanceBinding,
    *,
    store: CredentialStore | None = None,
) -> LegacyRuntimeRecoveryBundle:
    target = _bundle_path(binding)
    _require_instance_path(binding, target)
    _reject_link_chain(target, boundary=binding.instance_root)
    key = _load_or_create_key(binding, store or credential_store(), allow_create=False)
    return _verify_bundle_file(binding, target, key=key)


def retire_legacy_runtime_plaintext(
    binding: RuntimeInstanceBinding,
    *,
    postgres_running: bool,
    store: CredentialStore | None = None,
) -> LegacyRuntimeRecoveryBundle:
    """Delete legacy plaintext only after irreversible authority and recovery proof."""
    if read_cutover_record().state is not CutoverState.CORE_FS_AUTHORITATIVE_FORWARD_ONLY:
        raise LegacyRuntimeRecoveryError(
            "legacy Runtime plaintext retirement requires forward-only CoreFS authority"
        )
    if postgres_running:
        raise LegacyRuntimeRecoveryError(
            "legacy Runtime plaintext retirement requires PostgreSQL is stopped"
        )
    fresh = binding.pg_data_dir.expanduser()
    _require_instance_path(binding, fresh)
    _reject_link_chain(fresh, boundary=binding.instance_root)
    if not fresh.is_dir() or not (fresh / "PG_VERSION").is_file():
        raise LegacyRuntimeRecoveryError("fresh Runtime database is not ready")
    verified = verify_legacy_runtime_recovery_bundle(binding, store=store)
    source = binding.legacy_pg_data_dir.expanduser()
    _require_instance_path(binding, source)
    _reject_link_chain(source, boundary=binding.instance_root)
    if source.exists():
        shutil.rmtree(source)
        _fsync_directory(source.parent)
    if source.exists():
        raise LegacyRuntimeRecoveryError("legacy Runtime plaintext retirement was incomplete")
    return verified


def _verify_bundle_file(
    binding: RuntimeInstanceBinding,
    path: Path,
    *,
    key: bytes,
) -> LegacyRuntimeRecoveryBundle:
    _reject_link(path)
    if not path.is_file():
        raise LegacyRuntimeRecoveryError("legacy Runtime recovery bundle is unavailable")
    try:
        with path.open("rb") as handle:
            header = handle.read(_HEADER.size)
            if len(header) != _HEADER.size:
                raise LegacyRuntimeRecoveryError("legacy Runtime recovery header is truncated")
            magic, version, header_length, raw_bundle_id, nonce_prefix = _HEADER.unpack(header)
            if magic != _MAGIC or version != _VERSION or header_length != _HEADER.size:
                raise LegacyRuntimeRecoveryError("legacy Runtime recovery header is invalid")
            bundle_id = UUID(bytes=raw_bundle_id)
            header_hash = hashlib.sha256(header).digest()
            encrypted_manifest = _read_prefixed(handle, _MAX_LENGTH_PREFIX)
            manifest_bytes = AESGCM(key).decrypt(
                _nonce(nonce_prefix, 0),
                encrypted_manifest,
                _envelope_aad(_MANIFEST_AAD_DOMAIN, header_hash),
            )
            manifest = _parse_manifest(binding, manifest_bytes, bundle_id=bundle_id)
            records = cast(list[dict[str, object]], manifest["records"])
            ordinal = 1
            for expected_ordinal, raw_record in enumerate(records):
                record = _parse_record(raw_record, expected_ordinal=expected_ordinal)
                streamed_hash = hashlib.sha256()
                offset = 0
                for chunk_index in range(record.chunk_count):
                    ciphertext = _read_prefixed(handle, _CHUNK_BYTES + _TAG_BYTES)
                    plaintext = AESGCM(key).decrypt(
                        _nonce(nonce_prefix, ordinal),
                        ciphertext,
                        _chunk_aad(
                            header_hash=header_hash,
                            bundle_id=bundle_id,
                            record=record,
                            chunk_index=chunk_index,
                            offset=offset,
                            plaintext_length=len(ciphertext) - _TAG_BYTES,
                        ),
                    )
                    streamed_hash.update(plaintext)
                    offset += len(plaintext)
                    ordinal += 1
                if offset != record.plaintext_length or streamed_hash.hexdigest() != record.sha256:
                    raise LegacyRuntimeRecoveryError(
                        "legacy Runtime recovery record authentication failed"
                    )
            manifest_hash = hashlib.sha256(manifest_bytes).digest()
            encrypted_footer = _read_prefixed(handle, _MAX_LENGTH_PREFIX)
            footer_bytes = AESGCM(key).decrypt(
                _nonce(nonce_prefix, ordinal),
                encrypted_footer,
                _envelope_aad(_FOOTER_AAD_DOMAIN, header_hash, manifest_hash),
            )
            footer = json.loads(footer_bytes)
            expected_footer = {
                "version": 1,
                "bundleId": str(bundle_id),
                "fileCount": manifest["fileCount"],
                "plaintextBytes": manifest["plaintextBytes"],
                "inventoryDigest": manifest["inventoryDigest"],
                "manifestSha256": manifest_hash.hex(),
            }
            if footer != expected_footer:
                raise LegacyRuntimeRecoveryError("legacy Runtime recovery footer is invalid")
            if handle.read(len(_TRAILER)) != _TRAILER or handle.read(1):
                raise LegacyRuntimeRecoveryError(
                    "legacy Runtime recovery completion marker is invalid"
                )
    except (InvalidTag, json.JSONDecodeError, OSError, ValueError) as exc:
        if isinstance(exc, LegacyRuntimeRecoveryError):
            raise
        raise LegacyRuntimeRecoveryError("legacy Runtime recovery authentication failed") from exc
    return LegacyRuntimeRecoveryBundle(
        path=path.resolve(strict=True),
        bundle_id=str(bundle_id),
        file_count=cast(int, manifest["fileCount"]),
        plaintext_bytes=cast(int, manifest["plaintextBytes"]),
        inventory_digest=cast(str, manifest["inventoryDigest"]),
    )


def _parse_manifest(
    binding: RuntimeInstanceBinding,
    encoded: bytes,
    *,
    bundle_id: UUID,
) -> dict[str, object]:
    if len(encoded) > _MAX_MANIFEST_BYTES:
        raise LegacyRuntimeRecoveryError("legacy Runtime recovery manifest exceeds its bound")
    raw = json.loads(encoded)
    if not isinstance(raw, dict) or set(raw) != {
        "version",
        "bundleId",
        "coreIdentityHash",
        "instanceIdentityHash",
        "fileCount",
        "plaintextBytes",
        "inventoryDigest",
        "records",
    }:
        raise LegacyRuntimeRecoveryError("legacy Runtime recovery manifest is invalid")
    records = raw.get("records")
    file_count = raw.get("fileCount")
    plaintext_bytes = raw.get("plaintextBytes")
    if (
        raw.get("version") != 1
        or raw.get("bundleId") != str(bundle_id)
        or raw.get("coreIdentityHash") != _identity_hash("core", binding.core_id)
        or raw.get("instanceIdentityHash") != _identity_hash("instance", binding.local_instance_id)
        or not isinstance(records, list)
        or isinstance(file_count, bool)
        or not isinstance(file_count, int)
        or file_count != len(records)
        or file_count <= 0
        or file_count > _MAX_FILES
        or isinstance(plaintext_bytes, bool)
        or not isinstance(plaintext_bytes, int)
        or plaintext_bytes < 0
        or not _is_sha256(raw.get("inventoryDigest"))
    ):
        raise LegacyRuntimeRecoveryError("legacy Runtime recovery manifest is invalid")
    parsed = [_parse_record(record, expected_ordinal=index) for index, record in enumerate(records)]
    if sum(record.plaintext_length for record in parsed) != plaintext_bytes:
        raise LegacyRuntimeRecoveryError("legacy Runtime recovery inventory is invalid")
    if _inventory_digest(parsed) != raw["inventoryDigest"]:
        raise LegacyRuntimeRecoveryError("legacy Runtime recovery inventory is invalid")
    return raw


def _parse_record(raw: object, *, expected_ordinal: int) -> _SourceRecord:
    if not isinstance(raw, dict) or set(raw) != {
        "ordinal",
        "path",
        "plaintextLength",
        "chunkCount",
        "sha256",
    }:
        raise LegacyRuntimeRecoveryError("legacy Runtime recovery record is invalid")
    path = raw.get("path")
    length = raw.get("plaintextLength")
    chunks = raw.get("chunkCount")
    if (
        raw.get("ordinal") != expected_ordinal
        or not isinstance(path, str)
        or not _safe_relative_path(path)
        or isinstance(length, bool)
        or not isinstance(length, int)
        or length < 0
        or isinstance(chunks, bool)
        or not isinstance(chunks, int)
        or chunks != _chunk_count(length)
        or not _is_sha256(raw.get("sha256"))
    ):
        raise LegacyRuntimeRecoveryError("legacy Runtime recovery record is invalid")
    return _SourceRecord(
        ordinal=expected_ordinal,
        relative_path=path,
        source_path=Path(),
        plaintext_length=length,
        chunk_count=chunks,
        sha256=cast(str, raw["sha256"]),
        identity=(0, 0, 0, 0),
    )


def _inventory(root: Path) -> tuple[list[_SourceRecord], str, int]:
    records: list[_SourceRecord] = []
    for directory, child_directories, child_files in os.walk(
        root,
        topdown=True,
        followlinks=False,
    ):
        directory_path = Path(directory)
        child_directories.sort()
        child_files.sort()
        for name in child_directories:
            _reject_link(directory_path / name)
        for name in child_files:
            path = directory_path / name
            _reject_link(path)
            relative = path.relative_to(root).as_posix()
            if not _safe_relative_path(relative):
                raise LegacyRuntimeRecoveryError("legacy Runtime recovery source path is invalid")
            identity = _file_identity(path)
            length = identity[2]
            digest = _hash_file(path)
            if _file_identity(path) != identity:
                raise LegacyRuntimeRecoveryError(
                    "legacy Runtime source changed during recovery inventory"
                )
            records.append(
                _SourceRecord(
                    ordinal=len(records),
                    relative_path=relative,
                    source_path=path,
                    plaintext_length=length,
                    chunk_count=_chunk_count(length),
                    sha256=digest,
                    identity=identity,
                )
            )
            if len(records) > _MAX_FILES:
                raise LegacyRuntimeRecoveryError(
                    "legacy Runtime recovery file count exceeds its bound"
                )
    if not records:
        raise LegacyRuntimeRecoveryError("legacy Runtime source contains no files")
    plaintext_bytes = sum(record.plaintext_length for record in records)
    return records, _inventory_digest(records), plaintext_bytes


def _inventory_digest(records: list[_SourceRecord]) -> str:
    digest = hashlib.sha256(_INVENTORY_DOMAIN)
    for record in records:
        for value in (
            record.relative_path.encode("utf-8"),
            record.plaintext_length.to_bytes(8, "big"),
            record.chunk_count.to_bytes(8, "big"),
            bytes.fromhex(record.sha256),
        ):
            digest.update(len(value).to_bytes(4, "big"))
            digest.update(value)
    return digest.hexdigest()


def _chunk_aad(
    *,
    header_hash: bytes,
    bundle_id: UUID,
    record: _SourceRecord,
    chunk_index: int,
    offset: int,
    plaintext_length: int,
) -> bytes:
    return _aad(
        _CHUNK_AAD_DOMAIN,
        header_hash,
        bundle_id.bytes,
        record.ordinal.to_bytes(8, "big"),
        record.relative_path.encode("utf-8"),
        bytes.fromhex(record.sha256),
        chunk_index.to_bytes(8, "big"),
        record.chunk_count.to_bytes(8, "big"),
        offset.to_bytes(8, "big"),
        plaintext_length.to_bytes(8, "big"),
        (plaintext_length + _TAG_BYTES).to_bytes(8, "big"),
        bytes([chunk_index + 1 == record.chunk_count]),
    )


def _envelope_aad(domain: bytes, *values: bytes) -> bytes:
    return _aad(domain, *values)


def _aad(*values: bytes) -> bytes:
    encoded = bytearray()
    for value in values:
        encoded.extend(len(value).to_bytes(4, "big"))
        encoded.extend(value)
    return bytes(encoded)


def _load_or_create_key(
    binding: RuntimeInstanceBinding,
    store: CredentialStore,
    *,
    allow_create: bool,
) -> bytes:
    reference = _credential_reference(binding)
    encoded = store.get(reference)
    if encoded is None:
        if not allow_create:
            raise LegacyRuntimeRecoveryError("legacy Runtime recovery credential is unavailable")
        encoded = base64.b64encode(secrets.token_bytes(_KEY_BYTES)).decode("ascii")
        store.put(reference, encoded)
    try:
        key = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise LegacyRuntimeRecoveryError("legacy Runtime recovery credential is invalid") from exc
    if len(key) != _KEY_BYTES or base64.b64encode(key).decode("ascii") != encoded:
        raise LegacyRuntimeRecoveryError("legacy Runtime recovery credential is invalid")
    return key


def _credential_reference(binding: RuntimeInstanceBinding) -> str:
    digest = hashlib.sha256(_CREDENTIAL_DOMAIN)
    for value in (binding.core_id, binding.local_instance_id):
        encoded = value.encode("utf-8")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
    return credential_reference("core-transfer", f"legacy-runtime-recovery-v1:{digest.hexdigest()}")


def _identity_hash(kind: str, value: str) -> str:
    digest = hashlib.sha256()
    digest.update(b"anima-legacy-runtime-recovery-identity-v1\x00")
    digest.update(len(kind).to_bytes(4, "big"))
    digest.update(kind.encode("ascii"))
    encoded = value.encode("utf-8")
    digest.update(len(encoded).to_bytes(4, "big"))
    digest.update(encoded)
    return digest.hexdigest()


def _bundle_path(binding: RuntimeInstanceBinding) -> Path:
    return binding.instance_root / "recovery" / "legacy-runtime-source.anima-runtime-recovery"


def _publish_create_only(partial: Path, target: Path) -> None:
    try:
        os.link(partial, target)
    except FileExistsError:
        return
    partial.unlink()
    _fsync_directory(target.parent)


def _require_expected_inventory(
    bundle: LegacyRuntimeRecoveryBundle,
    *,
    records: list[_SourceRecord],
    plaintext_bytes: int,
    inventory_digest: str,
) -> None:
    if (
        bundle.file_count != len(records)
        or bundle.plaintext_bytes != plaintext_bytes
        or bundle.inventory_digest != inventory_digest
    ):
        raise LegacyRuntimeRecoveryError("legacy Runtime recovery bundle does not match the source")


def _require_instance_path(binding: RuntimeInstanceBinding, path: Path) -> None:
    root = binding.instance_root.expanduser().resolve(strict=True)
    resolved = path.expanduser().resolve(strict=False)
    if resolved == root or not resolved.is_relative_to(root):
        raise LegacyRuntimeRecoveryError("legacy Runtime recovery path escapes its instance")


def _reject_link_chain(path: Path, *, boundary: Path) -> None:
    root = boundary.expanduser().resolve(strict=True)
    candidate = path.expanduser()
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise LegacyRuntimeRecoveryError(
            "legacy Runtime recovery path escapes its instance"
        ) from exc
    current = root
    _reject_link(current)
    for part in relative.parts:
        current /= part
        _reject_link(current)


def _reject_link(path: Path) -> None:
    is_junction = getattr(path, "is_junction", lambda: False)
    if path.is_symlink() or is_junction():
        raise LegacyRuntimeRecoveryError("legacy Runtime recovery rejects links and junctions")


def _file_identity(path: Path) -> tuple[int, int, int, int]:
    metadata = path.stat()
    if not path.is_file():
        raise LegacyRuntimeRecoveryError("legacy Runtime source contains a non-file")
    return (
        int(metadata.st_dev),
        int(metadata.st_ino),
        int(metadata.st_size),
        int(metadata.st_mtime_ns),
    )


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _chunk_count(length: int) -> int:
    return max(1, (length + _CHUNK_BYTES - 1) // _CHUNK_BYTES)


def _safe_relative_path(value: str) -> bool:
    if not value or len(value.encode("utf-8")) > _MAX_PATH_BYTES or "\\" in value:
        return False
    path = PurePosixPath(value)
    return (
        not path.is_absolute()
        and path.as_posix() == value
        and all(part not in {"", ".", ".."} for part in path.parts)
    )


def _nonce(prefix: bytes, ordinal: int) -> bytes:
    if len(prefix) != 4 or ordinal < 0 or ordinal > (2**64 - 1):
        raise LegacyRuntimeRecoveryError("legacy Runtime recovery nonce is invalid")
    return prefix + ordinal.to_bytes(8, "big")


def _write_prefixed(handle: BinaryIO, value: bytes) -> None:
    handle.write(len(value).to_bytes(8, "big"))
    handle.write(value)


def _read_prefixed(handle: BinaryIO, maximum: int) -> bytes:
    encoded = handle.read(8)
    if len(encoded) != 8:
        raise LegacyRuntimeRecoveryError("legacy Runtime recovery frame is truncated")
    length = int.from_bytes(encoded, "big")
    if length <= 0 or length > maximum:
        raise LegacyRuntimeRecoveryError("legacy Runtime recovery frame exceeds its bound")
    value = handle.read(length)
    if len(value) != length:
        raise LegacyRuntimeRecoveryError("legacy Runtime recovery frame is truncated")
    return value


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _is_sha256(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        bytes.fromhex(value)
    except ValueError:
        return False
    return value == value.casefold()


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _boundary(hook: BoundaryHook | None, name: str) -> None:
    if hook is not None:
        hook(name)
