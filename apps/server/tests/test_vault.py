from __future__ import annotations

import base64
import json
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest
from anima_server.db.session import get_user_session_factory
from anima_server.models import (
    AgentExperience,
    AgentSkill,
    ExperienceClusterState,
    ForesightSignal,
    KGEntity,
    KGRelation,
    MemoryItem,
    MemoryItemEvidence,
    User,
    UserProfileField,
    UserProfileFieldEvidence,
)
from anima_server.services import vault as vault_module
from anima_server.services.data_crypto import df, ef
from anima_server.services.storage import get_user_data_dir
from anima_server.services.vault import decrypt_string, encrypt_string
from conftest import managed_test_client
from fastapi.testclient import TestClient
from sqlalchemy import delete


def _register_user(
    client: TestClient,
    *,
    username: str = "alice",
    password: str = "pw123456",
    name: str = "Alice",
) -> dict[str, object]:
    response = client.post(
        "/api/auth/register",
        json={"username": username, "password": password, "name": name},
    )
    assert response.status_code == 201
    return response.json()


def test_export_vault_requires_unlock_session() -> None:
    with managed_test_client("anima-vault-test-") as client:
        response = client.post("/api/vault/export", json={"passphrase": "vault-pass"})

        assert response.status_code == 401
        assert response.json() == {"error": "Session locked. Please sign in again."}


def test_export_and_import_vault_restores_auth_and_files() -> None:
    with managed_test_client("anima-vault-test-") as client:
        alice = _register_user(client)

        user_id = int(alice["id"])
        headers = {"x-anima-unlock": alice["unlockToken"]}
        user_dir = get_user_data_dir(user_id)
        user_dir.mkdir(parents=True, exist_ok=True)
        (user_dir / "memory" / "entry.md").parent.mkdir(parents=True, exist_ok=True)
        (user_dir / "memory" / "entry.md").write_text("hello from vault", encoding="utf-8")

        export_response = client.post(
            "/api/vault/export",
            headers=headers,
            json={"passphrase": "vault-pass"},
        )

        assert export_response.status_code == 200
        export_payload = export_response.json()
        envelope = json.loads(export_payload["vault"])
        assert envelope["version"] == 2
        assert "Alice" not in export_payload["vault"]

        with get_user_session_factory(user_id)() as db:
            user = db.get(User, user_id)
            assert user is not None
            user.display_name = "Changed"
            db.commit()

        (user_dir / "memory" / "entry.md").write_text("changed", encoding="utf-8")

        import_response = client.post(
            "/api/vault/import",
            headers=headers,
            json={"passphrase": "vault-pass", "vault": export_payload["vault"]},
        )

        assert import_response.status_code == 200
        import_payload = import_response.json()
        assert import_payload == {
            "status": "ok",
            "restoredUsers": 1,
            "restoredMemoryFiles": 1,
            "requiresReauth": True,
            "format": "vault_json",
        }

        with get_user_session_factory(user_id)() as db:
            users = db.query(User).all()
            assert [record.username for record in users] == ["alice"]
            assert users[0].display_name == "Alice"

        assert (user_dir / "memory" / "entry.md").read_text(encoding="utf-8") == "hello from vault"

        stale_session_response = client.get(
            "/api/auth/me",
            headers={"x-anima-unlock": alice["unlockToken"]},
        )
        assert stale_session_response.status_code == 401

        login_response = client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "pw123456"},
        )
        assert login_response.status_code == 200


def test_import_vault_rejects_wrong_passphrase() -> None:
    with managed_test_client("anima-vault-test-") as client:
        user = _register_user(client)
        token = str(user["unlockToken"])

        export_response = client.post(
            "/api/vault/export",
            headers={"x-anima-unlock": token},
            json={"passphrase": "vault-pass"},
        )
        assert export_response.status_code == 200

        import_response = client.post(
            "/api/vault/import",
            headers={"x-anima-unlock": token},
            json={"passphrase": "wrong-pass", "vault": export_response.json()["vault"]},
        )

        assert import_response.status_code == 400
        assert import_response.json() == {
            "error": "Failed to decrypt vault. Check the passphrase and payload.",
        }


