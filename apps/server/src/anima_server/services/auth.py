from __future__ import annotations

from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.models import User, UserKey
from anima_server.services.crypto import WrappedDekRecord, create_wrapped_deks_for_domains, unwrap_dek, wrap_dek
from anima_server.services.data_crypto import ALL_DOMAINS

PASSWORD_HASHER = PasswordHasher(
    time_cost=3,
    memory_cost=64 * 1024,
    parallelism=4,
    hash_len=32,
    salt_len=16,
)


@dataclass(frozen=True, slots=True)
class PasswordVerification:
    valid: bool
    needs_rehash: bool = False


def normalize_username(username: str) -> str:
    return username.strip().lower()


def hash_password(password: str) -> str:
    return PASSWORD_HASHER.hash(password)


def verify_password(password: str, encoded_password: str) -> PasswordVerification:
    if not encoded_password.startswith("$argon2"):
        return PasswordVerification(valid=False)

    try:
        PASSWORD_HASHER.verify(encoded_password, password)
    except (InvalidHashError, VerificationError, VerifyMismatchError):
        return PasswordVerification(valid=False)
    return PasswordVerification(
        valid=True,
        needs_rehash=PASSWORD_HASHER.check_needs_rehash(encoded_password),
    )


def get_user_by_username(db: Session, username: str) -> User | None:
    return db.scalar(select(User).where(User.username == username))


def get_user_by_id(db: Session, user_id: int) -> User | None:
    return db.get(User, user_id)


def get_user_keys_by_user_id(db: Session, user_id: int) -> list[UserKey]:
    return list(db.scalars(
        select(UserKey).where(UserKey.user_id == user_id)
    ).all())


def build_user_key(user_id: int, domain: str, wrapped_dek: WrappedDekRecord) -> UserKey:
    return UserKey(
        user_id=user_id,
        domain=domain,
        kdf_salt=wrapped_dek.kdf_salt,
        kdf_time_cost=wrapped_dek.kdf_time_cost,
        kdf_memory_cost_kib=wrapped_dek.kdf_memory_cost_kib,
        kdf_parallelism=wrapped_dek.kdf_parallelism,
        kdf_key_length=wrapped_dek.kdf_key_length,
        wrap_iv=wrapped_dek.wrap_iv,
        wrap_tag=wrapped_dek.wrap_tag,
        wrapped_dek=wrapped_dek.wrapped_dek,
    )


def create_user(
    db: Session,
    username: str,
    password: str,
    display_name: str,
    agent_name: str = "Anima",
    user_directive: str = "",
    relationship: str = "companion",
    *,
    user_id: int | None = None,
) -> tuple[User, dict[str, bytes]]:
    from anima_server.models import AgentProfile, SelfModelBlock
    from anima_server.services.agent.system_prompt import render_origin_block, render_persona_seed

    if user_id is None:
        raise ValueError("User id is required to create wrapped DEKs")

    deks, wrapped_records = create_wrapped_deks_for_domains(password, ALL_DOMAINS, user_id)
    user = User(
        id=user_id,
        username=username,
        password_hash=hash_password(password),
        display_name=display_name,
    )
    db.add(user)
    db.flush()

    for domain, wrapped_dek in wrapped_records:
        db.add(build_user_key(user.id, domain, wrapped_dek))

    # Seed structured agent profile for fast lookup
    db.add(AgentProfile(
        user_id=user.id,
        agent_name=agent_name,
        creator_name=display_name,
        relationship=relationship,
    ))

    # Seed immutable origin block
    soul_content = render_origin_block(
        agent_name=agent_name, creator_name=display_name)
    db.add(SelfModelBlock(
        user_id=user.id,
        section="soul",
        content=soul_content,
        version=1,
        updated_by="system",
    ))

    # Seed persona from template (mutable — evolves through reflection)
    from anima_server.config import settings
    persona_content = render_persona_seed(settings.agent_persona_template)
    db.add(SelfModelBlock(
        user_id=user.id,
        section="persona",
        content=persona_content,
        version=1,
        updated_by="system",
    ))

    # Seed human block — the agent's living understanding of the user.
    # Starts with basic facts; the agent enriches this through conversation
    # via the update_human_memory tool.
    human_lines = [f"Name: {display_name}"]
    if relationship.strip():
        human_lines.append(f"Relationship: {relationship}")
    db.add(SelfModelBlock(
        user_id=user.id,
        section="human",
        content="\n".join(human_lines),
        version=1,
        updated_by="system",
    ))

    # Seed user directive if provided
    if user_directive.strip():
        db.add(SelfModelBlock(
            user_id=user.id,
            section="user_directive",
            content=user_directive.strip(),
            version=1,
            updated_by="system",
        ))

    db.commit()
    db.refresh(user)
    return user, deks


