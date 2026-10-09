from __future__ import annotations

from pathlib import Path

import pytest
from anima_server.config import settings
from anima_server.services.corefs.archive_transfer import (
    CoreArchivePayloadKind,
    export_core_archive_v2,
)
from anima_server.services.corefs.authority import read_authority_record
from anima_server.services.sessions import unlock_session_store
from conftest import managed_test_client


def test_current_core_verified_backup_prune_and_restore_safety(tmp_path: Path):
    with managed_test_client("current-core-maintenance-") as client:
        registered = client.post(
            "/api/auth/register",
            json={
                "username": "maintenance-owner",
                "password": "password-one",
                "name": "Private Name",
            },
        )
        assert registered.status_code == 201, registered.text
        account = registered.json()
        headers = {"X-Anima-Unlock": account["unlockToken"]}
        credentials = {
            "currentPassword": "password-one",
            "recoveryPhrase": account["recoveryPhrase"],
        }
        session = unlock_session_store.resolve(account["unlockToken"])
        assert session is not None
        inspection = client.post(
            "/api/corefs/maintenance/inventory", headers=headers, json=credentials
        )
        assert inspection.status_code == 200, inspection.text
        assert inspection.json()["retentionDays"] == 30
        assert inspection.json()["trashExpiresAutomatically"] is False

        backup = tmp_path / "current-core.anima"
        export_core_archive_v2(
            session=session,
            output_path=backup,
            passphrase="backup-passphrase",
            payload_kind=CoreArchivePayloadKind.FULL,
            soul_generation=read_authority_record().authoritative_generation,
        )
        orphan = settings.data_dir / "objects" / ("object-" + "e" * 32 + ".acore")
        orphan.write_bytes(b"unreferenced-encrypted-publication-fixture")
        body = {
            **credentials,
            "parameters": {"action": "prune"},
            "backupPath": str(backup),
            "backupPassphrase": "backup-passphrase",
        }
        prepared = client.post("/api/corefs/maintenance/prepare", headers=headers, json=body)
        assert prepared.status_code == 200, prepared.text
        reviewed = prepared.json()
        assert reviewed["backupVerified"] is True
        assert reviewed["inventory"]["candidateObjectCount"] >= 1
        execute = {
            **body,
            "confirmation": reviewed["confirmation"],
            "inventoryDigest": reviewed["inventory"]["inventoryDigest"],
            "observedAtMs": reviewed["observedAtMs"],
        }
        result = client.post("/api/corefs/maintenance/execute", headers=headers, json=execute)
        assert result.status_code == 200, result.text
        replay = client.post("/api/corefs/maintenance/execute", headers=headers, json=execute)
        assert replay.status_code == 409
        for _batch in range(4):
            if result.json()["inventory"]["operation"]["state"] in {"complete", "completed"}:
                break
            resume_body = {**body, "parameters": {"action": "resume"}}
            resumed_review = client.post(
                "/api/corefs/maintenance/prepare", headers=headers, json=resume_body
            )
            assert resumed_review.status_code == 200, resumed_review.text
            resume = resumed_review.json()
            result = client.post(
                "/api/corefs/maintenance/execute",
                headers=headers,
                json={
                    **resume_body,
                    "confirmation": resume["confirmation"],
                    "inventoryDigest": resume["inventory"]["inventoryDigest"],
                    "observedAtMs": resume["observedAtMs"],
                },
            )
            assert result.status_code == 200, result.text
        else:
            raise AssertionError("Single-orphan cleanup did not finish within four bounded resumes")
        assert not orphan.exists()
        assert backup.is_file()
        assert client.get("/api/auth/me", headers=headers).status_code == 200
        assert client.get("/health").status_code == 200


