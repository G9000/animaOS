from __future__ import annotations

import json

import anima_core
import pytest
from anima_server.config import settings
from anima_server.services.core import ensure_core_manifest, update_core_manifest
from anima_server.services.corefs.authority import (
    AuthorityState,
    AuthorityStateError,
    activate_content_authority,
    read_authority_record,
    reconcile_content_authority,
)
from anima_server.services.corefs.diary_migration import (
    build_inactive_diary_catalog,
    migration_opaque_id,
)
from corefs_writing_test_support import publish_catalog_native


def test_pre_release_manifest_is_rejected(tmp_path, monkeypatch) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"version": 2}), encoding="utf-8")
    monkeypatch.setattr(
        "anima_server.services.corefs.authority.get_manifest_path", lambda: manifest
    )

    with pytest.raises(AuthorityStateError, match="pre-release Core is not supported"):
        read_authority_record()


def test_greenfield_activation_is_content_preserving_and_idempotent(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "data_dir", tmp_path / ".anima")
    ensure_core_manifest()

    def mark_first_release(manifest: dict[str, object]) -> None:
        manifest["portable_core_release"] = 1

    update_core_manifest(mark_first_release)
    native = anima_core.CorefsSession(
        str(tmp_path / "core"),
        migration_opaque_id("test-core", "greenfield-authority"),
    )
    keys = anima_core.corefs_derive_subkeys(anima_core.corefs_generate_root_key(), 1)
    published = publish_catalog_native(
        build_inactive_diary_catalog(user_id=7, folders=(), entries=()),
        corefs_session=native,
        keys=keys,
    )
    before = native.validation_snapshot(keys)

    marker = activate_content_authority(
        corefs_session=native,
        keys=keys,
        generation=int(published["generation"]),
        catalog_hash=str(published["catalogHash"]),
    )
    after = native.validation_snapshot(keys)

    assert marker["state"] == "authoritative"
    assert marker["authorityImmutable"] is True
    assert int(after["generation"]) == int(before["generation"]) + 1
    assert (
        activate_content_authority(
            corefs_session=native,
            keys=keys,
            generation=int(published["generation"]),
            catalog_hash=str(published["catalogHash"]),
        )
        == marker
    )
    assert reconcile_content_authority(corefs_session=native, keys=keys) == marker
    assert read_authority_record().state is AuthorityState.AUTHORITATIVE
