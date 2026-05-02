from __future__ import annotations

import logging

from mnemonic import Mnemonic
from sqlalchemy.orm import Session

from anima_server.services.auth import (
    get_user_keys_by_user_id,
    hash_password,
    to_wrapped_dek_record,
)
from anima_server.services.crypto import unwrap_dek, wrap_dek
from anima_server.services.data_crypto import ALL_DOMAINS

logger = logging.getLogger(__name__)

RECOVERY_DOMAIN_PREFIX = "recovery:"


def generate_recovery_phrase() -> str:
    """Generate a 12-word BIP39 mnemonic recovery phrase."""
    m = Mnemonic("english")
    return m.generate(strength=128)  # 128 bits → 12 words


def wrap_keys_for_recovery(
    phrase: str,
    deks: dict[str, bytes],
    user_id: int,
) -> list[tuple[str, object]]:
    """Wrap all domain DEKs with the recovery phrase.

    Returns a list of (recovery_domain, WrappedDekRecord) pairs suitable
    for inserting as UserKey rows.
    """
    records = []
    for domain in ALL_DOMAINS:
        dek = deks.get(domain)
        if dek is None:
            continue
        recovery_domain = f"{RECOVERY_DOMAIN_PREFIX}{domain}"
        wrapped = wrap_dek(phrase, dek, user_id, recovery_domain)
        records.append((recovery_domain, wrapped))
    return records


def wrap_sqlcipher_key_for_recovery(
    phrase: str,
    raw_key: bytes,
    user_id: int,
) -> dict[str, object]:
    """Wrap the SQLCipher key with the recovery phrase for manifest storage."""
    wrapped = wrap_dek(phrase, raw_key, user_id, "recovery:sqlcipher")
    return {
        "user_id": user_id,
        "kdf_salt": wrapped.kdf_salt,
        "kdf_time_cost": wrapped.kdf_time_cost,
        "kdf_memory_cost_kib": wrapped.kdf_memory_cost_kib,
        "kdf_parallelism": wrapped.kdf_parallelism,
        "kdf_key_length": wrapped.kdf_key_length,
        "wrap_iv": wrapped.wrap_iv,
        "wrap_tag": wrapped.wrap_tag,
        "wrapped_key": wrapped.wrapped_dek,
    }


def recover_account(
    db: Session,
    phrase: str,
    new_password: str,
    user_id: int,
) -> dict[str, bytes]:
    """Recover an account using the recovery phrase and set a new password.

    1. Unwrap all recovery-domain DEKs using the phrase
    2. Re-wrap DEKs with the new password
    3. Update the password hash
    4. Return the plaintext DEKs for session use
    """
    from anima_server.models import User

    user = db.get(User, user_id)
    if user is None:
        raise ValueError("User not found")

    user_keys = get_user_keys_by_user_id(db, user_id)

    # Separate recovery keys from password keys
    recovery_keys = [uk for uk in user_keys if uk.domain.startswith(RECOVERY_DOMAIN_PREFIX)]
    password_keys = [uk for uk in user_keys if not uk.domain.startswith(RECOVERY_DOMAIN_PREFIX)]

    if not recovery_keys:
        raise ValueError("No recovery keys found for this account")

    # Unwrap DEKs using recovery phrase
    deks: dict[str, bytes] = {}
    for uk in recovery_keys:
        original_domain = uk.domain[len(RECOVERY_DOMAIN_PREFIX):]
        try:
            dek = unwrap_dek(phrase, to_wrapped_dek_record(uk), user_id, uk.domain)
        except Exception as exc:
            raise ValueError("Invalid recovery phrase") from exc
        deks[original_domain] = dek

    # Re-wrap DEKs with new password (update existing password-wrapped keys)
    for uk in password_keys:
        dek = deks.get(uk.domain)
        if dek is None:
            continue
        wrapped = wrap_dek(new_password, dek, user_id, uk.domain)
        uk.kdf_salt = wrapped.kdf_salt
        uk.kdf_time_cost = wrapped.kdf_time_cost
        uk.kdf_memory_cost_kib = wrapped.kdf_memory_cost_kib
        uk.kdf_parallelism = wrapped.kdf_parallelism
        uk.kdf_key_length = wrapped.kdf_key_length
        uk.wrap_iv = wrapped.wrap_iv
        uk.wrap_tag = wrapped.wrap_tag
        uk.wrapped_dek = wrapped.wrapped_dek

    # Update password hash
    user.password_hash = hash_password(new_password)
    db.commit()
    db.refresh(user)

    return deks
