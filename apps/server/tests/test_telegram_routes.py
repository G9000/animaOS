from __future__ import annotations

import json
import os
from pathlib import Path

from anima_server.config import settings
from anima_server.db.session import get_user_session_factory
from anima_server.models import TelegramLink
from conftest import managed_test_client
from sqlalchemy import select


def _register(client):
    """Register a test user and return (user_id, headers_with_unlock_token)."""
    resp = client.post(
        "/api/auth/register",
        json={
            "username": "tgtest",
            "password": "testpass123",
            "name": "TG Test",
        },
    )
    assert resp.status_code in (200, 201), resp.text
    data = resp.json()
    headers = {"x-anima-unlock": data["unlockToken"]}
    return int(data["id"]), headers


class TestTelegramLinkRoutes:
    """Tests for POST/GET/DELETE /api/telegram/link."""

    def test_link_creates_mapping(self):
        os.environ.pop("TELEGRAM_LINK_SECRET", None)
        with managed_test_client("tg-link-") as client:
            uid, headers = _register(client)
            resp = client.post(
                "/api/telegram/link",
                json={
                    "chatId": 99001,
                    "userId": uid,
                },
                headers=headers,
            )
            assert resp.status_code == 201
            data = resp.json()
            assert data["chatId"] == 99001
            assert data["userId"] == uid
            registry_path = (
                settings.runtime_instance_data_dir
                and os.path.join(
                    settings.runtime_instance_data_dir,
                    "config",
                    "integration-links.json",
                )
            )
            assert registry_path
            registry = json.loads(Path(registry_path).read_text(encoding="utf-8"))
            assert registry["links"] == [
                {"externalId": "99001", "provider": "telegram", "userId": uid}
            ]
            assert not os.path.realpath(registry_path).startswith(
                os.path.realpath(settings.data_dir) + os.sep
            )
            with get_user_session_factory(uid)() as db:
                assert db.scalar(select(TelegramLink)) is None

    def test_lookup_returns_linked_user(self):
        os.environ.pop("TELEGRAM_LINK_SECRET", None)
        with managed_test_client("tg-lookup-") as client:
            uid, headers = _register(client)
            client.post(
                "/api/telegram/link",
                json={
                    "chatId": 99002,
                    "userId": uid,
                },
                headers=headers,
            )
            resp = client.get("/api/telegram/link", params={"chatId": 99002}, headers=headers)
            assert resp.status_code == 200
            assert resp.json()["userId"] == uid

    def test_lookup_returns_404_when_not_linked(self):
        with managed_test_client("tg-404-") as client:
            _, headers = _register(client)
            resp = client.get("/api/telegram/link", params={"chatId": 99999}, headers=headers)
            assert resp.status_code == 404

    def test_unlink_removes_mapping(self):
        os.environ.pop("TELEGRAM_LINK_SECRET", None)
        with managed_test_client("tg-unlink-") as client:
            uid, headers = _register(client)
            client.post(
                "/api/telegram/link",
                json={
                    "chatId": 99003,
                    "userId": uid,
                },
                headers=headers,
            )
            resp = client.delete("/api/telegram/link", params={"chatId": 99003}, headers=headers)
            assert resp.status_code == 200
            resp = client.get("/api/telegram/link", params={"chatId": 99003}, headers=headers)
            assert resp.status_code == 404

    def test_link_replaces_existing_for_same_chat(self):
        os.environ.pop("TELEGRAM_LINK_SECRET", None)
        with managed_test_client("tg-replace-") as client:
            uid, headers = _register(client)
            client.post(
                "/api/telegram/link",
                json={
                    "chatId": 99004,
                    "userId": uid,
                },
                headers=headers,
            )
            resp = client.post(
                "/api/telegram/link",
                json={
                    "chatId": 99004,
                    "userId": uid,
                },
                headers=headers,
            )
            assert resp.status_code == 201

    def test_link_requires_secret_when_configured(self):
        os.environ["TELEGRAM_LINK_SECRET"] = "test-secret-123"
        try:
            with managed_test_client("tg-secret-req-") as client:
                uid, headers = _register(client)
                resp = client.post(
                    "/api/telegram/link",
                    json={
                        "chatId": 99005,
                        "userId": uid,
                    },
                    headers=headers,
                )
                assert resp.status_code == 403
        finally:
            os.environ.pop("TELEGRAM_LINK_SECRET", None)

    def test_link_accepts_correct_secret(self):
        os.environ["TELEGRAM_LINK_SECRET"] = "test-secret-123"
        try:
            with managed_test_client("tg-secret-ok-") as client:
                uid, headers = _register(client)
                resp = client.post(
                    "/api/telegram/link",
                    json={
                        "chatId": 99006,
                        "userId": uid,
                        "linkSecret": "test-secret-123",
                    },
                    headers=headers,
                )
                assert resp.status_code == 201
        finally:
            os.environ.pop("TELEGRAM_LINK_SECRET", None)

    def test_link_rejects_wrong_secret(self):
        os.environ["TELEGRAM_LINK_SECRET"] = "test-secret-123"
        try:
            with managed_test_client("tg-secret-bad-") as client:
                uid, headers = _register(client)
                resp = client.post(
                    "/api/telegram/link",
                    json={
                        "chatId": 99007,
                        "userId": uid,
                        "linkSecret": "wrong",
                    },
                    headers=headers,
                )
                assert resp.status_code == 403
        finally:
            os.environ.pop("TELEGRAM_LINK_SECRET", None)

    def test_link_rejects_a_different_session_user(self):
        os.environ.pop("TELEGRAM_LINK_SECRET", None)
        with managed_test_client("tg-nouser-") as client:
            _, headers = _register(client)
            resp = client.post(
                "/api/telegram/link",
                json={
                    "chatId": 99009,
                    "userId": 99999,
                },
                headers=headers,
            )
            assert resp.status_code == 403

    def test_me_migrates_legacy_link_copy_verify_delete(self):
        os.environ.pop("TELEGRAM_LINK_SECRET", None)
        with managed_test_client("tg-legacy-migrate-") as client:
            uid, headers = _register(client)
            with get_user_session_factory(uid)() as db:
                db.add(TelegramLink(chat_id=99010, user_id=uid))
                db.commit()

            response = client.get("/api/auth/me", headers=headers)
            assert response.status_code == 200, response.text

            with get_user_session_factory(uid)() as db:
                assert db.scalar(select(TelegramLink)) is None
            registry_path = os.path.join(
                settings.runtime_instance_data_dir,
                "config",
                "integration-links.json",
            )
            registry = json.loads(Path(registry_path).read_text(encoding="utf-8"))
            assert registry["links"] == [
                {"externalId": "99010", "provider": "telegram", "userId": uid}
            ]

    def test_link_replaces_existing_for_same_user(self):
        os.environ.pop("TELEGRAM_LINK_SECRET", None)
        with managed_test_client("tg-replace-user-") as client:
            uid, headers = _register(client)
            # Link user to chat A
            client.post(
                "/api/telegram/link",
                json={
                    "chatId": 88001,
                    "userId": uid,
                },
                headers=headers,
            )
            # Link same user to chat B — old link for chat A should be removed
            resp = client.post(
                "/api/telegram/link",
                json={
                    "chatId": 88002,
                    "userId": uid,
                },
                headers=headers,
            )
            assert resp.status_code == 201
            # Chat A should no longer be linked
            resp = client.get("/api/telegram/link", params={"chatId": 88001}, headers=headers)
            assert resp.status_code == 404
            # Chat B should be linked
            resp = client.get("/api/telegram/link", params={"chatId": 88002}, headers=headers)
            assert resp.status_code == 200
            assert resp.json()["userId"] == uid

    def test_link_requires_auth(self):
        with managed_test_client("tg-noauth-") as client:
            resp = client.post(
                "/api/telegram/link",
                json={
                    "chatId": 99008,
                    "userId": 1,
                },
            )
            assert resp.status_code == 401
