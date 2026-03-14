from __future__ import annotations

from dataclasses import dataclass
from math import ceil
import json

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from anima_server.models import AgentMessage, AgentThread

SUMMARY_LINE_LIMIT = 12
SUMMARY_TEXT_LIMIT = 180


@dataclass(frozen=True, slots=True)
class CompactionResult:
    compacted_message_count: int
    kept_message_count: int
    summary_sequence_id: int
    estimated_tokens_before: int
    estimated_tokens_after: int


def estimate_message_tokens(
    *,
    content_text: str | None,
    content_json: dict[str, object] | None = None,
    tool_name: str | None = None,
) -> int:
    text_parts: list[str] = []
    if tool_name:
        text_parts.append(tool_name)
    if content_text:
        text_parts.append(content_text)
    if content_json:
        text_parts.append(json.dumps(content_json, sort_keys=True))

    combined_text = "\n".join(text_parts).strip()
    if not combined_text:
        return 0
    return max(1, ceil(len(combined_text) / 4))


def compact_thread_context(
    db: Session,
    *,
    thread: AgentThread,
    run_id: int | None,
    trigger_token_limit: int,
    keep_last_messages: int,
) -> CompactionResult | None:
    in_context_rows = db.scalars(
        select(AgentMessage)
        .where(
            AgentMessage.thread_id == thread.id,
            AgentMessage.is_in_context.is_(True),
            AgentMessage.role.in_(("summary", "user", "assistant", "tool")),
        )
        .order_by(
            case((AgentMessage.role == "summary", 0), else_=1),
            AgentMessage.sequence_id,
        )
    ).all()

    if not in_context_rows:
        return None

    estimated_tokens_before = sum(
        row.token_estimate
        if row.token_estimate is not None
        else estimate_message_tokens(
            content_text=row.content_text,
            content_json=row.content_json,
            tool_name=row.tool_name,
        )
        for row in in_context_rows
    )
    if estimated_tokens_before <= trigger_token_limit:
        return None

    existing_summary_rows = [row for row in in_context_rows if row.role == "summary"]
    conversation_rows = [row for row in in_context_rows if row.role != "summary"]
    if len(conversation_rows) <= keep_last_messages:
        return None

    compacted_rows = conversation_rows[:-keep_last_messages]
    kept_rows = conversation_rows[-keep_last_messages:]
    if not compacted_rows:
        return None

    summary_text = render_summary_text(existing_summary_rows, compacted_rows)

    for row in existing_summary_rows:
        row.is_in_context = False
        db.add(row)

    for row in compacted_rows:
        row.is_in_context = False
        db.add(row)

    summary_sequence_id = _next_sequence_id(db, thread.id)
    summary_message = AgentMessage(
        thread_id=thread.id,
        run_id=run_id,
        step_id=None,
        sequence_id=summary_sequence_id,
        role="summary",
        content_text=summary_text,
        content_json={
            "compacted_message_count": len(compacted_rows),
            "source_sequence_end": compacted_rows[-1].sequence_id,
        },
        is_in_context=True,
        token_estimate=estimate_message_tokens(
            content_text=summary_text,
            content_json=None,
            tool_name=None,
        ),
    )
    db.add(summary_message)
    db.flush()

    estimated_tokens_after = summary_message.token_estimate or 0
    estimated_tokens_after += sum(
        row.token_estimate
        if row.token_estimate is not None
        else estimate_message_tokens(
            content_text=row.content_text,
            content_json=row.content_json,
            tool_name=row.tool_name,
        )
        for row in kept_rows
    )

    return CompactionResult(
        compacted_message_count=len(compacted_rows),
        kept_message_count=len(kept_rows),
        summary_sequence_id=summary_sequence_id,
        estimated_tokens_before=estimated_tokens_before,
        estimated_tokens_after=estimated_tokens_after,
    )


def render_summary_text(
    summary_rows: list[AgentMessage],
    compacted_rows: list[AgentMessage],
) -> str:
    lines: list[str] = ["Conversation summary:"]

    for summary_row in summary_rows:
        content = (summary_row.content_text or "").strip()
        if not content:
            continue
        compact_content = _trim_summary_text(content)
        if compact_content:
            lines.append(f"- Earlier summary: {compact_content}")
            break

    for row in compacted_rows[:SUMMARY_LINE_LIMIT]:
        line = _summarize_row(row)
        if line:
            lines.append(f"- {line}")

    hidden_count = len(compacted_rows) - min(len(compacted_rows), SUMMARY_LINE_LIMIT)
    if hidden_count > 0:
        lines.append(f"- {hidden_count} additional earlier messages were compacted.")

    return "\n".join(lines)


def _summarize_row(row: AgentMessage) -> str:
    role_label = "Tool" if row.role == "tool" else row.role.capitalize()
    if row.role == "tool" and row.tool_name:
        role_label = f"Tool {row.tool_name}"

    content = _trim_summary_text(row.content_text or "")
    if not content:
        return f"{role_label}: [empty]"
    return f"{role_label}: {content}"


def _trim_summary_text(text: str) -> str:
    normalized = " ".join(text.strip().split())
    if len(normalized) <= SUMMARY_TEXT_LIMIT:
        return normalized
    return f"{normalized[: SUMMARY_TEXT_LIMIT - 3].rstrip()}..."


def _next_sequence_id(db: Session, thread_id: int) -> int:
    max_sequence = db.scalar(
        select(func.max(AgentMessage.sequence_id)).where(AgentMessage.thread_id == thread_id)
    )
    return (int(max_sequence) if max_sequence is not None else 0) + 1
