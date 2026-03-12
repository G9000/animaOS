from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from anima_server.db.base import Base
from anima_server.db.session import get_db
from anima_server.main import create_app
from anima_server.models import User
from anima_server.services.auth import hash_password
from anima_server.services.sessions import unlock_session_store
from anima_server.services.storage import get_user_data_dir


@pytest.fixture()
def session_factory() -> Generator[sessionmaker, None, None]:
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
    Base.metadata.create_all(bind=engine)
    try:
        yield factory
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


@pytest.fixture()
def client(session_factory: sessionmaker) -> Generator[TestClient, None, None]:
    app = create_app()

    def override_get_db() -> Generator[Session, None, None]:
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    unlock_session_store.clear()
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        unlock_session_store.clear()
        app.dependency_overrides.clear()


def seed_user(session_factory: sessionmaker) -> User:
    with session_factory() as db:
        user = User(
            username="alice",
            password_hash=hash_password("pw123"),
            display_name="Alice",
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user


def test_get_user_requires_matching_unlock_session(
    client: TestClient,
    session_factory: sessionmaker,
) -> None:
    user = seed_user(session_factory)

    unauthorized = client.get(f"/api/users/{user.id}")
    assert unauthorized.status_code == 401
    assert unauthorized.json() == {"error": "Session locked. Please sign in again."}

    token = unlock_session_store.create(user.id)
    response = client.get(
        f"/api/users/{user.id}",
        headers={"x-anima-unlock": token},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == user.id
    assert payload["username"] == "alice"
    assert payload["name"] == "Alice"
    assert payload["gender"] is None


def test_update_user_updates_profile_fields(
    client: TestClient,
    session_factory: sessionmaker,
) -> None:
    user = seed_user(session_factory)
    token = unlock_session_store.create(user.id)

    response = client.put(
        f"/api/users/{user.id}",
        headers={"x-anima-unlock": token},
        json={
            "name": "Alice Updated",
            "gender": "female",
            "age": 29,
            "birthday": "1996-03-12",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "Alice Updated"
    assert payload["gender"] == "female"
    assert payload["age"] == 29
    assert payload["birthday"] == "1996-03-12"


def test_delete_user_removes_database_row_and_files(
    client: TestClient,
    session_factory: sessionmaker,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = seed_user(session_factory)
    token = unlock_session_store.create(user.id)
    data_dir = tmp_path / "anima-user-data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("anima_server.config.settings.data_dir", data_dir)
    user_dir = get_user_data_dir(user.id)
    user_dir.mkdir(parents=True, exist_ok=True)
    (user_dir / "profile.txt").write_text("exists", encoding="utf-8")

    response = client.delete(
        f"/api/users/{user.id}",
        headers={"x-anima-unlock": token},
    )

    assert response.status_code == 200
    assert response.json() == {"message": "User deleted"}
    assert not user_dir.exists()

    with session_factory() as db:
        assert db.get(User, user.id) is None
