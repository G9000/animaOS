from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from anima_server.config import settings
from anima_server.models.runtime import RuntimeImageAsset
from sqlalchemy import func, select

pytest_plugins = ("conftest_runtime",)

PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
    b"\x90wS\xde"
)

GIF_BYTES = b"GIF89a" + (b"\x00" * 16)


def test_register_image_asset_stores_binary_under_user_media_path(
    runtime_db,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.images.store import register_image_asset

    monkeypatch.setattr(settings, "data_dir", tmp_path)

    result = register_image_asset(
        runtime_db,
        user_id=7,
        data=PNG_BYTES,
        mime_type="image/png",
        filename="../pixel.png",
        metadata_json={"origin": "chat"},
    )

    digest = hashlib.sha256(PNG_BYTES).hexdigest()
    expected_storage_path = f"users/7/media/images/{digest[:2]}/{digest}.png"

    assert result.created is True
    assert result.asset.id is not None
    assert result.asset.user_id == 7
    assert result.asset.filename == "pixel.png"
    assert result.asset.mime_type == "image/png"
    assert result.asset.storage_path == expected_storage_path
    assert result.asset.sha256 == digest
    assert result.asset.size_bytes == len(PNG_BYTES)
    assert result.asset.retention_state == "transient"
    assert result.asset.metadata_json == {"origin": "chat"}
    assert result.path == tmp_path / expected_storage_path
    assert result.path.read_bytes() == PNG_BYTES


def test_register_image_asset_reuses_existing_asset_and_file(
    runtime_db,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.images.store import register_image_asset

    monkeypatch.setattr(settings, "data_dir", tmp_path)

    first = register_image_asset(
        runtime_db,
        user_id=7,
        data=PNG_BYTES,
        mime_type="image/png",
        filename="first.png",
    )
    second = register_image_asset(
        runtime_db,
        user_id=7,
        data=PNG_BYTES,
        mime_type="image/png",
        filename="second.png",
    )

    assert second.created is False
    assert second.asset.id == first.asset.id
    assert second.asset.filename == "first.png"
    assert second.path == first.path
    assert runtime_db.scalar(select(func.count(RuntimeImageAsset.id))) == 1


def test_same_image_bytes_are_deduped_per_user_not_globally(
    runtime_db,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.images.store import register_image_asset

    monkeypatch.setattr(settings, "data_dir", tmp_path)

    first = register_image_asset(
        runtime_db,
        user_id=7,
        data=PNG_BYTES,
        mime_type="image/png",
        filename="pixel.png",
    )
    second = register_image_asset(
        runtime_db,
        user_id=8,
        data=PNG_BYTES,
        mime_type="image/png",
        filename="pixel.png",
    )

    assert first.asset.id != second.asset.id
    assert first.asset.sha256 == second.asset.sha256
    assert first.asset.storage_path.startswith("users/7/")
    assert second.asset.storage_path.startswith("users/8/")
    assert runtime_db.scalar(select(func.count(RuntimeImageAsset.id))) == 2


def test_register_image_asset_rejects_declared_mime_mismatch(
    runtime_db,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.images.store import ImageAssetValidationError, register_image_asset

    monkeypatch.setattr(settings, "data_dir", tmp_path)

    with pytest.raises(ImageAssetValidationError, match="does not match"):
        register_image_asset(
            runtime_db,
            user_id=7,
            data=GIF_BYTES,
            mime_type="image/png",
            filename="fake.png",
        )


def test_register_image_asset_rejects_unsupported_mime(
    runtime_db,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.images.store import ImageAssetValidationError, register_image_asset

    monkeypatch.setattr(settings, "data_dir", tmp_path)

    with pytest.raises(ImageAssetValidationError, match="Unsupported image type"):
        register_image_asset(
            runtime_db,
            user_id=7,
            data=b"<svg></svg>",
            mime_type="image/svg+xml",
            filename="unsafe.svg",
        )


def test_resolve_image_storage_path_rejects_absolute_and_cross_user_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.images.store import (
        ImageStoragePathError,
        resolve_image_storage_path,
    )

    monkeypatch.setattr(settings, "data_dir", tmp_path)

    with pytest.raises(ImageStoragePathError):
        resolve_image_storage_path(str(tmp_path / "users/7/media/images/x.png"), user_id=7)

    with pytest.raises(ImageStoragePathError):
        resolve_image_storage_path("users/8/media/images/aa/file.png", user_id=7)

    with pytest.raises(ImageStoragePathError):
        resolve_image_storage_path("users/7/media/images/aa/../../escape.png", user_id=7)


def test_delete_image_asset_file_if_safe_only_deletes_below_user_media_root(
    runtime_db,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.images.store import (
        ImageStoragePathError,
        delete_image_asset_file_if_safe,
        register_image_asset,
    )

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    result = register_image_asset(
        runtime_db,
        user_id=7,
        data=PNG_BYTES,
        mime_type="image/png",
        filename="pixel.png",
    )

    assert delete_image_asset_file_if_safe(result.asset) is True
    assert not result.path.exists()
    assert delete_image_asset_file_if_safe(result.asset) is False

    unsafe = RuntimeImageAsset(
        user_id=7,
        filename="bad.png",
        mime_type="image/png",
        storage_path="users/7/attachments/chat/bad.png",
        sha256="c" * 64,
        size_bytes=1,
    )

    with pytest.raises(ImageStoragePathError):
        delete_image_asset_file_if_safe(unsafe)
