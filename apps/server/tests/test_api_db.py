"""Tests for the database viewer API routes."""

from __future__ import annotations

from fastapi.testclient import TestClient

from conftest import managed_test_client


def _register_user(client: TestClient) -> dict[str, object]:
    response = client.post(
        "/api/auth/register",
        json={"username": "dbtest", "password": "pw1234", "name": "DB Test"},
    )
    assert response.status_code == 201
    return response.json()


# --------------------------------------------------------------------------- #
# GET /api/db/tables
# --------------------------------------------------------------------------- #


def test_list_tables() -> None:
    with managed_test_client("anima-db-test-") as client:
        reg = _register_user(client)
        headers = {"x-anima-unlock": reg["unlockToken"]}

        resp = client.get("/api/db/tables", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) > 0

        # Each entry has name and rowCount
        table_names = [t["name"] for t in data]
        assert "users" in table_names
        for table in data:
            assert "name" in table
            assert "rowCount" in table
            assert isinstance(table["rowCount"], int)


# --------------------------------------------------------------------------- #
# GET /api/db/tables/{table_name}
# --------------------------------------------------------------------------- #


def test_get_table_rows() -> None:
    with managed_test_client("anima-db-test-") as client:
        reg = _register_user(client)
        headers = {"x-anima-unlock": reg["unlockToken"]}

        resp = client.get("/api/db/tables/users", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["table"] == "users"
        assert "columns" in data
        assert "rows" in data
        assert "total" in data
        assert "primaryKeys" in data
        assert data["total"] >= 1  # At least the registered user


def test_get_table_rows_not_found() -> None:
    with managed_test_client("anima-db-test-") as client:
        reg = _register_user(client)
        headers = {"x-anima-unlock": reg["unlockToken"]}

        resp = client.get("/api/db/tables/nonexistent", headers=headers)
        assert resp.status_code == 404


def test_get_table_rows_pagination() -> None:
    with managed_test_client("anima-db-test-") as client:
        reg = _register_user(client)
        headers = {"x-anima-unlock": reg["unlockToken"]}

        resp = client.get(
            "/api/db/tables/users?limit=1&offset=0", headers=headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["rows"]) <= 1


# --------------------------------------------------------------------------- #
# POST /api/db/query
# --------------------------------------------------------------------------- #


def test_run_select_query() -> None:
    with managed_test_client("anima-db-test-") as client:
        reg = _register_user(client)
        headers = {"x-anima-unlock": reg["unlockToken"]}

        resp = client.post(
            "/api/db/query",
            headers=headers,
            json={"sql": "SELECT COUNT(*) as cnt FROM users"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "columns" in data
        assert "rows" in data
        assert data["rowCount"] >= 1


def test_run_query_rejects_non_select() -> None:
    with managed_test_client("anima-db-test-") as client:
        reg = _register_user(client)
        headers = {"x-anima-unlock": reg["unlockToken"]}

        resp = client.post(
            "/api/db/query",
            headers=headers,
            json={"sql": "DROP TABLE users"},
        )
        assert resp.status_code == 400


def test_run_query_empty_sql() -> None:
    with managed_test_client("anima-db-test-") as client:
        reg = _register_user(client)
        headers = {"x-anima-unlock": reg["unlockToken"]}

        resp = client.post(
            "/api/db/query",
            headers=headers,
            json={"sql": ""},
        )
        assert resp.status_code == 400


# --------------------------------------------------------------------------- #
# DELETE /api/db/tables/{table_name}/rows
# --------------------------------------------------------------------------- #


def test_delete_row_requires_conditions() -> None:
    with managed_test_client("anima-db-test-") as client:
        reg = _register_user(client)
        headers = {"x-anima-unlock": reg["unlockToken"]}

        resp = client.request(
            "DELETE",
            "/api/db/tables/users/rows",
            headers=headers,
            json={"conditions": {}},
        )
        assert resp.status_code == 400


def test_delete_row_table_not_found() -> None:
    with managed_test_client("anima-db-test-") as client:
        reg = _register_user(client)
        headers = {"x-anima-unlock": reg["unlockToken"]}

        resp = client.request(
            "DELETE",
            "/api/db/tables/nonexistent/rows",
            headers=headers,
            json={"conditions": {"id": 1}},
        )
        assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# PUT /api/db/tables/{table_name}/rows
# --------------------------------------------------------------------------- #


def test_update_row_requires_conditions() -> None:
    with managed_test_client("anima-db-test-") as client:
        reg = _register_user(client)
        headers = {"x-anima-unlock": reg["unlockToken"]}

        resp = client.put(
            "/api/db/tables/users/rows",
            headers=headers,
            json={"conditions": {}, "updates": {"name": "New"}},
        )
        assert resp.status_code == 400


def test_update_row_requires_updates() -> None:
    with managed_test_client("anima-db-test-") as client:
        reg = _register_user(client)
        headers = {"x-anima-unlock": reg["unlockToken"]}

        resp = client.put(
            "/api/db/tables/users/rows",
            headers=headers,
            json={"conditions": {"id": 1}, "updates": {}},
        )
        assert resp.status_code == 400


def test_update_row_table_not_found() -> None:
    with managed_test_client("anima-db-test-") as client:
        reg = _register_user(client)
        headers = {"x-anima-unlock": reg["unlockToken"]}

        resp = client.put(
            "/api/db/tables/nonexistent/rows",
            headers=headers,
            json={"conditions": {"id": 1}, "updates": {"name": "New"}},
        )
        assert resp.status_code == 404
