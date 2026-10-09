from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from anima_server.services.corefs.asset_authority import CoreFsSourceError
from anima_server.services.images.store import detect_image_mime, resolve_image_byte_source


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (b"\x89PNG\r\n\x1a\ncontent", "image/png"),
        (b"\xff\xd8\xffcontent", "image/jpeg"),
        (b"GIF89acontent", "image/gif"),
        (b"RIFF0000WEBPcontent", "image/webp"),
        (b"<svg>unsupported</svg>", None),
    ],
)
def test_image_mime_detection(data: bytes, expected: str | None) -> None:
    assert detect_image_mime(data) == expected


def test_image_source_rejects_physical_files_without_unlocked_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    physical = tmp_path / "private.png"
    physical.write_bytes(b"\x89PNG\r\n\x1a\nprivate")
    monkeypatch.setattr(
        "anima_server.services.corefs.asset_authority.active_asset_authority_session",
        lambda user_id: None,
    )
    asset = SimpleNamespace(id=1, storage_path=str(physical))
    with pytest.raises(CoreFsSourceError, match="unavailable"):
        resolve_image_byte_source(asset, user_id=7)
    assert physical.read_bytes().endswith(b"private")
