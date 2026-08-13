from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from anima_server.config import settings
from anima_server.services import anima_core_bindings
from anima_server.services.corefs.archive_transfer import (
    CoreArchivePayloadKind,
    CoreArchiveTransferError,
    export_core_archive_v2,
    stage_core_archive_v2,
    verify_core_archive_v2,
)

CORE_ID = "018f0f4e-4ee4-7aa5-8eb2-1eb7699855bd"
OWNER_ID = "018f0f4e-4ee4-7aa5-8eb2-1eb7699855be"
ARCHIVE_ID = "018f0f4e-4ee4-7aa5-8eb2-1eb7699855bf"


def _core(managed_tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = managed_tmp_path / ".anima"
    (root / "soul").mkdir(parents=True)
    (root / "fs" / "catalogs").mkdir(parents=True)
    (root / "objects").mkdir()
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "core_id": CORE_ID,
                "owner_id": OWNER_ID,
                "keyslots_version": 1,
                "keyslots": [{"kind": "wrapped-test-key"}],
            }
        ),
        encoding="utf-8",
    )
    soul = sqlite3.connect(root / "soul" / "soul.db")
    try:
        soul.execute("PRAGMA journal_mode = WAL")
        soul.execute("CREATE TABLE alembic_version (version_num TEXT PRIMARY KEY)")
        soul.execute("INSERT INTO alembic_version VALUES ('202608130001')")
        soul.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT NOT NULL)")
        soul.execute("INSERT INTO users VALUES (7, 'owner')")
        soul.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY, content TEXT NOT NULL)")
        soul.execute("INSERT INTO memories VALUES (1, 'portable memory')")
        soul.commit()
    finally:
        soul.close()
    (root / "fs" / "HEAD").write_bytes(b"authenticated-head")
    catalog = "catalog-00000000000000000007-" + ("a" * 64) + ".acore"
    (root / "fs" / "catalogs" / catalog).write_bytes(b"encrypted-catalog")
    (root / "objects" / "reachable.acore").write_bytes(b"encrypted-object")
    (root / "objects" / "unreachable.acore").write_bytes(b"garbage")
    monkeypatch.setattr(settings, "data_dir", root)
    return root


def _session(
    root: Path,
    *,
    source_override: Path | None = None,
    writer: object | None = None,
) -> SimpleNamespace:
    catalog = "catalog-00000000000000000007-" + ("a" * 64) + ".acore"

    class Native:
        def archive_inventory_v2(self, _keys: object) -> dict[str, object]:
            object_source = source_override or (root / "objects" / "reachable.acore")
            return {
                "version": 1,
                "generation": 7,
                "catalogHash": "a" * 64,
                "sources": [
                    {
                        "recordType": "catalog",
                        "recordPath": "fs/HEAD",
                        "sourcePath": str(root / "fs" / "HEAD"),
                    },
                    {
                        "recordType": "catalog",
                        "recordPath": f"fs/catalogs/{catalog}",
                        "sourcePath": str(root / "fs" / "catalogs" / catalog),
                    },
                    {
                        "recordType": "object",
                        "recordPath": "objects/reachable.acore",
                        "sourcePath": str(object_source),
                    },
                ],
            }

        def archive_write_v2(
            self,
            _keys: object,
            path: str,
            passphrase: bytes,
            request_json: str,
        ) -> dict[str, object]:
            if not callable(writer):
                raise AssertionError("archive writer was not installed")
            return writer(path, passphrase, request_json)

    return SimpleNamespace(corefs_session=Native(), corefs_keys=object())


