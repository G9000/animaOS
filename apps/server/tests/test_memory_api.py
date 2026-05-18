from __future__ import annotations

from pathlib import Path

import pytest
from anima_server.db.session import get_user_session_factory
from anima_server.models import MemoryItemEvidence
from anima_server.services import anima_core_retrieval as retrieval_module
from anima_server.services.data_crypto import df
from conftest import managed_test_client
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session


def _register_user(client: TestClient) -> dict[str, object]:
    response = client.post(
        "/api/auth/register",
        json={"username": "memtest", "password": "pw123456", "name": "Mem Test"},
    )
    assert response.status_code == 201
    return response.json()


def test_memory_crud_lifecycle() -> None:
    with managed_test_client("anima-memory-test-") as client:
        reg = _register_user(client)
        user_id = reg["id"]
        headers = {"x-anima-unlock": reg["unlockToken"]}

        resp = client.get(f"/api/memory/{user_id}", headers=headers)
        assert resp.status_code == 200
        overview = resp.json()
        assert overview["totalItems"] == 0
        assert overview["currentFocus"] is None

        resp = client.post(
            f"/api/memory/{user_id}/items",
            headers=headers,
            json={"content": "Works as a designer", "category": "fact", "importance": 4},
        )
        assert resp.status_code == 201
        item = resp.json()
        assert item["content"] == "Works as a designer"
        assert item["category"] == "fact"
        assert item["importance"] == 4
        assert item["source"] == "user"
        item_id = item["id"]

        resp = client.post(
            f"/api/memory/{user_id}/items",
            headers=headers,
            json={"content": "Likes dark mode", "category": "preference"},
        )
        assert resp.status_code == 201

        resp = client.get(f"/api/memory/{user_id}", headers=headers)
        overview = resp.json()
        assert overview["totalItems"] == 2
        assert overview["factCount"] == 1
        assert overview["preferenceCount"] == 1

        resp = client.get(f"/api/memory/{user_id}/items?category=fact", headers=headers)
        assert resp.status_code == 200
        facts = resp.json()
        assert len(facts) == 1
        assert facts[0]["content"] == "Works as a designer"

        resp = client.put(
            f"/api/memory/{user_id}/items/{item_id}",
            headers=headers,
            json={"content": "Works as a product manager"},
        )
        assert resp.status_code == 200
        updated = resp.json()
        assert updated["content"] == "Works as a product manager"
        assert updated["id"] != item_id

        resp = client.get(f"/api/memory/{user_id}/items?category=fact", headers=headers)
        facts = resp.json()
        assert len(facts) == 1
        assert facts[0]["content"] == "Works as a product manager"

        resp = client.delete(
            f"/api/memory/{user_id}/items/{updated['id']}",
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True

        resp = client.get(f"/api/memory/{user_id}/items?category=fact", headers=headers)
        assert len(resp.json()) == 0


def test_memory_delete_removes_item_evidence() -> None:
    with managed_test_client("anima-memory-test-") as client:
        reg = _register_user(client)
        user_id = int(reg["id"])
        headers = {"x-anima-unlock": reg["unlockToken"]}

        resp = client.post(
            f"/api/memory/{user_id}/items",
            headers=headers,
            json={"content": "Keeps provenance", "category": "fact", "importance": 4},
        )
        assert resp.status_code == 201
        item_id = int(resp.json()["id"])

        with get_user_session_factory(user_id)() as db:
            db.add(
                MemoryItemEvidence(
                    user_id=user_id,
                    memory_item_id=item_id,
                    source_kind="explicit_save",
                    evidence_text="Keeps provenance",
                )
            )
            db.commit()

        resp = client.delete(
            f"/api/memory/{user_id}/items/{item_id}",
            headers=headers,
        )

        assert resp.status_code == 200
        with get_user_session_factory(user_id)() as db:
            remaining = db.scalar(
                select(MemoryItemEvidence).where(
                    MemoryItemEvidence.user_id == user_id,
                    MemoryItemEvidence.memory_item_id == item_id,
                )
            )
        assert remaining is None


def test_memory_create_records_item_evidence() -> None:
    with managed_test_client("anima-memory-test-") as client:
        reg = _register_user(client)
        user_id = int(reg["id"])
        headers = {"x-anima-unlock": reg["unlockToken"]}

        resp = client.post(
            f"/api/memory/{user_id}/items",
            headers=headers,
            json={"content": "Keeps provenance automatically", "category": "fact"},
        )

        assert resp.status_code == 201
        item_id = int(resp.json()["id"])
        with get_user_session_factory(user_id)() as db:
            evidence_rows = list(
                db.scalars(
                    select(MemoryItemEvidence).where(
                        MemoryItemEvidence.user_id == user_id,
                        MemoryItemEvidence.memory_item_id == item_id,
                    )
                ).all()
            )

        assert len(evidence_rows) == 1
        assert evidence_rows[0].source_kind == "explicit_save"
        assert evidence_rows[0].speaker == "user"
        assert evidence_rows[0].observed_at is not None
        assert evidence_rows[0].source_created_at is not None
        assert (
            df(
                user_id,
                evidence_rows[0].evidence_text,
                table="memory_item_evidence",
                field="evidence_text",
            )
            == "Keeps provenance automatically"
        )


def test_memory_create_defers_retrieval_index_upsert_until_after_commit(
    monkeypatch,
) -> None:
    upserts: list[dict[str, object]] = []
    monkeypatch.setattr(
        retrieval_module,
        "memory_index_upsert",
        lambda **kwargs: upserts.append(kwargs) or True,
    )

    with managed_test_client("anima-memory-test-") as client:
        reg = _register_user(client)
        user_id = int(reg["id"])
        headers = {"x-anima-unlock": reg["unlockToken"]}
        upserts.clear()

        def fail_commit(self: Session) -> None:
            raise RuntimeError("commit failed")

        monkeypatch.setattr(Session, "commit", fail_commit)

        with pytest.raises(RuntimeError, match="commit failed"):
            client.post(
                f"/api/memory/{user_id}/items",
                headers=headers,
                json={
                    "content": "Uncommitted create should not reach recall",
                    "category": "fact",
                },
            )

    assert upserts == []


def test_memory_update_records_replacement_evidence() -> None:
    with managed_test_client("anima-memory-test-") as client:
        reg = _register_user(client)
        user_id = int(reg["id"])
        headers = {"x-anima-unlock": reg["unlockToken"]}

        resp = client.post(
            f"/api/memory/{user_id}/items",
            headers=headers,
            json={"content": "Works as a designer", "category": "fact"},
        )
        assert resp.status_code == 201
        original_id = int(resp.json()["id"])

        resp = client.put(
            f"/api/memory/{user_id}/items/{original_id}",
            headers=headers,
            json={"content": "Works as a product manager"},
        )

        assert resp.status_code == 200
        replacement_id = int(resp.json()["id"])
        with get_user_session_factory(user_id)() as db:
            evidence = db.scalar(
                select(MemoryItemEvidence).where(
                    MemoryItemEvidence.user_id == user_id,
                    MemoryItemEvidence.memory_item_id == replacement_id,
                )
            )

        assert evidence is not None
        assert evidence.source_kind == "explicit_update"
        assert evidence.speaker == "user"
        assert evidence.observed_at is not None
        assert (
            df(
                user_id,
                evidence.evidence_text,
                table="memory_item_evidence",
                field="evidence_text",
            )
            == "Works as a product manager"
        )


def test_memory_update_defers_retrieval_index_changes_until_after_commit(
    monkeypatch,
) -> None:
    upserts: list[dict[str, object]] = []
    deletes: list[dict[str, object]] = []
    monkeypatch.setattr(
        retrieval_module,
        "memory_index_upsert",
        lambda **kwargs: upserts.append(kwargs) or True,
    )
    monkeypatch.setattr(
        retrieval_module,
        "memory_index_delete",
        lambda **kwargs: deletes.append(kwargs) or True,
    )

    with managed_test_client("anima-memory-test-") as client:
        reg = _register_user(client)
        user_id = int(reg["id"])
        headers = {"x-anima-unlock": reg["unlockToken"]}

        resp = client.post(
            f"/api/memory/{user_id}/items",
            headers=headers,
            json={"content": "Commit failure should keep old recall", "category": "fact"},
        )
        assert resp.status_code == 201
        item_id = int(resp.json()["id"])
        upserts.clear()
        deletes.clear()

        def fail_commit(self: Session) -> None:
            raise RuntimeError("commit failed")

        monkeypatch.setattr(Session, "commit", fail_commit)

        with pytest.raises(RuntimeError, match="commit failed"):
            client.put(
                f"/api/memory/{user_id}/items/{item_id}",
                headers=headers,
                json={"content": "Uncommitted replacement"},
            )

    assert upserts == []
    assert deletes == []


def test_memory_supersede_drops_deferred_retrieval_index_changes_on_rollback(
    monkeypatch,
) -> None:
    upserts: list[dict[str, object]] = []
    deletes: list[dict[str, object]] = []
    monkeypatch.setattr(
        retrieval_module,
        "memory_index_upsert",
        lambda **kwargs: upserts.append(kwargs) or True,
    )
    monkeypatch.setattr(
        retrieval_module,
        "memory_index_delete",
        lambda **kwargs: deletes.append(kwargs) or True,
    )

    with managed_test_client("anima-memory-test-") as client:
        reg = _register_user(client)
        user_id = int(reg["id"])
        headers = {"x-anima-unlock": reg["unlockToken"]}

        resp = client.post(
            f"/api/memory/{user_id}/items",
            headers=headers,
            json={"content": "Rollback should keep old recall", "category": "fact"},
        )
        assert resp.status_code == 201
        item_id = int(resp.json()["id"])
        upserts.clear()
        deletes.clear()

        from anima_server.services.agent.memory_store import supersede_memory_item

        with get_user_session_factory(user_id)() as db:
            supersede_memory_item(
                db,
                old_item_id=item_id,
                new_content="Rolled back replacement",
                evidence_text="Rolled back replacement",
                evidence_source_kind="explicit_update",
            )
            db.rollback()
            db.commit()

    assert upserts == []
    assert deletes == []


def test_memory_delete_defers_retrieval_index_cleanup_until_after_commit(
    monkeypatch,
) -> None:
    deletes: list[dict[str, object]] = []
    monkeypatch.setattr(
        retrieval_module,
        "memory_index_delete",
        lambda **kwargs: deletes.append(kwargs) or True,
    )

    with managed_test_client("anima-memory-test-") as client:
        reg = _register_user(client)
        user_id = int(reg["id"])
        headers = {"x-anima-unlock": reg["unlockToken"]}

        resp = client.post(
            f"/api/memory/{user_id}/items",
            headers=headers,
            json={"content": "Commit failure should keep recall indexed", "category": "fact"},
        )
        assert resp.status_code == 201
        item_id = int(resp.json()["id"])
        deletes.clear()

        def fail_commit(self: Session) -> None:
            raise RuntimeError("commit failed")

        monkeypatch.setattr(Session, "commit", fail_commit)

        with pytest.raises(RuntimeError, match="commit failed"):
            client.delete(
                f"/api/memory/{user_id}/items/{item_id}",
                headers=headers,
            )

    assert deletes == []


def test_memory_duplicate_rejected() -> None:
    with managed_test_client("anima-memory-test-") as client:
        reg = _register_user(client)
        user_id = reg["id"]
        headers = {"x-anima-unlock": reg["unlockToken"]}

        resp = client.post(
            f"/api/memory/{user_id}/items",
            headers=headers,
            json={"content": "Likes tea", "category": "preference"},
        )
        assert resp.status_code == 201

        resp = client.post(
            f"/api/memory/{user_id}/items",
            headers=headers,
            json={"content": "Likes tea", "category": "preference"},
        )
        assert resp.status_code == 409


def test_memory_requires_auth() -> None:
    with managed_test_client("anima-memory-test-") as client:
        resp = client.get("/api/memory/1", headers={})
        assert resp.status_code == 401


def test_memory_episodes_empty() -> None:
    with managed_test_client("anima-memory-test-") as client:
        reg = _register_user(client)
        user_id = reg["id"]
        headers = {"x-anima-unlock": reg["unlockToken"]}

        resp = client.get(f"/api/memory/{user_id}/episodes", headers=headers)
        assert resp.status_code == 200
        assert resp.json() == []


def test_memory_writes_update_rust_index(monkeypatch) -> None:
    upserts: list[dict[str, object]] = []
    deletes: list[dict[str, object]] = []

    monkeypatch.setattr(
        retrieval_module,
        "memory_index_upsert",
        lambda **kwargs: upserts.append(kwargs),
    )
    monkeypatch.setattr(
        retrieval_module,
        "memory_index_delete",
        lambda **kwargs: deletes.append(kwargs) or True,
    )

    with managed_test_client("anima-memory-test-") as client:
        reg = _register_user(client)
        user_id = reg["id"]
        headers = {"x-anima-unlock": reg["unlockToken"]}

        resp = client.post(
            f"/api/memory/{user_id}/items",
            headers=headers,
            json={"content": "Works as a designer", "category": "fact", "importance": 4},
        )
        assert resp.status_code == 201
        created = resp.json()

        assert len(upserts) == 1
        assert upserts[0]["record_id"] == created["id"]
        assert upserts[0]["text"] == "Works as a designer"

        resp = client.put(
            f"/api/memory/{user_id}/items/{created['id']}",
            headers=headers,
            json={"content": "Works as a product manager"},
        )
        assert resp.status_code == 200
        updated = resp.json()

        assert deletes[0]["record_id"] == created["id"]
        assert upserts[-1]["record_id"] == updated["id"]
        assert upserts[-1]["text"] == "Works as a product manager"

        resp = client.delete(
            f"/api/memory/{user_id}/items/{updated['id']}",
            headers=headers,
        )
        assert resp.status_code == 200
        assert deletes[-1]["record_id"] == updated["id"]


def test_memory_search_uses_rust_index_when_available(monkeypatch) -> None:
    search_calls: list[dict[str, object]] = []

    def _fake_search(**kwargs):
        search_calls.append(kwargs)
        return [
            {
                "record_id": kwargs.get("record_id", 2),
                "source_type": "memory_item",
                "category": "preference",
                "importance": 4,
                "created_at": 1_710_000_000,
                "score": 2.5,
            }
        ]

    monkeypatch.setattr(retrieval_module, "memory_index_search", _fake_search)

    with managed_test_client("anima-memory-test-") as client:
        reg = _register_user(client)
        user_id = reg["id"]
        headers = {"x-anima-unlock": reg["unlockToken"]}

        resp = client.post(
            f"/api/memory/{user_id}/items",
            headers=headers,
            json={"content": "Works as a designer", "category": "fact", "importance": 4},
        )
        assert resp.status_code == 201

        resp = client.post(
            f"/api/memory/{user_id}/items",
            headers=headers,
            json={"content": "Likes pour over coffee", "category": "preference", "importance": 4},
        )
        assert resp.status_code == 201
        coffee_item = resp.json()

        def _search_with_id(**kwargs):
            search_calls.append(kwargs)
            return [
                {
                    "record_id": coffee_item["id"],
                    "source_type": "memory_item",
                    "category": "preference",
                    "importance": 4,
                    "created_at": 1_710_000_000,
                    "score": 2.5,
                }
            ]

        monkeypatch.setattr(retrieval_module, "memory_index_search", _search_with_id)

        resp = client.get(
            f"/api/memory/{user_id}/search",
            headers=headers,
            params={"q": "coffee", "mode": "keyword"},
        )
        assert resp.status_code == 200
        payload = resp.json()

        assert len(search_calls) == 1
        assert search_calls[0]["query"] == "coffee"
        assert payload["count"] == 1
        assert payload["results"][0]["id"] == coffee_item["id"]


def test_memory_search_falls_back_when_rust_index_fails(monkeypatch) -> None:
    search_calls: list[dict[str, object]] = []

    def _failing_search(**kwargs):
        search_calls.append(kwargs)
        raise RuntimeError("retrieval index unavailable")

    monkeypatch.setattr(retrieval_module, "memory_index_search", _failing_search)

    with managed_test_client("anima-memory-test-") as client:
        reg = _register_user(client)
        user_id = reg["id"]
        headers = {"x-anima-unlock": reg["unlockToken"]}

        resp = client.post(
            f"/api/memory/{user_id}/items",
            headers=headers,
            json={"content": "Likes pour over coffee", "category": "preference", "importance": 4},
        )
        assert resp.status_code == 201

        resp = client.get(
            f"/api/memory/{user_id}/search",
            headers=headers,
            params={"q": "coffee", "mode": "keyword"},
        )
        assert resp.status_code == 200
        payload = resp.json()

        assert len(search_calls) == 1
        assert payload["count"] == 1
        assert payload["results"][0]["content"] == "Likes pour over coffee"


def test_memory_search_falls_back_when_memory_index_is_dirty(monkeypatch) -> None:
    search_calls: list[dict[str, object]] = []

    monkeypatch.setattr(
        retrieval_module,
        "is_retrieval_family_dirty",
        lambda **kwargs: True,
    )

    def _unexpected_search(**kwargs):
        search_calls.append(kwargs)
        raise AssertionError("dirty memory index should not be queried")

    monkeypatch.setattr(retrieval_module, "memory_index_search", _unexpected_search)

    with managed_test_client("anima-memory-test-") as client:
        reg = _register_user(client)
        user_id = reg["id"]
        headers = {"x-anima-unlock": reg["unlockToken"]}

        resp = client.post(
            f"/api/memory/{user_id}/items",
            headers=headers,
            json={"content": "Likes pour over coffee", "category": "preference", "importance": 4},
        )
        assert resp.status_code == 201

        resp = client.get(
            f"/api/memory/{user_id}/search",
            headers=headers,
            params={"q": "coffee", "mode": "keyword"},
        )
        assert resp.status_code == 200
        payload = resp.json()

        assert search_calls == []
        assert payload["count"] == 1
        assert payload["results"][0]["content"] == "Likes pour over coffee"


def test_memory_search_falls_back_when_rust_index_returns_no_hits(monkeypatch) -> None:
    search_calls: list[dict[str, object]] = []

    monkeypatch.setattr(
        retrieval_module,
        "memory_index_search",
        lambda **kwargs: search_calls.append(kwargs) or [],
    )

    with managed_test_client("anima-memory-test-") as client:
        reg = _register_user(client)
        user_id = reg["id"]
        headers = {"x-anima-unlock": reg["unlockToken"]}

        resp = client.post(
            f"/api/memory/{user_id}/items",
            headers=headers,
            json={"content": "Likes pour over coffee", "category": "preference", "importance": 4},
        )
        assert resp.status_code == 201

        resp = client.get(
            f"/api/memory/{user_id}/search",
            headers=headers,
            params={"q": "coffee", "mode": "keyword"},
        )
        assert resp.status_code == 200
        payload = resp.json()

        assert len(search_calls) == 1
        assert payload["count"] == 1
        assert payload["results"][0]["content"] == "Likes pour over coffee"


def test_memory_search_rebuilds_missing_rust_index_from_canonical() -> None:
    with managed_test_client("anima-memory-test-") as client:
        reg = _register_user(client)
        user_id = reg["id"]
        headers = {"x-anima-unlock": reg["unlockToken"]}

        resp = client.post(
            f"/api/memory/{user_id}/items",
            headers=headers,
            json={"content": "Likes pour over coffee", "category": "preference", "importance": 4},
        )
        assert resp.status_code == 201

        root = Path(retrieval_module.get_retrieval_root())
        docs_path = root / "memory" / "documents.json"
        assert docs_path.exists()
        docs_path.unlink()
        assert not docs_path.exists()

        resp = client.get(
            f"/api/memory/{user_id}/search",
            headers=headers,
            params={"q": "coffee", "mode": "keyword"},
        )
        assert resp.status_code == 200
        payload = resp.json()

        assert payload["count"] == 1
        assert payload["results"][0]["content"] == "Likes pour over coffee"
        assert docs_path.exists()
