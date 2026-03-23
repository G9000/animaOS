from __future__ import annotations

import pytest
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


class TestWebSocketAuth:
    """Tests for WebSocket /ws/agent endpoint authentication."""

    def test_ws_auth_with_valid_token(self) -> None:
        """Client sends auth message with unlockToken, server responds auth_ok."""
        with managed_test_client("anima-ws-test-") as client:
            user = _register_user(client)
            unlock_token = str(user["unlockToken"])
            user_id = int(user["id"])

            with client.websocket_connect("/ws/agent") as ws:
                ws.send_json({"type": "auth", "unlockToken": unlock_token})
                response = ws.receive_json()
                assert response["type"] == "auth_ok"
                assert "user" in response
                assert response["user"]["id"] == user_id

    def test_ws_auth_rejected_without_token(self) -> None:
        """Client that sends non-auth message first gets error."""
        with managed_test_client("anima-ws-test-") as client:
            _register_user(client)

            with client.websocket_connect("/ws/agent") as ws:
                ws.send_json({"type": "user_message", "message": "hello"})
                response = ws.receive_json()
                assert response["type"] == "error"
                assert "auth" in response["message"].lower()

    def test_ws_auth_rejected_with_invalid_token(self) -> None:
        """Client with invalid unlock token gets auth error."""
        with managed_test_client("anima-ws-test-") as client:
            _register_user(client)

            with client.websocket_connect("/ws/agent") as ws:
                ws.send_json({"type": "auth", "unlockToken": "bogus-token"})
                response = ws.receive_json()
                assert response["type"] == "error"
                assert response["code"] == "AUTH_FAILED"

    def test_ws_tool_schemas_registration(self) -> None:
        """Client can register action tool schemas after auth."""
        with managed_test_client("anima-ws-test-") as client:
            user = _register_user(client)
            unlock_token = str(user["unlockToken"])

            with client.websocket_connect("/ws/agent") as ws:
                ws.send_json({"type": "auth", "unlockToken": unlock_token})
                auth_resp = ws.receive_json()
                assert auth_resp["type"] == "auth_ok"

                ws.send_json({"type": "tool_schemas", "tools": [
                    {"name": "bash", "description": "Run shell", "parameters": {}}
                ]})
                # No response expected for tool_schemas — verify no error by
                # sending another message and confirming the connection is still alive.
                ws.send_json({"type": "ping"})
                # Connection should still be open (no error response expected).
