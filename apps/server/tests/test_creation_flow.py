"""Tests for the full agent creation flow: registration → agent setup → first use.

Covers:
- Registration seeds AgentProfile, soul, persona, human self_model_blocks
- GET /consciousness/{id}/agent-profile returns correct initial state
- PATCH /consciousness/{id}/agent-profile updates profile, re-renders blocks
- setup_complete flag lifecycle
- Persona template selection (anima, companion, default)
- Agent name propagation to soul/origin block
"""

from __future__ import annotations

from conftest import managed_test_client
from anima_server.db.runtime import get_runtime_session_factory
from anima_server.services.agent.pending_ops import create_pending_op
from fastapi.testclient import TestClient


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


def _headers(payload: dict[str, object]) -> dict[str, str]:
    return {"x-anima-unlock": str(payload["unlockToken"])}


# --- Registration Seeds ---


def test_register_creates_agent_profile() -> None:
    with managed_test_client("anima-creation-test-") as client:
        payload = _register_user(client)
        h = _headers(payload)
        user_id = int(payload["id"])

        profile = client.get(
            f"/api/consciousness/{user_id}/agent-profile", headers=h)
        assert profile.status_code == 200
        data = profile.json()
        assert data["agentName"] == "Anima"
        assert data["setupComplete"] is False


def test_register_seeds_soul_block() -> None:
    with managed_test_client("anima-creation-test-") as client:
        payload = _register_user(client, name="Alice")
        h = _headers(payload)
        user_id = int(payload["id"])

        resp = client.get(
            f"/api/consciousness/{user_id}/self-model", headers=h)
        assert resp.status_code == 200
        sections = resp.json()["sections"]
        assert "soul" in sections
        assert "Anima" in sections["soul"]["content"]
        assert "Alice" in sections["soul"]["content"]


def test_register_seeds_persona_block_default() -> None:
    with managed_test_client("anima-creation-test-") as client:
        payload = _register_user(client)
        h = _headers(payload)
        user_id = int(payload["id"])

        resp = client.get(
            f"/api/consciousness/{user_id}/self-model", headers=h)
        sections = resp.json()["sections"]
        assert "persona" in sections
        # Default template is blank slate
        assert "A new presence" in sections["persona"]["content"]
        assert sections["persona"]["version"] == 1


def test_register_seeds_human_block() -> None:
    with managed_test_client("anima-creation-test-") as client:
        payload = _register_user(client, name="Alice")
        h = _headers(payload)
        user_id = int(payload["id"])

        resp = client.get(
            f"/api/consciousness/{user_id}/self-model", headers=h)
        sections = resp.json()["sections"]
        assert "human" in sections
        assert "Alice" in sections["human"]["content"]
        assert "companion" in sections["human"]["content"]


def test_self_model_response_includes_pending_ops() -> None:
    with managed_test_client("anima-creation-test-") as client:
        payload = _register_user(client, name="Alice")
        h = _headers(payload)
        user_id = int(payload["id"])

        rt_factory = get_runtime_session_factory()
        with rt_factory() as runtime_db:
            create_pending_op(
                runtime_db,
                user_id=user_id,
                op_type="append",
                target_block="human",
                content="Has a dog named Biscuit.",
                old_content=None,
                source_run_id=101,
                source_tool_call_id="test-pending-1",
            )
            runtime_db.commit()

        resp = client.get(
            f"/api/consciousness/{user_id}/self-model", headers=h)
        assert resp.status_code == 200

        data = resp.json()
        assert data["sections"]["human"]["content"].endswith(
            "Has a dog named Biscuit.")
        assert len(data["pendingOps"]) == 1
        assert data["pendingOps"][0]["targetBlock"] == "human"
        assert data["pendingOps"][0]["opType"] == "append"
        assert data["pendingOps"][0]["content"] == "Has a dog named Biscuit."


