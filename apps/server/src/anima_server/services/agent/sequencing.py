from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from anima_server.models.runtime import RuntimeThread
from anima_server.services.agent.state import AgentResult


def reserve_message_sequences(
    db: Session,
    *,
    thread_id: int,
    count: int,
) -> int:
    """Reserve a contiguous sequence range for a thread and return its start."""
    if count < 1:
        raise ValueError("count must be at least 1")

    row = db.execute(
        select(RuntimeThread.next_message_sequence)
        .where(RuntimeThread.id == thread_id)
        .with_for_update()
    ).scalar_one()

    start = int(row)
    db.execute(
        update(RuntimeThread)
        .where(RuntimeThread.id == thread_id)
        .values(next_message_sequence=start + count)
    )
    return start


def count_persisted_result_messages(result: AgentResult) -> int:
    count = 0
    for trace in result.step_traces:
        if trace.assistant_text or trace.tool_calls:
            count += 1
        count += len(trace.tool_results)
    return count
