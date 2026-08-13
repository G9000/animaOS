from __future__ import annotations

import json
import os
import shutil
import tempfile
from copy import deepcopy
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from anima_server.services import anima_core_bindings
from anima_server.services.core import get_manifest_path
from anima_server.services.corefs.soul_relocation import (
    SoulRelocationError,
    create_verified_soul_snapshot,
)
from anima_server.services.corefs.types import PayloadScope

_MAX_MANIFEST_BYTES = 8 * 1024 * 1024
_MAX_RECOVERY_RECORDS = 10_000


class CoreArchiveTransferError(RuntimeError):
    pass


class CoreArchivePayloadKind(StrEnum):
    FULL = "full"
    SOUL = "soul"
    FS = "fs"


@dataclass(frozen=True, slots=True)
class CoreArchiveInventory:
    payload_kind: CoreArchivePayloadKind
    core_id: str
    owner_id: str
    soul_generation: int | None
    filesystem_generation: int | None
    selected_bytes: int
    record_count: int
    soul_inventory_hash: str | None = None
    filesystem_catalog_hash: str | None = None


@dataclass(frozen=True, slots=True)
class CoreArchiveExportResult:
    inventory: CoreArchiveInventory
    archive_id: str
    plaintext_bytes: int
    chunk_count: int
    max_buffer_bytes: int


@dataclass(frozen=True, slots=True)
class CoreArchiveImportResult:
    inventory: CoreArchiveInventory
    archive_id: str
    staging_path: Path
    chunk_count: int
    max_buffer_bytes: int
    control_records: tuple[tuple[str, int, str], ...] = ()


@dataclass(frozen=True, slots=True)
class _PreparedCoreArchive:
    inventory: CoreArchiveInventory
    sources: tuple[dict[str, str], ...]
    manifest_snapshot: bytes
    keyslot_snapshot: bytes


def inspect_core_archive_v2(
    *,
    session: Any,
    payload_kind: CoreArchivePayloadKind,
    soul_generation: int | None,
    core_root: Path | None = None,
    manifest: dict[str, object] | None = None,
) -> CoreArchiveInventory:
    """Return the exact current archive selection without exposing its paths."""
    with tempfile.TemporaryDirectory(prefix="anima-core-archive-inspect-") as temporary_name:
        return _prepare_core_archive(
            session=session,
            payload_kind=payload_kind,
            soul_generation=soul_generation,
            snapshot_root=Path(temporary_name),
            core_root=core_root,
            manifest=manifest,
        ).inventory


def export_core_archive_v2(
    *,
    session: Any,
    output_path: Path,
    passphrase: str,
    payload_kind: CoreArchivePayloadKind,
    soul_generation: int | None,
    core_root: Path | None = None,
    manifest: dict[str, object] | None = None,
) -> CoreArchiveExportResult:
    """Write one bounded V2 archive through the native streaming implementation.

    The caller owns destination publication (`.partial`, verification, and rename).
    This function accepts only an unlocked CoreFS session and constructs every
    source path itself; renderer-supplied paths never enter the archive inventory.
    """
    if len(passphrase) < 8:
        raise CoreArchiveTransferError("archive passphrase must be at least 8 characters")
    with tempfile.TemporaryDirectory(prefix="anima-core-archive-metadata-") as temporary_name:
        temporary_root = Path(temporary_name)
        prepared = _prepare_core_archive(
            session=session,
            payload_kind=payload_kind,
            soul_generation=soul_generation,
            snapshot_root=temporary_root,
            core_root=core_root,
            manifest=manifest,
        )
        inventory = prepared.inventory
        sources = list(prepared.sources)
        manifest_path = temporary_root / "manifest.json"
        keyslots_path = temporary_root / "root-keyslots.json"
        _write_private_file(manifest_path, prepared.manifest_snapshot)
        _write_private_file(keyslots_path, prepared.keyslot_snapshot)
        sources = [
            _source("manifest", "manifest.json", manifest_path),
            *sources,
            _source("keyslots", "keyslots/root-keyslots.json", keyslots_path),
        ]
        request = {
            "payloadKind": payload_kind.value,
            "coreId": inventory.core_id,
            "ownerId": inventory.owner_id,
            "soulGeneration": inventory.soul_generation,
            "filesystemGeneration": inventory.filesystem_generation,
            "filesystemCatalogHash": inventory.filesystem_catalog_hash,
            "volumeMode": "single",
            "declaredVolumeCount": 1,
            "volumeOrdinal": 0,
            "sources": sources,
        }
        try:
            raw_summary = session.corefs_session.archive_write_v2(
                session.corefs_keys,
                os.fspath(output_path),
                passphrase.encode("utf-8"),
                json.dumps(request, sort_keys=True, separators=(",", ":")),
            )
        except (OSError, RuntimeError, ValueError) as exc:
            raise CoreArchiveTransferError("native ANIMA CORE archive export failed") from exc

    summary = _validate_summary(
        raw_summary,
        payload_kind=payload_kind,
        core_id=inventory.core_id,
        owner_id=inventory.owner_id,
        soul_generation=inventory.soul_generation,
        filesystem_generation=inventory.filesystem_generation,
        record_count=inventory.record_count,
        selected_bytes=inventory.selected_bytes,
    )
    return CoreArchiveExportResult(
        inventory=inventory,
        archive_id=str(summary["archiveId"]),
        plaintext_bytes=int(summary["plaintextBytes"]),
        chunk_count=int(summary["chunkCount"]),
        max_buffer_bytes=int(summary["maxBufferBytes"]),
    )