def test_pending_ops_endpoint_returns_unconsolidated_ops() -> None:
    with managed_test_client("anima-creation-test-") as client:
        payload = _register_user(client, name="Alice")
        h = _headers(payload)
        user_id = int(payload["id"])

        rt_factory = get_runtime_session_factory()
        with rt_factory() as runtime_db:
            create_pending_op(
                runtime_db,
                user_id=user_id,
                op_type="replace",
                target_block="persona",
                content="Speak more directly.",
                old_content="Speak softly.",
                source_run_id=102,
                source_tool_call_id="test-pending-2",
            )
            runtime_db.commit()

        resp = client.get(
            f"/api/consciousness/{user_id}/pending-ops", headers=h)
        assert resp.status_code == 200

        data = resp.json()
        assert data["userId"] == user_id
        assert len(data["pendingOps"]) == 1
        assert data["pendingOps"][0]["targetBlock"] == "persona"
        assert data["pendingOps"][0]["opType"] == "replace"
        assert data["pendingOps"][0]["oldContent"] == "Speak softly."


def test_consolidate_pending_ops_endpoint_runs_soul_writer() -> None:
    with managed_test_client("anima-creation-test-") as client:
        payload = _register_user(client, name="Alice")
        h = _headers(payload)
        user_id = int(payload["id"])

        rt_factory = get_runtime_session_factory()
        with rt_factory() as runtime_db:
            create_pending_op(
                runtime_db,
                user_id=user_id,
                op_type="append",
                target_block="human",
                content="Likes pour-over coffee.",
                old_content=None,
                source_run_id=103,
                source_tool_call_id="test-pending-3",
            )
            runtime_db.commit()

        resp = client.post(
            f"/api/consciousness/{user_id}/pending-ops/consolidate", headers=h)
        assert resp.status_code == 200

        data = resp.json()
        assert data["userId"] == user_id
        assert data["status"] == "ok"
        assert data["opsProcessed"] >= 1
        assert data["remainingPendingOps"] == 0

        resp = client.get(
            f"/api/consciousness/{user_id}/pending-ops", headers=h)
        assert resp.status_code == 200
        assert resp.json()["pendingOps"] == []


# --- Agent Setup (PATCH) ---


