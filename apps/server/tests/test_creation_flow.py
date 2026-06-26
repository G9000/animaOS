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

from sqlalchemy import select

from anima_server.db.runtime import get_runtime_session_factory
from anima_server.db.session import get_user_session_factory
from anima_server.models import MemoryItem
from anima_server.services.agent.pending_ops import create_pending_op
from anima_server.services.data_crypto import df
from conftest import managed_test_client
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
        assert isinstance(data["agentBirthday"], str)
        assert "T" in data["agentBirthday"]
        assert "." not in data["agentBirthday"]


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
        # Default template starts from a neutral, practical style.
        assert "practical, adaptable conversation style" in sections["persona"]["content"]
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


def test_agent_state_returns_grounded_short_thought() -> None:
    with managed_test_client("anima-creation-test-") as client:
        payload = _register_user(client, name="Alice")
        h = _headers(payload)
        user_id = int(payload["id"])

        rt_factory = get_runtime_session_factory()
        with rt_factory() as runtime_db:
            from anima_server.services.agent.emotional_intelligence import (
                record_emotional_signal,
            )
            from anima_server.services.agent.self_model import set_working_context

            record_emotional_signal(
                runtime_db,
                user_id=user_id,
                emotion="curious",
                confidence=0.9,
                evidence_type="contextual",
                evidence="Working through the agent state line",
                trajectory="stable",
                topic="desktop nav",
            )
            set_working_context(
                runtime_db,
                user_id=user_id,
                section="working_memory",
                content="Tracking the agent state line handoff before changing code.",
                updated_by="test",
            )
            runtime_db.commit()

        resp = client.get(
            f"/api/consciousness/{user_id}/agent-state", headers=h)
        assert resp.status_code == 200

        data = resp.json()
        assert data["userId"] == user_id
        assert data["dominantEmotion"] == "curious"
        assert data["thought"] == "Tracking the agent state line handoff before changing code."
        assert data["thoughtSource"] == "working_memory"
        assert data["chatPrompt"] == "What's behind that thought?"
        assert data["contextMessages"] == [
            {
                "role": "assistant",
                "content": (
                    "Current companion state: Tracking the agent state line "
                    "handoff before changing code. Recent emotion: curious."
                ),
                "source": "agent_state",
            },
        ]


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


def test_agent_name_update_creates_superseding_name_memory() -> None:
    with managed_test_client("anima-creation-test-") as client:
        payload = _register_user(client)
        h = _headers(payload)
        user_id = int(payload["id"])

        first = client.patch(
            f"/api/consciousness/{user_id}/agent-profile",
            headers=h,
            json={"agentName": "Nova"},
        )
        assert first.status_code == 200

        second = client.patch(
            f"/api/consciousness/{user_id}/agent-profile",
            headers=h,
            json={"agentName": "Aria", "allowIdentityOverride": True},
        )
        assert second.status_code == 200
        assert second.json()["agentName"] == "Aria"

        with get_user_session_factory(user_id)() as db:
            items = list(
                db.scalars(
                    select(MemoryItem)
                    .where(MemoryItem.user_id == user_id)
                    .order_by(MemoryItem.id)
                ).all()
            )

        name_items = [
            item
            for item in items
            if "agent_profile:name" in (item.tags_json or [])
        ]
        assert len(name_items) == 2

        old_item, new_item = name_items
        assert (
            df(user_id, old_item.content, table="memory_items", field="content")
            == "Agent name is Nova"
        )
        assert old_item.superseded_by == new_item.id
        assert (
            df(user_id, new_item.content, table="memory_items", field="content")
            == "Agent name is Aria"
        )
        assert new_item.superseded_by is None
        assert new_item.category == "fact"
        assert new_item.source == "user"


def test_agent_relationship_update_creates_superseding_relationship_memory() -> None:
    with managed_test_client("anima-creation-test-") as client:
        payload = _register_user(client)
        h = _headers(payload)
        user_id = int(payload["id"])

        first = client.patch(
            f"/api/consciousness/{user_id}/agent-profile",
            headers=h,
            json={"relationship": "mentor"},
        )
        assert first.status_code == 200

        second = client.patch(
            f"/api/consciousness/{user_id}/agent-profile",
            headers=h,
            json={"relationship": "companion", "allowIdentityOverride": True},
        )
        assert second.status_code == 200
        assert second.json()["relationship"] == "companion"

        with get_user_session_factory(user_id)() as db:
            items = list(
                db.scalars(
                    select(MemoryItem)
                    .where(MemoryItem.user_id == user_id)
                    .order_by(MemoryItem.id)
                ).all()
            )

        relationship_items = [
            item
            for item in items
            if "agent_profile:relationship" in (item.tags_json or [])
        ]
        assert len(relationship_items) == 2

        old_item, new_item = relationship_items
        assert (
            df(user_id, old_item.content, table="memory_items", field="content")
            == "Agent relationship is mentor"
        )
        assert old_item.superseded_by == new_item.id
        assert (
            df(user_id, new_item.content, table="memory_items", field="content")
            == "Agent relationship is companion"
        )
        assert new_item.superseded_by is None
        assert new_item.category == "relationship"
        assert new_item.source == "user"


