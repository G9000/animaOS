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
from anima_server.services.agent.delegation import DelegatedToolResult
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

    # Without keyword scores the ranking order is untouched, but the RRF
    # scores are still rescaled to [0, 1] so a semantic-only top hit survives
    # the downstream raw absolute_min gate (raw RRF ~1/(k+rank) < 0.02).
    semantic_only = embeddings._blend_keyword_scores(results, [])
    assert [item.id for item, _ in semantic_only] == [1, 2]  # order preserved
    assert semantic_only[0][1] == 1.0  # top normalized to 1.0
    assert 0.9 < semantic_only[1][1] < 1.0


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
async def test_mixed_tool_execution_preserves_ordering_barriers() -> None:
    events: list[str] = []
    both_clients_started = asyncio.Event()
    release_clients = asyncio.Event()
    started_count = 0

    class ServerTool:
        def __init__(self, name: str) -> None:
            self.name = name

        async def ainvoke(self, payload: dict) -> str:
            del payload
            events.append(f"{self.name}:start")
            await asyncio.sleep(0)
            events.append(f"{self.name}:finish")
            return self.name

    async def delegate(
        call_id: str,
        name: str,
        args: dict,
    ) -> DelegatedToolResult:
        nonlocal started_count
        del args
        events.append(f"{name}:start")
        started_count += 1
        if started_count == 2:
            both_clients_started.set()
        await release_clients.wait()
        events.append(f"{name}:finish")
        return DelegatedToolResult(call_id=call_id, name=name, output=name)

    executor = ToolExecutor(
        [ServerTool("server_before"), ServerTool("server_after")],
        delegate=delegate,
        delegated_tool_names=frozenset({"client_a", "client_b"}),
    )
    task = asyncio.create_task(
        executor.execute_parallel(
            [
                (_tool_call("server_before", "s1"), False),
                (_tool_call("client_a", "c1"), False),
                (_tool_call("client_b", "c2"), False),
                (_tool_call("server_after", "s2"), False),
            ]
        )
    )
    try:
        await asyncio.wait_for(both_clients_started.wait(), timeout=1)
        assert events[:2] == ["server_before:start", "server_before:finish"]
        assert {"client_a:start", "client_b:start"}.issubset(events)
        assert "server_after:start" not in events
    finally:
        release_clients.set()
        results = await asyncio.wait_for(task, timeout=1)

    assert [result.call_id for result in results] == ["s1", "c1", "c2", "s2"]
    assert events[-2:] == ["server_after:start", "server_after:finish"]


@pytest.mark.asyncio
async def test_delegated_error_result_continues_to_later_server_barrier() -> None:
    events: list[str] = []

    class ServerTool:
        name = "server_after"

        async def ainvoke(self, payload: dict) -> str:
            del payload
            events.append("server_after:start")
            await asyncio.sleep(0)
            events.append("server_after:finish")
            return "server_after"

    async def delegate(
        call_id: str,
        name: str,
        args: dict,
    ) -> DelegatedToolResult:
        del args
        events.append(name)
        if name == "client_fail":
            raise RuntimeError("delegated failure")
        return DelegatedToolResult(call_id=call_id, name=name, output=name)

    executor = ToolExecutor(
        [ServerTool()],
        delegate=delegate,
        delegated_tool_names=frozenset({"client_fail", "client_ok"}),
    )
    results = await executor.execute_parallel(
        [
            (_tool_call("client_fail", "c1"), False),
            (_tool_call("client_ok", "c2"), False),
            (_tool_call("server_after", "s1"), False),
        ]
    )

    assert [result.call_id for result in results] == ["c1", "c2", "s1"]
    assert results[0].is_error is True
    assert results[1].is_error is False
    assert set(events[:2]) == {"client_fail", "client_ok"}
    assert events[-2:] == ["server_after:start", "server_after:finish"]


@pytest.mark.asyncio
async def test_execute_parallel_cancellation_skips_later_server_barrier() -> None:
    events: list[str] = []
    both_clients_started = asyncio.Event()
    never_release = asyncio.Event()
    started_count = 0

    class ServerTool:
        name = "server_after"

        async def ainvoke(self, payload: dict) -> str:
            del payload
            events.append("server_after:start")
            return "server_after"

    async def delegate(
        call_id: str,
        name: str,
        args: dict,
    ) -> DelegatedToolResult:
        nonlocal started_count
        del args
        events.append(f"{name}:start")
        started_count += 1
        if started_count == 2:
            both_clients_started.set()
        await never_release.wait()
        return DelegatedToolResult(call_id=call_id, name=name, output=name)

    executor = ToolExecutor(
        [ServerTool()],
        delegate=delegate,
        delegated_tool_names=frozenset({"client_a", "client_b"}),
    )
    task = asyncio.create_task(
        executor.execute_parallel(
            [
                (_tool_call("client_a", "c1"), False),
                (_tool_call("client_b", "c2"), False),
                (_tool_call("server_after", "s1"), False),
            ]
        )
    )
    try:
        await asyncio.wait_for(both_clients_started.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=1)
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert "server_after:start" not in events


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