def _archive_source(
    *,
    core_root: Path | None,
    manifest: dict[str, object] | None,
) -> tuple[Path, dict[str, object]]:
    if core_root is None and manifest is None:
        manifest_path = get_manifest_path().expanduser().resolve(strict=True)
        return manifest_path.parent, _read_manifest(manifest_path)
    if core_root is None or manifest is None:
        raise CoreArchiveTransferError(
            "explicit Core archive source requires both root and manifest"
        )
    candidate = core_root.expanduser()
    if candidate.is_symlink():
        raise CoreArchiveTransferError("explicit Core archive root is invalid")
    resolved = candidate.resolve(strict=True)
    if not resolved.is_dir():
        raise CoreArchiveTransferError("explicit Core archive root is invalid")
    return resolved, deepcopy(manifest)


def _prepare_core_archive(
    *,
    session: Any,
    payload_kind: CoreArchivePayloadKind,
    soul_generation: int | None,
    snapshot_root: Path,
    core_root: Path | None = None,
    manifest: dict[str, object] | None = None,
) -> _PreparedCoreArchive:
    if session.corefs_session is None or session.corefs_keys is None:
        raise CoreArchiveTransferError("ANIMA CORE archive requires an unlocked Core")
    core_root, manifest = _archive_source(core_root=core_root, manifest=manifest)
    core_id = _required_uuid(manifest, "core_id")
    owner_id = _required_uuid(manifest, "owner_id")

    filesystem_inventory: dict[str, object] | None = None
    if payload_kind in {CoreArchivePayloadKind.FULL, CoreArchivePayloadKind.FS}:
        raw = session.corefs_session.archive_inventory_v2(session.corefs_keys)
        if not isinstance(raw, dict):
            raise CoreArchiveTransferError("native archive inventory is invalid")
        filesystem_inventory = cast(dict[str, object], raw)
    filesystem_generation, filesystem_catalog_hash = _filesystem_checkpoint(filesystem_inventory)
    normalized_soul_generation = _soul_generation(payload_kind, soul_generation)

    sources: list[dict[str, str]] = []
    soul_inventory_hash: str | None = None
    if payload_kind in {CoreArchivePayloadKind.FULL, CoreArchivePayloadKind.SOUL}:
        soul_path = core_root / "soul" / "soul.db"
        if not soul_path.is_file() or soul_path.is_symlink():
            raise CoreArchiveTransferError("canonical Soul database is unavailable")
        soul_snapshot = snapshot_root / "soul.db"
        try:
            soul_inventory = create_verified_soul_snapshot(soul_path, soul_snapshot)
        except SoulRelocationError as exc:
            raise CoreArchiveTransferError("canonical Soul snapshot failed verification") from exc
        soul_inventory_hash = soul_inventory.combined_hash
        sources.append(_source("soul_database", "soul/soul.db", soul_snapshot))
    if filesystem_inventory is not None:
        filesystem_sources = _native_filesystem_sources(
            filesystem_inventory,
            core_root=core_root,
        )
        sources.extend(
            _freeze_filesystem_pointer(
                filesystem_sources,
                snapshot_root=snapshot_root,
            )
        )
    if payload_kind in {CoreArchivePayloadKind.FULL, CoreArchivePayloadKind.SOUL}:
        sources.extend(_recovery_sources(core_root))
    if filesystem_inventory is not None:
        refreshed = session.corefs_session.archive_inventory_v2(session.corefs_keys)
        if not isinstance(refreshed, dict) or not _filesystem_inventory_matches(
            filesystem_inventory,
            cast(dict[str, object], refreshed),
            core_root=core_root,
        ):
            raise CoreArchiveTransferError(
                "committed filesystem snapshot changed during archive capture"
            )
    scoped_manifest = _scoped_archive_manifest(manifest, payload_kind)
    manifest_snapshot = json.dumps(
        scoped_manifest,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    keyslot_snapshot = keyslot_snapshot_bytes(scoped_manifest)
    selected_bytes = (
        sum(Path(item["sourcePath"]).stat().st_size for item in sources)
        + len(manifest_snapshot)
        + len(keyslot_snapshot)
    )
    return _PreparedCoreArchive(
        inventory=CoreArchiveInventory(
            payload_kind=payload_kind,
            core_id=core_id,
            owner_id=owner_id,
            soul_generation=normalized_soul_generation,
            filesystem_generation=filesystem_generation,
            selected_bytes=selected_bytes,
            record_count=len(sources) + 2,
            soul_inventory_hash=soul_inventory_hash,
            filesystem_catalog_hash=filesystem_catalog_hash,
        ),
        sources=tuple(sources),
        manifest_snapshot=manifest_snapshot,
        keyslot_snapshot=keyslot_snapshot,
    )


def verify_core_archive_v2(
    archive_path: Path,
    *,
    passphrase: str,
    expected: CoreArchiveInventory | None = None,
) -> dict[str, object]:
    extractor = anima_core_bindings.require_binding("core_archive_extract_v2")
    staging_parent = archive_path.expanduser().resolve(strict=True).parent
    staging = Path(tempfile.mkdtemp(prefix=".anima-core-verify-", dir=staging_parent))
    shutil.rmtree(staging)
    try:
        try:
            raw = extractor(
                os.fspath(archive_path),
                passphrase.encode("utf-8"),
                os.fspath(staging),
            )
        except (OSError, RuntimeError, ValueError) as exc:
            raise CoreArchiveTransferError("ANIMA CORE archive verification failed") from exc
        summary = _validate_extracted_summary(raw)
        if expected is not None:
            _match_expected_summary(summary, expected)
        return summary
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def stage_core_archive_v2(
    archive_path: Path,
    *,
    passphrase: str,
    staging_path: Path,
) -> CoreArchiveImportResult:
    """Authenticate and extract one V2 artifact into a non-live staging Core.

    The caller must capacity-check the staging parent before this call. The
    destination is create-only and is removed on every validation failure; this
    function never changes the active-Core registry or the running Core.
    """
    if len(passphrase) < 8:
        raise CoreArchiveTransferError("archive passphrase must be at least 8 characters")
    archive_candidate = archive_path.expanduser()
    if archive_candidate.is_symlink():
        raise CoreArchiveTransferError("ANIMA CORE archive must be a regular file")
    archive = archive_candidate.resolve(strict=True)
    if not archive.is_file():
        raise CoreArchiveTransferError("ANIMA CORE archive must be a regular file")
    staging = staging_path.expanduser().resolve(strict=False)
    parent = staging.parent.resolve(strict=True)
    if staging.parent != parent or staging.exists():
        raise CoreArchiveTransferError("ANIMA CORE import staging path is invalid")

    extractor = anima_core_bindings.require_binding("core_archive_extract_v2")
    try:
        try:
            raw = extractor(
                os.fspath(archive),
                passphrase.encode("utf-8"),
                os.fspath(staging),
            )
        except (OSError, RuntimeError, ValueError) as exc:
            raise CoreArchiveTransferError("ANIMA CORE archive extraction failed") from exc
        summary = _validate_extracted_summary(raw)
        inventory = _validate_staged_core(staging, summary)
        return CoreArchiveImportResult(
            inventory=inventory,
            archive_id=str(summary["archiveId"]),
            staging_path=staging,
            chunk_count=int(summary["chunkCount"]),
            max_buffer_bytes=int(summary["maxBufferBytes"]),
            control_records=_authenticated_control_records(summary),
        )
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _read_manifest(path: Path) -> dict[str, object]:
    if path.stat().st_size > _MAX_MANIFEST_BYTES:
        raise CoreArchiveTransferError("ANIMA CORE manifest exceeds its archive bound")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CoreArchiveTransferError("ANIMA CORE manifest is unavailable") from exc
    if not isinstance(value, dict):
        raise CoreArchiveTransferError("ANIMA CORE manifest is invalid")
    return cast(dict[str, object], value)


def _required_uuid(manifest: dict[str, object], key: str) -> str:
    value = manifest.get(key)
    if not isinstance(value, str):
        raise CoreArchiveTransferError(f"ANIMA CORE manifest {key} is unavailable")
    try:
        return str(UUID(value))
    except (TypeError, ValueError) as exc:
        raise CoreArchiveTransferError(f"ANIMA CORE manifest {key} is invalid") from exc


def _filesystem_checkpoint(
    inventory: dict[str, object] | None,
) -> tuple[int | None, str | None]:
    if inventory is None:
        return None, None
    if inventory.get("version") != 1 or set(inventory) != {
        "version",
        "generation",
        "catalogHash",
        "sources",
    }:
        raise CoreArchiveTransferError("native archive inventory shape is invalid")
    generation = inventory.get("generation")
    if isinstance(generation, bool) or not isinstance(generation, int) or generation <= 0:
        raise CoreArchiveTransferError("native archive generation is invalid")
    catalog_hash = inventory.get("catalogHash")
    if not _is_sha256(catalog_hash):
        raise CoreArchiveTransferError("native archive catalog hash is invalid")
    return generation, catalog_hash


def _soul_generation(
    payload_kind: CoreArchivePayloadKind,
    value: int | None,
) -> int | None:
    if payload_kind is CoreArchivePayloadKind.FS:
        if value is not None:
            raise CoreArchiveTransferError("CoreFS-only archive cannot declare Soul generation")
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise CoreArchiveTransferError("Soul-bearing archive requires a positive Soul generation")
    return value


def _source(record_type: str, record_path: str, source_path: Path) -> dict[str, str]:
    candidate = source_path.expanduser()
    if candidate.is_symlink():
        raise CoreArchiveTransferError("archive source must be a regular file")
    resolved = candidate.resolve(strict=True)
    if not resolved.is_file():
        raise CoreArchiveTransferError("archive source must be a regular file")
    return {
        "recordType": record_type,
        "recordPath": record_path,
        "sourcePath": os.fspath(resolved),
    }


def keyslot_snapshot_bytes(manifest: dict[str, object]) -> bytes:
    payload = {
        "version": 1,
        "keyslotsVersion": manifest.get("keyslots_version"),
        "keyslots": manifest.get("keyslots"),
        "activeFilesystemRootGeneration": manifest.get("active_filesystem_root_generation"),
        "activeRecoveryCredentialGeneration": manifest.get("active_recovery_credential_generation"),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _scoped_archive_manifest(
    manifest: dict[str, object],
    payload_kind: CoreArchivePayloadKind,
) -> dict[str, object]:
    """Copy only key authority declared by the authenticated payload kind.

    Keyslots remain wrapped under their normal Core credentials; the archive
    passphrase protects transport but never becomes a Core unlock credential.
    A partial restore must run the dedicated scoped-credential flow before it
    can activate, so this snapshot also carries an explicit degraded state.
    """
    snapshot = deepcopy(manifest)
    if payload_kind is CoreArchivePayloadKind.FULL:
        snapshot["archive_payload_scope"] = payload_kind.value
        return snapshot

    raw_slots = snapshot.get("keyslots")
    if not isinstance(raw_slots, list):
        raise CoreArchiveTransferError("ANIMA CORE manifest keyslots are invalid")
    required_purpose = "soul" if payload_kind is CoreArchivePayloadKind.SOUL else "filesystem-root"
    selected_slots: list[dict[str, object]] = []
    for raw_slot in raw_slots:
        if not isinstance(raw_slot, dict):
            raise CoreArchiveTransferError("ANIMA CORE manifest keyslot is invalid")
        purpose = raw_slot.get("purpose")
        if purpose not in {"soul", "filesystem-root"}:
            raise CoreArchiveTransferError("ANIMA CORE manifest keyslot purpose is invalid")
        if purpose == required_purpose:
            selected_slots.append(deepcopy(raw_slot))
    if not selected_slots:
        raise CoreArchiveTransferError("ANIMA CORE scoped keyslot set is incomplete")

    snapshot["keyslots"] = selected_slots
    snapshot["archive_payload_scope"] = payload_kind.value
    snapshot.pop("pending_recovery_credential", None)
    if payload_kind is CoreArchivePayloadKind.SOUL:
        snapshot["degraded_state"] = "filesystem_missing"
        snapshot.pop("frk_rotation", None)
        snapshot.pop("active_filesystem_root_generation", None)
        snapshot.pop("corefs_cutover", None)
    else:
        snapshot["degraded_state"] = "recovery_only"
        snapshot.pop("wrapped_sqlcipher_key", None)
        snapshot.pop("recovery_sqlcipher_key", None)
        snapshot.pop("sqlcipher_kdf_salt", None)
    return snapshot


def _write_private_file(path: Path, encoded: bytes) -> None:
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


def _native_filesystem_sources(
    inventory: dict[str, object],
    *,
    core_root: Path,
) -> list[dict[str, str]]:
    raw_sources = inventory.get("sources")
    if not isinstance(raw_sources, list) or len(raw_sources) < 2:
        raise CoreArchiveTransferError("native archive inventory is incomplete")
    sources: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in raw_sources:
        if not isinstance(raw, dict) or set(raw) != {
            "recordType",
            "recordPath",
            "sourcePath",
        }:
            raise CoreArchiveTransferError("native archive source is invalid")
        record_type = raw.get("recordType")
        record_path = raw.get("recordPath")
        source_path = raw.get("sourcePath")
        if (
            record_type not in {"catalog", "object"}
            or not isinstance(record_path, str)
            or not isinstance(source_path, str)
            or record_path in seen
        ):
            raise CoreArchiveTransferError("native archive source identity is invalid")
        resolved = Path(source_path).expanduser().resolve(strict=True)
        if not resolved.is_relative_to(core_root):
            raise CoreArchiveTransferError("native archive source escaped the active Core")
        sources.append(_source(str(record_type), record_path, resolved))
        seen.add(record_path)
    if "fs/HEAD" not in seen or not any(path.startswith("fs/catalogs/") for path in seen):
        raise CoreArchiveTransferError("native archive inventory has no committed catalog")
    return sources


def _freeze_filesystem_pointer(
    sources: list[dict[str, str]],
    *,
    snapshot_root: Path,
) -> list[dict[str, str]]:
    frozen: list[dict[str, str]] = []
    found_head = False
    for source in sources:
        if source["recordPath"] != "fs/HEAD":
            frozen.append(source)
            continue
        if found_head:
            raise CoreArchiveTransferError("native archive inventory repeats fs/HEAD")
        found_head = True
        snapshot = snapshot_root / "fs-HEAD"
        _copy_private_file(Path(source["sourcePath"]), snapshot)
        frozen.append(_source(source["recordType"], source["recordPath"], snapshot))
    if not found_head:
        raise CoreArchiveTransferError("native archive inventory has no committed pointer")
    return frozen


def _copy_private_file(source: Path, target: Path) -> None:
    descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with source.open("rb") as source_handle, os.fdopen(descriptor, "wb") as target_handle:
            shutil.copyfileobj(source_handle, target_handle, length=1024 * 1024)
            target_handle.flush()
            os.fsync(target_handle.fileno())
    except BaseException:
        target.unlink(missing_ok=True)
        raise


def _filesystem_inventory_matches(
    expected: dict[str, object],
    actual: dict[str, object],
    *,
    core_root: Path,
) -> bool:
    try:
        if _filesystem_checkpoint(expected) != _filesystem_checkpoint(actual):
            return False
        expected_sources = _native_filesystem_sources(expected, core_root=core_root)
        actual_sources = _native_filesystem_sources(actual, core_root=core_root)
    except (CoreArchiveTransferError, OSError):
        return False
    return expected_sources == actual_sources


def _recovery_sources(core_root: Path) -> list[dict[str, str]]:
    recovery = core_root / "recovery"
    if not recovery.exists():
        return []
    if recovery.is_symlink() or not recovery.is_dir():
        raise CoreArchiveTransferError("Core recovery source is invalid")
    sources: list[dict[str, str]] = []
    for directory, child_directories, child_files in os.walk(recovery, followlinks=False):
        directory_path = Path(directory)
        for name in child_directories:
            child = directory_path / name
            if child.is_symlink():
                raise CoreArchiveTransferError("Core recovery source contains a link")
        child_directories.sort()
        for name in sorted(child_files):
            path = directory_path / name
            if path.is_symlink() or not path.is_file():
                raise CoreArchiveTransferError("Core recovery source contains a non-file")
            relative = path.relative_to(core_root).as_posix()
            sources.append(_source("recovery", relative, path))
            if len(sources) > _MAX_RECOVERY_RECORDS:
                raise CoreArchiveTransferError("Core recovery inventory exceeds its bound")
    return sources


def _validate_summary(
    raw: object,
    *,
    payload_kind: CoreArchivePayloadKind,
    core_id: str,
    owner_id: str,
    soul_generation: int | None,
    filesystem_generation: int | None,
    record_count: int,
    selected_bytes: int,
) -> dict[str, object]:
    summary = _validate_extracted_summary(raw)
    expected = {
        "payloadKind": payload_kind.value,
        "coreId": core_id,
        "ownerId": owner_id,
        "soulGeneration": soul_generation,
        "filesystemGeneration": filesystem_generation,
        "recordCount": record_count,
        "plaintextBytes": selected_bytes,
    }
    if any(summary.get(key) != value for key, value in expected.items()):
        raise CoreArchiveTransferError("native archive summary changed its selected inventory")
    return summary


def _validate_extracted_summary(raw: object) -> dict[str, object]:
    if not isinstance(raw, dict) or raw.get("version") != 2:
        raise CoreArchiveTransferError("native archive summary is invalid")
    summary = cast(dict[str, object], raw)
    for key in ("archiveId", "volumeSetId", "coreId", "ownerId"):
        value = summary.get(key)
        if not isinstance(value, str):
            raise CoreArchiveTransferError("native archive summary identity is invalid")
        try:
            UUID(value)
        except ValueError as exc:
            raise CoreArchiveTransferError("native archive summary identity is invalid") from exc
    for key in ("recordCount", "chunkCount", "plaintextBytes", "maxBufferBytes"):
        value = summary.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise CoreArchiveTransferError("native archive summary counters are invalid")
    if summary["recordCount"] <= 0 or summary["chunkCount"] <= 0:
        raise CoreArchiveTransferError("native archive summary is incomplete")
    if summary["maxBufferBytes"] > 32 * 1024 * 1024:
        raise CoreArchiveTransferError("native archive exceeded its memory bound")
    try:
        payload_kind = CoreArchivePayloadKind(summary.get("payloadKind"))
    except (TypeError, ValueError) as exc:
        raise CoreArchiveTransferError("native archive payload kind is invalid") from exc
    soul_generation = summary.get("soulGeneration")
    filesystem_generation = summary.get("filesystemGeneration")
    if payload_kind is CoreArchivePayloadKind.FULL:
        _positive_generation(soul_generation, "Soul")
        _positive_generation(filesystem_generation, "filesystem")
    elif payload_kind is CoreArchivePayloadKind.SOUL:
        _positive_generation(soul_generation, "Soul")
        if filesystem_generation is not None:
            raise CoreArchiveTransferError("Soul archive declared a filesystem generation")
    else:
        if soul_generation is not None:
            raise CoreArchiveTransferError("CoreFS archive declared a Soul generation")
        _positive_generation(filesystem_generation, "filesystem")
    return summary


def _positive_generation(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise CoreArchiveTransferError(f"native archive {name} generation is invalid")
    return value


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _validate_staged_core(
    staging: Path,
    summary: dict[str, object],
) -> CoreArchiveInventory:
    if not staging.is_dir() or staging.is_symlink():
        raise CoreArchiveTransferError("native archive did not produce a staging Core")
    raw_records = summary.get("records")
    if not isinstance(raw_records, list) or len(raw_records) != summary["recordCount"]:
        raise CoreArchiveTransferError("native archive record inventory is invalid")
    expected_paths: set[str] = set()
    for raw_record in raw_records:
        if not isinstance(raw_record, dict) or set(raw_record) != {
            "recordType",
            "recordPath",
            "plaintextLength",
            "recordHash",
        }:
            raise CoreArchiveTransferError("native archive record inventory is invalid")
        record_path = raw_record.get("recordPath")
        record_length = raw_record.get("plaintextLength")
        record_hash = raw_record.get("recordHash")
        if (
            not isinstance(record_path, str)
            or record_path in expected_paths
            or isinstance(record_length, bool)
            or not isinstance(record_length, int)
            or record_length < 0
            or not isinstance(record_hash, str)
            or len(record_hash) != 64
            or any(character not in "0123456789abcdef" for character in record_hash)
        ):
            raise CoreArchiveTransferError("native archive record inventory is invalid")
        expected_paths.add(record_path)

    actual_paths: set[str] = set()
    for directory, child_directories, child_files in os.walk(staging, followlinks=False):
        directory_path = Path(directory)
        for name in child_directories:
            if (directory_path / name).is_symlink():
                raise CoreArchiveTransferError("staged Core contains a symbolic link")
        for name in child_files:
            child = directory_path / name
            if child.is_symlink() or not child.is_file():
                raise CoreArchiveTransferError("staged Core contains an invalid record")
            actual_paths.add(child.relative_to(staging).as_posix())
    if actual_paths != expected_paths:
        raise CoreArchiveTransferError("staged Core does not match its authenticated inventory")

    manifest = _read_manifest(staging / "manifest.json")
    payload_kind = CoreArchivePayloadKind(str(summary["payloadKind"]))
    if (
        _required_uuid(manifest, "core_id") != summary["coreId"]
        or _required_uuid(manifest, "owner_id") != summary["ownerId"]
        or manifest.get("archive_payload_scope") != payload_kind.value
    ):
        raise CoreArchiveTransferError("staged Core manifest does not match its archive")
    expected_state = {
        CoreArchivePayloadKind.FULL: None,
        CoreArchivePayloadKind.SOUL: "filesystem_missing",
        CoreArchivePayloadKind.FS: "recovery_only",
    }[payload_kind]
    if payload_kind is CoreArchivePayloadKind.FULL:
        if manifest.get("degraded_state") in {"filesystem_missing", "recovery_only"}:
            raise CoreArchiveTransferError("full archive cannot declare a partial recovery state")
    elif manifest.get("degraded_state") != expected_state:
        raise CoreArchiveTransferError("partial archive recovery state is invalid")

    keyslot_path = staging / "keyslots" / "root-keyslots.json"
    try:
        keyslot_snapshot = json.loads(keyslot_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CoreArchiveTransferError("staged Core keyslot snapshot is invalid") from exc
    if not isinstance(keyslot_snapshot, dict) or keyslot_snapshot != json.loads(
        keyslot_snapshot_bytes(manifest)
    ):
        raise CoreArchiveTransferError("staged Core keyslot snapshot does not match its manifest")
    _validate_imported_keyslot_scope(manifest, payload_kind)

    return CoreArchiveInventory(
        payload_kind=payload_kind,
        core_id=str(summary["coreId"]),
        owner_id=str(summary["ownerId"]),
        soul_generation=(
            int(summary["soulGeneration"]) if summary["soulGeneration"] is not None else None
        ),
        filesystem_generation=(
            int(summary["filesystemGeneration"])
            if summary["filesystemGeneration"] is not None
            else None
        ),
        selected_bytes=int(summary["plaintextBytes"]),
        record_count=int(summary["recordCount"]),
    )


def _authenticated_control_records(
    summary: dict[str, object],
) -> tuple[tuple[str, int, str], ...]:
    """Retain the authenticated non-object records that select a staged Core."""
    raw_records = summary.get("records")
    if not isinstance(raw_records, list):
        raise CoreArchiveTransferError("native archive control inventory is invalid")
    retained: list[tuple[str, int, str]] = []
    for raw in raw_records:
        if not isinstance(raw, dict):
            raise CoreArchiveTransferError("native archive control inventory is invalid")
        if raw.get("recordType") not in {"manifest", "keyslots", "catalog"}:
            continue
        path = raw.get("recordPath")
        length = raw.get("plaintextLength")
        digest = raw.get("recordHash")
        if (
            not isinstance(path, str)
            or isinstance(length, bool)
            or not isinstance(length, int)
            or length < 0
            or not _is_sha256(digest)
        ):
            raise CoreArchiveTransferError("native archive control inventory is invalid")
        retained.append((path, length, str(digest)))
    required = {"manifest.json", "keyslots/root-keyslots.json"}
    paths = {path for path, _length, _digest in retained}
    payload_kind = CoreArchivePayloadKind(str(summary.get("payloadKind")))
    if payload_kind in {CoreArchivePayloadKind.FULL, CoreArchivePayloadKind.FS}:
        required.add("fs/HEAD")
        if not any(path.startswith("fs/catalogs/") for path in paths):
            raise CoreArchiveTransferError("native archive control inventory is incomplete")
    if not required.issubset(paths):
        raise CoreArchiveTransferError("native archive control inventory is incomplete")
    return tuple(retained)


def _validate_imported_keyslot_scope(
    manifest: dict[str, object],
    payload_kind: CoreArchivePayloadKind,
) -> None:
    raw_slots = manifest.get("keyslots")
    if not isinstance(raw_slots, list) or not raw_slots:
        raise CoreArchiveTransferError("staged Core keyslot set is incomplete")
    purposes: set[str] = set()
    scopes: set[str] = set()
    for slot in raw_slots:
        if not isinstance(slot, dict) or slot.get("purpose") not in {
            "soul",
            "filesystem-root",
        }:
            raise CoreArchiveTransferError("staged Core keyslot set is invalid")
        purposes.add(str(slot["purpose"]))
        scope = slot.get("scope")
        if not isinstance(scope, str):
            raise CoreArchiveTransferError("staged Core keyslot scope is invalid")
        scopes.add(scope)
    expected = {
        CoreArchivePayloadKind.FULL: {"soul", "filesystem-root"},
        CoreArchivePayloadKind.SOUL: {"soul"},
        CoreArchivePayloadKind.FS: {"filesystem-root"},
    }[payload_kind]
    if purposes != expected:
        raise CoreArchiveTransferError("staged Core keyslot scope is invalid")
    allowed_scopes = (
        {PayloadScope.FULL.value}
        if payload_kind is CoreArchivePayloadKind.FULL
        else {PayloadScope.FULL.value, payload_kind.value}
    )
    if not scopes.issubset(allowed_scopes):
        raise CoreArchiveTransferError("staged Core keyslot scope is invalid")


def _match_expected_summary(summary: dict[str, object], expected: CoreArchiveInventory) -> None:
    expected_values = {
        "payloadKind": expected.payload_kind.value,
        "coreId": expected.core_id,
        "ownerId": expected.owner_id,
        "soulGeneration": expected.soul_generation,
        "filesystemGeneration": expected.filesystem_generation,
        "recordCount": expected.record_count,
        "plaintextBytes": expected.selected_bytes,
    }
    if any(summary.get(key) != value for key, value in expected_values.items()):
        raise CoreArchiveTransferError("verified archive does not match its prepared inventory")
