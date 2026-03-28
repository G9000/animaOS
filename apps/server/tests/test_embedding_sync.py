from __future__ import annotations

import sys
from types import SimpleNamespace

from anima_server.services.agent.embeddings import sync_embeddings_to_runtime


def _memory_item(
    *,
    item_id: int,
    user_id: int,
    embedding_json: object,
    content: str = "encrypted",
    category: str = "fact",
    importance: int = 3,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=item_id,
        user_id=user_id,
        embedding_json=embedding_json,
        content=content,
        category=category,
        importance=importance,
    )


def test_sync_returns_zero_when_no_items(monkeypatch) -> None:
    soul_db = SimpleNamespace(
        scalars=lambda _stmt: SimpleNamespace(all=lambda: []),
    )

    import anima_server.db.runtime as runtime_mod

    monkeypatch.setattr(
        runtime_mod,
        "get_runtime_session_factory",
        lambda: (_ for _ in ()).throw(AssertionError("runtime session should not be requested")),
    )

    assert sync_embeddings_to_runtime(soul_db, user_id=1) == 0


def test_sync_returns_negative_when_runtime_is_unavailable(monkeypatch) -> None:
    soul_db = SimpleNamespace(
        scalars=lambda _stmt: SimpleNamespace(
            all=lambda: [_memory_item(item_id=1, user_id=7, embedding_json=[0.1, 0.2])]
        ),
    )

    import anima_server.db.runtime as runtime_mod

    monkeypatch.setattr(
        runtime_mod,
        "get_runtime_session_factory",
        lambda: (_ for _ in ()).throw(RuntimeError("runtime unavailable")),
    )

    assert sync_embeddings_to_runtime(soul_db, user_id=7) == -1


def test_sync_upserts_valid_embeddings_and_skips_invalid(monkeypatch) -> None:
    runtime_session = SimpleNamespace(
        committed=False,
        rolled_back=False,
        closed=False,
    )

    def _commit() -> None:
        runtime_session.committed = True

    def _rollback() -> None:
        runtime_session.rolled_back = True

    def _close() -> None:
        runtime_session.closed = True

    runtime_session.commit = _commit
    runtime_session.rollback = _rollback
    runtime_session.close = _close

    items = [
        _memory_item(
            item_id=11,
            user_id=5,
            embedding_json=[0.1, 0.2, 0.3],
            content="ciphertext-1",
            category="fact",
            importance=4,
        ),
        _memory_item(
            item_id=12,
            user_id=5,
            embedding_json="not-json",
            content="ciphertext-2",
            category="preference",
            importance=2,
        ),
    ]
    soul_db = SimpleNamespace(
        scalars=lambda _stmt: SimpleNamespace(all=lambda: items),
    )

    import anima_server.db.runtime as runtime_mod

    monkeypatch.setattr(runtime_mod, "get_runtime_session_factory", lambda: lambda: runtime_session)
    monkeypatch.setattr(
        "anima_server.services.agent.embeddings.df",
        lambda user_id, content, *, table, field: f"plain:{user_id}:{content}:{table}:{field}",
    )

    upsert_calls: list[dict[str, object]] = []

    class FakePgVecStore:
        def __init__(self, db) -> None:
            assert db is runtime_session

        def upsert(
            self,
            user_id: int,
            *,
            item_id: int,
            content: str,
            embedding: list[float],
            category: str = "fact",
            importance: int = 3,
        ) -> None:
            upsert_calls.append(
                {
                    "user_id": user_id,
                    "item_id": item_id,
                    "content": content,
                    "embedding": embedding,
                    "category": category,
                    "importance": importance,
                }
            )

    monkeypatch.setitem(
        sys.modules,
        "anima_server.services.agent.pgvec_store",
        SimpleNamespace(PgVecStore=FakePgVecStore),
    )

    count = sync_embeddings_to_runtime(soul_db, user_id=5)

    assert count == 1
    assert upsert_calls == [
        {
            "user_id": 5,
            "item_id": 11,
            "content": "plain:5:ciphertext-1:memory_items:content",
            "embedding": [0.1, 0.2, 0.3],
            "category": "fact",
            "importance": 4,
        }
    ]
    assert runtime_session.committed is True
    assert runtime_session.rolled_back is False
    assert runtime_session.closed is True
