from __future__ import annotations

import os
import stat

import pytest

from anima_server.services.core import ensure_core_manifest, get_manifest_path


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions are not enforced on Windows")
def test_manifest_is_written_with_owner_only_permissions(managed_tmp_path) -> None:
    from anima_server.config import settings

    original_data_dir = settings.data_dir
    try:
        settings.data_dir = managed_tmp_path / "anima-data"

        ensure_core_manifest()

        manifest_mode = stat.S_IMODE(get_manifest_path().stat().st_mode)
        assert manifest_mode == 0o600
    finally:
        settings.data_dir = original_data_dir
