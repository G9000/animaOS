from __future__ import annotations

import json
import multiprocessing
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from anima_server.config import settings
from anima_server.services.core import migrate_legacy_manifest_runtime_state
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


def _delayed_stale_lock_reclaimer(
    app_data: str,
    stale_unlink_entered: Any,
    competitor_attempted: Any,
    result_path: str,
) -> None:
    lock_path = Path(app_data) / ".core-instance-registry.lock"
    original_unlink = Path.unlink
    delayed = False

    def delayed_unlink(path: Path, *args: Any, **kwargs: Any) -> None:
        nonlocal delayed
        if path == lock_path and not delayed:
            delayed = True
            stale_unlink_entered.set()
            if not competitor_attempted.wait(10):
                raise RuntimeError("competitor did not attempt the registry lock")
        original_unlink(path, *args, **kwargs)

    Path.unlink = delayed_unlink
    try:
        registry = RuntimeInstanceRegistry(
            Path(app_data),
            pid_is_alive=lambda _pid: False,
            process_start_identity=lambda pid: f"process-{pid}",
            hostname="test-host",
        )
        try:
            with registry._locked_registry():
                Path(result_path).write_text("acquired", encoding="utf-8")
        except InstanceBindingCollision:
            Path(result_path).write_text("collision", encoding="utf-8")
    finally:
        Path.unlink = original_unlink


def _competing_stale_lock_reclaimer(
    app_data: str,
    stale_unlink_entered: Any,
    competitor_attempted: Any,
    release_competitor: Any,
    result_path: str,
) -> None:
    if not stale_unlink_entered.wait(10):
        Path(result_path).write_text("stale-wait-timeout", encoding="utf-8")
        competitor_attempted.set()
        return
    registry = RuntimeInstanceRegistry(
        Path(app_data),
        pid_is_alive=lambda _pid: False,
        process_start_identity=lambda pid: f"process-{pid}",
        hostname="test-host",
    )
    try:
        with registry._locked_registry():
            Path(result_path).write_text("acquired", encoding="utf-8")
            competitor_attempted.set()
            release_competitor.wait(10)
    except InstanceBindingCollision:
        Path(result_path).write_text("collision", encoding="utf-8")
        competitor_attempted.set()


def test_default_pid_probe_does_not_signal_current_process() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import os; "
                "from anima_server.services.corefs.instance_registry "
                "import _pid_is_alive; "
                "raise SystemExit(0 if _pid_is_alive(os.getpid()) else 1)"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


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


def test_registry_copy_verifies_legacy_manifest_runtime_state(
    managed_tmp_path: Path,
) -> None:
    app_data = managed_tmp_path / "app-data"
    core = _make_core(managed_tmp_path / "portable" / ".anima")
    registry = RuntimeInstanceRegistry(app_data)
    binding = registry.resolve(core)
    values: dict[str, object] = {
        "runtime_database_engine": "postgres",
        "runtime_database_target_engine": "turso",
        "runtime_migration_state": {"state": "requested"},
    }

    registry.record_legacy_manifest_state(binding, values)
    registry.record_legacy_manifest_state(binding, values)

    payload = json.loads((app_data / "core-instance-registry.json").read_text())
    record = next(
        item
        for item in payload["instances"]
        if item["local_instance_id"] == binding.local_instance_id
    )
    assert record["legacy_runtime_manifest_state"] == values

    with pytest.raises(InstanceBindingCollision, match="conflicts"):
        registry.record_legacy_manifest_state(
            binding,
            {"runtime_database_engine": "turso"},
        )