def test_import_vault_preserves_original_password_hash() -> None:
    with managed_test_client("anima-vault-test-") as client:
        user = _register_user(client, username="vault-user", password="pw123456", name="Vault User")
        user_id = int(user["id"])
        token = str(user["unlockToken"])

        with get_user_session_factory(user_id)() as db:
            original_user = db.get(User, user_id)
            assert original_user is not None
            original_password_hash = original_user.password_hash

        export_response = client.post(
            "/api/vault/export",
            headers={"x-anima-unlock": token},
            json={"passphrase": "vault-pass"},
        )
        assert export_response.status_code == 200

        envelope = json.loads(export_response.json()["vault"])
        plaintext = decrypt_string(envelope, "vault-pass")
        payload = json.loads(plaintext)
        payload["database"]["users"][0]["password_hash"] = (
            "$argon2id$v=19$m=65536,t=3,p=4$invalid$invalid"
        )
        tampered_vault = json.dumps(
            encrypt_string(
                json.dumps(payload),
                "vault-pass",
                aad=base64.b64decode(envelope["aad_b64"]),
            )
        )

        import_response = client.post(
            "/api/vault/import",
            headers={"x-anima-unlock": token},
            json={"passphrase": "vault-pass", "vault": tampered_vault},
        )
        assert import_response.status_code == 200

        with get_user_session_factory(user_id)() as db:
            imported_user = db.get(User, user_id)
            assert imported_user is not None
            assert imported_user.password_hash == original_password_hash

        stale_session_response = client.get(
            "/api/auth/me",
            headers={"x-anima-unlock": token},
        )
        assert stale_session_response.status_code == 401

        login_response = client.post(
            "/api/auth/login",
            json={"username": "vault-user", "password": "pw123456"},
        )
        assert login_response.status_code == 200

        bad_login_response = client.post(
            "/api/auth/login",
            json={"username": "vault-user", "password": "tampered-password"},
        )
        assert bad_login_response.status_code == 401


def test_export_and_import_vault_restores_memory_item_evidence() -> None:
    with managed_test_client("anima-vault-test-") as client:
        user = _register_user(client, username="evidence-user", password="pw123456")
        user_id = int(user["id"])
        headers = {"x-anima-unlock": user["unlockToken"]}
        observed_at = datetime(2026, 5, 17, 9, 30, tzinfo=UTC)

        with get_user_session_factory(user_id)() as db:
            item = MemoryItem(
                user_id=user_id,
                content=ef(user_id, "Likes oolong tea.", table="memory_items", field="content"),
                category="preference",
                importance=4,
                source="test",
            )
            db.add(item)
            db.flush()
            evidence = MemoryItemEvidence(
                user_id=user_id,
                memory_item_id=item.id,
                source_kind="explicit_save",
                observed_at=observed_at,
                confidence=0.8,
                evidence_text=ef(
                    user_id,
                    "User: I like oolong tea.",
                    table="memory_item_evidence",
                    field="evidence_text",
                ),
                metadata_json={"source": "test"},
            )
            db.add(evidence)
            db.commit()
            item_id = item.id
            evidence_id = evidence.id

        export_response = client.post(
            "/api/vault/export",
            headers=headers,
            json={"passphrase": "vault-pass"},
        )
        assert export_response.status_code == 200

        envelope = json.loads(export_response.json()["vault"])
        payload = json.loads(decrypt_string(envelope, "vault-pass"))
        evidence_payload = payload["database"]["memoryItemEvidence"]
        assert evidence_payload[0]["evidence_text"] == "User: I like oolong tea."

        with get_user_session_factory(user_id)() as db:
            db.execute(delete(MemoryItemEvidence).where(MemoryItemEvidence.id == evidence_id))
            db.commit()

        import_response = client.post(
            "/api/vault/import",
            headers=headers,
            json={"passphrase": "vault-pass", "vault": export_response.json()["vault"]},
        )
        assert import_response.status_code == 200
        login_response = client.post(
            "/api/auth/login",
            json={"username": "evidence-user", "password": "pw123456"},
        )
        assert login_response.status_code == 200

        with get_user_session_factory(user_id)() as db:
            restored = db.get(MemoryItemEvidence, evidence_id)
            assert restored is not None
            assert restored.memory_item_id == item_id
            assert restored.observed_at == observed_at.replace(tzinfo=None)
            assert restored.metadata_json == {"source": "test"}
            assert (
                df(
                    user_id,
                    restored.evidence_text,
                    table="memory_item_evidence",
                    field="evidence_text",
                )
                == "User: I like oolong tea."
            )