def authenticate_user(db: Session, username: str, password: str) -> tuple[User, dict[str, bytes]]:
    user = get_user_by_username(db, username)
    if user is None:
        raise ValueError("Invalid credentials")

    verification = verify_password(password, user.password_hash)
    if not verification.valid:
        raise ValueError("Invalid credentials")

    user_keys = get_user_keys_by_user_id(db, user.id)
    if not user_keys:
        raise RuntimeError(f"User {user.id} is missing key material")

    # Legacy migration: single key without domain → clone to all domains
    if len(user_keys) == 1 and (user_keys[0].domain == "memories" or not user_keys[0].domain):
        deks = _migrate_legacy_single_key(db, user.id, password, user_keys[0])
    else:
        deks = _unwrap_all_domain_keys(password, user_keys)

    if verification.needs_rehash:
        user.password_hash = hash_password(password)
        db.commit()
        db.refresh(user)

    return user, deks


def _migrate_legacy_single_key(
    db: Session,
    user_id: int,
    password: str,
    legacy_key: UserKey,
) -> dict[str, bytes]:
    """Migrate a legacy single-DEK user to per-domain DEKs.

    The legacy DEK is preserved for all domains (same key, new architecture).
    Future key rotation will produce independent per-domain keys.
    """
    legacy_domain = legacy_key.domain or "memories"
    legacy_dek = unwrap_dek(password, to_wrapped_dek_record(legacy_key), user_id, legacy_domain)

    deks: dict[str, bytes] = {}
    for domain in ALL_DOMAINS:
        if domain == (legacy_key.domain or "memories"):
            deks[domain] = legacy_dek
            continue
        # Wrap the same DEK under a new row for each domain
        wrapped = wrap_dek(password, legacy_dek, user_id, domain)
        db.add(build_user_key(user_id, domain, wrapped))
        deks[domain] = legacy_dek

    # Update legacy row to ensure it has explicit domain
    if not legacy_key.domain or legacy_key.domain == "memories":
        legacy_key.domain = "memories"

    db.commit()
    return deks


def _unwrap_all_domain_keys(
    password: str,
    user_keys: list[UserKey],
) -> dict[str, bytes]:
    """Unwrap all per-domain DEKs from stored UserKey records."""
    deks: dict[str, bytes] = {}
    for uk in user_keys:
        try:
            dek = unwrap_dek(password, to_wrapped_dek_record(uk), uk.user_id, uk.domain)
            deks[uk.domain] = dek
        except Exception as exc:  # noqa: BLE001
            raise ValueError("Invalid credentials") from exc
    return deks


def change_user_password(
    db: Session,
    user: User,
    old_password: str,
    new_password: str,
    current_deks: dict[str, bytes],
) -> None:
    verification = verify_password(old_password, user.password_hash)
    if not verification.valid:
        raise ValueError("Invalid credentials")

    user.password_hash = hash_password(new_password)

    user_keys = get_user_keys_by_user_id(db, user.id)
    if not user_keys:
        raise RuntimeError(f"User {user.id} is missing key material")

    # Re-wrap each domain DEK with the new password
    for uk in user_keys:
        dek = current_deks.get(uk.domain)
        if dek is None:
            continue
        wrapped = wrap_dek(new_password, dek, uk.user_id, uk.domain)
        uk.kdf_salt = wrapped.kdf_salt
        uk.kdf_time_cost = wrapped.kdf_time_cost
        uk.kdf_memory_cost_kib = wrapped.kdf_memory_cost_kib
        uk.kdf_parallelism = wrapped.kdf_parallelism
        uk.kdf_key_length = wrapped.kdf_key_length
        uk.wrap_iv = wrapped.wrap_iv
        uk.wrap_tag = wrapped.wrap_tag
        uk.wrapped_dek = wrapped.wrapped_dek

    db.commit()
    db.refresh(user)


def to_wrapped_dek_record(user_key: UserKey) -> WrappedDekRecord:
    return WrappedDekRecord(
        kdf_salt=user_key.kdf_salt,
        kdf_time_cost=user_key.kdf_time_cost,
        kdf_memory_cost_kib=user_key.kdf_memory_cost_kib,
        kdf_parallelism=user_key.kdf_parallelism,
        kdf_key_length=user_key.kdf_key_length,
        wrap_iv=user_key.wrap_iv,
        wrap_tag=user_key.wrap_tag,
        wrapped_dek=user_key.wrapped_dek,
    )


def serialize_user(user: User) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": user.id,
        "username": user.username,
        "name": user.display_name,
        "gender": user.gender,
        "age": user.age,
        "birthday": user.birthday,
    }
    if user.created_at is not None:
        payload["createdAt"] = user.created_at.isoformat()
    if user.updated_at is not None:
        payload["updatedAt"] = user.updated_at.isoformat()
    return payload