def test_legacy_manifest_runtime_state_is_scrubbed_only_after_registry_copy(
    managed_tmp_path: Path,
) -> None:
    previous_data_dir = settings.data_dir
    app_data = managed_tmp_path / "app-data"
    core = _make_core(managed_tmp_path / "portable" / ".anima")
    manifest_path = core / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest.update(
        {
            "runtime_database_engine": "postgres",
            "runtime_migration_state": {"state": "ready"},
        }
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    settings.data_dir = core
    try:
        registry = RuntimeInstanceRegistry(app_data)
        binding = registry.resolve(core)
        migrate_legacy_manifest_runtime_state(registry, binding)
    finally:
        settings.data_dir = previous_data_dir

    scrubbed = json.loads(manifest_path.read_text())
    assert "runtime_database_engine" not in scrubbed
    assert "runtime_migration_state" not in scrubbed
    recorded = json.loads((app_data / "core-instance-registry.json").read_text())
    state = recorded["instances"][0]["legacy_runtime_manifest_state"]
    assert state == {
        "runtime_database_engine": "postgres",
        "runtime_migration_state": {"state": "ready"},
    }


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


def test_registry_refuses_a_second_live_owner_for_the_same_core_path(
    managed_tmp_path: Path,
) -> None:
    now = datetime(2026, 7, 29, tzinfo=UTC)
    app_data = managed_tmp_path / "app-data"
    core = _make_core(managed_tmp_path / "portable" / ".anima")
    process_starts = {
        100: "process-100",
        200: "process-200",
    }
    first = RuntimeInstanceRegistry(
        app_data,
        pid_is_alive=lambda pid: pid in process_starts,
        process_start_identity=lambda pid: process_starts.get(pid),
        process_id=100,
        now=lambda: now,
        hostname="test-host",
    )
    first_binding = first.resolve(core)

    second = RuntimeInstanceRegistry(
        app_data,
        pid_is_alive=lambda pid: pid in process_starts,
        process_start_identity=lambda pid: process_starts.get(pid),
        process_id=200,
        now=lambda: now + timedelta(minutes=1),
        hostname="test-host",
    )

    with pytest.raises(InstanceBindingCollision, match="already owns"):
        second.resolve(core)

    registry_payload = json.loads(
        (app_data / "core-instance-registry.json").read_text(encoding="utf-8")
    )
    lease_payload = json.loads(
        (first_binding.instance_root / "instance-lease.json").read_text(
            encoding="utf-8"
        )
    )
    assert registry_payload["instances"][0]["pid"] == 100
    assert lease_payload["pid"] == 100

    del process_starts[100]
    reclaimed = second.resolve(core)
    assert reclaimed.local_instance_id == first_binding.local_instance_id


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


def test_registry_reclaims_malformed_lock_after_bounded_freshness_window(
    managed_tmp_path: Path,
) -> None:
    now = datetime(2026, 7, 29, tzinfo=UTC)
    app_data = managed_tmp_path / "app-data"
    core = _make_core(managed_tmp_path / "portable" / ".anima")
    lock_path = app_data / ".core-instance-registry.lock"
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text("{", encoding="utf-8")
    registry = RuntimeInstanceRegistry(
        app_data,
        process_start_identity=lambda pid: f"process-{pid}",
        process_id=200,
        now=lambda: now,
        hostname="test-host",
    )

    with pytest.raises(InstanceBindingCollision, match="being updated"):
        registry.resolve(core)

    stale_timestamp = (now - timedelta(minutes=2)).timestamp()
    os.utime(lock_path, (stale_timestamp, stale_timestamp))

    binding = registry.resolve(core)

    assert binding.core_path == core.resolve()
    assert not lock_path.exists()


def test_registry_reclaims_foreign_host_lock_after_bounded_freshness_window(
    managed_tmp_path: Path,
) -> None:
    now = datetime(2026, 7, 29, tzinfo=UTC)
    app_data = managed_tmp_path / "app-data"
    core = _make_core(managed_tmp_path / "portable" / ".anima")
    lock_path = app_data / ".core-instance-registry.lock"
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text(
        json.dumps(
            {
                "pid": 100,
                "hostname": "old-hostname",
                "process_start_identity": "process-100",
            }
        ),
        encoding="utf-8",
    )
    registry = RuntimeInstanceRegistry(
        app_data,
        process_start_identity=lambda pid: f"process-{pid}",
        process_id=200,
        now=lambda: now,
        hostname="new-hostname",
    )

    with pytest.raises(InstanceBindingCollision, match="being updated"):
        registry.resolve(core)

    stale_timestamp = (now - timedelta(minutes=2)).timestamp()
    os.utime(lock_path, (stale_timestamp, stale_timestamp))

    binding = registry.resolve(core)

    assert binding.core_path == core.resolve()
    assert not lock_path.exists()


def test_registry_stale_lock_reclamation_is_atomic_across_processes(
    managed_tmp_path: Path,
) -> None:
    app_data = managed_tmp_path / "app-data"
    lock_path = app_data / ".core-instance-registry.lock"
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text(
        json.dumps(
            {
                "pid": 999_999_999,
                "hostname": "test-host",
                "process_start_identity": "dead-process",
            }
        ),
        encoding="utf-8",
    )
    context = multiprocessing.get_context("spawn")
    stale_unlink_entered = context.Event()
    competitor_attempted = context.Event()
    release_competitor = context.Event()
    first_result = managed_tmp_path / "first-result.txt"
    second_result = managed_tmp_path / "second-result.txt"
    first = context.Process(
        target=_delayed_stale_lock_reclaimer,
        args=(
            str(app_data),
            stale_unlink_entered,
            competitor_attempted,
            str(first_result),
        ),
    )
    second = context.Process(
        target=_competing_stale_lock_reclaimer,
        args=(
            str(app_data),
            stale_unlink_entered,
            competitor_attempted,
            release_competitor,
            str(second_result),
        ),
    )

    first.start()
    assert stale_unlink_entered.wait(10)
    second.start()
    assert competitor_attempted.wait(10)
    first.join(timeout=10)
    release_competitor.set()
    second.join(timeout=10)

    assert first.exitcode == 0
    assert second.exitcode == 0
    assert first_result.read_text(encoding="utf-8") == "acquired"
    assert second_result.read_text(encoding="utf-8") == "collision"


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
