from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from threading import Lock

from anima_server.config import settings
from anima_server.services.agent.llm import invalidate_llm_cache
from anima_server.services.agent.runtime import AgentRuntime, build_loop_runtime
from anima_server.services.agent.state import AgentResult
from anima_server.services.agent.store import thread_store
from anima_server.services.agent.system_prompt import invalidate_system_prompt_template_cache

_runner_lock = Lock()
_cached_runner: AgentRuntime | None = None


def get_or_build_runner() -> AgentRuntime:
    global _cached_runner
    if _cached_runner is not None:
        return _cached_runner

    with _runner_lock:
        if _cached_runner is None:
            _cached_runner = build_loop_runtime()
        return _cached_runner


def ensure_agent_ready() -> None:
    runner = get_or_build_runner()
    runner.prepare_system_prompt()


def invalidate_agent_runtime_cache() -> None:
    global _cached_runner
    with _runner_lock:
        _cached_runner = None
    invalidate_llm_cache()
    invalidate_system_prompt_template_cache()


def clear_agent_threads() -> None:
    thread_store.clear()


async def run_agent(user_message: str, user_id: int) -> AgentResult:
    history = thread_store.read(user_id)
    runner = get_or_build_runner()
    result = await runner.invoke(user_message, user_id, history)
    thread_store.append_turn(user_id, user_message, result.response)
    return result


async def stream_agent(
    user_message: str,
    user_id: int,
) -> AsyncGenerator[str, None]:
    result = await run_agent(user_message, user_id)
    chunk_size = max(1, settings.agent_stream_chunk_size)
    for start in range(0, len(result.response), chunk_size):
        await asyncio.sleep(0)
        yield result.response[start : start + chunk_size]


async def reset_agent_thread(user_id: int) -> None:
    thread_store.reset(user_id)