def test_agent_setup_updates_name_and_marks_complete() -> None:
    with managed_test_client("anima-creation-test-") as client:
        payload = _register_user(client)
        h = _headers(payload)
        user_id = int(payload["id"])

        resp = client.patch(
            f"/api/consciousness/{user_id}/agent-profile",
            headers=h,
            json={
                "agentName": "Nova",
                "relationship": "companion",
                "personaTemplate": "companion",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["agentName"] == "Nova"
        assert data["setupComplete"] is True


def test_agent_setup_rerenders_soul_with_new_name() -> None:
    with managed_test_client("anima-creation-test-") as client:
        payload = _register_user(client, name="Alice")
        h = _headers(payload)
        user_id = int(payload["id"])

        # Change agent name from Anima to Nova
        client.patch(
            f"/api/consciousness/{user_id}/agent-profile",
            headers=h,
            json={"agentName": "Nova", "personaTemplate": "default"},
        )

        resp = client.get(
            f"/api/consciousness/{user_id}/self-model", headers=h)
        sections = resp.json()["sections"]
        assert "Nova" in sections["soul"]["content"]
        assert "Alice" in sections["soul"]["content"]


def test_agent_setup_rerenders_persona_with_chosen_template() -> None:
    with managed_test_client("anima-creation-test-") as client:
        payload = _register_user(client)
        h = _headers(payload)
        user_id = int(payload["id"])

        # Initially the persona is "default" (blank slate)
        resp = client.get(
            f"/api/consciousness/{user_id}/self-model", headers=h)
        assert "A new presence" in resp.json(
        )["sections"]["persona"]["content"]

        # Switch to companion template
        client.patch(
            f"/api/consciousness/{user_id}/agent-profile",
            headers=h,
            json={"agentName": "Nova", "personaTemplate": "companion"},
        )

        resp = client.get(
            f"/api/consciousness/{user_id}/self-model", headers=h)
        persona = resp.json()["sections"]["persona"]["content"]
        assert "warm, emotionally attuned companion" in persona
        assert "Nova" in persona


def test_agent_setup_anima_template() -> None:
    with managed_test_client("anima-creation-test-") as client:
        payload = _register_user(client)
        h = _headers(payload)
        user_id = int(payload["id"])

        client.patch(
            f"/api/consciousness/{user_id}/agent-profile",
            headers=h,
            json={"agentName": "Anima", "personaTemplate": "anima"},
        )

        resp = client.get(
            f"/api/consciousness/{user_id}/self-model", headers=h)
        persona = resp.json()["sections"]["persona"]["content"]
        assert "quiet presence" in persona.lower()
        assert "Anima" in persona


def test_agent_setup_updates_human_relationship() -> None:
    with managed_test_client("anima-creation-test-") as client:
        payload = _register_user(client, name="Alice")
        h = _headers(payload)
        user_id = int(payload["id"])

        # Default relationship is "companion"
        resp = client.get(
            f"/api/consciousness/{user_id}/self-model", headers=h)
        assert "companion" in resp.json()["sections"]["human"]["content"]

        # Change relationship to empty (blank slate mode)
        client.patch(
            f"/api/consciousness/{user_id}/agent-profile",
            headers=h,
            json={"agentName": "Nova", "relationship": ""},
        )

        resp = client.get(
            f"/api/consciousness/{user_id}/self-model", headers=h)
        human = resp.json()["sections"]["human"]["content"]
        assert "Alice" in human
        # Relationship line should be removed
        assert "companion" not in human


# --- setup_complete Lifecycle ---


def test_setup_complete_persists_after_setup() -> None:
    with managed_test_client("anima-creation-test-") as client:
        payload = _register_user(client)
        h = _headers(payload)
        user_id = int(payload["id"])

        # Before setup
        resp = client.get(
            f"/api/consciousness/{user_id}/agent-profile", headers=h)
        assert resp.json()["setupComplete"] is False

        # Complete setup
        client.patch(
            f"/api/consciousness/{user_id}/agent-profile",
            headers=h,
            json={"agentName": "Nova"},
        )

        # After setup
        resp = client.get(
            f"/api/consciousness/{user_id}/agent-profile", headers=h)
        assert resp.json()["setupComplete"] is True


def test_agent_profile_requires_unlock_token() -> None:
    with managed_test_client("anima-creation-test-") as client:
        payload = _register_user(client)
        user_id = int(payload["id"])

        # No token
        resp = client.get(f"/api/consciousness/{user_id}/agent-profile")
        assert resp.status_code == 401


def test_agent_profile_patch_requires_unlock_token() -> None:
    with managed_test_client("anima-creation-test-") as client:
        payload = _register_user(client)
        user_id = int(payload["id"])

        resp = client.patch(
            f"/api/consciousness/{user_id}/agent-profile",
            json={"agentName": "Hacker"},
        )
        assert resp.status_code == 401


# --- Persona Template Validation ---


def test_agent_setup_rejects_invalid_template() -> None:
    with managed_test_client("anima-creation-test-") as client:
        payload = _register_user(client)
        h = _headers(payload)
        user_id = int(payload["id"])

        resp = client.patch(
            f"/api/consciousness/{user_id}/agent-profile",
            headers=h,
            json={"agentName": "Nova", "personaTemplate": "../secrets"},
        )
        # Should fail — path traversal
        assert resp.status_code == 400


def test_agent_setup_rejects_nonexistent_template() -> None:
    with managed_test_client("anima-creation-test-") as client:
        payload = _register_user(client)
        h = _headers(payload)
        user_id = int(payload["id"])

        resp = client.patch(
            f"/api/consciousness/{user_id}/agent-profile",
            headers=h,
            json={"agentName": "Nova", "personaTemplate": "nonexistent"},
        )
        assert resp.status_code == 400


# --- Agent Name Edge Cases ---


def test_agent_setup_empty_name_defaults_to_anima() -> None:
    with managed_test_client("anima-creation-test-") as client:
        payload = _register_user(client)
        h = _headers(payload)
        user_id = int(payload["id"])

        resp = client.patch(
            f"/api/consciousness/{user_id}/agent-profile",
            headers=h,
            json={"agentName": "   "},
        )
        assert resp.status_code == 200
        assert resp.json()["agentName"] == "Anima"


def test_agent_name_used_in_persona_template() -> None:
    """The {{ agent_name }} variable in persona templates renders correctly."""
    with managed_test_client("anima-creation-test-") as client:
        payload = _register_user(client)
        h = _headers(payload)
        user_id = int(payload["id"])

        client.patch(
            f"/api/consciousness/{user_id}/agent-profile",
            headers=h,
            json={"agentName": "Aria", "personaTemplate": "anima"},
        )

        resp = client.get(
            f"/api/consciousness/{user_id}/self-model", headers=h)
        persona = resp.json()["sections"]["persona"]["content"]
        # Template should render with custom name, not "Anima"
        assert "Aria" in persona
