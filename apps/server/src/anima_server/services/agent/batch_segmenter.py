"""Batch episode segmentation: LLM-driven topic-coherent grouping.

When a conversation buffer exceeds BATCH_THRESHOLD messages, the LLM groups
messages into non-contiguous topic episodes instead of using fixed-size
sequential chunking.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from anima_server.config import settings
from anima_server.models import MemoryEpisode
from anima_server.services.agent.json_utils import parse_json_array

logger = logging.getLogger(__name__)

BATCH_THRESHOLD: int = 8  # Min messages for batch segmentation
_BATCH_SEGMENTATION_TIMEOUT_SECONDS: float = 15.0
_BATCH_SEGMENTATION_MAX_TOKENS: int = 512

# Batch segmentation prompt is now in Jinja2 template: batch_segmentation.md.j2
# Use PromptLoader.batch_segmentation() instead.


def should_batch_segment(buffer_size: int) -> bool:
    """True if buffer_size >= BATCH_THRESHOLD."""
    return buffer_size >= BATCH_THRESHOLD


def validate_indices(groups: list[list[int]], total_messages: int) -> bool:
    """Validate that all indices from 1..total_messages appear exactly once.

    Checks:
    1. No index is out of range (< 1 or > total_messages)
    2. No index appears in multiple groups
    3. All indices 1..total_messages are covered
    """
    seen: set[int] = set()
    for group in groups:
        for idx in group:
            if idx < 1 or idx > total_messages:
                return False
            if idx in seen:
                return False
            seen.add(idx)
    return seen == set(range(1, total_messages + 1))


def indices_to_0based(groups: list[list[int]]) -> list[list[int]]:
    """Convert 1-based LLM indices to 0-based Python indices."""
    return [[i - 1 for i in group] for group in groups]


async def segment_messages_batch(
    messages: list[tuple[str, str]],
    *,
    user_id: int = 0,
) -> list[list[int]]:
    """Use LLM to group messages into topic-coherent episodes.

    Args:
        messages: List of (user_message, assistant_response) pairs.
        user_id: User ID for LLM configuration.

    Returns:
        List of groups, each a list of 1-based message indices.
        Returns [] on LLM failure or invalid output so the caller can use
        sequential fallback.
    """
    total = len(messages)

    try:
        groups = await _call_llm_for_segmentation(messages)
    except Exception as exc:
        logger.warning(
            "LLM batch segmentation failed, using sequential fallback: %s", exc)
        return []

    if not isinstance(groups, list) or not groups:
        logger.warning(
            "LLM returned no usable segmentation groups, using sequential fallback")
        return []

    # Validate structure: list of lists of ints
    for group in groups:
        if not isinstance(group, list):
            logger.warning(
                "LLM returned a non-list group, using sequential fallback")
            return []
        for idx in group:
            if not isinstance(idx, int):
                logger.warning(
                    "LLM returned a non-integer index, using sequential fallback")
                return []

    if not validate_indices(groups, total):
        logger.warning(
            "LLM segmentation indices invalid (total=%d, groups=%s), using sequential fallback",
            total,
            groups,
        )
        return []

    return groups


async def _call_llm_for_segmentation(
    messages: list[tuple[str, str]],
) -> list[list[int]]:
    """Call LLM to segment messages by topic coherence."""
    from anima_server.services.agent.llm import (
        build_provider_headers,
        resolve_base_url,
    )
    from anima_server.services.agent.messages import HumanMessage, SystemMessage
    from anima_server.services.agent.openai_compatible_client import (
        OpenAICompatibleChatClient,
    )

    # Format messages for the prompt
    lines: list[str] = []
    for i, (user_msg, assistant_resp) in enumerate(messages, start=1):
        lines.append(f"[{i}] User: {user_msg}")
        lines.append(f"[{i}] Assistant: {assistant_resp}")

    from anima_server.services.agent.prompt_loader import PromptLoader

    prompt_loader = PromptLoader(agent_name="Anima")
    prompt = prompt_loader.batch_segmentation(messages="\n".join(lines))
    model = settings.agent_extraction_model.strip() or settings.agent_model
    timeout = min(settings.agent_llm_timeout,
                  _BATCH_SEGMENTATION_TIMEOUT_SECONDS)
    max_tokens = min(settings.agent_max_tokens, _BATCH_SEGMENTATION_MAX_TOKENS)

    # Segmentation is a small extraction task, so keep it on the faster path.
    client = OpenAICompatibleChatClient(
        provider=settings.agent_provider,
        model=model,
        base_url=resolve_base_url(settings.agent_provider),
        headers=build_provider_headers(settings.agent_provider),
        timeout=timeout,
        max_tokens=max_tokens,
        temperature=0.2,
    )

    try:
        response = await client.ainvoke(
            [
                SystemMessage(
                    content="You group conversation messages by topic. Respond only with a JSON array of arrays."
                ),
                HumanMessage(content=prompt),
            ]
        )
    finally:
        await client.aclose()

    content = getattr(response, "content", "")
    if not isinstance(content, str):
        content = str(content)

    return _parse_json_array(content)


def _parse_json_array(text: str) -> list[list[int]]:
    """Parse a JSON array of arrays from LLM output."""
    result = parse_json_array(text)
    if not result:
        raise ValueError(f"No JSON array found in: {text[:100]}")
    return result


async def generate_episodes_from_segments(
    db: Session,
    *,
    user_id: int,
    thread_id: int | None,
    pairs: list[tuple[str, str]],
    segments: list[list[int]],
    today: str,
) -> list[MemoryEpisode]:
    """Generate one episode per segment group.

    Each episode:
    - Contains only the pairs at the specified 0-based indices
    - Records message_indices_json (1-based, as received from LLM)
    - Records segmentation_method='batch_llm'
    - Gets its own LLM-generated summary via _generate_episode_via_llm()
    """
    from anima_server.services.agent.episodes import (
        _build_episode_from_parsed,
        _call_llm_for_episode_safe,
        _create_fallback_episode,
    )

    # segments are already 0-based at this point
    episodes: list[MemoryEpisode] = []

    for segment_0based in segments:
        # Sort indices chronologically so the summarizer sees turns in order
        sorted_indices = sorted(segment_0based)
        segment_pairs = [pairs[i] for i in sorted_indices]
        # Store 1-based indices for the DB column
        indices_1based = [i + 1 for i in sorted_indices]

        if settings.agent_provider == "scaffold":
            episode = _create_fallback_episode(
                db,
                user_id=user_id,
                thread_id=thread_id,
                pairs=segment_pairs,
                today=today,
            )
        else:
            parsed = await _call_llm_for_episode_safe(segment_pairs, user_id=user_id)
            episode = _build_episode_from_parsed(
                db,
                parsed=parsed,
                user_id=user_id,
                thread_id=thread_id,
                pairs=segment_pairs,
                today=today,
            )

        # Annotate with segmentation metadata
        episode.message_indices_json = indices_1based
        episode.segmentation_method = "batch_llm"
        episodes.append(episode)

    return episodes
