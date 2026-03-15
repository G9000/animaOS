from __future__ import annotations

import shutil
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from anima_server.config import settings
from anima_server.db.base import Base
from anima_server.db.session import get_db
from anima_server.main import create_app
from anima_server.services.agent import invalidate_agent_runtime_cache
from anima_server.services.agent.vector_store import reset_vector_store
from anima_server.services.sessions import unlock_session_store
from conftest import create_managed_temp_dir


def _build_session_factory() -> tuple[Engine, sessionmaker]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        class_=Session,
    )
    return engine, factory


@contextmanager
def _client() -> Generator[TestClient, None, None]:
    engine, factory = _build_session_factory()
    Base.metadata.create_all(bind=engine)

    app = create_app()
    original_data_dir = settings.data_dir
    temp_root = create_managed_temp_dir("anima-dashboard-test-")

    def override_get_db() -> Generator[Session, None, None]:
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    unlock_session_store.clear()
    invalidate_agent_runtime_cache()
    settings.data_dir = temp_root / "anima-data"

    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        reset_vector_store()
        settings.data_dir = original_data_dir
        invalidate_agent_runtime_cache()
        unlock_session_store.clear()
        app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
        shutil.rmtree(temp_root, ignore_errors=True)


def _register_user(client: TestClient) -> dict[str, object]:
    response = client.post(
        "/api/auth/register",
        json={"username": "dashtest", "password": "pw1234", "name": "Dash Test"},
    )
    assert response.status_code == 201
    return response.json()


def test_brief_endpoint() -> None:
    with _client() as client:
        reg = _register_user(client)
        user_id = reg["id"]
        headers = {"x-anima-unlock": reg["unlockToken"]}

        resp = client.get(f"/api/chat/brief?userId={user_id}", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "message" in data
        assert "context" in data
        assert "currentFocus" in data["context"]
        assert "openTaskCount" in data["context"]


def test_greeting_endpoint() -> None:
    with _client() as client:
        reg = _register_user(client)
        user_id = reg["id"]
        headers = {"x-anima-unlock": reg["unlockToken"]}

        resp = client.get(
            f"/api/chat/greeting?userId={user_id}", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "message" in data
        assert isinstance(data["message"], str)
        assert len(data["message"]) > 0
        assert "context" in data
        assert "openTaskCount" in data["context"]
        assert "overdueTasks" in data["context"]
        assert "upcomingDeadlines" in data["context"]
        assert isinstance(data["llmGenerated"], bool)


def test_nudges_endpoint() -> None:
    with _client() as client:
        reg = _register_user(client)
        user_id = reg["id"]
        headers = {"x-anima-unlock": reg["unlockToken"]}

        resp = client.get(
            f"/api/chat/nudges?userId={user_id}", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "nudges" in data
        assert isinstance(data["nudges"], list)


def test_home_endpoint() -> None:
    with _client() as client:
        reg = _register_user(client)
        user_id = reg["id"]
        headers = {"x-anima-unlock": reg["unlockToken"]}

        resp = client.get(f"/api/chat/home?userId={user_id}", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "currentFocus" in data
        assert "tasks" in data
        assert "memoryCount" in data
        assert "messageCount" in data
        assert data["memoryCount"] == 0
        assert data["messageCount"] == 0


def test_config_providers() -> None:
    with _client() as client:
        resp = client.get("/api/config/providers")
        assert resp.status_code == 200
        providers = resp.json()
        assert len(providers) >= 2
        names = [p["name"] for p in providers]
        assert "scaffold" in names
        assert "ollama" in names


def test_config_get_update() -> None:
    with _client() as client:
        reg = _register_user(client)
        user_id = reg["id"]
        headers = {"x-anima-unlock": reg["unlockToken"]}

        resp = client.get(f"/api/config/{user_id}", headers=headers)
        assert resp.status_code == 200
        config = resp.json()
        assert "provider" in config
        assert "model" in config


def test_home_journal_streak() -> None:
    with _client() as client:
        reg = _register_user(client)
        user_id = reg["id"]
        headers = {"x-anima-unlock": reg["unlockToken"]}

        # Create a memory item so we have something to search
        client.post(
            f"/api/memory/{user_id}/items",
            headers=headers,
            json={"content": "Loves hiking in mountains",
                  "category": "preference"},
        )

        resp = client.get(f"/api/chat/home?userId={user_id}", headers=headers)
        data = resp.json()
        assert data["journalStreak"] == 0
        assert data["journalTotal"] == 0
        assert data["memoryCount"] == 1


def test_memory_search() -> None:
    with _client() as client:
        reg = _register_user(client)
        user_id = reg["id"]
        headers = {"x-anima-unlock": reg["unlockToken"]}

        client.post(
            f"/api/memory/{user_id}/items",
            headers=headers,
            json={"content": "Works as a designer", "category": "fact"},
        )
        client.post(
            f"/api/memory/{user_id}/items",
            headers=headers,
            json={"content": "Likes dark mode", "category": "preference"},
        )

        # Search for "designer"
        resp = client.get(
            f"/api/memory/{user_id}/search?q=designer", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["results"][0]["content"] == "Works as a designer"

        # Search for "dark"
        resp = client.get(
            f"/api/memory/{user_id}/search?q=dark", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["count"] == 1

        # Search for nonexistent
        resp = client.get(
            f"/api/memory/{user_id}/search?q=zzzzz", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["count"] == 0


def test_user_directive_get_put() -> None:
    with _client() as client:
        reg = _register_user(client)
        user_id = reg["id"]
        headers = {"x-anima-unlock": reg["unlockToken"]}

        # Get empty user directive
        resp = client.get(f"/api/soul/{user_id}", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["content"] == ""
        assert resp.json()["source"] == "database"

        # Update user directive
        resp = client.put(
            f"/api/soul/{user_id}",
            headers=headers,
            json={"content": "I am a helpful companion."},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "updated"

        # Verify
        resp = client.get(f"/api/soul/{user_id}", headers=headers)
        assert resp.json()["content"] == "I am a helpful companion."
        assert resp.json()["source"] == "database"