def test_maintenance_rejects_damaged_soul_recovery_wrapper_with_valid_frks():
    import json

    from anima_server.services.core import get_manifest_path, update_core_manifest
    from anima_server.services.corefs import rotation
    from anima_server.services.corefs.types import KeyslotStatus, WrappingPath

    with managed_test_client("soul-reopen-maintenance-") as client:
        registered = client.post(
            "/api/auth/register",
            json={
                "username": "soul-check",
                "password": "password-one",
                "name": "Soul check",
            },
        )
        assert registered.status_code == 201, registered.text
        account = registered.json()
        credentials = {
            "currentPassword": "password-one",
            "recoveryPhrase": account["recoveryPhrase"],
        }

        def damage_soul_recovery(value):
            slot = next(
                slot
                for slot in value["keyslots"]
                if slot["purpose"] == "soul"
                and slot["wrapping_path"] == "recovery"
                and slot["status"] == "active"
            )
            slot["wrapped"]["wrap_tag"] = "AAAAAAAAAAAAAAAAAAAAAA=="

        update_core_manifest(damage_soul_recovery)
        manifest = json.loads(get_manifest_path().read_text())
        # The old maintenance verifier saw matching FS roots despite the damaged Soul slot.
        versions = {int(manifest["frk_rotation"]["active_version"])}
        password = rotation._open_roots(
            manifest,
            credential="password-one",
            wrapping_path=WrappingPath.PASSWORD,
            statuses={KeyslotStatus.ACTIVE},
            versions=versions,
        )
        recovery = rotation._open_roots(
            manifest,
            credential=account["recoveryPhrase"],
            wrapping_path=WrappingPath.RECOVERY,
            statuses={KeyslotStatus.ACTIVE},
            versions=versions,
        )
        assert rotation._roots_match(password, recovery)
        result = client.post(
            "/api/corefs/maintenance/inventory",
            headers={"X-Anima-Unlock": account["unlockToken"]},
            json=credentials,
        )
        assert result.status_code == 409, result.text
        assert "full Core" in result.json()["details"]["message"]


def test_explicit_staged_soul_reopen_checks_domains_without_publishing_authority(tmp_path):
    import json

    from anima_server.services.core import get_core_dir, get_manifest_path
    from anima_server.services.corefs import gc
    from anima_server.services.corefs.archive_transfer import stage_core_archive_v2
    from anima_server.services.sessions import get_sqlcipher_key

    with managed_test_client("staged-soul-reopen-") as client:
        registered = client.post(
            "/api/auth/register",
            json={
                "username": "staged-soul",
                "password": "password-one",
                "name": "Staged Soul",
            },
        )
        assert registered.status_code == 201, registered.text
        account = registered.json()
        token = account["unlockToken"]
        session = unlock_session_store.resolve(token)
        assert session is not None
        current_root = get_core_dir()
        current_sql_key = get_sqlcipher_key()
        backup = tmp_path / "full-core.anima"
        export_core_archive_v2(
            session=session,
            output_path=backup,
            passphrase="archive-password",
            payload_kind=CoreArchivePayloadKind.FULL,
            soul_generation=read_authority_record().authoritative_generation,
        )
        staged = stage_core_archive_v2(
            backup, passphrase="archive-password", staging_path=tmp_path / "staged"
        )
        manifest_path = staged.staging_path / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        reopened = gc._verify_full_core_reopen(
            staged.staging_path,
            manifest=manifest,
            password="password-one",
            recovery_phrase=account["recoveryPhrase"],
        )
        assert reopened.soul_domains == session.deks
        assert get_core_dir() == current_root
        assert get_sqlcipher_key() == current_sql_key
        assert unlock_session_store.resolve(token) is session
        # Damage only the staged backup's Soul recovery wrapper; live authority stays valid.
        slot = next(
            slot
            for slot in manifest["keyslots"]
            if slot["purpose"] == "soul"
            and slot["wrapping_path"] == "recovery"
            and slot["status"] == "active"
        )
        slot["wrapped"]["wrap_tag"] = "AAAAAAAAAAAAAAAAAAAAAA=="
        import pytest

        with pytest.raises(gc.MaintenanceError, match="full Core"):
            gc._verify_full_core_reopen(
                staged.staging_path,
                manifest=manifest,
                password="password-one",
                recovery_phrase=account["recoveryPhrase"],
            )
        live_manifest = json.loads(get_manifest_path().read_text())
        assert (
            gc._verify_full_core_reopen(
                current_root,
                manifest=live_manifest,
                password="password-one",
                recovery_phrase=account["recoveryPhrase"],
            ).soul_domains
            == session.deks
        )
        assert unlock_session_store.resolve(token) is session


