from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import inspect, select

from anima_server.config import settings
from anima_server.db.session import (
    SessionLocal,
    dispose_database,
    dispose_user_database,
    ensure_user_database,
    get_user_database_path,
    get_user_session_factory,
)
from anima_server.models import User
from anima_server.services.auth import (
    authenticate_user,
    create_user,
    normalize_username,
    serialize_user,
)
from anima_server.services.core import allocate_user_id, ensure_core_manifest, is_provisioned, set_next_user_id, set_owner_user_id
from anima_server.services.storage import get_user_data_dir
from anima_server.services.vault import export_database_snapshot, restore_database_snapshot

logger = logging.getLogger(__name__)

_bootstrap_lock = Lock()
_bootstrapped_roots: set[str] = set()


@dataclass(frozen=True, slots=True)
class AccountRecord:
    user_id: int
    username: str
    name: str


def ensure_per_user_databases_ready() -> None:
    ensure_core_manifest()
    if not settings.database_url.startswith("sqlite"):
        return

    data_root_key = str(settings.data_dir.resolve())
    with _bootstrap_lock:
        if data_root_key in _bootstrapped_roots:
            return

        _migrate_legacy_shared_database_locked()
        _bootstrapped_roots.add(data_root_key)


def list_user_ids() -> list[int]:
    ensure_per_user_databases_ready()
    users_root = settings.data_dir / "users"
    user_ids: list[int] = []
    if not users_root.is_dir():
        return user_ids
    for child in users_root.iterdir():
        if not child.is_dir():
            continue
        try:
            user_id = int(child.name)
        except ValueError:
            continue
        if get_user_database_path(user_id).is_file():
            user_ids.append(user_id)
    return sorted(user_ids)


def find_account_by_username(
    username: str,
    *,
    exclude_user_id: int | None = None,
) -> AccountRecord | None:
    normalized = normalize_username(username)
    if not normalized:
        return None

    for user_id in list_user_ids():
        if exclude_user_id is not None and user_id == exclude_user_id:
            continue

        with get_user_session_factory(user_id)() as db:
            user = db.scalar(select(User).where(User.username == normalized))
            if user is None:
                continue
            return AccountRecord(
                user_id=user.id,
                username=user.username,
                name=user.display_name,
            )

    return None


def username_exists(username: str, *, exclude_user_id: int | None = None) -> bool:
    return find_account_by_username(username, exclude_user_id=exclude_user_id) is not None


def register_account(
    *,
    username: str,
    password: str,
    display_name: str,
    agent_name: str = "Anima",
    user_directive: str = "",
    relationship: str = "companion",
) -> tuple[dict[str, object], bytes]:
    if is_provisioned():
        raise ValueError("Core is already provisioned")
    normalized = normalize_username(username)
    if not normalized:
        raise ValueError("Username is required")

    user_id = allocate_user_id()
    factory = ensure_user_database(user_id)
    with factory() as db:
        user, deks = create_user(
            db,
            username=normalized,
            password=password,
            display_name=display_name,
            agent_name=agent_name,
            user_directive=user_directive,
            relationship=relationship,
            user_id=user_id,
        )
    set_owner_user_id(user_id)
    return serialize_user(user), deks


def authenticate_account(username: str, password: str) -> tuple[dict[str, object], bytes]:
    normalized = normalize_username(username)
    if not normalized:
        raise ValueError("Invalid credentials")

    account = find_account_by_username(normalized)
    if account is None:
        raise ValueError("Invalid credentials")

    with get_user_session_factory(account.user_id)() as db:
        user, deks = authenticate_user(db, normalized, password)
        return serialize_user(user), deks


def delete_account_storage(user_id: int) -> None:
    dispose_user_database(user_id)
    shutil.rmtree(get_user_data_dir(user_id), ignore_errors=True)


def _migrate_legacy_shared_database_locked() -> None:
    shared_db_path = _legacy_shared_database_path()
    if shared_db_path is None or not shared_db_path.is_file():
        return

    try:
        with SessionLocal() as legacy_db:
            bind = legacy_db.get_bind()
            if bind is None or not inspect(bind).has_table("users"):
                return

            users = list(legacy_db.scalars(
                select(User).order_by(User.id)).all())
            if not users:
                return

            logger.info(
                "Migrating legacy shared database at %s into per-user databases.",
                shared_db_path,
            )
            max_user_id = 0
            for user in users:
                max_user_id = max(max_user_id, user.id)
                target_path = get_user_database_path(user.id)
                if target_path.is_file():
                    continue

                snapshot = export_database_snapshot(legacy_db, user_id=user.id)
                user_factory = ensure_user_database(user.id)
                with user_factory() as user_db:
                    restore_database_snapshot(user_db, snapshot)
    except SQLAlchemyError:
        logger.warning(
            "Skipping legacy shared database migration for %s because it could not be opened as a regular SQLite database.",
            shared_db_path,
        )
        return

    dispose_database(settings.database_url)
    backup_path = _legacy_backup_path(shared_db_path)
    try:
        shared_db_path.rename(backup_path)
    except PermissionError:
        logger.warning(
            "Legacy shared database %s is busy; leaving it in place after copying user data.",
            shared_db_path,
        )
    set_next_user_id(max_user_id + 1)


def _legacy_shared_database_path() -> Path | None:
    if not settings.database_url.startswith("sqlite"):
        return None

    database = make_legacy_database_path()
    if database is None:
        return None

    if settings.data_dir.resolve() != database.parent.resolve():
        return None
    return database


def make_legacy_database_path() -> Path | None:
    prefix = "sqlite:///"
    if not settings.database_url.startswith(prefix):
        return None
    return Path(settings.database_url[len(prefix):]).expanduser().resolve()


def _legacy_backup_path(shared_db_path: Path) -> Path:
    candidate = shared_db_path.with_name(
        f"{shared_db_path.stem}.legacy-shared{shared_db_path.suffix}")
    if not candidate.exists():
        return candidate

    counter = 1
    while True:
        candidate = shared_db_path.with_name(
            f"{shared_db_path.stem}.legacy-shared.{counter}{shared_db_path.suffix}"
        )
        if not candidate.exists():
            return candidate
        counter += 1
