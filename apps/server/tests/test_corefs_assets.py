from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from anima_server.services.corefs.asset_authority import CoreFsSourceError
from corefs_asset_test_support import (
    PortableBinaryAssetSource,
    build_portable_asset_shadow,
)
from corefs_catalog_test_support import (
    migration_opaque_id,
)


def test_gallery_shadow_keeps_original_bytes_and_opaque_references(tmp_path: Path) -> None:
    image = tmp_path / "private-user-image.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nportable-image")
    digest = hashlib.sha256(image.read_bytes()).hexdigest()

    shadow = build_portable_asset_shadow(
        user_id=7,
        binary_assets=[
            PortableBinaryAssetSource(
                namespace="image-asset",
                legacy_id="8",
                name="portrait.png",
                kind="gallery-asset",
                content_type="image/png",
                path=image,
                size=image.stat().st_size,
                sha256=digest,
                created_at="2026-08-13T00:00:00.000000+00:00",
                updated_at="2026-08-13T00:00:00.000000+00:00",
                metadata={"width": 1, "height": 1},
            )
        ],
    )

    assert len(shadow.folders) == 1
    gallery = shadow.folders[0]
    assert gallery.role == "core.gallery"
    assert (gallery.owner, gallery.agent_access, gallery.policy) == (
        "user",
        "write",
        "user-write",
    )
    asset = shadow.objects[0]
    expected_id = migration_opaque_id("image-asset", "8")
    assert asset.descriptor.stable_id == expected_id
    assert asset.descriptor.parent_id == gallery.stable_id
    assert asset.descriptor.body_source == "supplemental_path"
    assert asset.descriptor.source_key == str(image)
    assert asset.descriptor.content_sha256 == digest
    assert asset.body is None
    assert str(image) not in repr(asset.descriptor.metadata)
    assert shadow.resolve_reference({"assetId": 8}) == f"corefs://object/{expected_id}"


def test_gallery_reference_map_reconciles_message_attachment_links(tmp_path: Path) -> None:
    image = tmp_path / "linked.webp"
    image.write_bytes(b"RIFF\x00\x00\x00\x00WEBPportable")
    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    shadow = build_portable_asset_shadow(
        user_id=7,
        binary_assets=[
            PortableBinaryAssetSource(
                namespace="image-asset",
                legacy_id="9",
                name="linked.webp",
                kind="gallery-asset",
                content_type="image/webp",
                path=image,
                size=image.stat().st_size,
                sha256=digest,
                created_at="2026-08-13T00:00:00.000000+00:00",
                updated_at="2026-08-13T00:00:00.000000+00:00",
            )
        ],
        attachment_links={"message-attachment-9": "9"},
    )

    expected_id = migration_opaque_id("image-asset", "9")
    assert shadow.resolve_reference({"id": "message-attachment-9"}) == (
        f"corefs://object/{expected_id}"
    )
    assert shadow.resolve_reference({"sha256": digest}) == (f"corefs://object/{expected_id}")
    assert shadow.resolve_reference({"storagePath": str(image)}) is None


def test_asset_bytes_without_current_authority_never_read_runtime_or_disk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.images.store import resolve_image_byte_source

    monkeypatch.setattr(
        "anima_server.services.corefs.asset_authority.active_asset_authority_session",
        lambda _user_id: None,
    )
    asset = SimpleNamespace(id=4, storage_path="users/7/media/images/private.png")
    with pytest.raises(CoreFsSourceError, match="unavailable"):
        resolve_image_byte_source(asset, user_id=7)