def test_full_export_uses_only_native_reachable_inventory_and_wrapped_keyslots(
    managed_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _core(managed_tmp_path, monkeypatch)
    output = managed_tmp_path / "core.anima"
    captured: dict[str, object] = {}

    def writer(path: str, passphrase: bytes, request_json: str) -> dict[str, object]:
        request = json.loads(request_json)
        captured.update(request)
        sources = request["sources"]
        assert passphrase == b"correct horse battery staple"
        assert Path(path) == output
        assert all(Path(item["sourcePath"]).is_file() for item in sources)
        keyslot = next(item for item in sources if item["recordType"] == "keyslots")
        head = next(item for item in sources if item["recordPath"] == "fs/HEAD")
        assert Path(head["sourcePath"]) != root / "fs" / "HEAD"
        assert Path(head["sourcePath"]).read_bytes() == b"authenticated-head"
        assert request["filesystemCatalogHash"] == "a" * 64
        assert json.loads(Path(keyslot["sourcePath"]).read_text(encoding="utf-8"))["keyslots"] == [
            {"kind": "wrapped-test-key"}
        ]
        selected_bytes = sum(Path(item["sourcePath"]).stat().st_size for item in sources)
        output.write_bytes(b"archive")
        return {
            "version": 2,
            "archiveId": ARCHIVE_ID,
            "volumeSetId": ARCHIVE_ID,
            "payloadKind": "full",
            "coreId": CORE_ID,
            "ownerId": OWNER_ID,
            "soulGeneration": 3,
            "filesystemGeneration": 7,
            "recordCount": len(sources),
            "chunkCount": len(sources),
            "plaintextBytes": selected_bytes,
            "maxBufferBytes": 2 * 1024 * 1024,
        }

    result = export_core_archive_v2(
        session=_session(root, writer=writer),
        output_path=output,
        passphrase="correct horse battery staple",
        payload_kind=CoreArchivePayloadKind.FULL,
        soul_generation=3,
    )

    record_paths = {item["recordPath"] for item in captured["sources"]}  # type: ignore[index]
    assert "objects/reachable.acore" in record_paths
    assert "objects/unreachable.acore" not in record_paths
    assert result.inventory.filesystem_generation == 7
    assert result.inventory.soul_generation == 3
    assert result.max_buffer_bytes <= 32 * 1024 * 1024
    assert result.inventory.soul_inventory_hash is not None


def test_live_export_snapshots_committed_wal_and_cleans_temporary_database(
    managed_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _core(managed_tmp_path, monkeypatch)
    output = managed_tmp_path / "core.anima"
    live = sqlite3.connect(root / "soul" / "soul.db")
    captured_snapshot: Path | None = None
    try:
        live.execute("PRAGMA wal_autocheckpoint = 0")
        live.execute("INSERT INTO memories VALUES (2, 'committed only in WAL')")
        live.commit()
        wal_path = root / "soul" / "soul.db-wal"
        assert wal_path.is_file() and wal_path.stat().st_size > 0

        def writer(path: str, _passphrase: bytes, request_json: str) -> dict[str, object]:
            nonlocal captured_snapshot
            request = json.loads(request_json)
            sources = request["sources"]
            soul_source = next(item for item in sources if item["recordType"] == "soul_database")
            captured_snapshot = Path(soul_source["sourcePath"])
            assert captured_snapshot != root / "soul" / "soul.db"
            snapshot = sqlite3.connect(captured_snapshot)
            try:
                assert snapshot.execute("SELECT content FROM memories WHERE id = 2").fetchone() == (
                    "committed only in WAL",
                )
                assert snapshot.execute("PRAGMA integrity_check").fetchone() == ("ok",)
            finally:
                snapshot.close()
            selected_bytes = sum(Path(item["sourcePath"]).stat().st_size for item in sources)
            Path(path).write_bytes(b"archive")
            return {
                "version": 2,
                "archiveId": ARCHIVE_ID,
                "volumeSetId": ARCHIVE_ID,
                "payloadKind": "full",
                "coreId": CORE_ID,
                "ownerId": OWNER_ID,
                "soulGeneration": 3,
                "filesystemGeneration": 7,
                "recordCount": len(sources),
                "chunkCount": len(sources),
                "plaintextBytes": selected_bytes,
                "maxBufferBytes": 2 * 1024 * 1024,
            }

        result = export_core_archive_v2(
            session=_session(root, writer=writer),
            output_path=output,
            passphrase="correct horse battery staple",
            payload_kind=CoreArchivePayloadKind.FULL,
            soul_generation=3,
        )
        assert result.inventory.soul_inventory_hash is not None
    finally:
        live.close()

    assert captured_snapshot is not None
    assert not captured_snapshot.exists()


def test_full_export_rejects_filesystem_generation_change_during_soul_snapshot(
    managed_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _core(managed_tmp_path, monkeypatch)
    session = _session(root, writer=lambda *_args: None)
    original_inventory = session.corefs_session.archive_inventory_v2
    calls = 0

    def changing_inventory(keys: object) -> dict[str, object]:
        nonlocal calls
        calls += 1
        inventory = original_inventory(keys)
        if calls == 2:
            inventory["generation"] = 8
            inventory["catalogHash"] = "b" * 64
        return inventory

    session.corefs_session.archive_inventory_v2 = changing_inventory

    with pytest.raises(CoreArchiveTransferError, match="changed during archive capture"):
        export_core_archive_v2(
            session=session,
            output_path=managed_tmp_path / "core.anima",
            passphrase="correct horse battery staple",
            payload_kind=CoreArchivePayloadKind.FULL,
            soul_generation=3,
        )


def test_failed_native_export_removes_verified_soul_snapshot(
    managed_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _core(managed_tmp_path, monkeypatch)
    captured_snapshot: Path | None = None

    def fail(_path: str, _passphrase: bytes, request_json: str) -> None:
        nonlocal captured_snapshot
        request = json.loads(request_json)
        soul_source = next(
            item for item in request["sources"] if item["recordType"] == "soul_database"
        )
        captured_snapshot = Path(soul_source["sourcePath"])
        assert captured_snapshot.is_file()
        raise RuntimeError("simulated native failure")

    with pytest.raises(CoreArchiveTransferError, match="native ANIMA CORE archive export failed"):
        export_core_archive_v2(
            session=_session(root, writer=fail),
            output_path=managed_tmp_path / "core.anima",
            passphrase="correct horse battery staple",
            payload_kind=CoreArchivePayloadKind.FULL,
            soul_generation=3,
        )

    assert captured_snapshot is not None
    assert not captured_snapshot.exists()


@pytest.mark.parametrize(
    ("payload_kind", "included_purpose", "excluded_purpose", "degraded_state"),
    [
        (CoreArchivePayloadKind.SOUL, "soul", "filesystem-root", "filesystem_missing"),
        (CoreArchivePayloadKind.FS, "filesystem-root", "soul", "recovery_only"),
    ],
)
def test_scoped_export_excludes_other_compartment_key_material(
    managed_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    payload_kind: CoreArchivePayloadKind,
    included_purpose: str,
    excluded_purpose: str,
    degraded_state: str,
) -> None:
    root = _core(managed_tmp_path, monkeypatch)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(
        {
            "wrapped_sqlcipher_key": {"wrapped_key": "soul-password-root"},
            "recovery_sqlcipher_key": {"wrapped_key": "soul-recovery-root"},
            "frk_rotation": {
                "active_version": 1,
                "pending_version": None,
                "decrypt_only_versions": [],
                "phase": "idle",
                "object_key_epoch": 1,
            },
            "active_filesystem_root_generation": 1,
            "keyslots": [
                {
                    "purpose": "soul",
                    "scope": "full",
                    "wrapped": {"wrapped_key": "soul-root"},
                },
                {
                    "purpose": "filesystem-root",
                    "scope": "full",
                    "wrapped": {"wrapped_key": "filesystem-root"},
                },
            ],
        }
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    output = managed_tmp_path / f"{payload_kind.value}.anima"

    def writer(path: str, _passphrase: bytes, request_json: str) -> dict[str, object]:
        request = json.loads(request_json)
        sources = request["sources"]
        manifest_source = next(item for item in sources if item["recordType"] == "manifest")
        keyslot_source = next(item for item in sources if item["recordType"] == "keyslots")
        archived_manifest = json.loads(
            Path(manifest_source["sourcePath"]).read_text(encoding="utf-8")
        )
        archived_keyslots = json.loads(
            Path(keyslot_source["sourcePath"]).read_text(encoding="utf-8")
        )

        assert archived_manifest["archive_payload_scope"] == payload_kind.value
        assert archived_manifest["degraded_state"] == degraded_state
        assert {slot["purpose"] for slot in archived_manifest["keyslots"]} == {included_purpose}
        assert {slot["scope"] for slot in archived_manifest["keyslots"]} == {"full"}
        assert archived_keyslots["keyslots"] == archived_manifest["keyslots"]
        assert excluded_purpose not in json.dumps(archived_manifest["keyslots"])
        if payload_kind is CoreArchivePayloadKind.SOUL:
            assert "frk_rotation" not in archived_manifest
            assert "active_filesystem_root_generation" not in archived_manifest
        else:
            assert "wrapped_sqlcipher_key" not in archived_manifest
            assert "recovery_sqlcipher_key" not in archived_manifest
            assert "sqlcipher_kdf_salt" not in archived_manifest

        selected_bytes = sum(Path(item["sourcePath"]).stat().st_size for item in sources)
        Path(path).write_bytes(b"archive")
        return {
            "version": 2,
            "archiveId": ARCHIVE_ID,
            "volumeSetId": ARCHIVE_ID,
            "payloadKind": payload_kind.value,
            "coreId": CORE_ID,
            "ownerId": OWNER_ID,
            "soulGeneration": 3 if payload_kind is CoreArchivePayloadKind.SOUL else None,
            "filesystemGeneration": 7 if payload_kind is CoreArchivePayloadKind.FS else None,
            "recordCount": len(sources),
            "chunkCount": len(sources),
            "plaintextBytes": selected_bytes,
            "maxBufferBytes": 2 * 1024 * 1024,
        }

    result = export_core_archive_v2(
        session=_session(root, writer=writer),
        output_path=output,
        passphrase="correct horse battery staple",
        payload_kind=payload_kind,
        soul_generation=3 if payload_kind is CoreArchivePayloadKind.SOUL else None,
    )

    assert result.inventory.payload_kind is payload_kind


def test_scoped_export_rejects_ambiguous_keyslot_shape(
    managed_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _core(managed_tmp_path, monkeypatch)

    with pytest.raises(CoreArchiveTransferError, match="keyslot"):
        export_core_archive_v2(
            session=_session(root, writer=lambda *_args: None),
            output_path=managed_tmp_path / "soul.anima",
            passphrase="correct horse battery staple",
            payload_kind=CoreArchivePayloadKind.SOUL,
            soul_generation=3,
        )


def test_native_inventory_source_must_stay_inside_active_core(
    managed_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _core(managed_tmp_path, monkeypatch)
    outside = managed_tmp_path / "outside.acore"
    outside.write_bytes(b"not-the-pinned-object")

    with pytest.raises(CoreArchiveTransferError, match="escaped"):
        export_core_archive_v2(
            session=_session(root, source_override=outside),
            output_path=managed_tmp_path / "core.anima",
            passphrase="correct horse battery staple",
            payload_kind=CoreArchivePayloadKind.FULL,
            soul_generation=3,
        )


def test_verifier_extracts_to_disposable_sibling_and_matches_prepared_inventory(
    managed_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = managed_tmp_path / "core.anima"
    archive.write_bytes(b"archive")
    observed: list[Path] = []

    def extractor(path: str, passphrase: bytes, destination: str) -> dict[str, object]:
        assert Path(path) == archive
        assert passphrase == b"correct horse battery staple"
        staging = Path(destination)
        observed.append(staging)
        staging.mkdir()
        (staging / "manifest.json").write_bytes(b"manifest")
        return {
            "version": 2,
            "archiveId": ARCHIVE_ID,
            "volumeSetId": ARCHIVE_ID,
            "payloadKind": "fs",
            "coreId": CORE_ID,
            "ownerId": OWNER_ID,
            "soulGeneration": None,
            "filesystemGeneration": 7,
            "recordCount": 4,
            "chunkCount": 4,
            "plaintextBytes": 100,
            "maxBufferBytes": 1024,
            "records": [],
        }

    monkeypatch.setattr(
        anima_core_bindings,
        "require_binding",
        lambda name: extractor if name == "core_archive_extract_v2" else None,
    )

    summary = verify_core_archive_v2(
        archive,
        passphrase="correct horse battery staple",
    )

    assert summary["payloadKind"] == "fs"
    assert len(observed) == 1
    assert not observed[0].exists()


def _staged_archive_summary(
    staging: Path,
    *,
    payload_kind: CoreArchivePayloadKind,
    tamper_keyslots: bool = False,
) -> dict[str, object]:
    slots = (
        [
            {
                "purpose": "soul",
                "scope": "full",
                "wrapped": {"wrapped_key": "soul-root"},
            }
        ]
        if payload_kind is CoreArchivePayloadKind.SOUL
        else [
            {
                "purpose": "filesystem-root",
                "scope": "full",
                "wrapped": {"wrapped_key": "filesystem-root"},
            }
        ]
    )
    if payload_kind is CoreArchivePayloadKind.FULL:
        slots = [
            {
                "purpose": "soul",
                "scope": "full",
                "wrapped": {"wrapped_key": "soul-root"},
            },
            {
                "purpose": "filesystem-root",
                "scope": "full",
                "wrapped": {"wrapped_key": "filesystem-root"},
            },
        ]
    manifest = {
        "core_id": CORE_ID,
        "owner_id": OWNER_ID,
        "keyslots_version": 1,
        "keyslots": slots,
        "active_recovery_credential_generation": 1,
        "active_filesystem_root_generation": (
            1 if payload_kind is not CoreArchivePayloadKind.SOUL else None
        ),
        "archive_payload_scope": payload_kind.value,
    }
    if payload_kind is CoreArchivePayloadKind.SOUL:
        manifest["degraded_state"] = "filesystem_missing"
    elif payload_kind is CoreArchivePayloadKind.FS:
        manifest["degraded_state"] = "recovery_only"

    files: dict[str, bytes] = {
        "manifest.json": json.dumps(manifest, sort_keys=True).encode(),
        "keyslots/root-keyslots.json": json.dumps(
            {
                "version": 1,
                "keyslotsVersion": 1,
                "keyslots": (
                    [
                        {
                            "purpose": "soul",
                            "scope": "full",
                            "wrapped": {"wrapped_key": "tampered"},
                        }
                    ]
                    if tamper_keyslots
                    else slots
                ),
                "activeFilesystemRootGeneration": manifest.get("active_filesystem_root_generation"),
                "activeRecoveryCredentialGeneration": 1,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode(),
    }
    record_types = {
        "manifest.json": "manifest",
        "keyslots/root-keyslots.json": "keyslots",
    }
    if payload_kind in {CoreArchivePayloadKind.FULL, CoreArchivePayloadKind.SOUL}:
        files["soul/soul.db"] = b"encrypted-soul"
        record_types["soul/soul.db"] = "soul_database"
    if payload_kind in {CoreArchivePayloadKind.FULL, CoreArchivePayloadKind.FS}:
        files["fs/HEAD"] = b"authenticated-head"
        files["fs/catalogs/catalog.acore"] = b"encrypted-catalog"
        record_types["fs/HEAD"] = "catalog"
        record_types["fs/catalogs/catalog.acore"] = "catalog"

    staging.mkdir()
    for relative, content in files.items():
        path = staging / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    records = [
        {
            "recordType": record_types[relative],
            "recordPath": relative,
            "plaintextLength": len(content),
            "recordHash": "a" * 64,
        }
        for relative, content in files.items()
    ]
    return {
        "version": 2,
        "archiveId": ARCHIVE_ID,
        "volumeSetId": ARCHIVE_ID,
        "payloadKind": payload_kind.value,
        "coreId": CORE_ID,
        "ownerId": OWNER_ID,
        "soulGeneration": (
            3
            if payload_kind in {CoreArchivePayloadKind.FULL, CoreArchivePayloadKind.SOUL}
            else None
        ),
        "filesystemGeneration": (
            7 if payload_kind in {CoreArchivePayloadKind.FULL, CoreArchivePayloadKind.FS} else None
        ),
        "recordCount": len(records),
        "chunkCount": len(records),
        "plaintextBytes": sum(len(content) for content in files.values()),
        "maxBufferBytes": 1024,
        "records": records,
    }


@pytest.mark.parametrize(
    "payload_kind",
    [CoreArchivePayloadKind.FULL, CoreArchivePayloadKind.SOUL, CoreArchivePayloadKind.FS],
)
def test_stage_archive_authenticates_exact_tree_without_activating(
    managed_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    payload_kind: CoreArchivePayloadKind,
) -> None:
    archive = managed_tmp_path / f"{payload_kind.value}.anima"
    archive.write_bytes(b"archive")
    staging = managed_tmp_path / f".{payload_kind.value}.partial"

    def extractor(_path: str, passphrase: bytes, destination: str) -> dict[str, object]:
        assert passphrase == b"correct horse battery staple"
        return _staged_archive_summary(Path(destination), payload_kind=payload_kind)

    monkeypatch.setattr(anima_core_bindings, "require_binding", lambda _name: extractor)
    result = stage_core_archive_v2(
        archive,
        passphrase="correct horse battery staple",
        staging_path=staging,
    )

    assert result.inventory.payload_kind is payload_kind
    assert result.staging_path == staging
    assert staging.is_dir()
    assert {path for path, _length, _digest in result.control_records} == {
        "manifest.json",
        "keyslots/root-keyslots.json",
        *(
            {"fs/HEAD", "fs/catalogs/catalog.acore"}
            if payload_kind in {CoreArchivePayloadKind.FULL, CoreArchivePayloadKind.FS}
            else set()
        ),
    }


@pytest.mark.parametrize("tamper", ["keyslots", "extra_file"])
def test_stage_archive_failure_removes_all_staging_residue(
    managed_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tamper: str,
) -> None:
    archive = managed_tmp_path / "soul.anima"
    archive.write_bytes(b"archive")
    staging = managed_tmp_path / ".soul.partial"

    def extractor(_path: str, _passphrase: bytes, destination: str) -> dict[str, object]:
        destination_path = Path(destination)
        summary = _staged_archive_summary(
            destination_path,
            payload_kind=CoreArchivePayloadKind.SOUL,
            tamper_keyslots=tamper == "keyslots",
        )
        if tamper == "extra_file":
            (destination_path / "unexpected").write_bytes(b"not authenticated")
        return summary

    monkeypatch.setattr(anima_core_bindings, "require_binding", lambda _name: extractor)
    with pytest.raises(CoreArchiveTransferError):
        stage_core_archive_v2(
            archive,
            passphrase="correct horse battery staple",
            staging_path=staging,
        )

    assert not staging.exists()


def test_stage_archive_rejects_symbolic_link_source(
    managed_tmp_path: Path,
) -> None:
    archive = managed_tmp_path / "source.anima"
    archive.write_bytes(b"archive")
    alias = managed_tmp_path / "alias.anima"
    alias.symlink_to(archive)

    with pytest.raises(CoreArchiveTransferError, match="regular file"):
        stage_core_archive_v2(
            alias,
            passphrase="correct horse battery staple",
            staging_path=managed_tmp_path / ".restore.partial",
        )
