from __future__ import annotations

import anima_core
import pytest

OBJECT_ID = "01J00000000000000000000000"
OTHER_OBJECT_ID = "01J00000000000000000000001"


def _aad_kwargs(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "core_id": "019f-core",
        "object_id": OBJECT_ID,
        "revision": 7,
        "kind": "knowledge-source",
        "envelope_version": 1,
        "object_key_epoch": 2,
        "frk_version": 3,
    }
    values.update(overrides)
    return values


def test_native_frk_subkeys_match_rust_vectors_and_are_separated() -> None:
    vectors = anima_core.corefs_fixed_subkey_test_vector()

    assert vectors["object_wrap"].hex() == (
        "2b1484b92cd77e9cae8871e6c615bcb218918a01a3102e14b88302859f045c7a"
    )
    assert vectors["catalog"].hex() == (
        "dab4b339534c78530468e3944584409e5f35a396fe716b8214d603437f75836e"
    )
    assert vectors["search"].hex() == (
        "994ab3afcf3dfcd8a3dd7eda0253e373eab665f253378ef6a0a5e4f9bcab9c4a"
    )
    assert len(set(vectors.values())) == 3


def test_native_root_key_is_opaque_and_credential_wrapping_roundtrips() -> None:
    root = anima_core.corefs_generate_root_key()
    wrapped = anima_core.corefs_wrap_root_key("credential", root, b"root-aad")
    reopened = anima_core.corefs_unwrap_root_key("credential", wrapped, b"root-aad")

    assert reopened.matches(root)
    assert not hasattr(root, "__bytes__")
    assert not hasattr(root, "key")
    assert not hasattr(anima_core.corefs_derive_subkeys(root, 1), "object_wrap_key")
    with pytest.raises(ValueError):
        anima_core.corefs_unwrap_root_key("credential", wrapped, b"wrong-aad")


def test_native_object_deks_are_independent() -> None:
    first = anima_core.corefs_generate_object_dek()
    second = anima_core.corefs_generate_object_dek()

    assert not first.matches(second)
    assert not hasattr(first, "__bytes__")


def test_native_object_dek_wrap_rejects_every_aad_mismatch() -> None:
    root = anima_core.corefs_generate_root_key()
    subkeys = anima_core.corefs_derive_subkeys(root, 3)
    dek = anima_core.corefs_generate_object_dek()
    wrapped = anima_core.corefs_wrap_object_dek(
        subkeys,
        dek,
        **_aad_kwargs(),
    )

    reopened = anima_core.corefs_unwrap_object_dek(
        subkeys,
        wrapped,
        **_aad_kwargs(),
    )
    assert reopened.matches(dek)
    assert wrapped.algorithm == "aes-256-gcm"
    assert wrapped.envelope_version == 1
    assert len(wrapped.nonce) == 12

    for field, changed in {
        "core_id": "other-core",
        "object_id": OTHER_OBJECT_ID,
        "revision": 8,
        "kind": "gallery-asset",
        "envelope_version": 2,
        "object_key_epoch": 3,
        "frk_version": 4,
    }.items():
        with pytest.raises(ValueError):
            anima_core.corefs_unwrap_object_dek(
                subkeys,
                wrapped,
                **_aad_kwargs(**{field: changed}),
            )


def test_native_object_crypto_rejects_unknown_kind_and_algorithm() -> None:
    root = anima_core.corefs_generate_root_key()
    subkeys = anima_core.corefs_derive_subkeys(root, 3)
    with pytest.raises(ValueError, match="unsupported object kind"):
        anima_core.corefs_wrap_object_dek(
            subkeys,
            anima_core.corefs_generate_object_dek(),
            **_aad_kwargs(kind="unknown"),
        )

    with pytest.raises(ValueError, match="unsupported object-key wrapping algorithm"):
        anima_core.CorefsWrappedObjectDek(
            "aes-128-gcm",
            1,
            bytes(12),
            bytes(48),
        )


def test_native_aad_bytes_match_rust_encoding() -> None:
    assert anima_core.corefs_object_key_aad(**_aad_kwargs()).hex() == (
        "616e696d612d636f726566732d6f626a6563742d6b65792d777261702d763100626173653d313538"
        "3a616e696d612d636f726566732d6f626a6563742d626173652d763100636f72652d69643d393a30"
        "3139662d636f7265006f626a6563742d69643d32363a30314a303030303030303030303030303030"
        "3030303030303030006b696e643d6b6e6f776c656467652d736f7572636500656e76656c6f70652d"
        "76657273696f6e3d31006f626a6563742d6b65792d65706f63683d32007265766973696f6e3d3700"
        "66726b2d76657273696f6e3d33"
    )


def test_native_payload_frame_aad_uses_base_fields_without_frk_version() -> None:
    base_kwargs = {key: value for key, value in _aad_kwargs().items() if key != "frk_version"}
    base = anima_core.corefs_object_base_aad(**base_kwargs)
    metadata = anima_core.corefs_metadata_frame_aad(
        **base_kwargs,
        chunking_version=1,
    )
    body = anima_core.corefs_body_frame_aad(
        **base_kwargs,
        metadata_frame_sha256=bytes([0xAB]) * 32,
        chunk_index=0,
        chunk_count=1,
        plaintext_offset=0,
        plaintext_length=12,
        total_body_length=12,
        final_chunk=True,
    )

    assert base.startswith(b"anima-corefs-object-base-v1\0")
    assert metadata.startswith(b"anima-corefs-metadata-frame-v1\0")
    assert body.startswith(b"anima-corefs-body-frame-v1\0")
    assert b"frk-version" not in base
    assert b"frk-version" not in metadata
    assert b"frk-version" not in body