def test_export_and_import_vault_restores_user_profile_fields() -> None:
    with managed_test_client("anima-vault-test-") as client:
        user = _register_user(client, username="profile-vault-user", password="pw123456")
        user_id = int(user["id"])
        headers = {"x-anima-unlock": user["unlockToken"]}
        observed_at = datetime(2026, 6, 30, 10, 0, tzinfo=UTC)

        from anima_server.services.agent.user_profile import upsert_profile_field

        with get_user_session_factory(user_id)() as db:
            field = upsert_profile_field(
                db,
                user_id=user_id,
                category="work",
                key="role",
                value="Systems designer",
                confidence=0.93,
                evidence_text="I work as a systems designer.",
                source_kind="profile_llm",
                observed_at=observed_at,
            )
            db.commit()
            field_id = field.id
            evidence_id = field.evidence[0].id

        export_response = client.post(
            "/api/vault/export",
            headers=headers,
            json={"passphrase": "vault-pass"},
        )
        assert export_response.status_code == 200

        envelope = json.loads(export_response.json()["vault"])
        payload = json.loads(decrypt_string(envelope, "vault-pass"))
        profile_payload = payload["database"]["userProfileFields"]
        evidence_payload = payload["database"]["userProfileFieldEvidence"]
        assert profile_payload[0]["value_text"] == "Systems designer"
        assert evidence_payload[0]["evidence_text"] == "I work as a systems designer."

        with get_user_session_factory(user_id)() as db:
            db.execute(
                delete(UserProfileFieldEvidence).where(
                    UserProfileFieldEvidence.id == evidence_id,
                )
            )
            db.execute(delete(UserProfileField).where(UserProfileField.id == field_id))
            db.commit()

        import_response = client.post(
            "/api/vault/import",
            headers=headers,
            json={"passphrase": "vault-pass", "vault": export_response.json()["vault"]},
        )
        assert import_response.status_code == 200
        login_response = client.post(
            "/api/auth/login",
            json={"username": "profile-vault-user", "password": "pw123456"},
        )
        assert login_response.status_code == 200

        with get_user_session_factory(user_id)() as db:
            restored = db.get(UserProfileField, field_id)
            restored_evidence = db.get(UserProfileFieldEvidence, evidence_id)
            assert restored is not None
            assert restored_evidence is not None
            assert restored.category == "work"
            assert restored.key == "role"
            assert restored.status == "active"
            assert restored.confidence == 0.93
            assert restored.first_observed_at == observed_at.replace(tzinfo=None)
            assert (
                df(
                    user_id,
                    restored.value_text,
                    table="user_profile_fields",
                    field="value_text",
                )
                == "Systems designer"
            )
            assert restored_evidence.profile_field_id == field_id
            assert (
                df(
                    user_id,
                    restored_evidence.evidence_text,
                    table="user_profile_field_evidence",
                    field="evidence_text",
                )
                == "I work as a systems designer."
            )


