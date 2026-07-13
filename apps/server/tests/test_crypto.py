from __future__ import annotations

from dataclasses import replace

import anima_server.services.crypto as crypto_module
import pytest
from anima_server.services.crypto import (
    create_wrapped_dek,
    decrypt_text_with_dek,
    encrypt_text_with_dek,
    unwrap_dek,
    wrap_dek,
)


def test_create_wrapped_dek_roundtrip() -> None:
    dek, record = create_wrapped_dek("pw123456", 1, "memories")

    assert unwrap_dek("pw123456", record, 1, "memories") == dek


def test_unwrap_dek_rejects_wrong_passphrase() -> None:
    _, record = create_wrapped_dek("right-password", 1, "memories")

    with pytest.raises(Exception):  # noqa: B017
        unwrap_dek("wrong-password", record, 1, "memories")


def test_encrypt_text_with_dek_roundtrip() -> None:
    dek, _ = create_wrapped_dek("pw123456", 1, "memories")
    ciphertext = encrypt_text_with_dek("hello world", dek)

    assert decrypt_text_with_dek(ciphertext, dek) == "hello world"


def test_wrap_dek_preserves_existing_dek() -> None:
    dek, _ = create_wrapped_dek("old-password", 1, "memories")
    record = wrap_dek("new-password", dek, 1, "memories")

    assert unwrap_dek("new-password", record, 1, "memories") == dek


def test_unwrap_dek_rejects_domain_swap() -> None:
    dek, record = create_wrapped_dek("pw123456", 7, "memories")

    with pytest.raises(Exception):  # noqa: B017
        unwrap_dek("pw123456", record, 7, "tasks")

    assert unwrap_dek("pw123456", record, 7, "memories") == dek


def test_unwrap_dek_rejects_persisted_kdf_abuse_before_derivation(monkeypatch) -> None:
    _, record = create_wrapped_dek("pw123456", 7, "memories")
    malicious = replace(record, kdf_memory_cost_kib=2**31)
    monkeypatch.setattr(
        crypto_module,
        "derive_argon2id_key",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("unbounded Argon2 derivation was reached")
        ),
    )

    with pytest.raises(ValueError, match="unsupported wrapped-key profile"):
        unwrap_dek("pw123456", malicious, 7, "memories")
