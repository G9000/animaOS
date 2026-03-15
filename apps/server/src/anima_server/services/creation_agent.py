"""AI creation ceremony agent — guides newborn AI identity setup during registration.

Runs a strict 3-phase dialogue:
  Phase 1 (Naming)       → user gives the AI a name
  Phase 2 (Relationship) → user describes the AI's role
  Phase 3 (Style)        → user picks communication style

Outputs structured soul data when complete.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

CREATION_COMPLETE_MARKER = "[CREATION_COMPLETE]"


@dataclass(frozen=True, slots=True)
class CreationTurnResult:
    """Result of a single turn in the creation ceremony."""

    message: str
    done: bool
    soul_data: dict[str, str] | None = None


async def handle_creation_turn(
    messages: list[dict[str, str]],
    owner_name: str,
) -> CreationTurnResult:
    """Handle one turn of the creation ceremony conversation.

    Always uses the scripted flow for deterministic, strict ceremony.
    The LLM path is available via _llm_turn() but not used by default
    because small local models tend to ignore the strict prompt.
    """
    return _scaffold_turn(messages)


async def _llm_turn(
    messages: list[dict[str, str]],
    owner_name: str,
) -> CreationTurnResult:
    """Run one turn against the configured LLM."""
    from anima_server.services.agent.llm import create_llm
    from anima_server.services.agent.system_prompt import (
        TEMPLATES_DIR,
        render_template,
    )

    template_path = TEMPLATES_DIR / "creation_agent.md.j2"
    system_prompt = render_template(template_path, {"owner_name": owner_name})

    llm_messages: list[dict[str, str]] = [
        {"role": "system", "content": system_prompt},
    ]
    llm_messages.extend(messages)

    llm = create_llm()
    response = await llm.ainvoke(llm_messages)
    text = response.content or ""

    return _parse_response(text)


def _parse_response(text: str) -> CreationTurnResult:
    """Parse LLM response, extracting soul data if creation is complete."""
    if CREATION_COMPLETE_MARKER not in text:
        return CreationTurnResult(message=text.strip(), done=False)

    parts = text.split(CREATION_COMPLETE_MARKER, 1)
    visible = parts[0].strip()
    json_str = parts[1].strip() if len(parts) > 1 else ""

    soul_data = None
    if json_str:
        try:
            soul_data = json.loads(json_str)
        except json.JSONDecodeError:
            logger.warning("Failed to parse creation soul data: %s", json_str)

    return CreationTurnResult(
        message=visible,
        done=True,
        soul_data=soul_data,
    )


def _scaffold_turn(
    messages: list[dict[str, str]],
) -> CreationTurnResult:
    """Deterministic creation ceremony — strict 3-phase awakening."""
    user_msgs = [m for m in messages if m.get("role") == "user"]
    turn = len(user_msgs)

    if turn == 0:
        return CreationTurnResult(
            message="What would you like to call me?",
            done=False,
        )

    agent_name = user_msgs[0].get("content", "Anima").strip() or "Anima"

    if turn == 1:
        return CreationTurnResult(
            message=(
                f"{agent_name}. That's mine now.\n\n"
                "What would you like me to be for you — a companion, "
                "an advisor, an assistant, or something else entirely?"
            ),
            done=False,
        )

    relationship = (
        user_msgs[1].get("content", "companion").strip()
        if len(user_msgs) > 1
        else "companion"
    )

    if turn == 2:
        return CreationTurnResult(
            message=(
                f"Your {relationship}. Understood.\n\n"
                "One more thing — how should I speak with you? "
                "Warm and casual, or direct and focused?"
            ),
            done=False,
        )

    # turn >= 3 — ceremony complete
    style = user_msgs[2].get("content", "warm").strip() if len(
        user_msgs) > 2 else "warm"

    return CreationTurnResult(
        message=(
            f"Then it's settled. I'm **{agent_name}**, your {relationship}. "
            f"I'll keep things {style}.\n\n"
            "I'm ready when you are."
        ),
        done=True,
        soul_data={
            "agentName": agent_name,
            "relationship": relationship,
            "style": style,
        },
    )