def test_export_and_import_vault_restores_foresight_and_procedural_memory() -> None:
    with managed_test_client("anima-vault-test-") as client:
        user = _register_user(client, username="sum-vault-user", password="pw123456")
        user_id = int(user["id"])
        headers = {"x-anima-unlock": user["unlockToken"]}
        observed_at = datetime(2026, 7, 3, 9, 0, tzinfo=UTC)

        from anima_server.services.agent.agent_experience import (
            AgentExperienceCandidate,
            AgentSkillCandidate,
            store_agent_experience,
            upsert_agent_skill,
        )
        from anima_server.services.agent.foresight import (
            ForesightCandidate,
            upsert_foresight_signal,
        )

        with get_user_session_factory(user_id)() as db:
            signal = upsert_foresight_signal(
                db,
                user_id=user_id,
                signal=ForesightCandidate(
                    content="User has a product review",
                    evidence="I have a product review next Tuesday.",
                    relative_text="next Tuesday",
                    start_date=date(2026, 7, 7),
                    end_date=date(2026, 7, 7),
                    duration_days=1,
                    confidence=0.91,
                ),
                source_thread_id=42,
                source_message_ids=[101, 102],
                observed_at=observed_at,
            )
            experience = store_agent_experience(
                db,
                user_id=user_id,
                candidate=AgentExperienceCandidate(
                    task_intent="Recover from a failed local search tool call",
                    approach="Narrow the query after a timeout, then retry once.",
                    quality_score=0.84,
                    source_thread_id=42,
                    source_run_id=7,
                    tool_names=("search_memory",),
                    turn_count=2,
                    embedding=[0.9, 0.1],
                    cluster_id="cluster_1_000",
                    created_at=observed_at,
                ),
            )
            state = ExperienceClusterState(
                user_id=user_id,
                state_json={
                    "next_index": 1,
                    "clusters": {
                        "cluster_1_000": {
                            "centroid": [0.9, 0.1],
                            "count": 1,
                            "experience_ids": [experience.id],
                            "last_activity": observed_at.isoformat(),
                        }
                    },
                },
                created_at=observed_at,
                updated_at=observed_at,
            )
            db.add(state)
            skill = upsert_agent_skill(
                db,
                user_id=user_id,
                skill=AgentSkillCandidate(
                    cluster_id="cluster_1_000",
                    name="Search Recovery",
                    description="Recover from local search tool timeouts.",
                    content="Narrow the query after a timeout and retry once.",
                    confidence=0.82,
                    experience_count=3,
                    embedding=[0.9, 0.1],
                ),
            )
            db.commit()
            signal_id = signal.id
            experience_id = experience.id
            state_id = state.id
            skill_id = skill.id

        export_response = client.post(
            "/api/vault/export",
            headers=headers,
            json={"passphrase": "vault-pass"},
        )
        assert export_response.status_code == 200

        envelope = json.loads(export_response.json()["vault"])
        payload = json.loads(decrypt_string(envelope, "vault-pass"))
        database = payload["database"]
        assert database["foresightSignals"][0]["content"] == "User has a product review"
        assert database["foresightSignals"][0]["evidence"] == (
            "I have a product review next Tuesday."
        )
        assert database["agentExperiences"][0]["task_intent"] == (
            "Recover from a failed local search tool call"
        )
        assert database["agentExperiences"][0]["approach"] == (
            "Narrow the query after a timeout, then retry once."
        )
        assert database["agentSkills"][0]["content"] == (
            "Narrow the query after a timeout and retry once."
        )
        assert database["experienceClusterState"][0]["state_json"]["next_index"] == 1

        with get_user_session_factory(user_id)() as db:
            db.execute(delete(AgentSkill).where(AgentSkill.id == skill_id))
            db.execute(delete(ExperienceClusterState).where(ExperienceClusterState.id == state_id))
            db.execute(delete(AgentExperience).where(AgentExperience.id == experience_id))
            db.execute(delete(ForesightSignal).where(ForesightSignal.id == signal_id))
            db.commit()

        import_response = client.post(
            "/api/vault/import",
            headers=headers,
            json={"passphrase": "vault-pass", "vault": export_response.json()["vault"]},
        )
        assert import_response.status_code == 200
        login_response = client.post(
            "/api/auth/login",
            json={"username": "sum-vault-user", "password": "pw123456"},
        )
        assert login_response.status_code == 200

        with get_user_session_factory(user_id)() as db:
            restored_signal = db.get(ForesightSignal, signal_id)
            restored_experience = db.get(AgentExperience, experience_id)
            restored_state = db.get(ExperienceClusterState, state_id)
            restored_skill = db.get(AgentSkill, skill_id)

        assert restored_signal is not None
        assert restored_signal.source_thread_id == 42
        assert restored_signal.source_message_ids_json == [101, 102]
        assert restored_signal.start_date == date(2026, 7, 7)
        assert (
            df(user_id, restored_signal.content, table="foresight_signals", field="content")
            == "User has a product review"
        )
        assert restored_experience is not None
        assert restored_experience.tool_names_json == ["search_memory"]
        assert restored_experience.cluster_id == "cluster_1_000"
        assert (
            df(
                user_id,
                restored_experience.task_intent,
                table="agent_experiences",
                field="task_intent",
            )
            == "Recover from a failed local search tool call"
        )
        assert restored_state is not None
        assert restored_state.state_json["clusters"]["cluster_1_000"]["experience_ids"] == [
            experience_id
        ]
        assert restored_skill is not None
        assert restored_skill.cluster_id == "cluster_1_000"
        assert (
            df(user_id, restored_skill.name, table="agent_skills", field="name")
            == "Search Recovery"
        )


