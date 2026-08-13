from __future__ import annotations

import hashlib
import json
from base64 import b64encode
from pathlib import Path
from types import SimpleNamespace

import pytest
from anima_server.services.corefs import asset_authority
from anima_server.services.corefs.asset_authority import (
    AssetAuthoritySelection,
    CoreFsByteSource,
    CoreFsSourceError,
    open_corefs_byte_source,
)
from anima_server.services.corefs.asset_migration import (
    PortableBinaryAssetSource,
    build_portable_asset_shadow,
)
from anima_server.services.corefs.diary_migration import migration_opaque_id


def _logical_wire(result: dict[str, object], *, generation: int = 4) -> bytes:
    return json.dumps(
        {
            "version": "corefs-logical-v1",
            "result": {"generation": generation, **result},
        }
    ).encode()


def test_original_document_is_canonical_and_host_path_is_converter_only(
    tmp_path: Path,
) -> None:
    document = tmp_path / "private-report.pdf"
    document.write_bytes(b"%PDF-1.7\nportable original\n%%EOF")
    digest = hashlib.sha256(document.read_bytes()).hexdigest()

    shadow = build_portable_asset_shadow(
        user_id=7,
        binary_assets=[
            PortableBinaryAssetSource(
                namespace="document",
                legacy_id="23",
                name="report.pdf",
                kind="attachment",
                content_type="application/pdf",
                path=document,
                size=document.stat().st_size,
                sha256=digest,
                created_at="2026-08-13T00:00:00.000000+00:00",
                updated_at="2026-08-13T00:00:00.000000+00:00",
                metadata={"title": "Private report", "parseQuality": "full"},
            )
        ],
    )

    item = shadow.objects[0]
    assert item.descriptor.stable_id == migration_opaque_id("document", "23")
    assert item.descriptor.kind == "attachment"
    assert item.descriptor.content_sha256 == digest
    assert item.descriptor.metadata["title"] == "Private report"
    assert str(document) not in repr(item.descriptor.metadata)
    assert shadow.resolve_document(23) == (
        f"corefs://object/{migration_opaque_id('document', '23')}"
    )


def test_authenticated_document_source_streams_exact_snapshot_without_host_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = b"portable authenticated document"
    digest = hashlib.sha256(data).hexdigest()
    stable_id = migration_opaque_id("document", "23")
    session = SimpleNamespace(
        corefs_session=SimpleNamespace(
            resolve_validation_role_v1=lambda _keys, _role: {
                "generation": 4,
                "catalogHash": "a" * 64,
                "stableId": "gallery-id",
            }
        ),
        corefs_keys=object(),
        content_authority={
            "version": 1,
            "state": "cutover_complete",
            "families": ["assets", "documents", "knowledge"],
            "generation": 4,
            "catalogHash": "a" * 64,
        },
    )
    monkeypatch.setattr(
        asset_authority,
        "_walk_all",
        lambda **_kwargs: [
            {"kind": "directory", "stableId": "gallery-id", "path": "/Gallery"},
            {
                "kind": "file",
                "stableId": stable_id,
                "path": "/Gallery/report.pdf",
            },
        ],
    )
    monkeypatch.setattr(
        asset_authority.logical,
        "stat_v1",
        lambda **_kwargs: _logical_wire(
            {
                "stableId": stable_id,
                "path": "/Gallery/report.pdf",
                "objectKind": "attachment",
                "size": len(data),
                "contentHash": digest,
                "contentType": "application/pdf",
            }
        ),
    )

    def read_chunk(**kwargs: object) -> bytes:
        offset = int(kwargs["offset"])
        maximum = int(kwargs["max_bytes"])
        chunk = data[offset : offset + maximum]
        return _logical_wire(
            {
                "stableId": stable_id,
                "path": "/Gallery/report.pdf",
                "contentHash": digest,
                "offset": offset,
                "bytesBase64": b64encode(chunk).decode(),
            }
        )

    monkeypatch.setattr(asset_authority.logical, "read_chunk_v1", read_chunk)
    source = open_corefs_byte_source(
        session=session,
        object_uri=f"corefs://object/{stable_id}",
        expected_kinds=frozenset({"attachment"}),
    )

    assert source.name == "report.pdf"
    assert source.read_all(max_bytes=1024) == data
    assert "private" not in repr(source)


def test_authenticated_document_source_rejects_reordered_or_truncated_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = b"abcdef"
    digest = hashlib.sha256(data).hexdigest()
    stable_id = migration_opaque_id("document", "24")
    source = CoreFsByteSource(
        session=SimpleNamespace(corefs_session=object(), corefs_keys=object()),
        selection=AssetAuthoritySelection(1, "b" * 64),
        stable_id=stable_id,
        path="/Gallery/report.pdf",
        size=len(data),
        content_sha256=digest,
        content_type="application/pdf",
        object_kind="attachment",
    )
    monkeypatch.setattr(
        asset_authority.logical,
        "read_chunk_v1",
        lambda **kwargs: _logical_wire(
            {
                "stableId": stable_id,
                "path": "/Gallery/report.pdf",
                "contentHash": digest,
                "offset": int(kwargs["offset"]) + 1,
                "bytesBase64": b64encode(data).decode(),
            },
            generation=1,
        ),
    )
    with pytest.raises(CoreFsSourceError, match="changed identity"):
        source.read_all(max_bytes=1024)

    monkeypatch.setattr(
        asset_authority.logical,
        "read_chunk_v1",
        lambda **kwargs: _logical_wire(
            {
                "stableId": stable_id,
                "path": "/Gallery/report.pdf",
                "contentHash": digest,
                "offset": int(kwargs["offset"]),
                "bytesBase64": "",
            },
            generation=1,
        ),
    )
    with pytest.raises(CoreFsSourceError, match="truncated"):
        source.read_all(max_bytes=1024)
