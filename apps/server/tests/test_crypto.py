from __future__ import annotations

import pytest

from anima_server.services.crypto import (
    create_wrapped_dek,
    decrypt_text_with_dek,
    encrypt_text_with_dek,
    unwrap_dek,
    wrap_dek,
)


def test_create_wrapped_dek_roundtrip() -> None:
    dek, record = create_wrapped_dek("pw123456")

    assert unwrap_dek("pw123456", record) == dek


def test_unwrap_dek_rejects_wrong_passphrase() -> None:
    _, record = create_wrapped_dek("right-password")

    with pytest.raises(Exception):  # noqa: B017
        unwrap_dek("wrong-password", record)


def test_encrypt_text_with_dek_roundtrip() -> None:
    dek, _ = create_wrapped_dek("pw123456")
    ciphertext = encrypt_text_with_dek("hello world", dek)

    assert decrypt_text_with_dek(ciphertext, dek) == "hello world"


def test_wrap_dek_preserves_existing_dek() -> None:
    dek, _ = create_wrapped_dek("old-password")
    record = wrap_dek("new-password", dek)

    assert unwrap_dek("new-password", record) == dek