def test_restore_database_snapshot_defers_profile_links_and_drops_missing_claim_fks() -> None:
    from anima_server.models import Base
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record):  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    observed_at = "2026-06-30T10:00:00+00:00"
    snapshot = {
        "users": [
            {
                "id": 1,
                "username": "profile-link-user",
                "password_hash": "hash",
                "display_name": "Profile Link User",
                "gender": None,
                "age": None,
                "birthday": None,
                "created_at": observed_at,
                "updated_at": observed_at,
            }
        ],
        "userKeys": [
            {
                "id": 1,
                "user_id": 1,
                "domain": "memories",
                "kdf_salt": "salt",
                "kdf_time_cost": 2,
                "kdf_memory_cost_kib": 64,
                "kdf_parallelism": 1,
                "kdf_key_length": 32,
                "wrap_iv": "iv",
                "wrap_tag": "tag",
                "wrapped_dek": "dek",
                "created_at": observed_at,
                "updated_at": observed_at,
            }
        ],
        "userProfileFields": [
            {
                "id": 1,
                "user_id": 1,
                "category": "work",
                "key": "role",
                "value_text": "Product manager",
                "confidence": 0.8,
                "status": "superseded",
                "source_kind": "claim_reconciliation",
                "source_memory_id": None,
                "source_evidence_id": None,
                "source_claim_evidence_id": 999,
                "superseded_by_id": 2,
                "first_observed_at": observed_at,
                "last_observed_at": observed_at,
                "created_at": observed_at,
                "updated_at": observed_at,
            },
            {
                "id": 2,
                "user_id": 1,
                "category": "work",
                "key": "role",
                "value_text": "Systems designer",
                "confidence": 1.0,
                "status": "active",
                "source_kind": "user_correction",
                "source_memory_id": None,
                "source_evidence_id": None,
                "source_claim_evidence_id": None,
                "superseded_by_id": None,
                "first_observed_at": observed_at,
                "last_observed_at": observed_at,
                "created_at": observed_at,
                "updated_at": observed_at,
            },
        ],
        "userProfileFieldEvidence": [
            {
                "id": 1,
                "profile_field_id": 1,
                "user_id": 1,
                "source_kind": "claim_reconciliation",
                "source_memory_id": None,
                "source_evidence_id": None,
                "source_claim_evidence_id": 999,
                "runtime_thread_id": None,
                "runtime_message_id": None,
                "evidence_text": "I work as a product manager.",
                "observed_at": observed_at,
                "created_at": observed_at,
            }
        ],
    }

    with Session(engine) as db:
        vault_module.restore_database_snapshot(db, snapshot)
        db.commit()

        superseded = db.get(UserProfileField, 1)
        correction = db.get(UserProfileField, 2)
        evidence = db.get(UserProfileFieldEvidence, 1)

    assert superseded is not None
    assert correction is not None
    assert evidence is not None
    assert superseded.superseded_by_id == correction.id
    assert superseded.source_claim_evidence_id is None
    assert evidence.source_claim_evidence_id is None


