from __future__ import annotations

import pytest
from anima_server.config import settings
from anima_server.services.corefs.asset_authority import CoreFsSourceError, open_corefs_byte_source
from anima_server.services.corefs.asset_mutations import (
    trash_canonical_asset,
    upsert_canonical_binary_asset,
)
from anima_server.services.corefs.catalog_reader import stable_opaque_id
from anima_server.services.sessions import unlock_session_store
from conftest import managed_test_client


def test_image_deletion_is_recoverable_native_trash_not_physical_erasure() -> None:
    body = b"\x89PNG\r\n\x1a\nprivate-gallery-content"
    stable_id = stable_opaque_id("image-native-test", "photo")
    with managed_test_client("native-image-trash-") as client:
        response = client.post(
            "/api/auth/register",
            json={
                "username": "image-owner",
                "name": "Image owner",
                "password": "pw123456",
            },
        )
        assert response.status_code == 201, response.text
        session = unlock_session_store.resolve(response.json()["unlockToken"])
        assert session is not None
        upsert_canonical_binary_asset(
            session=session,
            stable_id=stable_id,
            name="photo.png",
            object_kind="gallery-asset",
            content_type="image/png",
            data=body,
        )
        encrypted_files = set((settings.data_dir / "objects").rglob("*.acore"))
        assert encrypted_files
        assert trash_canonical_asset(session=session, stable_id=stable_id) is True
        with pytest.raises(CoreFsSourceError, match="unavailable"):
            open_corefs_byte_source(session=session, object_uri=f"corefs://object/{stable_id}")
        assert all(path.is_file() for path in encrypted_files)
        assert trash_canonical_asset(session=session, stable_id=stable_id) is False
