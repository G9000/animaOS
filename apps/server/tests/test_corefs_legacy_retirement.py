from __future__ import annotations

import pytest
from alembic.config import Config
from anima_server.db import session as soul_session
from sqlalchemy import create_engine, inspect


def test_untracked_populated_soul_is_rejected_without_schema_repair(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'unsupported.db'}")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE users (id INTEGER PRIMARY KEY)")
        connection.exec_driver_sql("INSERT INTO users VALUES (7)")
    with pytest.raises(ValueError, match=r"Unsupported.*Soul"):
        soul_session._run_alembic_upgrade(engine)
    assert inspect(engine).get_table_names() == ["users"]
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT id FROM users").scalar_one() == 7


def test_tracked_soul_does_not_repair_missing_tables(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'tracked.db'}")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE alembic_version (version_num TEXT)")
        connection.exec_driver_sql("CREATE TABLE users (id INTEGER PRIMARY KEY)")
    from alembic import command

    called = []
    monkeypatch.setattr(command, "upgrade", lambda *_args: called.append("upgrade"))
    with engine.connect() as connection:
        soul_session._run_alembic_upgrade_on(connection, Config(), True, True)
    assert called == ["upgrade"]
    assert inspect(engine).get_table_names() == ["alembic_version", "users"]


def test_legacy_password_unlock_is_rejected_before_opening_soul(tmp_path, monkeypatch):
    from anima_server.config import settings
    from anima_server.db import user_store

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    (tmp_path / "manifest.json").write_text("{}")
    monkeypatch.setattr(
        user_store, "get_user_session_factory", lambda *_: pytest.fail("opened legacy Soul")
    )
    with pytest.raises(user_store.InvalidCredentialsError, match="Unsupported"):
        user_store.authenticate_account("owner", "password")


def test_legacy_recovery_is_rejected_before_opening_soul(tmp_path, monkeypatch):
    from anima_server.config import settings
    from anima_server.db import user_store

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    (tmp_path / "manifest.json").write_text("{}")
    monkeypatch.setattr(
        user_store, "get_user_session_factory", lambda *_: pytest.fail("opened legacy Soul")
    )
    with pytest.raises(ValueError, match="Unsupported"):
        user_store.recover_account_with_phrase("legacy phrase", "new-password")