def test_restore_database_snapshot_defers_kg_relation_self_links() -> None:
    from anima_server.models import Base
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record):  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    observed_at = "2026-06-30T10:00:00+00:00"
    snapshot = {
        "users": [
            {
                "id": 1,
                "username": "kg-link-user",
                "password_hash": "hash",
                "display_name": "KG Link User",
                "gender": None,
                "age": None,
                "birthday": None,
                "created_at": observed_at,
                "updated_at": observed_at,
            }
        ],
        "userKeys": [
            {
                "id": 1,
                "user_id": 1,
                "domain": "memories",
                "kdf_salt": "salt",
                "kdf_time_cost": 2,
                "kdf_memory_cost_kib": 64,
                "kdf_parallelism": 1,
                "kdf_key_length": 32,
                "wrap_iv": "iv",
                "wrap_tag": "tag",
                "wrapped_dek": "dek",
                "created_at": observed_at,
                "updated_at": observed_at,
            }
        ],
        "kgEntities": [
            {
                "id": 1,
                "user_id": 1,
                "name": "Ari",
                "name_normalized": "ari",
                "entity_type": "person",
                "description": "",
                "mentions": 1,
            },
            {
                "id": 2,
                "user_id": 1,
                "name": "Temporal Memory",
                "name_normalized": "temporal memory",
                "entity_type": "project",
                "description": "",
                "mentions": 1,
            },
        ],
        "kgRelations": [
            {
                "id": 2,
                "user_id": 1,
                "source_id": 1,
                "destination_id": 2,
                "relation_type": "leads",
                "mentions": 1,
                "status": "active",
                "supersedes_relation_id": 1,
                "evolves_from_relation_id": 1,
            },
            {
                "id": 1,
                "user_id": 1,
                "source_id": 1,
                "destination_id": 2,
                "relation_type": "collaborates_on",
                "mentions": 1,
                "status": "superseded",
                "supersedes_relation_id": None,
                "evolves_from_relation_id": None,
            },
        ],
    }

    with Session(engine) as db:
        vault_module.restore_database_snapshot(db, snapshot)
        db.commit()

        replacement = db.get(KGRelation, 2)
        historical = db.get(KGRelation, 1)

    assert historical is not None
    assert replacement is not None
    assert replacement.supersedes_relation_id == historical.id
    assert replacement.evolves_from_relation_id == historical.id


def test_export_and_import_vault_restores_knowledge_graph() -> None:
    with managed_test_client("anima-vault-test-") as client:
        user = _register_user(client, username="kg-vault-user", password="pw123456")
        user_id = int(user["id"])
        headers = {"x-anima-unlock": user["unlockToken"]}
        observed_at = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
        valid_from = datetime(2026, 5, 20, 9, 0, tzinfo=UTC)

        with get_user_session_factory(user_id)() as db:
            item = MemoryItem(
                user_id=user_id,
                content=ef(
                    user_id,
                    "Ari collaborates on Temporal Memory.",
                    table="memory_items",
                    field="content",
                ),
                category="relationship",
                importance=4,
                source="test",
            )
            db.add(item)
            db.flush()
            evidence = MemoryItemEvidence(
                user_id=user_id,
                memory_item_id=item.id,
                source_kind="explicit_save",
                observed_at=observed_at,
                confidence=0.82,
                evidence_text=ef(
                    user_id,
                    "User: Ari collaborates on Temporal Memory.",
                    table="memory_item_evidence",
                    field="evidence_text",
                ),
            )
            db.add(evidence)
            db.flush()
            person = KGEntity(
                user_id=user_id,
                name="Ari",
                name_normalized="ari",
                entity_type="person",
                description="Project collaborator",
                mentions=3,
                embedding_json=[0.1, 0.2],
                embedding_checksum="checksum-person",
            )
            project = KGEntity(
                user_id=user_id,
                name="Temporal Memory",
                name_normalized="temporal memory",
                entity_type="project",
                description="Memory v2 work",
                mentions=2,
            )
            db.add_all([person, project])
            db.flush()
            relation = KGRelation(
                user_id=user_id,
                source_id=person.id,
                destination_id=project.id,
                relation_type="collaborates_on",
                mentions=2,
                source_memory_id=item.id,
                evidence_id=evidence.id,
                observed_at=observed_at,
                valid_from=valid_from,
                confidence=0.82,
            )
            db.add(relation)
            db.commit()
            person_id = person.id
            relation_id = relation.id
            evidence_id = evidence.id

        export_response = client.post(
            "/api/vault/export",
            headers=headers,
            json={"passphrase": "vault-pass"},
        )
        assert export_response.status_code == 200

        envelope = json.loads(export_response.json()["vault"])
        payload = json.loads(decrypt_string(envelope, "vault-pass"))
        assert payload["database"]["kgEntities"][0]["name"] == "Ari"
        assert payload["database"]["kgRelations"][0]["relation_type"] == "collaborates_on"
        assert payload["database"]["kgRelations"][0]["evidence_id"] == evidence_id
        assert (
            payload["database"]["kgRelations"][0]["observed_at"]
            == observed_at.replace(tzinfo=None).isoformat()
        )
        assert (
            payload["database"]["kgRelations"][0]["valid_from"]
            == valid_from.replace(tzinfo=None).isoformat()
        )
        assert payload["database"]["kgRelations"][0]["confidence"] == 0.82

        with get_user_session_factory(user_id)() as db:
            db.execute(delete(KGRelation))
            db.execute(delete(KGEntity))
            db.commit()

        import_response = client.post(
            "/api/vault/import",
            headers=headers,
            json={"passphrase": "vault-pass", "vault": export_response.json()["vault"]},
        )
        assert import_response.status_code == 200

        with get_user_session_factory(user_id)() as db:
            restored_entity = db.get(KGEntity, person_id)
            restored_relation = db.get(KGRelation, relation_id)
            assert restored_entity is not None
            assert restored_entity.name == "Ari"
            assert restored_entity.embedding_json == [0.1, 0.2]
            assert restored_relation is not None
            assert restored_relation.source_id == person_id
            assert restored_relation.relation_type == "collaborates_on"
            assert restored_relation.evidence_id == evidence_id
            assert restored_relation.observed_at == observed_at.replace(tzinfo=None)
            assert restored_relation.valid_from == valid_from.replace(tzinfo=None)
            assert restored_relation.confidence == pytest.approx(0.82)


