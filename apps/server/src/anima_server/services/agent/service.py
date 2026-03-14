from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from threading import Lock

from anima_server.config import settings
from anima_server.services.agent.compaction import compact_thread_context
from anima_server.services.agent.llm import invalidate_llm_cache
from anima_server.services.agent.persistence import (
    append_user_message,
    count_messages_by_role,
    create_run,
    get_or_create_thread,
    load_thread_history,
    mark_run_failed,
    next_sequence_id,
    persist_agent_result,
    reset_thread,
)
from anima_server.services.agent.runtime import AgentRuntime, build_loop_runtime
from anima_server.services.agent.state import AgentResult
from anima_server.services.agent.system_prompt import invalidate_system_prompt_template_cache
from sqlalchemy.orm import Session

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


async def run_agent(user_message: str, user_id: int, db: Session) -> AgentResult:
    thread = get_or_create_thread(db, user_id)
    history = load_thread_history(db, thread.id)
    run = create_run(
        db,
        thread_id=thread.id,
        user_id=user_id,
        provider=settings.agent_provider,
        model=settings.agent_model,
        mode="blocking",
    )
    initial_sequence_id = next_sequence_id(db, thread.id)
    append_user_message(
        db,
        thread=thread,
        run_id=run.id,
        content=user_message,
        sequence_id=initial_sequence_id,
    )
    conversation_turn_count = count_messages_by_role(db, thread.id, "user")

    try:
        runner = get_or_build_runner()
        result = await runner.invoke(
            user_message,
            user_id,
            history,
            conversation_turn_count=conversation_turn_count,
        )
    except Exception as exc:
        mark_run_failed(db, run, str(exc))
        db.commit()
        raise

    persist_agent_result(
        db,
        thread=thread,
        run=run,
        result=result,
        initial_sequence_id=initial_sequence_id + 1,
    )
    compact_thread_context(
        db,
        thread=thread,
        run_id=run.id,
        trigger_token_limit=max(
            1,
            int(settings.agent_max_tokens * settings.agent_compaction_trigger_ratio),
        ),
        keep_last_messages=max(1, settings.agent_compaction_keep_last_messages),
    )
    db.commit()
    return result


async def stream_agent(
    user_message: str,
    user_id: int,
    db: Session,
) -> AsyncGenerator[str, None]:
    result = await run_agent(user_message, user_id, db)
    chunk_size = max(1, settings.agent_stream_chunk_size)
    for start in range(0, len(result.response), chunk_size):
        await asyncio.sleep(0)
        yield result.response[start : start + chunk_size]


async def reset_agent_thread(user_id: int, db: Session) -> None:
    reset_thread(db, user_id)
    db.commit()
