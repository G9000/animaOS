from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os

from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.models import User

PASSWORD_SCHEME = "scrypt"
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32
SCRYPT_SALT_BYTES = 16


def normalize_username(username: str) -> str:
    return username.strip().lower()


def hash_password(password: str) -> str:
    salt = os.urandom(SCRYPT_SALT_BYTES)
    derived_key = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_DKLEN,
    )
    return "$".join(
        [
            PASSWORD_SCHEME,
            str(SCRYPT_N),
            str(SCRYPT_R),
            str(SCRYPT_P),
            base64.urlsafe_b64encode(salt).decode("ascii"),
            base64.urlsafe_b64encode(derived_key).decode("ascii"),
        ]
    )


def verify_password(password: str, encoded_password: str) -> bool:
    try:
        scheme, n, r, p, salt_b64, hash_b64 = encoded_password.split("$", 5)
        if scheme != PASSWORD_SCHEME:
            return False

        salt = base64.urlsafe_b64decode(salt_b64.encode("ascii"))
        expected_hash = base64.urlsafe_b64decode(hash_b64.encode("ascii"))
        candidate_hash = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected_hash),
        )
    except (ValueError, TypeError, binascii.Error):
        return False

    return hmac.compare_digest(candidate_hash, expected_hash)


def get_user_by_username(db: Session, username: str) -> User | None:
    return db.scalar(select(User).where(User.username == username))


def get_user_by_id(db: Session, user_id: int) -> User | None:
    return db.get(User, user_id)


def create_user(db: Session, username: str, password: str, display_name: str) -> User:
    user = User(
        username=username,
        password_hash=hash_password(password),
        display_name=display_name,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def serialize_user(user: User) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": user.id,
        "username": user.username,
        "name": user.display_name,
    }
    if user.created_at is not None:
        payload["createdAt"] = user.created_at.isoformat()
    if user.updated_at is not None:
        payload["updatedAt"] = user.updated_at.isoformat()
    return payload