def test_capsule_sections_include_memory_item_evidence() -> None:
    payload = {
        "version": 2,
        "createdAt": "2026-05-17T00:00:00+00:00",
        "scope": "memories",
        "database": {
            "users": [],
            "userKeys": [],
            "memoryItems": [],
            "memoryItemEvidence": [{"id": 1, "memory_item_id": 10}],
        },
        "manifest": {},
        "userFiles": {},
    }

    sections = vault_module._payload_to_capsule_sections(payload)
    restored = vault_module._capsule_sections_to_payload(sections)

    assert restored["database"]["memoryItemEvidence"] == [{"id": 1, "memory_item_id": 10}]


def test_capsule_sections_include_knowledge_graph_tables() -> None:
    payload = {
        "version": 2,
        "createdAt": "2026-05-17T00:00:00+00:00",
        "scope": "memories",
        "database": {
            "users": [],
            "userKeys": [],
            "kgEntities": [{"id": 1, "name": "Ari"}],
            "kgRelations": [{"id": 2, "relation_type": "collaborates_on"}],
        },
        "manifest": {},
        "userFiles": {},
    }

    sections = vault_module._payload_to_capsule_sections(payload)
    restored = vault_module._capsule_sections_to_payload(sections)

    assert "graph" in sections
    assert restored["database"]["kgEntities"] == [{"id": 1, "name": "Ari"}]
    assert restored["database"]["kgRelations"] == [
        {"id": 2, "relation_type": "collaborates_on"}
    ]


def test_export_and_import_anima_capsule_restores_auth_and_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fake_write_capsule(sections: dict[str, bytes], passphrase: str) -> bytes:
        assert passphrase == "vault-pass"
        encoded = {
            key: base64.b64encode(value).decode("ascii") for key, value in sections.items()
        }
        return json.dumps(encoded, sort_keys=True).encode("utf-8")

    def _fake_read_capsule(data: bytes, passphrase: str) -> dict[str, bytes]:
        assert passphrase == "vault-pass"
        encoded = json.loads(data.decode("utf-8"))
        return {
            key: base64.b64decode(value.encode("ascii")) for key, value in encoded.items()
        }

    monkeypatch.setattr(vault_module, "_write_capsule_bytes", _fake_write_capsule)
    monkeypatch.setattr(vault_module, "_read_capsule_sections", _fake_read_capsule)

    with managed_test_client("anima-vault-test-") as client:
        alice = _register_user(client)

        user_id = int(alice["id"])
        headers = {"x-anima-unlock": alice["unlockToken"]}
        user_dir = get_user_data_dir(user_id)
        user_dir.mkdir(parents=True, exist_ok=True)
        (user_dir / "memory" / "entry.md").parent.mkdir(parents=True, exist_ok=True)
        (user_dir / "memory" / "entry.md").write_text("hello from capsule", encoding="utf-8")

        export_response = client.post(
            "/api/vault/export",
            headers=headers,
            json={"passphrase": "vault-pass", "format": "anima_capsule"},
        )

        assert export_response.status_code == 200
        export_payload = export_response.json()
        assert export_payload["format"] == "anima_capsule"
        assert export_payload["filename"].endswith(".anima")

        with get_user_session_factory(user_id)() as db:
            user = db.get(User, user_id)
            assert user is not None
            user.display_name = "Changed"
            db.commit()

        (user_dir / "memory" / "entry.md").write_text("changed", encoding="utf-8")

        import_response = client.post(
            "/api/vault/import",
            headers=headers,
            json={
                "passphrase": "vault-pass",
                "vault": export_payload["vault"],
                "format": "anima_capsule",
            },
        )

        assert import_response.status_code == 200
        assert import_response.json() == {
            "status": "ok",
            "restoredUsers": 1,
            "restoredMemoryFiles": 1,
            "requiresReauth": True,
            "format": "anima_capsule",
        }

        with get_user_session_factory(user_id)() as db:
            users = db.query(User).all()
            assert [record.username for record in users] == ["alice"]
            assert users[0].display_name == "Alice"

        assert (user_dir / "memory" / "entry.md").read_text(encoding="utf-8") == "hello from capsule"


