from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from math import ceil

from sqlalchemy import case, select
from sqlalchemy.orm import Session

from anima_server.models.runtime import RuntimeMessage, RuntimeThread
from anima_server.services.agent.sequencing import reserve_message_sequences

logger = logging.getLogger(__name__)

SUMMARY_LINE_LIMIT = 12
SUMMARY_TEXT_LIMIT = 180

# Summarization prompt is now in Jinja2 template: summarization.md.j2
# Use PromptLoader.summarization() instead.

# Cascade: max chars for the LLM summarization input transcript
_MAX_TRANSCRIPT_CHARS = 12000
# Tool return content cap during clamping fallback
_TOOL_CONTENT_CLAMP = 100


@dataclass(frozen=True, slots=True)
class CompactionResult:
    compacted_message_count: int
    kept_message_count: int
    summary_sequence_id: int
    estimated_tokens_before: int
    estimated_tokens_after: int
    reserved_prompt_tokens: int
    effective_trigger_token_limit: int


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
    thread: RuntimeThread,
    run_id: int | None,
    trigger_token_limit: int,
    keep_last_messages: int,
    reserved_prompt_tokens: int = 0,
) -> CompactionResult | None:
    in_context_rows = db.scalars(
        select(RuntimeMessage)
        .where(
            RuntimeMessage.thread_id == thread.id,
            RuntimeMessage.is_in_context.is_(True),
            RuntimeMessage.role.in_(("summary", "user", "assistant", "tool")),
        )
        .order_by(
            case((RuntimeMessage.role == "summary", 0), else_=1),
            RuntimeMessage.sequence_id,
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
    effective_trigger_token_limit = max(
        1,
        trigger_token_limit - max(0, reserved_prompt_tokens),
    )
    if estimated_tokens_before <= effective_trigger_token_limit:
        return None

    existing_summary_rows = [row for row in in_context_rows if row.role == "summary"]
    conversation_rows = [row for row in in_context_rows if row.role != "summary"]
    if len(conversation_rows) <= keep_last_messages:
        return None

    compacted_rows = conversation_rows[:-keep_last_messages]
    kept_rows = conversation_rows[-keep_last_messages:]
    if not compacted_rows:
        return None

    summary_text = render_summary_text(
        existing_summary_rows, compacted_rows, user_id=thread.user_id
    )

    for row in existing_summary_rows:
        row.is_in_context = False
        db.add(row)

    for row in compacted_rows:
        row.is_in_context = False
        db.add(row)

    summary_sequence_id = reserve_message_sequences(
        db,
        thread_id=thread.id,
        count=1,
    )
    summary_message = RuntimeMessage(
        thread_id=thread.id,
        user_id=thread.user_id,
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
        reserved_prompt_tokens=max(0, reserved_prompt_tokens),
        effective_trigger_token_limit=effective_trigger_token_limit,
    )


def render_summary_text(
    summary_rows: list[RuntimeMessage],
    compacted_rows: list[RuntimeMessage],
    *,
    user_id: int = 0,
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
        line = _summarize_row(row, user_id=user_id)
        if line:
            lines.append(f"- {line}")

    hidden_count = len(compacted_rows) - min(len(compacted_rows), SUMMARY_LINE_LIMIT)
    if hidden_count > 0:
        lines.append(f"- {hidden_count} additional earlier messages were compacted.")

    return "\n".join(lines)


def _summarize_row(row: RuntimeMessage, *, user_id: int = 0) -> str:
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


# ---------------------------------------------------------------------------
# LLM-powered summarization (Phase 2 upgrade)
# ---------------------------------------------------------------------------


def _build_transcript(rows: list[RuntimeMessage], *, clamp_tools: bool = False) -> str:
    """Build a plain-text transcript from message rows for LLM summarization."""
    lines: list[str] = []
    for row in rows:
        role_label = row.role.capitalize()
        if row.role == "tool" and row.tool_name:
            role_label = f"Tool({row.tool_name})"

        content = (row.content_text or "").strip()

        # Skip tool-call wrapper assistant messages (these have tool_calls
        # in content_json and may have empty or minimal text).
        if (
            row.role == "assistant"
            and isinstance(row.content_json, dict)
            and "tool_calls" in row.content_json
        ):
            continue

        if not content:
            continue

        if clamp_tools and row.role == "tool" and len(content) > _TOOL_CONTENT_CLAMP:
            content = content[:_TOOL_CONTENT_CLAMP] + "..."

        lines.append(f"{role_label}: {content}")
    return "\n".join(lines)


async def summarize_with_llm(
    rows: list[RuntimeMessage],
    *,
    transcript_override: str | None = None,
) -> str | None:
    """Attempt to summarize messages using the configured LLM.

    Returns the summary text on success, or None if the LLM call fails.
    Uses a lightweight model when configured (agent_extraction_model) to
    keep costs low, otherwise falls back to the primary model.

    If *transcript_override* is given it is used instead of building a
    fresh transcript from *rows* (used by the Level-2 cascade to pass a
    tool-content-clamped version).
    """
    from anima_server.config import settings
    from anima_server.services.agent.llm import (
        build_provider_headers,
        resolve_base_url,
    )

    provider = settings.agent_provider
    if provider == "scaffold":
        return None

    transcript = transcript_override or _build_transcript(rows)
    if not transcript.strip():
        return None

    # Clamp transcript length to avoid blowing up the summarizer's context
    if len(transcript) > _MAX_TRANSCRIPT_CHARS:
        transcript = transcript[:_MAX_TRANSCRIPT_CHARS] + "\n[...truncated]"

    from anima_server.services.agent.prompt_loader import PromptLoader

    prompt_loader = PromptLoader(agent_name="Anima")
    prompt_text = prompt_loader.summarization(transcript=transcript)

    # Prefer extraction model (cheaper) if configured, else use primary.
    model = settings.agent_extraction_model.strip() or settings.agent_model
    base_url = resolve_base_url(provider)
    headers = build_provider_headers(provider)
    headers["Content-Type"] = "application/json"

    try:
        import httpx

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{base_url}/chat/completions",
                headers=headers,
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": "You are a concise conversation summarizer."},
                        {"role": "user", "content": prompt_text},
                    ],
                    "max_tokens": 500,
                    "temperature": 0.3,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            choices = data.get("choices", [])
            if choices:
                message = choices[0].get("message", {})
                summary = message.get("content", "").strip()
                if summary:
                    return summary
    except Exception:
        logger.debug("LLM summarization failed, will use fallback", exc_info=True)

    return None


async def compact_thread_context_with_llm(
    db: Session,
    *,
    thread: RuntimeThread,
    run_id: int | None,
    trigger_token_limit: int,
    keep_last_messages: int,
    reserved_prompt_tokens: int = 0,
) -> CompactionResult | None:
    """LLM-enhanced compaction with cascade fallback.

    Cascade:
    1. Full LLM summarization of compacted messages
    2. LLM summarization with tool content clamped to 100 chars
    3. Fast text-based summary (original render_summary_text)

    Falls back to the next level on any failure.
    """
    # Use the same initial logic as compact_thread_context to find rows
    in_context_rows = db.scalars(
        select(RuntimeMessage)
        .where(
            RuntimeMessage.thread_id == thread.id,
            RuntimeMessage.is_in_context.is_(True),
            RuntimeMessage.role.in_(("summary", "user", "assistant", "tool")),
        )
        .order_by(
            case((RuntimeMessage.role == "summary", 0), else_=1),
            RuntimeMessage.sequence_id,
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
    effective_trigger_token_limit = max(
        1,
        trigger_token_limit - max(0, reserved_prompt_tokens),
    )
    if estimated_tokens_before <= effective_trigger_token_limit:
        return None

    existing_summary_rows = [row for row in in_context_rows if row.role == "summary"]
    conversation_rows = [row for row in in_context_rows if row.role != "summary"]
    if len(conversation_rows) <= keep_last_messages:
        return None

    compacted_rows = conversation_rows[:-keep_last_messages]
    kept_rows = conversation_rows[-keep_last_messages:]
    if not compacted_rows:
        return None

    # Cascade summarization
    summary_text = None

    # Level 1: Full LLM summarization
    try:
        summary_text = await summarize_with_llm(compacted_rows)
    except Exception:
        logger.debug("Level 1 LLM summarization failed")

    # Level 2: LLM summarization with clamped tool content
    if summary_text is None:
        try:
            clamped_transcript = _build_transcript(compacted_rows, clamp_tools=True)
            if clamped_transcript.strip():
                summary_text = await summarize_with_llm(
                    compacted_rows,
                    transcript_override=clamped_transcript,
                )
        except Exception:
            logger.debug("Level 2 clamped LLM summarization failed")

    # Level 3: Fast text-based fallback (always succeeds)
    if summary_text is None:
        summary_text = render_summary_text(
            existing_summary_rows, compacted_rows, user_id=thread.user_id
        )

    # Add metadata prefix
    total_hidden = len(compacted_rows)
    for sr in existing_summary_rows:
        cj = sr.content_json
        if isinstance(cj, dict):
            total_hidden += cj.get("compacted_message_count", 0)

    summary_text = (
        f"[Summary of previous conversation — "
        f"{len(compacted_rows)} messages compacted, "
        f"{total_hidden} total hidden messages]\n{summary_text}"
    )

    # Apply the compaction (same DB logic as compact_thread_context)
    for row in existing_summary_rows:
        row.is_in_context = False
        db.add(row)

    for row in compacted_rows:
        row.is_in_context = False
        db.add(row)

    summary_sequence_id = reserve_message_sequences(
        db,
        thread_id=thread.id,
        count=1,
    )
    # Track whether LLM was used before we potentially modify summary_text
    used_llm = summary_text != render_summary_text(
        existing_summary_rows, compacted_rows, user_id=thread.user_id
    )

    summary_message = RuntimeMessage(
        thread_id=thread.id,
        user_id=thread.user_id,
        run_id=run_id,
        step_id=None,
        sequence_id=summary_sequence_id,
        role="summary",
        content_text=summary_text,
        content_json={
            "compacted_message_count": len(compacted_rows),
            "total_hidden_message_count": total_hidden,
            "source_sequence_end": compacted_rows[-1].sequence_id,
            "llm_summarized": used_llm,
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
        reserved_prompt_tokens=max(0, reserved_prompt_tokens),
        effective_trigger_token_limit=effective_trigger_token_limit,
    )
