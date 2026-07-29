from __future__ import annotations

import hashlib
import json
import os
import platform
import tempfile
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import uuid4

from sqlalchemy.engine import make_url

_REGISTRY_VERSION = 1
_LEASE_TTL = timedelta(hours=24)
_REGISTRY_LOCK = threading.RLock()


class InstanceBindingCollision(RuntimeError):
    """Raised when a Core cannot safely claim machine-local Runtime state."""


@dataclass(frozen=True, slots=True)
class RuntimeInstanceBinding:
    core_id: str
    local_instance_id: str
    core_path: Path
    instance_root: Path
    pg_data_dir: Path
    legacy_pg_data_dir: Path
    indices_dir: Path
    health_log_dir: Path
    migration_journal_path: Path
    filesystem_identity: str
    runtime_url_fingerprint: str | None = None

    @property
    def active_pg_data_dir(self) -> Path:
        """Keep relocated legacy Runtime active until its converters finish."""
        if self.legacy_pg_data_dir.is_dir():
            return self.legacy_pg_data_dir
        return self.pg_data_dir


class RuntimeInstanceRegistry:
    def __init__(
        self,
        app_data_root: Path,
        *,
        pid_is_alive: Callable[[int], bool] | None = None,
        process_id: int | None = None,
        now: Callable[[], datetime] | None = None,
        hostname: str | None = None,
    ) -> None:
        self.app_data_root = app_data_root.expanduser().resolve()
        self._registry_path = self.app_data_root / "core-instance-registry.json"
        self._pid_is_alive = pid_is_alive or _pid_is_alive
        self._process_id = process_id if process_id is not None else os.getpid()
        self._now = now or (lambda: datetime.now(UTC))
        self._hostname = hostname or platform.node()

    def resolve(
        self,
        core_path: Path,
        *,
        runtime_url: str | None = None,
        fork: bool = False,
        rebuild: bool = False,
    ) -> RuntimeInstanceBinding:
        canonical_core_path = core_path.expanduser().resolve(strict=True)
        core_id = _read_core_id(canonical_core_path)
        filesystem_identity = _filesystem_identity(canonical_core_path)
        runtime_url_fingerprint = (
            _runtime_url_fingerprint(runtime_url) if runtime_url else None
        )
        now = self._now()

        with _REGISTRY_LOCK, self._locked_registry():
            registry = self._load_registry()
            records = cast(list[dict[str, object]], registry["instances"])
            same_core = [
                record for record in records if record.get("core_id") == core_id
            ]

            if runtime_url_fingerprint is not None:
                self._reject_runtime_url_collision(
                    records,
                    core_id=core_id,
                    filesystem_identity=filesystem_identity,
                    runtime_url_fingerprint=runtime_url_fingerprint,
                    now=now,
                )

            selected: dict[str, object] | None = None
            if not (fork or rebuild):
                selected = next(
                    (
                        record
                        for record in same_core
                        if record.get("filesystem_identity") == filesystem_identity
                    ),
                    None,
                )

            if selected is None and not (fork or rebuild):
                live_divergent = next(
                    (
                        record
                        for record in same_core
                        if self._record_is_live(record, now=now)
                    ),
                    None,
                )
                if live_divergent is not None:
                    raise InstanceBindingCollision(
                        "live divergent copy with the same core_id already owns "
                        "machine-local Runtime state"
                    )

            if selected is None:
                selected = {
                    "core_id": core_id,
                    "local_instance_id": uuid4().hex,
                    "filesystem_identity": filesystem_identity,
                    "created_at": now.isoformat(),
                }
                records.append(selected)

            selected.update(
                {
                    "core_path": str(canonical_core_path),
                    "filesystem_identity": filesystem_identity,
                    "hostname": self._hostname,
                    "pid": self._process_id,
                    "lease_updated_at": now.isoformat(),
                    "runtime_url_fingerprint": runtime_url_fingerprint,
                }
            )
            self._write_registry(registry)
            binding = self._binding_from_record(selected)
            self._write_instance_lease(binding, now=now)
            return binding

    def verify_runtime_url_claim(
        self,
        binding: RuntimeInstanceBinding,
        runtime_url: str,
    ) -> None:
        expected = _runtime_url_fingerprint(runtime_url)
        with _REGISTRY_LOCK, self._locked_registry():
            records = cast(
                list[dict[str, object]], self._load_registry()["instances"]
            )
            record = next(
                (
                    item
                    for item in records
                    if item.get("local_instance_id") == binding.local_instance_id
                ),
                None,
            )
            if (
                record is None
                or record.get("core_id") != binding.core_id
                or record.get("filesystem_identity") != binding.filesystem_identity
                or record.get("runtime_url_fingerprint") != expected
            ):
                raise InstanceBindingCollision(
                    "runtime URL is not atomically bound to this Core instance"
                )

    def release(self, binding: RuntimeInstanceBinding) -> None:
        """Mark this process lease stale without deleting instance identity."""
        with _REGISTRY_LOCK, self._locked_registry():
            registry = self._load_registry()
            records = cast(list[dict[str, object]], registry["instances"])
            for record in records:
                if (
                    record.get("local_instance_id") == binding.local_instance_id
                    and record.get("pid") == self._process_id
                    and record.get("hostname") == self._hostname
                ):
                    record["pid"] = None
                    record["lease_updated_at"] = self._now().isoformat()
                    self._write_registry(registry)
                    break
        binding.instance_root.joinpath("instance-lease.json").unlink(missing_ok=True)

    def _binding_from_record(
        self, record: dict[str, object]
    ) -> RuntimeInstanceBinding:
        core_id = str(record["core_id"])
        local_instance_id = str(record["local_instance_id"])
        instance_root = (
            self.app_data_root
            / "cores"
            / core_id
            / "instances"
            / local_instance_id
        )
        return RuntimeInstanceBinding(
            core_id=core_id,
            local_instance_id=local_instance_id,
            core_path=Path(str(record["core_path"])),
            instance_root=instance_root,
            pg_data_dir=instance_root / "runtime" / "pg_data",
            legacy_pg_data_dir=instance_root
            / "legacy-runtime-source"
            / "pg_data",
            indices_dir=instance_root / "cache" / "indices",
            health_log_dir=instance_root / "health-logs",
            migration_journal_path=instance_root
            / "migration"
            / "corefs-migration-journal.json",
            filesystem_identity=str(record["filesystem_identity"]),
            runtime_url_fingerprint=cast(
                str | None, record.get("runtime_url_fingerprint")
            ),
        )

    def _record_is_live(
        self, record: dict[str, object], *, now: datetime
    ) -> bool:
        raw_updated = record.get("lease_updated_at")
        pid = record.get("pid")
        if not isinstance(raw_updated, str) or not isinstance(pid, int):
            return False
        try:
            updated = datetime.fromisoformat(raw_updated)
        except ValueError:
            return False
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=UTC)
        if record.get("hostname") == self._hostname:
            return self._pid_is_alive(pid)
        return now - updated <= _LEASE_TTL

    def _reject_runtime_url_collision(
        self,
        records: list[dict[str, object]],
        *,
        core_id: str,
        filesystem_identity: str,
        runtime_url_fingerprint: str,
        now: datetime,
    ) -> None:
        for record in records:
            if record.get("runtime_url_fingerprint") != runtime_url_fingerprint:
                continue
            same_instance = (
                record.get("core_id") == core_id
                and record.get("filesystem_identity") == filesystem_identity
            )
            if same_instance:
                return
            if self._record_is_live(record, now=now):
                raise InstanceBindingCollision(
                    "runtime URL is already claimed by another live Core instance"
                )
            # Even a stale explicit database can contain divergent state. It must
            # be explicitly rebuilt/rebound instead of silently mixed.
            raise InstanceBindingCollision(
                "runtime URL is already bound to another Core instance"
            )

    def _load_registry(self) -> dict[str, object]:
        if not self._registry_path.exists():
            return {"version": _REGISTRY_VERSION, "instances": []}
        try:
            payload = json.loads(self._registry_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise InstanceBindingCollision(
                "machine-local instance registry is unreadable"
            ) from exc
        if (
            not isinstance(payload, dict)
            or payload.get("version") != _REGISTRY_VERSION
            or not isinstance(payload.get("instances"), list)
        ):
            raise InstanceBindingCollision(
                "machine-local instance registry has an unsupported format"
            )
        return payload

    def _write_registry(self, registry: dict[str, object]) -> None:
        self._registry_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(self._registry_path, registry)

    def _write_instance_lease(
        self, binding: RuntimeInstanceBinding, *, now: datetime
    ) -> None:
        binding.instance_root.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(
            binding.instance_root / "instance-lease.json",
            {
                "version": 1,
                "core_id": binding.core_id,
                "local_instance_id": binding.local_instance_id,
                "filesystem_identity": binding.filesystem_identity,
                "hostname": self._hostname,
                "pid": self._process_id,
                "updated_at": now.isoformat(),
            },
        )

    @contextmanager
    def _locked_registry(self) -> Iterator[None]:
        """Serialize cross-process registry updates with an atomic lock file."""
        lock_path = self.app_data_root / ".core-instance-registry.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor: int | None = None
        try:
            try:
                descriptor = os.open(
                    lock_path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o600,
                )
            except FileExistsError:
                # A process-local RLock already covers threads. A surviving
                # lock file is treated as stale only when its owner is dead.
                try:
                    lock_payload = json.loads(lock_path.read_text(encoding="utf-8"))
                    lock_pid = int(lock_payload.get("pid", 0))
                    lock_host = str(lock_payload.get("hostname", ""))
                except (OSError, ValueError, json.JSONDecodeError):
                    lock_pid = 0
                    lock_host = ""
                if lock_host == self._hostname and not self._pid_is_alive(lock_pid):
                    lock_path.unlink(missing_ok=True)
                    descriptor = os.open(
                        lock_path,
                        os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                        0o600,
                    )
                else:
                    raise InstanceBindingCollision(
                        "machine-local instance registry is being updated"
                    ) from None
            os.write(
                descriptor,
                json.dumps(
                    {"pid": self._process_id, "hostname": self._hostname}
                ).encode("utf-8"),
            )
            yield
        finally:
            if descriptor is not None:
                os.close(descriptor)
                lock_path.unlink(missing_ok=True)


def _read_core_id(core_path: Path) -> str:
    try:
        payload = json.loads((core_path / "manifest.json").read_text(encoding="utf-8"))
        core_id = payload["core_id"]
    except (OSError, KeyError, json.JSONDecodeError, TypeError) as exc:
        raise InstanceBindingCollision("Core manifest has no valid core_id") from exc
    if not isinstance(core_id, str) or not core_id or "/" in core_id or "\\" in core_id:
        raise InstanceBindingCollision("Core manifest has no valid core_id")
    return core_id


def _filesystem_identity(core_path: Path) -> str:
    stat_result = core_path.stat()
    identity = f"{stat_result.st_dev}:{stat_result.st_ino}"
    return hashlib.sha256(identity.encode("ascii")).hexdigest()


def _runtime_url_fingerprint(runtime_url: str) -> str:
    try:
        parsed = make_url(runtime_url)
    except Exception as exc:
        raise InstanceBindingCollision("runtime URL is invalid") from exc
    normalized = "|".join(
        (
            parsed.get_backend_name(),
            parsed.host or "",
            str(parsed.port or ""),
            parsed.database or "",
            parsed.username or "",
            json.dumps(sorted(parsed.query.items())),
        )
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False
    return True


def _atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    descriptor, raw_temporary = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(raw_temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
