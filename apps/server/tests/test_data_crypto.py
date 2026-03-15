"""Tests for data_crypto: conditional encryption/decryption and field helpers."""

from __future__ import annotations

from unittest.mock import patch

from anima_server.services.data_crypto import (
    decrypt_field,
    encrypt_field,
    maybe_decrypt_for_user,
    maybe_encrypt_for_user,
    require_dek_for_user,
)

import pytest


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

_FAKE_DEK = b"0123456789abcdef0123456789abcdef"  # 32 bytes


def _mock_get_active_dek_none(user_id: int) -> None:
    return None


def _mock_get_active_dek_present(user_id: int) -> bytes:
    return _FAKE_DEK


# --------------------------------------------------------------------------- #
# maybe_encrypt_for_user / maybe_decrypt_for_user
# --------------------------------------------------------------------------- #


@patch(
    "anima_server.services.data_crypto.get_active_dek",
    side_effect=_mock_get_active_dek_none,
)
def test_maybe_encrypt_no_dek_returns_plaintext(mock_dek: object) -> None:
    result = maybe_encrypt_for_user(1, "hello")
    assert result == "hello"


@patch(
    "anima_server.services.data_crypto.get_active_dek",
    side_effect=_mock_get_active_dek_none,
)
def test_maybe_decrypt_no_dek_returns_value(mock_dek: object) -> None:
    result = maybe_decrypt_for_user(1, "ciphertext")
    assert result == "ciphertext"


@patch(
    "anima_server.services.data_crypto.get_active_dek",
    side_effect=_mock_get_active_dek_present,
)
def test_maybe_encrypt_decrypt_roundtrip(mock_dek: object) -> None:
    encrypted = maybe_encrypt_for_user(1, "secret data")
    assert encrypted != "secret data"

    decrypted = maybe_decrypt_for_user(1, encrypted)
    assert decrypted == "secret data"


# --------------------------------------------------------------------------- #
# encrypt_field / decrypt_field
# --------------------------------------------------------------------------- #


@patch(
    "anima_server.services.data_crypto.get_active_dek",
    side_effect=_mock_get_active_dek_none,
)
def test_encrypt_field_none_value(mock_dek: object) -> None:
    assert encrypt_field(1, None) is None


@patch(
    "anima_server.services.data_crypto.get_active_dek",
    side_effect=_mock_get_active_dek_none,
)
def test_encrypt_field_empty_value(mock_dek: object) -> None:
    assert encrypt_field(1, "") == ""


@patch(
    "anima_server.services.data_crypto.get_active_dek",
    side_effect=_mock_get_active_dek_none,
)
def test_encrypt_field_no_dek(mock_dek: object) -> None:
    assert encrypt_field(1, "hello") == "hello"


@patch(
    "anima_server.services.data_crypto.get_active_dek",
    side_effect=_mock_get_active_dek_present,
)
def test_encrypt_field_with_dek(mock_dek: object) -> None:
    result = encrypt_field(1, "secret")
    assert result is not None
    assert result != "secret"


@patch(
    "anima_server.services.data_crypto.get_active_dek",
    side_effect=_mock_get_active_dek_none,
)
def test_decrypt_field_none_value(mock_dek: object) -> None:
    assert decrypt_field(1, None) == ""


@patch(
    "anima_server.services.data_crypto.get_active_dek",
    side_effect=_mock_get_active_dek_none,
)
def test_decrypt_field_empty_value(mock_dek: object) -> None:
    assert decrypt_field(1, "") == ""


@patch(
    "anima_server.services.data_crypto.get_active_dek",
    side_effect=_mock_get_active_dek_present,
)
def test_decrypt_field_roundtrip(mock_dek: object) -> None:
    encrypted = encrypt_field(1, "my secret")
    assert encrypted is not None
    decrypted = decrypt_field(1, encrypted)
    assert decrypted == "my secret"


# --------------------------------------------------------------------------- #
# require_dek_for_user
# --------------------------------------------------------------------------- #


@patch(
    "anima_server.services.data_crypto.get_active_dek",
    side_effect=_mock_get_active_dek_present,
)
def test_require_dek_for_user_returns_dek(mock_dek: object) -> None:
    dek = require_dek_for_user(1)
    assert dek == _FAKE_DEK


@patch(
    "anima_server.services.data_crypto.get_active_dek",
    side_effect=_mock_get_active_dek_none,
)
def test_require_dek_for_user_raises_when_locked(mock_dek: object) -> None:
    with pytest.raises(ValueError, match="Session key is locked"):
        require_dek_for_user(1)
