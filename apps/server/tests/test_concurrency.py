from __future__ import annotations

import asyncio

import pytest
from anima_server.services.agent import service as agent_service
from anima_server.services.agent import turn_coordinator
from anima_server.services.agent.state import AgentResult


@pytest.fixture(autouse=True)
def _reset_locks() -> None:
    with turn_coordinator._global_lock:
        turn_coordinator._thread_locks.clear()


def _result(label: str) -> AgentResult:
    return AgentResult(
        response=label,
        model="test-model",
        provider="test-provider",
        stop_reason="end_turn",
    )


@pytest.mark.asyncio
async def test_execute_agent_turn_allows_different_threads_to_overlap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_started = asyncio.Event()
    second_started = asyncio.Event()
    release = asyncio.Event()

    async def fake_execute_agent_turn_locked(
        user_message: str,
        user_id: int,
        db: object,
        runtime_db: object,
        **kwargs: object,
    ) -> AgentResult:
        del user_id, db, runtime_db, kwargs
        if user_message == "first":
            first_started.set()
        else:
            second_started.set()
        await release.wait()
        return _result(user_message)

    monkeypatch.setattr(
        agent_service,
        "_execute_agent_turn_locked",
        fake_execute_agent_turn_locked,
    )

    task_one = asyncio.create_task(
        agent_service._execute_agent_turn(
            "first",
            1,
            object(),
            object(),
            thread_id=101,
        )
    )
    await first_started.wait()

    task_two = asyncio.create_task(
        agent_service._execute_agent_turn(
            "second",
            1,
            object(),
            object(),
            thread_id=202,
        )
    )

    await asyncio.wait_for(second_started.wait(), timeout=0.1)
    release.set()

    result_one, result_two = await asyncio.gather(task_one, task_two)
    assert result_one.response == "first"
    assert result_two.response == "second"


@pytest.mark.asyncio
async def test_execute_agent_turn_serializes_same_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_started = asyncio.Event()
    second_started = asyncio.Event()
    release_first = asyncio.Event()

    async def fake_execute_agent_turn_locked(
        user_message: str,
        user_id: int,
        db: object,
        runtime_db: object,
        **kwargs: object,
    ) -> AgentResult:
        del user_id, db, runtime_db, kwargs
        if user_message == "first":
            first_started.set()
            await release_first.wait()
        else:
            second_started.set()
        return _result(user_message)

    monkeypatch.setattr(
        agent_service,
        "_execute_agent_turn_locked",
        fake_execute_agent_turn_locked,
    )

    task_one = asyncio.create_task(
        agent_service._execute_agent_turn(
            "first",
            1,
            object(),
            object(),
            thread_id=303,
        )
    )
    await first_started.wait()

    task_two = asyncio.create_task(
        agent_service._execute_agent_turn(
            "second",
            1,
            object(),
            object(),
            thread_id=303,
        )
    )

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(second_started.wait(), timeout=0.05)

    release_first.set()
    await asyncio.wait_for(second_started.wait(), timeout=0.1)

    result_one, result_two = await asyncio.gather(task_one, task_two)
    assert result_one.response == "first"
    assert result_two.response == "second"


@pytest.mark.asyncio
async def test_execute_agent_turn_resolves_thread_id_when_not_provided(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved: list[tuple[int, object]] = []

    def fake_resolve_thread_id(user_id: int, runtime_db: object) -> int:
        resolved.append((user_id, runtime_db))
        return 404

    async def fake_execute_agent_turn_locked(
        user_message: str,
        user_id: int,
        db: object,
        runtime_db: object,
        **kwargs: object,
    ) -> AgentResult:
        del user_id, db, runtime_db, kwargs
        return _result(user_message)

    monkeypatch.setattr(agent_service, "_resolve_thread_id", fake_resolve_thread_id)
    monkeypatch.setattr(
        agent_service,
        "_execute_agent_turn_locked",
        fake_execute_agent_turn_locked,
    )

    runtime_db = object()
    result = await agent_service._execute_agent_turn(
        "single-thread",
        9,
        object(),
        runtime_db,
    )

    assert result.response == "single-thread"
    assert resolved == [(9, runtime_db)]
    assert 404 in turn_coordinator._thread_locks