def test_full_reopen_rejects_only_damaged_soul_domain_recovery_wrapper():
    import json

    import pytest
    from anima_server.db.session import ensure_user_database
    from anima_server.models import SoulKeyslot
    from anima_server.services.core import get_core_dir, get_manifest_path
    from anima_server.services.corefs import gc
    from sqlalchemy import select

    with managed_test_client("soul-domain-reopen-") as client:
        registered = client.post(
            "/api/auth/register",
            json={
                "username": "domain-soul",
                "password": "password-one",
                "name": "Domain Soul",
            },
        )
        assert registered.status_code == 201, registered.text
        account = registered.json()
        with ensure_user_database(account["id"])() as db:
            row = db.scalar(select(SoulKeyslot).where(SoulKeyslot.wrapping_path == "recovery"))
            assert row is not None
            row.wrap_tag = "AAAAAAAAAAAAAAAAAAAAAA=="
            db.commit()
        manifest = json.loads(get_manifest_path().read_text())
        with pytest.raises(gc.MaintenanceError, match="full Core"):
            gc._verify_full_core_reopen(
                get_core_dir(),
                manifest=manifest,
                password="password-one",
                recovery_phrase=account["recoveryPhrase"],
            )


@pytest.mark.parametrize("damage", ["stale-head", "catalog", "object"])
def test_full_backup_checkpoint_proof_rejects_stale_or_corrupt_staged_core(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, damage: str
):
    import hashlib

    from anima_server.services.core import get_core_dir
    from anima_server.services.corefs import archive_transfer
    from anima_server.services.corefs.asset_mutations import upsert_canonical_binary_asset
    from anima_server.services.corefs.catalog_reader import stable_opaque_id
    from anima_server.services.sessions import get_sqlcipher_key

    with managed_test_client("staged-checkpoint-proof-") as client:
        registered = client.post(
            "/api/auth/register",
            json={"username": "backup-proof", "password": "password-one", "name": "Backup proof"},
        )
        assert registered.status_code == 201, registered.text
        account = registered.json()
        token = account["unlockToken"]
        session = unlock_session_store.resolve(token)
        assert session is not None
        current_root = get_core_dir()
        current_sql_key = get_sqlcipher_key()
        backup = tmp_path / "full-core.anima"
        export_core_archive_v2(
            session=session,
            output_path=backup,
            passphrase="archive-password",
            payload_kind=CoreArchivePayloadKind.FULL,
            soul_generation=read_authority_record().authoritative_generation,
        )
        original_digest = hashlib.sha256(backup.read_bytes()).digest()
        if damage == "stale-head":
            upsert_canonical_binary_asset(
                session=session,
                stable_id=stable_opaque_id("test-backup", "new-current-asset"),
                name="new-current-asset.bin",
                object_kind="attachment",
                content_type="application/octet-stream",
                data=b"new current data after backup",
            )
        else:
            original_stage = archive_transfer.stage_core_archive_v2

            def damage_after_authenticated_extraction(*args, **kwargs):
                staged = original_stage(*args, **kwargs)
                directory = staged.staging_path / (
                    "fs/catalogs" if damage == "catalog" else "objects"
                )
                path = next(directory.glob("*.acore"))
                payload = bytearray(path.read_bytes())
                payload[-1] ^= 1
                path.write_bytes(payload)
                return staged

            monkeypatch.setattr(
                archive_transfer, "stage_core_archive_v2", damage_after_authenticated_extraction
            )
        prepared = client.post(
            "/api/corefs/maintenance/prepare",
            headers={"X-Anima-Unlock": token},
            json={
                "currentPassword": "password-one",
                "recoveryPhrase": account["recoveryPhrase"],
                "parameters": {"action": "prune"},
                "backupPath": str(backup),
                "backupPassphrase": "archive-password",
            },
        )
        assert prepared.status_code == 409, prepared.text
        assert hashlib.sha256(backup.read_bytes()).digest() == original_digest
        assert get_core_dir() == current_root
        assert get_sqlcipher_key() == current_sql_key
        assert unlock_session_store.resolve(token) is session
        assert client.get("/api/auth/me", headers={"X-Anima-Unlock": token}).status_code == 200


