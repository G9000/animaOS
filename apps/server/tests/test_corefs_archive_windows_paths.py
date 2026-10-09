from __future__ import annotations

import os
from pathlib import Path

import pytest
from anima_server.services.corefs.archive_transfer import (
    CoreArchiveTransferError,
    _native_filesystem_sources,
)


@pytest.mark.skipif(os.name != "nt", reason="Windows extended canonical paths")
def test_native_archive_allows_extended_alias_inside_same_core(tmp_path: Path):
    root = tmp_path / "core"
    catalogs = root / "fs" / "catalogs"
    catalogs.mkdir(parents=True)
    head = root / "fs" / "HEAD"
    catalog = catalogs / "catalog.acat"
    head.write_bytes(b"head")
    catalog.write_bytes(b"catalog")
    inventory = {
        "sources": [
            {
                "recordType": "catalog",
                "recordPath": "fs/HEAD",
                "sourcePath": "\\\\?\\" + str(head.resolve()),
            },
            {
                "recordType": "catalog",
                "recordPath": "fs/catalogs/catalog.acat",
                "sourcePath": "\\\\?\\" + str(catalog.resolve()),
            },
        ]
    }
    assert len(_native_filesystem_sources(inventory, core_root=root.resolve())) == 2


@pytest.mark.skipif(os.name != "nt", reason="Windows extended canonical paths")
def test_extended_alias_does_not_allow_outside_hardlink(tmp_path: Path):
    root = tmp_path / "core"
    catalogs = root / "fs" / "catalogs"
    catalogs.mkdir(parents=True)
    head = root / "fs" / "HEAD"
    catalog = catalogs / "catalog.acat"
    head.write_bytes(b"head")
    catalog.write_bytes(b"catalog")
    outside = tmp_path / "outside.acat"
    os.link(catalog, outside)
    inventory = {
        "sources": [
            {"recordType": "catalog", "recordPath": "fs/HEAD", "sourcePath": str(head)},
            {
                "recordType": "catalog",
                "recordPath": "fs/catalogs/catalog.acat",
                "sourcePath": "\\\\?\\" + str(outside.resolve()),
            },
        ]
    }
    with pytest.raises(CoreArchiveTransferError, match="escaped"):
        _native_filesystem_sources(inventory, core_root=root.resolve())
