from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

DEFAULT_THINKING_MONOLOGUE: list[str] = [
    "one sec",
    "checking this",
    "looking this over",
    "working on it",
    "almost there",
]

GENERATED_FALLBACK_THINKING_MONOLOGUE: list[str] = [
    "one sec",
    "checking the details",
    "looking this over",
    "sorting it out",
    "almost there",
]

_MAX_LINES = 12
_MAX_WORDS = 10
_BLOCKED_MARKERS = ("<", ">", "```", "chain-of-thought", "private reasoning", "analysis:")


def sanitize_thinking_monologue(value: Any) -> list[str]:
    """Return short visible thinking status lines, never raw reasoning."""
    if not isinstance(value, list):
        return list(DEFAULT_THINKING_MONOLOGUE)

    lines: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            continue
        line = " ".join(item.strip().split())
        lowered = line.lower()
        if not line or any(marker in lowered for marker in _BLOCKED_MARKERS):
            continue
        if len(line.split()) > _MAX_WORDS:
            continue
        key = lowered.rstrip(".")
        if key in seen:
            continue
        seen.add(key)
        lines.append(line)
        if len(lines) >= _MAX_LINES:
            break

    return lines or list(DEFAULT_THINKING_MONOLOGUE)


def serialize_thinking_monologue(value: Any) -> str:
    return json.dumps(sanitize_thinking_monologue(value), ensure_ascii=False)


def parse_thinking_monologue(value: str | None) -> list[str]:
    if not value:
        return list(DEFAULT_THINKING_MONOLOGUE)
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return list(DEFAULT_THINKING_MONOLOGUE)
    return sanitize_thinking_monologue(parsed)


async def generate_thinking_monologue(db: Session, *, user_id: int) -> list[str]:
    """Ask the configured model for visible thinking-status lines.

    The generated lines are display copy only. They must not expose hidden
    reasoning or claim access to private chain-of-thought.
    """
    from anima_server.services.agent.llm import LLMConfigError, LLMInvocationError
    from anima_server.services.agent.llm_json import call_llm_for_json
    from anima_server.services.agent.self_model import (
        get_self_model_block,
        render_self_model_section,
    )

    context_parts: list[str] = []
    for section in ("soul", "identity", "persona"):
        block = get_self_model_block(db, user_id=user_id, section=section)
        if block is not None:
            content = render_self_model_section(block, user_id=user_id).strip()
            if content:
                context_parts.append(f"{section.upper()}:\n{content[:900]}")

    system = (
        "You write short visible UI status lines for an AI companion. "
        "Do not reveal hidden chain-of-thought, private reasoning, analysis steps, "
        "tool decisions, or conclusions. Return JSON only."
    )
    prompt = (
        "Create 5 to 12 Thinking Monologue lines for the agent while it is preparing a reply.\n"
        "Rules:\n"
        "- JSON array of strings only.\n"
        "- Each line is first-person or close third-person, max 10 words.\n"
        "- Make them gentle, concise, and in character.\n"
        "- No detailed reasoning, no conclusions, no private thoughts.\n\n"
        + "\n\n".join(context_parts)
    )

    try:
        parsed = await call_llm_for_json(system, prompt, expect="array")
    except (LLMConfigError, LLMInvocationError):
        return list(GENERATED_FALLBACK_THINKING_MONOLOGUE)

    sanitized = sanitize_thinking_monologue(parsed)
    if sanitized == DEFAULT_THINKING_MONOLOGUE and not isinstance(parsed, list):
        return list(GENERATED_FALLBACK_THINKING_MONOLOGUE)
    return sanitized[:_MAX_LINES]