def test_legacy_credential_upgrade_is_rejected_before_credentials_are_read(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from anima_server.config import settings
    from anima_server.services.corefs.credentials import prepare_recovery_credential

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(
        "anima_server.services.corefs.credentials.verify_password",
        lambda *_: pytest.fail("read obsolete password verifier"),
        raising=False,
    )
    (tmp_path / "manifest.json").write_text('{"core_id":"core", "owner_id":"owner"}')
    with pytest.raises(ValueError, match="Unsupported"):
        prepare_recovery_credential(
            None,
            SimpleNamespace(id=7, password_hash="obsolete"),
            current_recovery_phrase="legacy",
            current_password="password",
        )


def test_soul_owner_contains_only_opaque_identity_and_timestamps():
    from anima_server import models

    owner = getattr(models, "SoulOwner", None)
    assert owner is not None, "Soul must expose opaque SoulOwner identity"
    assert set(owner.__table__.columns.keys()) == {
        "id",
        "core_owner_id",
        "created_at",
        "updated_at",
    }
    assert "users" not in models.Base.metadata.tables
    assert "user_keys" not in models.Base.metadata.tables


def test_current_account_password_recovery_remains_lossless():
    from conftest import managed_test_client

    with managed_test_client("soul-purity-current-") as client:
        registered = client.post(
            "/api/auth/register",
            json={
                "username": "opaque-owner",
                "password": "password-one",
                "name": "Private Name",
            },
        )
        assert registered.status_code == 201, registered.text
        payload = registered.json()
        assert payload["username"] == "opaque-owner"
        headers = {"X-Anima-Unlock": payload["unlockToken"]}
        changed = client.post(
            "/api/auth/change-password",
            headers=headers,
            json={
                "oldPassword": "password-one",
                "newPassword": "password-two",
            },
        )
        assert changed.status_code == 200, changed.text
        login = client.post(
            "/api/auth/login",
            json={
                "username": "opaque-owner",
                "password": "password-two",
            },
        )
        assert login.status_code == 200, login.text
        recovered = client.post(
            "/api/auth/recover",
            json={
                "recoveryPhrase": payload["recoveryPhrase"],
                "newPassword": "password-three",
            },
        )
        assert recovered.status_code == 200, recovered.text
        assert recovered.json()["name"] == "Private Name"


def test_unavailable_runtime_provenance_preserves_original_evidence():
    from anima_server.models import AgentExperience

    experience = AgentExperience(
        user_id=7, task_intent="Keep the lesson", approach="Retained", source_run_id=765
    )
    metadata = getattr(experience, "provenance_metadata", None)
    assert metadata is not None, "Missing source evidence needs explicit durable metadata"
    assert metadata["provenanceState"] == "source_unavailable"
    assert metadata["historicalSourceIds"]["source_run_id"] == 765
    assert experience.source_run_uri is None
    assert "source_run_id" not in AgentExperience.__table__.columns


@pytest.mark.parametrize("soul_only", [False, True])
@pytest.mark.parametrize("vacuum_interrupted", [False, True])
def test_supported_forward_soul_evolution_preserves_missing_runtime_evidence(
    tmp_path, monkeypatch, soul_only, vacuum_interrupted
):
    import json

    from alembic import command
    from anima_server.config import settings
    from anima_server.db import dispose_cached_engines
    from anima_server.db.user_store import authenticate_account
    from anima_server.services.sessions import clear_sqlcipher_key, unlock_session_store
    from conftest import managed_test_client

    with managed_test_client("soul-forward-preservation-") as client:
        registered = client.post(
            "/api/auth/register",
            json={
                "username": "retained-owner",
                "password": "current-password",
                "name": "Retained Name",
            },
        )
        assert registered.status_code == 201, registered.text
        owner_id = registered.json()["id"]
        source_path = soul_session.get_user_database_path(owner_id)
        with soul_session.get_engine(
            soul_session.get_user_database_url(owner_id)
        ).connect() as current:
            slots = list(current.exec_driver_sql("SELECT * FROM soul_keyslots").mappings())
        old_path = tmp_path / "supported-soul.db"
        engine = soul_session.get_engine(f"sqlite:///{old_path.as_posix()}")
        cfg = Config(str(soul_session._ALEMBIC_INI))
        with engine.begin() as connection:
            cfg.attributes["connection"] = connection
            command.upgrade(cfg, "20260812_0001")
            assert (
                len(
                    connection.exec_driver_sql(
                        "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'trg_corefs_writing_source_%'"
                    ).all()
                )
                == 12
            )
            connection.exec_driver_sql(
                "INSERT INTO users (id,username,password_hash,display_name) VALUES (?,?,?,?)",
                (owner_id, "retained-owner", "obsolete-verifier", "Retained Name"),
            )
            connection.exec_driver_sql(
                "INSERT INTO agent_experiences (id,user_id,task_intent,approach,quality_score,source_run_id,tool_names_json,turn_count) VALUES (?,?,?,?,?,?,?,?)",
                (23, owner_id, "Keep this lesson", "Preserve exact evidence", 0.9, 765, "[]", 2),
            )
            for row in slots:
                columns = ",".join(row)
                parameters = ",".join("?" for _ in row)
                connection.exec_driver_sql(
                    f"INSERT INTO soul_keyslots ({columns}) VALUES ({parameters})",
                    tuple(row.values()),
                )
            if soul_only:
                connection.exec_driver_sql(
                    "INSERT INTO diary_entries (id,user_id,title,body,entry_date) VALUES (?,?,?,?,?)",
                    (
                        91,
                        owner_id,
                        "Archival authored evidence",
                        "Private original retained only in verified encrypted archive",
                        "2026-10-09",
                    ),
                )
        with engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
        dispose_cached_engines()
        if soul_only:
            from test_corefs_keyslots import _make_soul_only

            _make_soul_only(
                password="current-password", recovery_phrase=registered.json()["recoveryPhrase"]
            )
        unlock_session_store.clear()
        clear_sqlcipher_key()
        dispose_cached_engines()
        source_path.unlink()
        for suffix in ("-wal", "-shm"):
            source_path.with_name(source_path.name + suffix).unlink(missing_ok=True)
        old_path.replace(source_path)
        encrypted_original = source_path.read_bytes()
        assert encrypted_original[:16] != b"SQLite format 3\x00"
        assert b"Keep this lesson" not in encrypted_original
        if vacuum_interrupted:
            from anima_server.services.corefs import soul_store

            original_vacuum = soul_store._vacuum_database

            def interrupt_vacuum(_connection):
                raise RuntimeError("injected vacuum interruption")

            monkeypatch.setattr(soul_store, "_vacuum_database", interrupt_vacuum)
            with pytest.raises(soul_store.SoulSnapshotError, match="vacuum is incomplete"):
                authenticate_account("retained-owner", "current-password")
            assert unlock_session_store.get_active_sessions(owner_id) == ()
            with soul_session.get_engine(
                soul_session.get_user_database_url(owner_id)
            ).connect() as interrupted:
                assert (
                    interrupted.exec_driver_sql(
                        "SELECT vacuum_completed FROM soul_schema_archives"
                    ).scalar_one()
                    == 0
                )
            originals = tuple(
                settings.data_dir.with_name(settings.data_dir.name + ".schema-recovery").glob(
                    "*.db"
                )
            )
            assert originals and originals[0].read_bytes()[:16] != b"SQLite format 3\x00"
            clear_sqlcipher_key()
            dispose_cached_engines()
            monkeypatch.setattr(soul_store, "_vacuum_database", original_vacuum)
        response, deks, keys = authenticate_account("retained-owner", "current-password")
        assert deks
        assert response["id"] == owner_id
        assert response["profileAvailable"] is (not soul_only)
        assert (keys is None) is soul_only
        with soul_session.get_engine(
            soul_session.get_user_database_url(owner_id)
        ).connect() as connection:
            row = connection.exec_driver_sql("SELECT * FROM agent_experiences").mappings().one()
            assert row["task_intent"] == "Keep this lesson"
            assert row["approach"] == "Preserve exact evidence"
            evidence = json.loads(row["provenance_metadata"])
            assert evidence["provenanceState"] == "source_unavailable"
            assert evidence["historicalSourceIds"]["source_run_id"] == 765
            assert row["source_run_uri"] is None
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []
            assert (
                connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'trg_corefs_writing_source_%'"
                ).all()
                == []
            )
            assert connection.exec_driver_sql("SELECT COUNT(*) FROM soul_owners").scalar_one() == 1
            archive = (
                connection.exec_driver_sql("SELECT * FROM soul_schema_archives").mappings().one()
            )
            assert archive["vacuum_completed"] == 1
            assert connection.exec_driver_sql("PRAGMA freelist_count").scalar_one() == 0
            archive_path = settings.data_dir.with_name(
                settings.data_dir.name + ".schema-recovery"
            ) / (archive["id"] + ".db")
            assert archive_path.is_file()
            assert archive_path.read_bytes()[:16] != b"SQLite format 3\x00"
            counts = json.loads(archive["source_inventory"])["tableCounts"]
            assert counts["agent_experiences"] == 1
            assert counts["diary_entries"] == int(soul_only)
        with soul_session.get_engine(f"sqlite:///{archive_path.as_posix()}").connect() as original:
            assert (
                original.exec_driver_sql("SELECT task_intent FROM agent_experiences").scalar_one()
                == "Keep this lesson"
            )
            if soul_only:
                assert (
                    original.exec_driver_sql("SELECT body FROM diary_entries").scalar_one()
                    == "Private original retained only in verified encrypted archive"
                )
        current_engine = soul_session.get_engine(soul_session.get_user_database_url(owner_id))
        assert "users" not in inspect(current_engine).get_table_names()
        assert "memory_vectors" not in inspect(current_engine).get_table_names()
        from anima_server.models import Base

        assert set(inspect(current_engine).get_table_names()) - {"alembic_version"} == set(
            Base.metadata.tables
        )


