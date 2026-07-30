from __future__ import annotations

from types import SimpleNamespace

from anima_server.services.corefs import rotation
from anima_server.services.corefs.types import (
    KeyPurpose,
    WrappingPath,
)


def test_preparing_a_later_rotation_resets_reopen_verification(
    monkeypatch,
) -> None:
    class Root:
        def matches(self, _other) -> bool:
            return True

    class PendingSlot:
        def __init__(self, path: WrappingPath) -> None:
            self.path = path

        def to_dict(self) -> dict[str, str]:
            return {"wrapping_path": self.path.value, "status": "pending"}

    active_slots = [
        SimpleNamespace(
            purpose=KeyPurpose.FILESYSTEM_ROOT,
            frk_version=2,
            status=rotation.KeyslotStatus.ACTIVE,
            wrapping_path=path,
            scope="core",
            key_version=2,
            credential_generation=1,
            to_dict=lambda path=path: {
                "wrapping_path": path.value,
                "status": "active",
            },
        )
        for path in (WrappingPath.PASSWORD, WrappingPath.RECOVERY)
    ]
    manifest: dict[str, object] = {
        "core_id": "core-a",
        "owner_id": "7",
        "frk_rotation": {
            "active_version": 2,
            "pending_version": None,
            "decrypt_only_versions": [1],
            "phase": "idle",
            "object_key_epoch": 1,
            "password_reopen_verified": True,
            "recovery_reopen_verified": True,
        },
    }
    root = Root()

    monkeypatch.setattr(
        rotation,
        "_open_roots",
        lambda *_args, **_kwargs: {1: root, 2: root},
    )
    monkeypatch.setattr(rotation, "_roots_match", lambda *_args: True)
    monkeypatch.setattr(
        rotation.keyslots,
        "_manifest_slots",
        lambda _value: active_slots,
    )
    monkeypatch.setattr(
        rotation.keyslots,
        "_manifest_slot",
        lambda _credential, _root, **kwargs: PendingSlot(kwargs["wrapping_path"]),
    )
    monkeypatch.setattr(
        rotation.anima_core,
        "corefs_generate_root_key",
        lambda: root,
    )
    monkeypatch.setattr(
        rotation,
        "update_core_manifest",
        lambda mutation: mutation(manifest),
    )

    pending_version, pending_root = rotation._prepare_rotation(
        manifest=manifest,
        current_password="password",
        recovery_phrase="recovery words",
        source_generation=8,
        source_catalog_hash="catalog-hash",
    )

    state = rotation._rotation_state(manifest)
    assert pending_version == 3
    assert pending_root is root
    assert state["phase"] == "prepared"
    assert state["password_reopen_verified"] is False
    assert state["recovery_reopen_verified"] is False
