"""ARH-011: TTFT — parallel assembly, single-decrypt retrieval, and
concurrent delegated tools."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest
from anima_server.db.base import Base
from anima_server.models import MemoryItem, User
from anima_server.services.agent import embeddings
from anima_server.services.agent.executor import ToolExecutor
from anima_server.services.agent.runtime_types import ToolCall
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture()
def soul_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as db:
        db.add(User(username="ttft", password_hash="x", display_name="T"))
        db.commit()
    yield factory
    engine.dispose()


# --------------------------------------------------------------------------- #
# Single-decrypt retrieval
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_hybrid_search_decrypts_each_item_exactly_once(
    soul_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pipeline used to decrypt every result 2-3 times before first
    token (keyword leg, independent rerank, fragment building)."""
    with soul_factory() as db:
        items = [
            MemoryItem(
                user_id=1,
                content=f"Likes {flavour} tea in the morning",
                category="preference",
                importance=3,
                source="extraction",
            )
            for flavour in ("green", "black", "oolong")
        ]
        db.add_all(items)
        db.commit()
        item_ids = [item.id for item in items]

    async def fake_embedding(text: str) -> list[float]:
        return [0.1] * 8

    monkeypatch.setattr(embeddings, "generate_embedding", fake_embedding)
    monkeypatch.setattr(
        embeddings,
        "_semantic_ranked_ids",
        lambda *args, **kwargs: [(item_ids[0], 0.9), (item_ids[1], 0.7)],
    )
    from anima_server.services.agent import bm25_index

    monkeypatch.setattr(
        bm25_index,
        "bm25_search",
        lambda *args, **kwargs: [(item_ids[1], 4.2), (item_ids[2], 2.1)],
    )

    decrypt_counts: dict[int, int] = {}
    real_df = embeddings.df

    def counting_df(user_id, value, **kwargs):
        if kwargs.get("table") == "memory_items":
            # Track per-call; value is the ciphertext, so count invocations.
            decrypt_counts["total"] = decrypt_counts.get("total", 0) + 1
        return real_df(user_id, value, **kwargs)

    monkeypatch.setattr(embeddings, "df", counting_df)

    with soul_factory() as db:
        result = await embeddings.hybrid_search(
            db,
            user_id=1,
            query="tea in the morning",
            limit=10,
        )

    assert result.items
    assert result.plaintexts is not None
    for item, _score in result.items:
        assert item.id in result.plaintexts
        assert "tea" in result.plaintexts[item.id]
    # One decrypt per surviving item — not one per rerank pass.
    assert decrypt_counts["total"] == len(result.items)


def test_blend_keyword_scores_reuses_leg_scores() -> None:
    item_a = SimpleNamespace(id=1)
    item_b = SimpleNamespace(id=2)
    results = [(item_a, 0.030), (item_b, 0.029)]  # near-tied RRF
    keyword_ranked = [(2, 9.0)]  # strong lexical hit for item_b only

    blended = embeddings._blend_keyword_scores(results, keyword_ranked)

    assert [item.id for item, _ in blended] == [2, 1]

    # Without keyword scores the ranking is untouched.
    assert embeddings._blend_keyword_scores(results, []) == results


# --------------------------------------------------------------------------- #
# Delegated tools run concurrently; server tools stay sequential
# --------------------------------------------------------------------------- #


def _tool_call(name: str, call_id: str) -> ToolCall:
    return ToolCall(id=call_id, name=name, arguments={})


@pytest.mark.asyncio
async def test_delegated_tools_run_concurrently() -> None:
    """Two slow client tools complete in ~max, not ~sum, duration."""

    async def slow_delegate(call_id: str, name: str, args: dict):
        await asyncio.sleep(0.2)
        return SimpleNamespace(output=f"{name} done", is_error=False)

    executor = ToolExecutor(
        [],
        delegate=slow_delegate,
        delegated_tool_names=frozenset({"client_a", "client_b"}),
    )

    started = time.monotonic()
    results = await executor.execute_parallel(
        [(_tool_call("client_a", "c1"), False), (_tool_call("client_b", "c2"), False)]
    )
    elapsed = time.monotonic() - started

    assert [r.call_id for r in results] == ["c1", "c2"]
    assert all(not r.is_error for r in results)
    assert elapsed < 0.35  # sequential would be ≥ 0.4


@pytest.mark.asyncio
async def test_server_tools_stay_sequential() -> None:
    concurrency = {"current": 0, "max": 0}

    class _ServerTool:
        name = "server_tool"

        async def ainvoke(self, payload):
            concurrency["current"] += 1
            concurrency["max"] = max(concurrency["max"], concurrency["current"])
            await asyncio.sleep(0.05)
            concurrency["current"] -= 1
            return "ok"

    executor = ToolExecutor([_ServerTool()])
    results = await executor.execute_parallel(
        [(_tool_call("server_tool", "s1"), False), (_tool_call("server_tool", "s2"), False)]
    )

    assert len(results) == 2
    assert concurrency["max"] == 1  # shared session: never concurrent


# --------------------------------------------------------------------------- #
# Feedback signals off the critical path
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_feedback_processing_runs_with_its_own_sessions(
    soul_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    from anima_server.services.agent import feedback_signals
    from anima_server.services.agent.service import (
        _process_feedback_signals_background,
    )

    calls: dict[str, object] = {}

    def fake_collect(**kwargs):
        calls["collected"] = True
        return [SimpleNamespace(signal_type="correction")]

    def fake_record(db, **kwargs):
        calls["recorded"] = True

    def fake_apply(db, **kwargs):
        calls["applied"] = True

    monkeypatch.setattr(feedback_signals, "collect_feedback_signals", fake_collect)
    monkeypatch.setattr(feedback_signals, "record_feedback_signals", fake_record)
    monkeypatch.setattr(feedback_signals, "apply_memory_correction", fake_apply)

    await _process_feedback_signals_background(
        user_id=1,
        user_message="actually I prefer black tea",
        thread_id=5,
        soul_db_factory=soul_factory,
        runtime_db_factory=soul_factory,
    )

    assert calls == {"collected": True, "recorded": True, "applied": True}
