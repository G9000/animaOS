from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from anima_server.services.corefs.instance_registry import (
    InstanceBindingCollision,
    RuntimeInstanceRegistry,
)


def _make_core(path: Path, *, core_id: str = "core-019f") -> Path:
    path.mkdir(parents=True)
    (path / "manifest.json").write_text(
        json.dumps({"core_id": core_id}),
        encoding="utf-8",
    )
    return path


def test_registry_places_runtime_outside_core_and_rebinds_a_moved_core(
    managed_tmp_path: Path,
) -> None:
    app_data = managed_tmp_path / "app-data"
    core = _make_core(managed_tmp_path / "portable" / ".anima")
    registry = RuntimeInstanceRegistry(app_data)

    first = registry.resolve(core)
    moved = managed_tmp_path / "moved" / ".anima"
    moved.parent.mkdir()
    core.rename(moved)
    rebound = registry.resolve(moved)

    assert rebound.local_instance_id == first.local_instance_id
    assert rebound.core_path == moved.resolve()
    assert rebound.pg_data_dir == (
        app_data
        / "cores"
        / "core-019f"
        / "instances"
        / first.local_instance_id
        / "runtime"
        / "pg_data"
    )
    assert not rebound.pg_data_dir.is_relative_to(moved)
    assert str(moved.resolve()) not in (moved / "manifest.json").read_text(
        encoding="utf-8"
    )


def test_registry_refuses_a_live_divergent_clone_and_rebuilds_after_stale_lease(
    managed_tmp_path: Path,
) -> None:
    now = datetime(2026, 7, 29, tzinfo=UTC)
    app_data = managed_tmp_path / "app-data"
    source = _make_core(managed_tmp_path / "source" / ".anima")
    clone = managed_tmp_path / "clone" / ".anima"
    shutil.copytree(source, clone)
    registry = RuntimeInstanceRegistry(
        app_data,
        pid_is_alive=lambda pid: pid == 100,
        process_start_identity=lambda pid: f"process-{pid}",
        process_id=100,
        now=lambda: now,
    )
    source_binding = registry.resolve(source)

    with pytest.raises(InstanceBindingCollision, match="live divergent copy"):
        registry.resolve(clone)

    stale_registry = RuntimeInstanceRegistry(
        app_data,
        pid_is_alive=lambda _pid: False,
        process_start_identity=lambda pid: f"process-{pid}",
        process_id=200,
        now=lambda: now + timedelta(hours=25),
    )
    clone_binding = stale_registry.resolve(clone)

    assert clone_binding.local_instance_id != source_binding.local_instance_id
    assert clone_binding.pg_data_dir != source_binding.pg_data_dir


def test_registry_never_expires_a_same_machine_lease_while_its_process_is_alive(
    managed_tmp_path: Path,
) -> None:
    now = datetime(2026, 7, 29, tzinfo=UTC)
    app_data = managed_tmp_path / "app-data"
    source = _make_core(managed_tmp_path / "source" / ".anima")
    clone = managed_tmp_path / "clone" / ".anima"
    shutil.copytree(source, clone)
    RuntimeInstanceRegistry(
        app_data,
        pid_is_alive=lambda pid: pid == 100,
        process_start_identity=lambda pid: f"process-{pid}",
        process_id=100,
        now=lambda: now,
    ).resolve(source)

    long_running_machine = RuntimeInstanceRegistry(
        app_data,
        pid_is_alive=lambda pid: pid == 100,
        process_start_identity=lambda pid: f"process-{pid}",
        process_id=200,
        now=lambda: now + timedelta(days=7),
    )

    with pytest.raises(InstanceBindingCollision, match="live divergent copy"):
        long_running_machine.resolve(clone)


def test_registry_reclaims_reused_pid_lease_and_lock(
    managed_tmp_path: Path,
) -> None:
    now = datetime(2026, 7, 29, tzinfo=UTC)
    app_data = managed_tmp_path / "app-data"
    source = _make_core(managed_tmp_path / "source" / ".anima")
    clone = managed_tmp_path / "clone" / ".anima"
    shutil.copytree(source, clone)
    process_starts = {
        100: "process-100-original",
        200: "process-200-current",
    }

    RuntimeInstanceRegistry(
        app_data,
        pid_is_alive=lambda pid: pid in process_starts,
        process_start_identity=lambda pid: process_starts.get(pid),
        process_id=100,
        now=lambda: now,
        hostname="test-host",
    ).resolve(source)

    process_starts[100] = "process-100-reused"
    lock_path = app_data / ".core-instance-registry.lock"
    lock_path.write_text(
        json.dumps(
            {
                "pid": 100,
                "hostname": "test-host",
                "process_start_identity": "process-100-original",
            }
        ),
        encoding="utf-8",
    )
    replacement = RuntimeInstanceRegistry(
        app_data,
        pid_is_alive=lambda pid: pid in process_starts,
        process_start_identity=lambda pid: process_starts.get(pid),
        process_id=200,
        now=lambda: now + timedelta(minutes=1),
        hostname="test-host",
    )

    clone_binding = replacement.resolve(clone)

    assert clone_binding.core_path == clone.resolve()
    assert not lock_path.exists()


def test_registry_explicit_fork_never_reuses_source_runtime(
    managed_tmp_path: Path,
) -> None:
    app_data = managed_tmp_path / "app-data"
    source = _make_core(managed_tmp_path / "source" / ".anima")
    clone = managed_tmp_path / "clone" / ".anima"
    shutil.copytree(source, clone)
    registry = RuntimeInstanceRegistry(app_data)
    source_binding = registry.resolve(source)

    fork_binding = registry.resolve(clone, fork=True)

    assert fork_binding.local_instance_id != source_binding.local_instance_id
    assert fork_binding.pg_data_dir != source_binding.pg_data_dir


def test_registry_rejects_explicit_runtime_url_collision(
    managed_tmp_path: Path,
) -> None:
    app_data = managed_tmp_path / "app-data"
    first_core = _make_core(
        managed_tmp_path / "first" / ".anima",
        core_id="core-first",
    )
    second_core = _make_core(
        managed_tmp_path / "second" / ".anima",
        core_id="core-second",
    )
    registry = RuntimeInstanceRegistry(app_data)
    explicit_url = "postgresql://anima:secret@localhost:5432/shared"

    registry.resolve(first_core, runtime_url=explicit_url)

    with pytest.raises(InstanceBindingCollision, match="runtime URL"):
        registry.resolve(second_core, runtime_url=explicit_url)


def test_same_instance_can_reclaim_its_explicit_runtime_url(
    managed_tmp_path: Path,
) -> None:
    app_data = managed_tmp_path / "app-data"
    core = _make_core(managed_tmp_path / "portable" / ".anima")
    registry = RuntimeInstanceRegistry(app_data)
    explicit_url = "postgresql://anima:secret@localhost:5432/runtime"

    first = registry.resolve(core, runtime_url=explicit_url)
    second = registry.resolve(core, runtime_url=explicit_url)

    assert second.local_instance_id == first.local_instance_id
    registry.verify_runtime_url_claim(second, explicit_url)