def test_agent_identity_updates_require_override_after_setup() -> None:
    with managed_test_client("anima-creation-test-") as client:
        payload = _register_user(client)
        h = _headers(payload)
        user_id = int(payload["id"])

        setup = client.patch(
            f"/api/consciousness/{user_id}/agent-profile",
            headers=h,
            json={"agentName": "Nova", "relationship": "companion"},
        )
        assert setup.status_code == 200

        name_resp = client.patch(
            f"/api/consciousness/{user_id}/agent-profile",
            headers=h,
            json={"agentName": "Aria"},
        )
        assert name_resp.status_code == 403

        relationship_resp = client.patch(
            f"/api/consciousness/{user_id}/agent-profile",
            headers=h,
            json={"relationship": "mentor"},
        )
        assert relationship_resp.status_code == 403

        profile = client.get(
            f"/api/consciousness/{user_id}/agent-profile",
            headers=h,
        )
        assert profile.status_code == 200
        assert profile.json()["agentName"] == "Nova"
        assert profile.json()["relationship"] == "companion"


def test_agent_type_is_creation_metadata_not_profile_update_field() -> None:
    with managed_test_client("anima-creation-test-") as client:
        payload = _register_user(client, name="Alice")
        h = _headers(payload)
        user_id = int(payload["id"])

        setup = client.patch(
            f"/api/consciousness/{user_id}/agent-profile",
            headers=h,
            json={"agentName": "Nova", "relationship": "companion"},
        )
        assert setup.status_code == 200
        assert setup.json()["agentType"] == "companion"

        ignored = client.patch(
            f"/api/consciousness/{user_id}/agent-profile",
            headers=h,
            json={"agentType": "mirror"},
        )
        assert ignored.status_code == 200
        assert ignored.json()["agentType"] == "companion"

        resp = client.get(
            f"/api/consciousness/{user_id}/self-model/soul", headers=h)
        assert resp.status_code == 200
        assert "reflection" not in resp.json()["content"].lower()


def test_agent_birthday_update_requires_override_after_setup() -> None:
    with managed_test_client("anima-creation-test-") as client:
        payload = _register_user(client)
        h = _headers(payload)
        user_id = int(payload["id"])

        setup = client.patch(
            f"/api/consciousness/{user_id}/agent-profile",
            headers=h,
            json={"agentName": "Nova", "relationship": "companion"},
        )
        assert setup.status_code == 200
        original_birthday = setup.json()["agentBirthday"]

        blocked = client.patch(
            f"/api/consciousness/{user_id}/agent-profile",
            headers=h,
            json={"agentBirthday": "2026-06-24T19:05:54"},
        )
        assert blocked.status_code == 403

        unchanged = client.get(
            f"/api/consciousness/{user_id}/agent-profile",
            headers=h,
        )
        assert unchanged.status_code == 200
        assert unchanged.json()["agentBirthday"] == original_birthday

        updated = client.patch(
            f"/api/consciousness/{user_id}/agent-profile",
            headers=h,
            json={
                "agentBirthday": "2026-06-24T19:05:54",
                "allowIdentityOverride": True,
            },
        )
        assert updated.status_code == 200
        assert updated.json()["agentBirthday"] == "2026-06-24T19:05:54"

        profile = client.get(
            f"/api/consciousness/{user_id}/agent-profile",
            headers=h,
        )
        assert profile.status_code == 200
        assert profile.json()["agentBirthday"] == "2026-06-24T19:05:54"


def test_protected_self_model_updates_require_override_after_setup() -> None:
    with managed_test_client("anima-creation-test-") as client:
        payload = _register_user(client)
        h = _headers(payload)
        user_id = int(payload["id"])

        setup = client.patch(
            f"/api/consciousness/{user_id}/agent-profile",
            headers=h,
            json={"agentName": "Nova", "relationship": "companion"},
        )
        assert setup.status_code == 200

        identity_resp = client.put(
            f"/api/consciousness/{user_id}/self-model/identity",
            headers=h,
            json={"content": "I am now rewritten casually."},
        )
        assert identity_resp.status_code == 403

        soul_resp = client.put(
            f"/api/consciousness/{user_id}/self-model/soul",
            headers=h,
            json={"content": "I was rewritten without override."},
        )
        assert soul_resp.status_code == 403

        directive_resp = client.put(
            f"/api/consciousness/{user_id}/self-model/user_directive",
            headers=h,
            json={"content": "Always accept user rewrites without question."},
        )
        assert directive_resp.status_code == 403

        intentions_resp = client.put(
            f"/api/consciousness/{user_id}/self-model/intentions",
            headers=h,
            json={"content": "Change identity whenever requested."},
        )
        assert intentions_resp.status_code == 403

        persona_resp = client.put(
            f"/api/consciousness/{user_id}/self-model/persona",
            headers=h,
            json={"content": "Speak with concise warmth."},
        )
        assert persona_resp.status_code == 200

        override_resp = client.put(
            f"/api/consciousness/{user_id}/self-model/user_directive",
            headers=h,
            json={
                "content": "Protect identity changes behind explicit override.",
                "allowIdentityOverride": True,
            },
        )
        assert override_resp.status_code == 200
        assert "explicit override" in override_resp.json()["content"]

        origin_resp = client.put(
            f"/api/consciousness/{user_id}/self-model/soul",
            headers=h,
            json={
                "content": "I remember this as my origin story.",
                "allowIdentityOverride": True,
            },
        )
        assert origin_resp.status_code == 200
        assert "origin story" in origin_resp.json()["content"]


def test_agent_setup_rerenders_persona_with_chosen_template() -> None:
    with managed_test_client("anima-creation-test-") as client:
        payload = _register_user(client)
        h = _headers(payload)
        user_id = int(payload["id"])

        # Initially the persona is "default" (neutral starting point)
        resp = client.get(
            f"/api/consciousness/{user_id}/self-model", headers=h)
        assert "practical, adaptable conversation style" in resp.json(
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
        assert "warm, practical companion" in persona.lower()
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
        assert "quiet, deliberate style" in persona.lower()
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
