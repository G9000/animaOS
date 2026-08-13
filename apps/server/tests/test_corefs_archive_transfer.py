from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from anima_server.config import settings
from anima_server.services import anima_core_bindings
from anima_server.services.corefs.archive_transfer import (
    CoreArchivePayloadKind,
    CoreArchiveTransferError,
    export_core_archive_v2,
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
    (root / "soul" / "soul.db").write_bytes(b"encrypted-soul")
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
