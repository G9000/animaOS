from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import anima_core
from anima_server.config import settings
from anima_server.db.base import Base
from anima_server.models import AgentProfile, PresenceConfig, Task, User
from anima_server.services.core import (
    ensure_core_manifest,
    get_core_id,
    get_manifest_path,
    get_owner_id,
    set_owner_user_id,
)
from anima_server.services.corefs.diary_migration import (
    migration_opaque_id,
    read_prepared_writing_body,
    read_prepared_writing_snapshot,
)
from anima_server.services.corefs.formats import (
    decode_account_profile_document,
    decode_preferences_document,
    decode_task_document,
)
from anima_server.services.corefs.writing_source import prepare_writing_source_catalog
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


@contextmanager
def _legacy_account_source(root: Path) -> Iterator[tuple[SimpleNamespace, Session]]:
    previous_data_dir = settings.data_dir
    settings.data_dir = root / "core"
    engine = create_engine(f"sqlite+pysqlite:///{(root / 'soul.db').as_posix()}")
    db: Session | None = None
    try:
        ensure_core_manifest()
        set_owner_user_id(7)
        Base.metadata.create_all(engine)
        db = Session(engine, expire_on_commit=False)
        db.add(
            User(
                id=7,
                username="private-user",
                password_hash="$argon2id$must-not-migrate",
                display_name="Private Name",
                gender="nonbinary",
                age=31,
                birthday="1995-04-12",
            )
        )
        db.add(
            AgentProfile(
                user_id=7,
                agent_name="ANIMA",
                creator_name="Private Name",
                relationship="companion",
                agent_type="companion",
                setup_complete=True,
            )
        )
        db.add(
            PresenceConfig(
                user_id=7,
                enabled=True,
                task_nudges_enabled=False,
                custom_instruction="Use a calm tone",
                initiative_enabled=True,
                dream_sharing="ambient",
            )
        )
        db.add(
            Task(
                id=11,
                user_id=7,
                text="Pack the portable Core",
                priority=3,
                due_date="2026-08-20",
            )
        )
        db.commit()
        native = anima_core.CorefsSession(str(settings.data_dir), get_core_id())
        keys = anima_core.corefs_derive_subkeys(anima_core.corefs_generate_root_key(), 1)
        yield (
            SimpleNamespace(
                user_id=7,
                corefs_session=native,
                corefs_keys=keys,
            ),
            db,
        )
    finally:
        if db is not None:
            db.close()
        engine.dispose()
        settings.data_dir = previous_data_dir


def test_account_tasks_and_presence_prepare_as_encrypted_core_objects(
    managed_tmp_path: Path,
) -> None:
    with _legacy_account_source(managed_tmp_path) as (session, db):
        result = prepare_writing_source_catalog(session=session, db=db)
        snapshot = read_prepared_writing_snapshot(session=session)
        by_kind = {item.kind: item for item in snapshot.objects}

        account = decode_account_profile_document(
            read_prepared_writing_body(session=session, item=by_kind["account-profile"])
        )
        preferences = decode_preferences_document(
            read_prepared_writing_body(session=session, item=by_kind["preferences"])
        )
        task = decode_task_document(
            read_prepared_writing_body(session=session, item=by_kind["task"])
        )

        assert result.source_counts["accountProfiles"] == 1
        assert result.source_counts["preferences"] == 1
        assert result.source_counts["tasks"] == 1
        assert account.owner_id == get_owner_id()
        assert account.legacy_user_id == 7
        assert account.username == "private-user"
        assert account.display_name == "Private Name"
        assert account.setup_complete is True
        assert preferences.owner_id == account.owner_id
        assert preferences.values["presence"]["taskNudgesEnabled"] is False
        assert preferences.values["presence"]["customInstruction"] == "Use a calm tone"
        assert task.stable_id == migration_opaque_id("task", "11")
        assert task.text == "Pack the portable Core"
        assert task.priority == 3

        manifest = json.loads(get_manifest_path().read_text(encoding="utf-8"))
        assert "user_index" not in manifest
        assert manifest["owner_id"] == account.owner_id

        raw_core = b"".join(
            path.read_bytes() for path in settings.data_dir.rglob("*") if path.is_file()
        )
        assert b"private-user" not in raw_core
        assert b"Private Name" not in raw_core
        assert b"Pack the portable Core" not in raw_core
        assert b"must-not-migrate" not in raw_core


def test_account_profile_body_never_contains_legacy_password_hash(
    managed_tmp_path: Path,
) -> None:
    with _legacy_account_source(managed_tmp_path) as (session, db):
        prepare_writing_source_catalog(session=session, db=db)
        account = next(
            item
            for item in read_prepared_writing_snapshot(session=session).objects
            if item.kind == "account-profile"
        )
        body = read_prepared_writing_body(session=session, item=account)
        assert b"password" not in body.lower()
        assert b"argon2" not in body.lower()