def test_retired_pre_release_reader_modules_are_not_available():
    from importlib.util import find_spec

    for module in (
        "vault",
        "corefs.legacy_soul",
        "corefs.writing_source",
        "corefs.conversation_migration",
        "corefs.asset_migration",
    ):
        assert find_spec(f"anima_server.services.{module}") is None, module


def test_locked_encrypted_server_import_does_not_open_a_shared_database(tmp_path):
    import os
    import subprocess
    import sys

    environment = dict(
        os.environ,
        ANIMA_CORE_REQUIRE_ENCRYPTION="true",
        ANIMA_CORE_PASSPHRASE="",
        ANIMA_DATA_DIR=str(tmp_path / "core"),
        ANIMA_DATABASE_URL=f"sqlite:///{tmp_path / 'obsolete-shared.db'}",
    )
    completed = subprocess.run(
        [sys.executable, "-c", "import anima_server.db.session"],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert not (tmp_path / "obsolete-shared.db").exists()


def test_registration_interruption_before_content_keeps_authenticated_pending_roots(monkeypatch):
    from anima_server.db import dispose_cached_engines, user_store
    from anima_server.services.corefs.keyslots import unlock_manifest_key_hierarchy
    from anima_server.services.corefs.types import KeyslotStatus, WrappingPath
    from anima_server.services.sessions import clear_sqlcipher_key, unlock_session_store
    from conftest import managed_test_client

    with managed_test_client("registration-pending-roots-"):
        original = user_store.create_user

        def interrupt(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError("injected interruption before content")

        monkeypatch.setattr(user_store, "create_user", interrupt)
        with pytest.raises(RuntimeError, match="injected"):
            user_store.register_account(
                username="owner", password="current-password", display_name="Private Name"
            )
        unlock_session_store.clear()
        clear_sqlcipher_key()
        dispose_cached_engines()
        roots = unlock_manifest_key_hierarchy(
            credential="current-password",
            wrapping_path=WrappingPath.PASSWORD,
            generation=1,
            status=KeyslotStatus.PENDING,
        )
        assert roots.sqlcipher_key is not None
        assert roots.frks
        monkeypatch.setattr(user_store, "create_user", original)
        response, _, phrase, _ = user_store.register_account(
            username="owner", password="current-password", display_name="Private Name"
        )
        assert response["name"] == "Private Name"
        assert phrase
        unlock_session_store.clear()
        clear_sqlcipher_key()
        dispose_cached_engines()
        assert (
            user_store.authenticate_account("owner", "current-password")[0]["name"]
            == "Private Name"
        )


def test_obsolete_wrapper_never_allows_plaintext_soul_downgrade(tmp_path, monkeypatch):
    from anima_server.config import settings
    from anima_server.services.sessions import clear_sqlcipher_key

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "core_require_encryption", True)
    monkeypatch.setattr(settings, "core_passphrase", "")
    clear_sqlcipher_key()
    (tmp_path / "manifest.json").write_text('{"wrapped_sqlcipher_key":{"obsolete":true}}')
    with pytest.raises(RuntimeError, match=r"unlock|Unlock"):
        soul_session._make_engine(f"sqlite:///{tmp_path / 'must-stay-absent.db'}")
    assert not (tmp_path / "must-stay-absent.db").exists()


def test_transcript_sidecars_are_rejected_without_authenticated_core_authority(
    tmp_path, monkeypatch
):
    from anima_server.services.agent.transcript_search import search_transcripts

    monkeypatch.setattr(
        "anima_server.services.corefs.conversation_authority.active_conversation_authority_session",
        lambda _: None,
    )
    (tmp_path / "old.meta.json").write_text(
        '{"user_id":7,"thread_id":12,"date_start":"2026-10-09","keywords":["private"]}'
    )
    (tmp_path / "old.jsonl").write_text('{"role":"user","content":"private retired transcript"}\n')
    with pytest.raises(RuntimeError, match=r"authenticated|authority"):
        search_transcripts(query="private", user_id=7, dek=None, transcripts_dir=tmp_path)


def test_registration_interruption_after_soul_commit_reopens_pending_initial_roots(monkeypatch):
    from anima_server.db import dispose_cached_engines, user_store
    from anima_server.services.corefs import keyslots
    from anima_server.services.sessions import clear_sqlcipher_key, unlock_session_store
    from conftest import managed_test_client

    with managed_test_client("registration-pending-activation-"):
        original = keyslots.activate_initial_manifest_roots
        monkeypatch.setattr(
            keyslots,
            "activate_initial_manifest_roots",
            lambda: (_ for _ in ()).throw(RuntimeError("injected after Soul commit")),
        )
        with pytest.raises(RuntimeError, match="injected"):
            user_store.register_account(
                username="owner", password="current-password", display_name="Private Name"
            )
        monkeypatch.setattr(keyslots, "activate_initial_manifest_roots", original)
        unlock_session_store.clear()
        clear_sqlcipher_key()
        dispose_cached_engines()
        response, deks, keys = user_store.authenticate_account("owner", "current-password")
        assert response["name"] == "Private Name"
        assert deks and keys is not None


def test_unavailable_historical_message_id_cannot_join_recycled_runtime_id():
    from anima_server.models import Base, MemoryItem, MemoryItemEvidence, SoulOwner
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(SoulOwner(id=7, core_owner_id="opaque"))
        db.flush()
        db.add(MemoryItem(id=8, user_id=7, content="Retained lesson", category="fact"))
        db.flush()
        row = MemoryItemEvidence(
            user_id=7,
            memory_item_id=8,
            source_kind="conversation",
            evidence_text="Original evidence",
            runtime_message_id=1,
        )
        db.add(row)
        db.commit()
        assert row.runtime_message_id == 1
        assert row.provenance_metadata["provenanceState"] == "source_unavailable"
        assert (
            db.scalars(
                select(MemoryItemEvidence).where(MemoryItemEvidence.runtime_message_id == 1)
            ).all()
            == []
        )


def test_historical_runtime_message_id_cannot_use_canonical_numeric_collision():
    from anima_server.services.corefs.soul_provenance import (
        SoulSourceProof,
        preserve_source_reference,
    )

    proof = SoulSourceProof(
        user_id=7,
        profile=None,
        objects=(),
        folders=(),
        authored_ids={},
        threads={},
        messages={"12": "corefs://object/unrelated#message=unrelated"},
        owner_id="owner",
    )
    uri, evidence = preserve_source_reference(
        proof=proof, field="runtime_message_id", family="runtime-message", value=12
    )
    assert uri is None
    assert evidence["provenanceState"] == "source_unavailable"
    assert evidence["historicalSourceIds"]["runtime_message_id"] == 12


def test_interrupted_forward_schema_ddl_rolls_back_before_retry(tmp_path, monkeypatch):
    from alembic import command

    engine = create_engine(f"sqlite:///{tmp_path / 'schema-transaction.db'}")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE alembic_version (version_num TEXT)")

    def fail_after_ddl(cfg, *_args):
        cfg.attributes["connection"].exec_driver_sql("CREATE TABLE must_rollback (id INTEGER)")
        raise RuntimeError("injected after schema DDL")

    monkeypatch.setattr(command, "upgrade", fail_after_ddl)
    with pytest.raises(RuntimeError, match="injected"):
        soul_session._run_alembic_upgrade(engine)
    assert inspect(engine).get_table_names() == ["alembic_version"]


def test_initial_root_publication_rejects_stale_manifest_snapshot(monkeypatch):
    from uuid import uuid4

    from anima_server.services.core import update_core_manifest
    from anima_server.services.corefs import keyslots
    from conftest import managed_test_client

    with managed_test_client("registration-stale-snapshot-"):

        def delayed_publish(mutation):
            update_core_manifest(lambda value: value.update(owner_id=str(uuid4())))
            return update_core_manifest(mutation)

        monkeypatch.setattr(keyslots, "update_core_manifest", delayed_publish)
        with pytest.raises(ValueError, match=r"changed|stale"):
            keyslots.prepare_initial_manifest_roots(
                user_id=0, password="password", recovery_phrase="recovery", sqlcipher_key=b"k" * 32
            )


def test_concurrent_registration_has_one_authenticated_winner():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from conftest import managed_test_client

    with managed_test_client("registration-concurrency-") as client:
        start = Barrier(2)

        def register(index):
            start.wait(timeout=10)
            return client.post(
                "/api/auth/register",
                json={
                    "username": f"owner{index}",
                    "password": f"password{index}",
                    "name": f"Private {index}",
                },
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(register, [1, 2]))
        assert sorted(response.status_code for response in results) == [201, 403]
        winner = next(response.json() for response in results if response.status_code == 201)
        suffix = winner["username"][-1]
        reopened = client.post(
            "/api/auth/login",
            json={"username": winner["username"], "password": f"password{suffix}"},
        )
        assert reopened.status_code == 200, reopened.text
        assert reopened.json()["ownerId"] == winner["ownerId"]


def test_stale_numeric_directories_cannot_discover_additional_core_owners():
    from anima_server.config import settings
    from anima_server.db.user_store import list_user_ids
    from conftest import managed_test_client

    with managed_test_client("opaque-owner-discovery-") as client:
        response = client.post(
            "/api/auth/register",
            json={"username": "owner", "password": "password", "name": "Private Name"},
        )
        assert response.status_code == 201, response.text
        stale = settings.data_dir / "users" / "999"
        stale.mkdir(parents=True)
        assert list_user_ids() == [response.json()["id"]]
        assert stale.is_dir()


@pytest.mark.parametrize("profile_available", [True, False])
def test_greeting_birthday_reads_only_authenticated_account_profile(monkeypatch, profile_available):
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from anima_server.models import Base
    from anima_server.services.agent import proactive
    from sqlalchemy.orm import Session

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(proactive, "require_active_task_authority_session", lambda _: object())
    monkeypatch.setattr(proactive, "list_canonical_tasks", lambda **_: ())
    monkeypatch.setattr(proactive, "_resolve_held_thought", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "anima_server.services.corefs.conversation_authority.active_conversation_authority_session",
        lambda _: object(),
    )
    monkeypatch.setattr(
        "anima_server.services.corefs.conversation_authority.list_canonical_threads", lambda **_: ()
    )
    observed = []

    def account(owner):
        observed.append(owner)
        return (
            SimpleNamespace(birthday=datetime.now(UTC).strftime("2000-%m-%d"))
            if profile_available
            else None
        )

    monkeypatch.setattr(
        "anima_server.services.corefs.account_profile.read_active_account_profile", account
    )
    with Session(engine) as db:
        context = proactive.gather_greeting_context(db, user_id=7)
    assert observed == [7]
    assert context.is_birthday is profile_available
    assert context.days_until_birthday == (0 if profile_available else None)
