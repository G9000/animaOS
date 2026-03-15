from __future__ import annotations

from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.models import User, UserKey
from anima_server.services.crypto import WrappedDekRecord, create_wrapped_dek, unwrap_dek, wrap_dek

PASSWORD_HASHER = PasswordHasher(
    time_cost=3,
    memory_cost=64 * 1024,
    parallelism=1,
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


def get_user_key_by_user_id(db: Session, user_id: int) -> UserKey | None:
    return db.scalar(select(UserKey).where(UserKey.user_id == user_id))


def build_user_key(user_id: int, wrapped_dek: WrappedDekRecord) -> UserKey:
    return UserKey(
        user_id=user_id,
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
    *,
    user_id: int | None = None,
) -> tuple[User, bytes]:
    from anima_server.models import SelfModelBlock
    from anima_server.services.agent.system_prompt import render_soul_biography

    dek, wrapped_dek = create_wrapped_dek(password)
    user = User(
        id=user_id,
        username=username,
        password_hash=hash_password(password),
        display_name=display_name,
    )
    db.add(user)
    db.flush()
    db.add(build_user_key(user.id, wrapped_dek))

    # Seed immutable soul biography
    soul_content = render_soul_biography(agent_name=agent_name)
    db.add(SelfModelBlock(
        user_id=user.id,
        section="soul",
        content=soul_content,
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
    return user, dek


def authenticate_user(db: Session, username: str, password: str) -> tuple[User, bytes]:
    user = get_user_by_username(db, username)
    if user is None:
        raise ValueError("Invalid credentials")

    verification = verify_password(password, user.password_hash)
    if not verification.valid:
        raise ValueError("Invalid credentials")

    user_key = get_user_key_by_user_id(db, user.id)
    if user_key is None:
        raise RuntimeError(f"User {user.id} is missing key material")

    try:
        dek = unwrap_dek(password, to_wrapped_dek_record(user_key))
    except Exception as exc:  # noqa: BLE001
        raise ValueError("Invalid credentials") from exc

    if verification.needs_rehash:
        user.password_hash = hash_password(password)
        db.commit()
        db.refresh(user)

    return user, dek


def change_user_password(
    db: Session,
    user: User,
    old_password: str,
    new_password: str,
    current_dek: bytes,
) -> None:
    verification = verify_password(old_password, user.password_hash)
    if not verification.valid:
        raise ValueError("Invalid credentials")

    wrapped_dek = wrap_dek(new_password, current_dek)
    user.password_hash = hash_password(new_password)

    user_key = get_user_key_by_user_id(db, user.id)
    if user_key is None:
        raise RuntimeError(f"User {user.id} is missing key material")

    user_key.kdf_salt = wrapped_dek.kdf_salt
    user_key.kdf_time_cost = wrapped_dek.kdf_time_cost
    user_key.kdf_memory_cost_kib = wrapped_dek.kdf_memory_cost_kib
    user_key.kdf_parallelism = wrapped_dek.kdf_parallelism
    user_key.kdf_key_length = wrapped_dek.kdf_key_length
    user_key.wrap_iv = wrapped_dek.wrap_iv
    user_key.wrap_tag = wrapped_dek.wrap_tag
    user_key.wrapped_dek = wrapped_dek.wrapped_dek

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
