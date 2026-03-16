from __future__ import annotations

from anima_server.services.crypto import decrypt_text_with_dek, encrypt_text_with_dek
from anima_server.services.sessions import get_active_dek


def _build_aad(table: str, user_id: int, field: str) -> bytes | None:
    """Build AAD context binding for field-level encryption.

    Returns None when no binding context is provided (backwards compat).
    """
    if not table or not field:
        return None
    return f"{table}:{user_id}:{field}".encode("utf-8")


def maybe_encrypt_for_user(
    user_id: int,
    plaintext: str,
    *,
    table: str = "",
    field: str = "",
) -> str:
    dek = get_active_dek(user_id)
    if dek is None:
        return plaintext
    aad = _build_aad(table, user_id, field)
    return encrypt_text_with_dek(plaintext, dek, aad=aad)


def maybe_decrypt_for_user(
    user_id: int,
    value: str,
    *,
    table: str = "",
    field: str = "",
) -> str:
    dek = get_active_dek(user_id)
    if dek is None:
        return value
    aad = _build_aad(table, user_id, field)
    return decrypt_text_with_dek(value, dek, aad=aad)


def encrypt_field(
    user_id: int,
    value: str | None,
    *,
    table: str = "",
    field: str = "",
) -> str | None:
    """Encrypt a text field if a DEK is active, otherwise return as-is."""
    if not value:
        return value
    return maybe_encrypt_for_user(user_id, value, table=table, field=field)


def decrypt_field(
    user_id: int,
    value: str | None,
    *,
    table: str = "",
    field: str = "",
) -> str:
    """Decrypt a text field if encrypted, return plaintext as-is. Returns '' for None."""
    if not value:
        return value or ""
    return maybe_decrypt_for_user(user_id, value, table=table, field=field)


def require_dek_for_user(user_id: int) -> bytes:
    dek = get_active_dek(user_id)
    if dek is None:
        raise ValueError("Session key is locked. Please sign in again.")
    return dek


# Short aliases for call-site readability
ef = encrypt_field
df = decrypt_field
