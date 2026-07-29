from __future__ import annotations

import json
from pathlib import Path

import pytest
from anima_server.services.corefs.instance_registry import RuntimeInstanceRegistry
from anima_server.services.corefs.legacy_runtime import (
    LegacyRuntimeCollision,
    assert_core_root_inventory,
    relocate_legacy_runtime,
)


def _make_core(path: Path) -> Path:
    path.mkdir(parents=True)
    (path / "manifest.json").write_text(
        json.dumps({"core_id": "core-runtime-relocation"}),
        encoding="utf-8",
    )
    return path


def test_relocation_quarantines_legacy_pg_and_moves_only_derived_directories(
    managed_tmp_path: Path,
) -> None:
    core = _make_core(managed_tmp_path / "portable" / ".anima")
    binding = RuntimeInstanceRegistry(managed_tmp_path / "app-data").resolve(core)
    seeded_marker = "seeded-message-plaintext"
    legacy_pg = core / "runtime" / "pg_data"
    legacy_pg.mkdir(parents=True)
    (legacy_pg / "PG_VERSION").write_text("17", encoding="ascii")
    (legacy_pg / "base").mkdir()
    (legacy_pg / "base" / "content.bin").write_text(
        seeded_marker,
        encoding="utf-8",
    )
    (core / "indices").mkdir()
    (core / "indices" / "checkpoint.bin").write_bytes(b"index")
    (core / "logs").mkdir()
    (core / "logs" / "health.jsonl").write_text("{}\n", encoding="utf-8")

    result = relocate_legacy_runtime(
        core,
        binding,
        postgres_running=False,
    )

    assert result.legacy_pg_moved is True
    assert result.indices_moved is True
    assert result.health_logs_moved is True
    assert not (core / "runtime").exists()
    assert not (core / "indices").exists()
    assert not (core / "logs").exists()
    assert (binding.legacy_pg_data_dir / "PG_VERSION").read_text(
        encoding="ascii"
    ) == "17"
    assert (binding.indices_dir / "checkpoint.bin").read_bytes() == b"index"
    assert (binding.health_log_dir / "health.jsonl").read_text(
        encoding="utf-8"
    ) == "{}\n"

    journal_text = binding.migration_journal_path.read_text(encoding="utf-8")
    journal = json.loads(journal_text)
    assert journal["version"] == 1
    assert journal["legacy_pg"]["status"] == "quarantined"
    assert journal["legacy_pg"]["files"] == 2
    assert seeded_marker not in journal_text
    assert assert_core_root_inventory(core) == ()


def test_relocation_refuses_to_touch_pg_data_while_postgres_is_running(
    managed_tmp_path: Path,
) -> None:
    core = _make_core(managed_tmp_path / "portable" / ".anima")
    binding = RuntimeInstanceRegistry(managed_tmp_path / "app-data").resolve(core)
    legacy_pg = core / "runtime" / "pg_data"
    legacy_pg.mkdir(parents=True)
    (legacy_pg / "PG_VERSION").write_text("17", encoding="ascii")

    with pytest.raises(LegacyRuntimeCollision, match="PostgreSQL is stopped"):
        relocate_legacy_runtime(core, binding, postgres_running=True)

    assert (legacy_pg / "PG_VERSION").is_file()
    assert not binding.legacy_pg_data_dir.exists()
    assert not binding.migration_journal_path.exists()


def test_relocation_preserves_source_when_quarantine_target_conflicts(
    managed_tmp_path: Path,
) -> None:
    core = _make_core(managed_tmp_path / "portable" / ".anima")
    binding = RuntimeInstanceRegistry(managed_tmp_path / "app-data").resolve(core)
    legacy_pg = core / "runtime" / "pg_data"
    legacy_pg.mkdir(parents=True)
    (legacy_pg / "PG_VERSION").write_text("17", encoding="ascii")
    binding.legacy_pg_data_dir.mkdir(parents=True)
    (binding.legacy_pg_data_dir / "PG_VERSION").write_text("16", encoding="ascii")

    with pytest.raises(LegacyRuntimeCollision, match="different bytes"):
        relocate_legacy_runtime(core, binding, postgres_running=False)

    assert (legacy_pg / "PG_VERSION").read_text(encoding="ascii") == "17"
    assert (binding.legacy_pg_data_dir / "PG_VERSION").read_text(
        encoding="ascii"
    ) == "16"


def test_core_root_inventory_rejects_machine_local_runtime_writers(
    managed_tmp_path: Path,
) -> None:
    core = _make_core(managed_tmp_path / "portable" / ".anima")
    for allowed in ("soul", "fs", "objects", "recovery"):
        (core / allowed).mkdir()
    (core / "core.lock").touch()
    for forbidden in (
        "runtime",
        "indices",
        "logs",
        "runtime-daemon",
        "runtime-daemon-release.json",
    ):
        (core / forbidden).mkdir()

    assert set(assert_core_root_inventory(core)) == {
        "indices",
        "logs",
        "runtime",
        "runtime-daemon",
        "runtime-daemon-release.json",
    }
