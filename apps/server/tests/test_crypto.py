from __future__ import annotations

from dataclasses import replace

import anima_server.services.crypto as crypto_module
import pytest
from anima_server.services.corefs.crypto import (
    soul_keyslot_aad,
    unwrap_keyslot_secret,
    wrap_keyslot_secret,
)
from anima_server.services.corefs.types import WrappingPath
from anima_server.services.crypto import (
    decrypt_text_with_dek,
    encrypt_text_with_dek,
    generate_domain_keys,
)
from cryptography.exceptions import InvalidTag


def _aad(domain="memories", owner_id="00000000-0000-4000-8000-000000000002"):
    return soul_keyslot_aad(
        core_id="00000000-0000-4000-8000-000000000001",
        owner_id=owner_id,
        domain=domain,
        key_version=1,
        credential_generation=1,
        wrapping_path=WrappingPath.PASSWORD,
    )


def _slot(password="current-password"):
    secret = generate_domain_keys(("memories",))["memories"]
    return secret, wrap_keyslot_secret(password, secret, _aad())


def test_current_soul_keyslot_roundtrip():
    secret, record = _slot()
    assert unwrap_keyslot_secret("current-password", record, _aad()) == secret


def test_current_soul_keyslot_rejects_wrong_password():
    _, record = _slot()
    with pytest.raises(InvalidTag):
        unwrap_keyslot_secret("wrong-password", record, _aad())


def test_encrypt_text_with_dek_roundtrip():
    secret = generate_domain_keys(("memories",))["memories"]
    assert (
        decrypt_text_with_dek(encrypt_text_with_dek("hello world", secret), secret) == "hello world"
    )


def test_current_soul_rewrap_preserves_existing_secret():
    secret, _ = _slot()
    record = wrap_keyslot_secret("new-password", secret, _aad())
    assert unwrap_keyslot_secret("new-password", record, _aad()) == secret


@pytest.mark.parametrize(
    "aad", [_aad(domain="tasks"), _aad(owner_id="00000000-0000-4000-8000-000000000003")]
)
def test_current_soul_keyslot_rejects_owner_or_domain_swap(aad):
    _, record = _slot()
    with pytest.raises(InvalidTag):
        unwrap_keyslot_secret("current-password", record, aad)


def test_current_keyslot_rejects_kdf_abuse_before_derivation(monkeypatch):
    _, record = _slot()
    malicious = replace(record, kdf_memory_cost_kib=2**31)
    monkeypatch.setattr(
        crypto_module,
        "derive_argon2id_key",
        lambda *_args, **_kwargs: pytest.fail("unbounded derivation"),
    )
    with pytest.raises(ValueError, match="unsupported wrapped-key profile"):
        unwrap_keyslot_secret("current-password", malicious, _aad())


def test_domain_keys_are_independent_and_have_current_secret_size():
    keys = generate_domain_keys(("memories", "identity"))
    assert set(keys) == {"memories", "identity"}
    assert all(len(value) == 32 for value in keys.values())
    assert keys["memories"] != keys["identity"]