def test_load_capsule_bindings_returns_none_when_adapter_bindings_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(vault_module.anima_core_bindings, "rust_read_capsule", None)
    monkeypatch.setattr(vault_module.anima_core_bindings, "rust_write_capsule", None)

    read_capsule, write_capsule = vault_module._load_capsule_bindings()

    assert read_capsule is None
    assert write_capsule is None


def test_load_capsule_bindings_returns_available_adapter_bindings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _read_capsule(*_args, **_kwargs):
        return {}

    def _write_capsule(*_args, **_kwargs):
        return b"capsule"

    monkeypatch.setattr(vault_module.anima_core_bindings, "rust_read_capsule", _read_capsule)
    monkeypatch.setattr(vault_module.anima_core_bindings, "rust_write_capsule", _write_capsule)

    assert vault_module._load_capsule_bindings() == (_read_capsule, _write_capsule)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("timeCost", 11, "Vault KDF timeCost exceeds maximum allowed value of 10."),
        (
            "memoryCostKiB",
            2_097_153,
            "Vault KDF memoryCostKiB exceeds maximum allowed value of 2097152.",
        ),
        (
            "parallelism",
            9,
            "Vault KDF parallelism exceeds maximum allowed value of 8.",
        ),
        ("keyLength", 31, "Vault KDF keyLength must be exactly 32."),
    ],
)
def test_decrypt_string_rejects_out_of_bounds_kdf_parameters(
    field: str,
    value: int,
    message: str,
) -> None:
    envelope = encrypt_string("secret", "vault-pass")
    envelope["kdf"][field] = value

    with pytest.raises(ValueError, match=message):
        decrypt_string(envelope, "vault-pass")


def test_encrypt_string_uses_checksum_and_decrypt_string_accepts_legacy_integrity() -> None:
    envelope = encrypt_string("secret", "vault-pass")

    assert "checksum" in envelope
    assert "integrity" not in envelope

    legacy_envelope = dict(envelope)
    legacy_envelope["integrity"] = legacy_envelope.pop("checksum")

    assert decrypt_string(legacy_envelope, "vault-pass") == "secret"


def test_rebuild_vector_indices_syncs_runtime_before_vector_store(monkeypatch) -> None:
    import anima_server.services.agent.embeddings as embeddings_module

    calls: list[tuple[str, int]] = []

    def _sync_runtime(db, *, user_id: int) -> int:
        assert isinstance(db, SimpleNamespace)
        calls.append(("runtime", user_id))
        return 1

    def _sync_vector_store(db, *, user_id: int) -> int:
        assert isinstance(db, SimpleNamespace)
        calls.append(("vector", user_id))
        return 1

    monkeypatch.setattr(embeddings_module, "sync_embeddings_to_runtime", _sync_runtime)
    monkeypatch.setattr(embeddings_module, "sync_to_vector_store", _sync_vector_store)

    vault_module._rebuild_vector_indices(
        SimpleNamespace(),
        {"users": [{"id": 42}, {"id": 42}, "skip-me"]},
    )

    assert calls == [("runtime", 42), ("vector", 42)]


def test_reset_identity_sequences_includes_knowledge_graph_tables() -> None:
    statements: list[str] = []

    class FakeSession:
        def get_bind(self):
            return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

        def execute(self, statement):
            statements.append(str(statement))

    vault_module.reset_identity_sequences(FakeSession())

    joined = "\n".join(statements)
    assert "kg_entities" in joined
    assert "kg_relations" in joined
