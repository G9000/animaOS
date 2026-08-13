from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from anima_server.services import anima_core_bindings
from anima_server.services.core import get_manifest_path

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


@dataclass(frozen=True, slots=True)
class CoreArchiveExportResult:
    inventory: CoreArchiveInventory
    archive_id: str
    plaintext_bytes: int
    chunk_count: int
    max_buffer_bytes: int


@dataclass(frozen=True, slots=True)
class _PreparedCoreArchive:
    inventory: CoreArchiveInventory
    sources: tuple[dict[str, str], ...]
    keyslot_snapshot: bytes


def inspect_core_archive_v2(
    *,
    session: Any,
    payload_kind: CoreArchivePayloadKind,
    soul_generation: int | None,
) -> CoreArchiveInventory:
    """Return the exact current archive selection without exposing its paths."""
    return _prepare_core_archive(
        session=session,
        payload_kind=payload_kind,
        soul_generation=soul_generation,
    ).inventory


def export_core_archive_v2(
    *,
    session: Any,
    output_path: Path,
    passphrase: str,
    payload_kind: CoreArchivePayloadKind,
    soul_generation: int | None,
) -> CoreArchiveExportResult:
    """Write one bounded V2 archive through the native streaming implementation.

    The caller owns destination publication (`.partial`, verification, and rename).
    This function accepts only an unlocked CoreFS session and constructs every
    source path itself; renderer-supplied paths never enter the archive inventory.
    """
    if len(passphrase) < 8:
        raise CoreArchiveTransferError("archive passphrase must be at least 8 characters")
    prepared = _prepare_core_archive(
        session=session,
        payload_kind=payload_kind,
        soul_generation=soul_generation,
    )
    inventory = prepared.inventory
    sources = list(prepared.sources)

    with tempfile.TemporaryDirectory(prefix="anima-core-keyslots-") as temporary_name:
        temporary_root = Path(temporary_name)
        keyslots_path = temporary_root / "root-keyslots.json"
        _write_private_file(keyslots_path, prepared.keyslot_snapshot)
        sources.append(_source("keyslots", "keyslots/root-keyslots.json", keyslots_path))
        request = {
            "payloadKind": payload_kind.value,
            "coreId": inventory.core_id,
            "ownerId": inventory.owner_id,
            "soulGeneration": inventory.soul_generation,
            "filesystemGeneration": inventory.filesystem_generation,
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


def _prepare_core_archive(
    *,
    session: Any,
    payload_kind: CoreArchivePayloadKind,
    soul_generation: int | None,
) -> _PreparedCoreArchive:
    if session.corefs_session is None or session.corefs_keys is None:
        raise CoreArchiveTransferError("ANIMA CORE archive requires an unlocked Core")
    manifest_path = get_manifest_path().expanduser().resolve(strict=True)
    core_root = manifest_path.parent
    manifest = _read_manifest(manifest_path)
    core_id = _required_uuid(manifest, "core_id")
    owner_id = _required_uuid(manifest, "owner_id")

    filesystem_inventory: dict[str, object] | None = None
    if payload_kind in {CoreArchivePayloadKind.FULL, CoreArchivePayloadKind.FS}:
        raw = session.corefs_session.archive_inventory_v2(session.corefs_keys)
        if not isinstance(raw, dict):
            raise CoreArchiveTransferError("native archive inventory is invalid")
        filesystem_inventory = cast(dict[str, object], raw)
    filesystem_generation = _filesystem_generation(filesystem_inventory)
    normalized_soul_generation = _soul_generation(payload_kind, soul_generation)

    sources: list[dict[str, str]] = [
        _source("manifest", "manifest.json", manifest_path),
    ]
    if payload_kind in {CoreArchivePayloadKind.FULL, CoreArchivePayloadKind.SOUL}:
        soul_path = core_root / "soul" / "soul.db"
        if not soul_path.is_file() or soul_path.is_symlink():
            raise CoreArchiveTransferError("canonical Soul database is unavailable")
        sources.append(_source("soul_database", "soul/soul.db", soul_path))
    if filesystem_inventory is not None:
        sources.extend(_native_filesystem_sources(filesystem_inventory, core_root=core_root))
    if payload_kind in {CoreArchivePayloadKind.FULL, CoreArchivePayloadKind.SOUL}:
        sources.extend(_recovery_sources(core_root))
    keyslot_snapshot = _keyslot_snapshot_bytes(manifest)
    selected_bytes = sum(Path(item["sourcePath"]).stat().st_size for item in sources) + len(
        keyslot_snapshot
    )
    return _PreparedCoreArchive(
        inventory=CoreArchiveInventory(
            payload_kind=payload_kind,
            core_id=core_id,
            owner_id=owner_id,
            soul_generation=normalized_soul_generation,
            filesystem_generation=filesystem_generation,
            selected_bytes=selected_bytes,
            record_count=len(sources) + 1,
        ),
        sources=tuple(sources),
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
    except ValueError as exc:
        raise CoreArchiveTransferError(f"ANIMA CORE manifest {key} is invalid") from exc


def _filesystem_generation(inventory: dict[str, object] | None) -> int | None:
    if inventory is None:
        return None
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
    return generation


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
    resolved = source_path.expanduser().resolve(strict=True)
    if not resolved.is_file() or resolved.is_symlink():
        raise CoreArchiveTransferError("archive source must be a regular file")
    return {
        "recordType": record_type,
        "recordPath": record_path,
        "sourcePath": os.fspath(resolved),
    }


def _keyslot_snapshot_bytes(manifest: dict[str, object]) -> bytes:
    payload = {
        "version": 1,
        "keyslotsVersion": manifest.get("keyslots_version"),
        "keyslots": manifest.get("keyslots"),
        "activeFilesystemRootGeneration": manifest.get("active_filesystem_root_generation"),
        "activeRecoveryCredentialGeneration": manifest.get("active_recovery_credential_generation"),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


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
    return summary


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
