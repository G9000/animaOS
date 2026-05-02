from __future__ import annotations

import logging

from conftest import managed_test_client


def test_app_startup_keeps_existing_anima_loggers_enabled() -> None:
    target_logger = logging.getLogger("anima_server.services.agent.embeddings")
    target_logger.disabled = False

    with managed_test_client("anima-logging-test-", invalidate_agent=False) as client:
        response = client.post(
            "/api/auth/register",
            json={
                "username": "alice",
                "password": "pw123456",
                "name": "Alice",
            },
        )

    assert response.status_code == 201
    assert target_logger.disabled is False
