from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from anima_server.services.corefs.instance_registry import RuntimeInstanceBinding

_APPROVED_CORE_ROOT_ENTRIES = frozenset(
    {
        "manifest.json",
        "core.lock",
        "soul",
        "fs",
        "objects",
        "recovery",
    }
)


class LegacyRuntimeCollision(RuntimeError):
    """Raised when legacy Runtime bytes cannot be relocated without ambiguity."""


@dataclass(frozen=True, slots=True)
class LegacyRuntimeRelocation:
    legacy_pg_moved: bool
    indices_moved: bool
    health_logs_moved: bool


@dataclass(frozen=True, slots=True)
class _TreeInventory:
    files: int
    digest: str


def relocate_legacy_runtime(
    core_path: Path,
    binding: RuntimeInstanceBinding,
    *,
    postgres_running: bool,
) -> LegacyRuntimeRelocation:
    """Move legacy Runtime bytes out of a Core before fresh Runtime startup."""
    canonical_core = core_path.expanduser().resolve(strict=True)
    if canonical_core != binding.core_path:
        raise LegacyRuntimeCollision(
            "Runtime relocation binding does not match the active Core path"
        )

    legacy_pg_source = canonical_core / "runtime" / "pg_data"
    if postgres_running and legacy_pg_source.exists():
        raise LegacyRuntimeCollision(
            "legacy Runtime relocation requires PostgreSQL is stopped"
        )

    legacy_pg_moved, legacy_pg_inventory = _move_verified_tree(
        legacy_pg_source,
        binding.legacy_pg_data_dir,
    )
    indices_moved, indices_inventory = _move_verified_tree(
        canonical_core / "indices",
        binding.indices_dir,
    )
    health_logs_moved, health_logs_inventory = _move_verified_tree(
        canonical_core / "logs",
        binding.health_log_dir,
    )
    _remove_empty_directory(canonical_core / "runtime")

    if legacy_pg_moved or indices_moved or health_logs_moved:
        _write_migration_journal(
            binding,
            legacy_pg=legacy_pg_inventory,
            indices=indices_inventory,
            health_logs=health_logs_inventory,
        )

    return LegacyRuntimeRelocation(
        legacy_pg_moved=legacy_pg_moved,
        indices_moved=indices_moved,
        health_logs_moved=health_logs_moved,
    )


def assert_core_root_inventory(core_path: Path) -> tuple[str, ...]:
    """Return unapproved top-level Core entries after Runtime relocation."""
    return tuple(
        sorted(
            entry.name
            for entry in core_path.iterdir()
            if entry.name not in _APPROVED_CORE_ROOT_ENTRIES
        )
    )


def _move_verified_tree(
    source: Path,
    target: Path,
) -> tuple[bool, _TreeInventory | None]:
    if not source.exists():
        return False, None
    if not source.is_dir():
        raise LegacyRuntimeCollision(f"legacy Runtime source is not a directory: {source}")

    source_inventory = _inventory_tree(source)
    if target.exists():
        if not target.is_dir() or _inventory_tree(target) != source_inventory:
            raise LegacyRuntimeCollision(
                f"legacy Runtime target contains different bytes: {target}"
            )
        shutil.rmtree(source)
        return True, source_inventory

    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    try:
        shutil.copytree(source, staging)
        if _inventory_tree(staging) != source_inventory:
            raise LegacyRuntimeCollision(
                f"legacy Runtime copy verification failed: {source}"
            )
        os.replace(staging, target)
        if _inventory_tree(target) != source_inventory:
            raise LegacyRuntimeCollision(
                f"legacy Runtime target verification failed: {target}"
            )
        shutil.rmtree(source)
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    return True, source_inventory


def _inventory_tree(root: Path) -> _TreeInventory:
    digest = hashlib.sha256()
    files = 0
    for directory, child_directories, child_files in os.walk(
        root,
        topdown=True,
        followlinks=False,
    ):
        directory_path = Path(directory)
        for name in child_directories:
            _reject_link(directory_path / name)
        for name in sorted(child_files):
            path = directory_path / name
            _reject_link(path)
            relative = path.relative_to(root).as_posix()
            content_digest = _hash_file(path)
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            digest.update(str(path.stat().st_size).encode("ascii"))
            digest.update(b"\0")
            digest.update(content_digest.encode("ascii"))
            digest.update(b"\n")
            files += 1
        child_directories.sort()
    return _TreeInventory(files=files, digest=digest.hexdigest())


def _reject_link(path: Path) -> None:
    is_junction = getattr(path, "is_junction", lambda: False)
    if path.is_symlink() or is_junction():
        raise LegacyRuntimeCollision(
            f"legacy Runtime relocation rejects links and junctions: {path}"
        )


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _remove_empty_directory(path: Path) -> None:
    try:
        path.rmdir()
    except FileNotFoundError:
        return
    except OSError:
        return


def _write_migration_journal(
    binding: RuntimeInstanceBinding,
    *,
    legacy_pg: _TreeInventory | None,
    indices: _TreeInventory | None,
    health_logs: _TreeInventory | None,
) -> None:
    payload: dict[str, object] = {
        "version": 1,
        "core_id": binding.core_id,
        "local_instance_id": binding.local_instance_id,
        "legacy_pg": _journal_entry(legacy_pg, status="quarantined"),
        "indices": _journal_entry(indices, status="relocated"),
        "health_logs": _journal_entry(health_logs, status="relocated"),
    }
    path = binding.migration_journal_path
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _journal_entry(
    inventory: _TreeInventory | None,
    *,
    status: str,
) -> dict[str, object]:
    if inventory is None:
        return {"status": "absent", "files": 0, "digest": None}
    return {
        "status": status,
        "files": inventory.files,
        "digest": inventory.digest,
    }