def test_current_core_retires_old_frk_after_authenticated_prune_and_full_backup(tmp_path: Path):
    import json
    import time

    from anima_server.db.runtime import get_runtime_session_factory
    from anima_server.models.runtime import RuntimeMessage, RuntimeThread
    from anima_server.services.agent.persistence import (
        append_corefs_message_reference,
        ensure_runtime_thread_reference,
        load_thread_history,
        rebuild_current_thread_message_references,
    )
    from anima_server.services.core import get_core_dir, get_manifest_path
    from anima_server.services.corefs import gc
    from anima_server.services.corefs.asset_authority import open_corefs_byte_source
    from anima_server.services.corefs.asset_mutations import upsert_canonical_binary_asset
    from anima_server.services.corefs.catalog_reader import stable_opaque_id
    from anima_server.services.corefs.conversation_authority import get_canonical_thread
    from anima_server.services.corefs.conversation_mutations import (
        append_canonical_message,
        close_canonical_thread,
    )
    from sqlalchemy import delete, select

    with managed_test_client("current-core-frk-retirement-") as client:
        registered = client.post(
            "/api/auth/register",
            json={
                "username": "retirement-owner",
                "password": "password-one",
                "name": "Retirement owner",
            },
        )
        assert registered.status_code == 201, registered.text
        account = registered.json()
        old_token = account["unlockToken"]
        headers = {"X-Anima-Unlock": old_token}
        credentials = {
            "currentPassword": "password-one",
            "recoveryPhrase": account["recoveryPhrase"],
        }
        session = unlock_session_store.resolve(old_token)
        assert session is not None
        authored_id = stable_opaque_id("retirement-regression", "authored-original")
        authored_bytes = b"Authored original survives retirement of the old Filesystem Root Key."
        upsert_canonical_binary_asset(
            session=session,
            stable_id=authored_id,
            name="authored-original.bin",
            object_kind="attachment",
            content_type="application/octet-stream",
            data=authored_bytes,
        )
        created_thread = client.post("/api/threads", headers=headers)
        assert created_thread.status_code == 201, created_thread.text
        thread_id = created_thread.json()["threadId"]
        authored_history = ["Authored request before pruning.", "Authored reply before pruning."]
        display_metadata = {"source": "native-retirement-history"}
        with get_runtime_session_factory()() as runtime_db:
            thread = ensure_runtime_thread_reference(
                runtime_db, user_id=session.user_id, thread_id=thread_id
            )
            for role, content in zip(("user", "assistant"), authored_history, strict=True):
                message = append_canonical_message(
                    session=session,
                    thread_id=thread_id,
                    role=role,
                    content=content,
                    display_metadata=display_metadata if role == "assistant" else None,
                )
                append_corefs_message_reference(
                    runtime_db, thread=thread, message=message, run_id=None
                )
            runtime_db.commit()
        # This is a maintenance acceptance fixture, so settle its authored
        # conversation before taking a checkpoint. Otherwise the real five-minute
        # inactivity sweep can correctly publish a new HEAD during slow KDF work.
        closed_thread, changed = close_canonical_thread(session=session, thread_id=thread_id)
        assert changed and closed_thread is not None
        assert closed_thread.document.status == "closed"
        authored_thread = get_canonical_thread(session=session, thread_id=thread_id)
        assert authored_thread is not None
        authored_segments = tuple(record.segment.data for record in authored_thread.segments)
        owned_threads_before = client.get("/api/threads", headers=headers).json()
        visible_history_before = client.get(
            f"/api/threads/{thread_id}/messages", headers=headers
        ).json()
        rotated = client.post("/api/corefs/security/rotate", headers=headers, json=credentials)
        assert rotated.status_code == 200, rotated.text
        assert rotated.json()["activeFrkVersion"] == 2
        rotation_token = rotated.json()["unlockToken"]
        headers = {"X-Anima-Unlock": rotation_token}
        session = unlock_session_store.resolve(rotation_token)
        assert session is not None

        # Disposable fixture only: let native code authenticate and seal historical
        # first-observation age. Production API actions below use the actual clock.
        assert not (get_core_dir() / "fs" / "RETENTION_V1").exists()
        _, keys = gc._reauthenticate(
            session, password="password-one", recovery_phrase=account["recoveryPhrase"]
        )
        aged = session.corefs_session.maintenance_inventory_v1(
            keys,
            json.dumps(
                {"retentionDays": 30, "nowMs": int(time.time() * 1000) - 31 * 86400000, "pins": []}
            ),
        )
        assert aged["blockers"] == []
        assert 1 in aged["requiredFrkVersions"]

        def export_backup(name):
            path = tmp_path / name
            export_core_archive_v2(
                session=session,
                output_path=path,
                passphrase="backup-passphrase",
                payload_kind=CoreArchivePayloadKind.FULL,
                soul_generation=read_authority_record().authoritative_generation,
            )
            return path

        def execute_action(action, backup, **parameters):
            current = session.corefs_session.maintenance_inventory_v1(
                keys,
                json.dumps({"retentionDays": 30, "nowMs": int(time.time() * 1000), "pins": []}),
            )
            if action not in {"resume", "cancel"}:
                assert current["blockers"] == [], f"{action} live inventory: {current['blockers']}"
            body = {
                **credentials,
                "parameters": {"action": action, **parameters},
                "backupPath": str(backup),
                "backupPassphrase": "backup-passphrase",
            }
            prepared = client.post("/api/corefs/maintenance/prepare", headers=headers, json=body)
            assert prepared.status_code == 200, f"{action} prepare: {prepared.text}"
            review = prepared.json()
            assert review["backupVerified"] is True
            executed = client.post(
                "/api/corefs/maintenance/execute",
                headers=headers,
                json={
                    **body,
                    "confirmation": review["confirmation"],
                    "inventoryDigest": review["inventory"]["inventoryDigest"],
                    "observedAtMs": review["observedAtMs"],
                },
            )
            assert executed.status_code == 200, f"{action} execute: {executed.text}"
            return executed.json()

        checkpoint_backup = export_backup("rotation-checkpoint.anima")
        # Restore the actual rotated archive and independently authenticate its
        # ordinary current read and complete maintenance inventory before use.
        import anima_core
        from anima_server.services.corefs.archive_transfer import stage_core_archive_v2

        staged = stage_core_archive_v2(
            checkpoint_backup,
            passphrase="backup-passphrase",
            staging_path=tmp_path / "rotated-backup-proof",
        )
        staged_manifest = json.loads((staged.staging_path / "manifest.json").read_text())
        staged_session = anima_core.CorefsSession(
            str(staged.staging_path), staged_manifest["core_id"]
        )
        try:
            ordinary = staged_session.archive_inventory_v2(session.corefs_keys)
            assert ordinary["generation"] == aged["headGeneration"]
            assert ordinary["catalogHash"] == aged["headCatalogHash"]
            verified = staged_session.maintenance_inventory_v1(
                keys,
                json.dumps({"retentionDays": 30, "nowMs": int(time.time() * 1000), "pins": []}),
            )
            assert verified["blockers"] == [], verified
        finally:
            staged_session.close()
            del staged_session
        checkpoint = execute_action("refresh-checkpoint", checkpoint_backup)
        assert checkpoint["inventory"]["operation"]["state"] == "complete"
        # A current-key predecessor must exist before old generations can expire.
        upsert_canonical_binary_asset(
            session=session,
            stable_id=stable_opaque_id("retirement-regression", "current-key-predecessor"),
            name="current-key-predecessor.bin",
            object_kind="attachment",
            content_type="application/octet-stream",
            data=b"second authored current-key generation",
        )
        prune_backup = export_backup("before-prune.anima")
        pruned = execute_action("prune", prune_backup)
        for _ in range(8):
            if pruned["inventory"]["operation"]["state"] == "complete":
                break
            pruned = execute_action("resume", prune_backup)
        else:
            raise AssertionError(
                "Authenticated retirement history prune did not finish within eight batches"
            )
        assert pruned["inventory"]["blockers"] == [], pruned["inventory"]
        assert pruned["inventory"]["requiredFrkVersions"] == [2]

        # Lose only this disposable fixture's conversation projections after
        # pruning. Rebuild from the remaining authenticated native history;
        # no Runtime body or transcript is a source for canonical content.
        with get_runtime_session_factory()() as runtime_db:
            for model in (RuntimeMessage, RuntimeThread):
                runtime_db.execute(delete(model).where(model.user_id == session.user_id))
            runtime_db.commit()
            assert (
                runtime_db.scalar(
                    select(RuntimeThread.id).where(RuntimeThread.user_id == session.user_id)
                )
                is None
            )
            assert (
                runtime_db.scalar(
                    select(RuntimeMessage.id).where(RuntimeMessage.user_id == session.user_id)
                )
                is None
            )
            assert client.get("/api/threads", headers=headers).json() == owned_threads_before
            assert (
                client.get(f"/api/threads/{thread_id}/messages", headers=headers).json()
                == visible_history_before
            )
            rebuilt_thread = ensure_runtime_thread_reference(
                runtime_db, user_id=session.user_id, thread_id=thread_id
            )
            rebuild_current_thread_message_references(runtime_db, thread=rebuilt_thread)
            runtime_db.commit()
            assert [
                message.content
                for message in load_thread_history(runtime_db, thread_id, user_id=session.user_id)
            ] == authored_history
            stored_rows = runtime_db.execute(
                select(
                    RuntimeMessage.content_text,
                    RuntimeMessage.content_json,
                    RuntimeMessage.corefs_message_id,
                ).where(RuntimeMessage.user_id == session.user_id)
            ).all()
            assert len(stored_rows) == 2
            assert all(
                text is None and metadata is None and reference
                for text, metadata, reference in stored_rows
            )
        rebuilt_native = get_canonical_thread(session=session, thread_id=thread_id)
        assert rebuilt_native is not None
        assert rebuilt_native.document == authored_thread.document
        assert tuple(record.segment.data for record in rebuilt_native.segments) == authored_segments
        rebuilt_inventory = session.corefs_session.maintenance_inventory_v1(
            keys,
            json.dumps({"retentionDays": 30, "nowMs": int(time.time() * 1000), "pins": []}),
        )
        assert rebuilt_inventory["blockers"] == []
        assert rebuilt_inventory["requiredFrkVersions"] == [2]
        assert (rebuilt_inventory["headGeneration"], rebuilt_inventory["headCatalogHash"]) == (
            pruned["inventory"]["headGeneration"],
            pruned["inventory"]["headCatalogHash"],
        )
        assert (
            open_corefs_byte_source(
                session=session,
                object_uri=f"corefs://object/{authored_id}",
                expected_kinds=frozenset({"attachment"}),
            ).read_all(max_bytes=1024)
            == authored_bytes
        )

        retirement_backup = export_backup("active-generation-full-core.anima")
        retired = execute_action("retire-key", retirement_backup, retiringFrkVersion=1)
        assert retired["retiredFrkVersion"] == 1
        replacement_token = retired["unlockToken"]
        assert replacement_token not in {old_token, rotation_token}
        assert unlock_session_store.resolve(old_token) is None
        assert unlock_session_store.resolve(rotation_token) is None
        assert (
            client.get("/api/auth/me", headers={"X-Anima-Unlock": rotation_token}).status_code
            == 401
        )
        replacement = unlock_session_store.resolve(replacement_token)
        assert replacement is not None
        assert (
            client.get("/api/auth/me", headers={"X-Anima-Unlock": replacement_token}).status_code
            == 200
        )
        manifest = json.loads(get_manifest_path().read_text())
        assert manifest["frk_rotation"]["decrypt_only_versions"] == []
        assert all(
            slot.get("frk_version") != 1
            for slot in manifest["keyslots"]
            if slot["purpose"] == "filesystem-root"
        )
        reopened = gc._verify_full_core_reopen(
            get_core_dir(),
            manifest=manifest,
            password="password-one",
            recovery_phrase=account["recoveryPhrase"],
        )
        assert set(reopened.frks) == {2}
        assert reopened.soul_domains == replacement.deks
        source = open_corefs_byte_source(
            session=replacement,
            object_uri=f"corefs://object/{authored_id}",
            expected_kinds=frozenset({"attachment"}),
        )
        assert source.read_all(max_bytes=1024) == authored_bytes
        assert client.get("/health").status_code == 200
