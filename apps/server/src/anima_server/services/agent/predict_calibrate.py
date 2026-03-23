"""Predict-Calibrate Consolidation -- F3.

Instead of extracting facts from conversations cold (producing duplicates),
first predict what the conversation likely contains based on existing
knowledge, then extract only the delta: surprises, corrections, and
genuinely new information.

Inspired by the Free Energy Principle: learning = prediction error
minimization.  Extract what surprises you, not what you already know.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from anima_server.services.agent.consolidation import (
    extract_memories_via_llm,
)
from anima_server.services.agent.embeddings import hybrid_search
from anima_server.services.agent.json_utils import (
    parse_json_array as _parse_json_array,
)
from anima_server.services.data_crypto import df

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Quality gate constants (heuristic-based, Option B from PRD)
# ---------------------------------------------------------------------------

# Persistence: temporal markers that indicate a statement won't be true
# in 6 months.
_TEMPORAL_MARKERS: tuple[str, ...] = (
    "today",
    "right now",
    "currently",
    "at the moment",
    "yesterday",
    "this morning",
    "this afternoon",
    "this evening",
    "tonight",
    "earlier today",
    "just now",
    "a moment ago",
    "this week",
)

# Utility: prefixes that indicate low-value conversational observations.
_UTILITY_PREFIXES: tuple[str, ...] = (
    "user said",
    "user asked",
    "user mentioned",
    "user told",
    "user replied",
    "user responded",
    "user noted",
    "user stated",
    "user expressed",
    "user was saying",
)

# Independence: context-dependent references that need conversation
# context to understand.
_CONTEXT_DEPENDENT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bagreed with that\b", re.IGNORECASE),
    re.compile(r"\bthe thing we\b", re.IGNORECASE),
    re.compile(r"\bwhat we discussed\b", re.IGNORECASE),
    re.compile(r"\bas we talked about\b", re.IGNORECASE),
    re.compile(r"\babout that\b", re.IGNORECASE),
    re.compile(r"\blike I said\b", re.IGNORECASE),
    re.compile(r"\bthe previous\b", re.IGNORECASE),
)

_COLD_START_THRESHOLD = 5
_MIN_WORD_COUNT = 3

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

# Prediction and delta extraction prompts are now in Jinja2 templates.
# Use PromptLoader.prediction() and PromptLoader.delta_extraction() instead.


# ---------------------------------------------------------------------------
# ID hallucination protection (F3.11)
# ---------------------------------------------------------------------------


def _build_id_map(
    real_ids: list[int],
) -> tuple[dict[int, int], dict[int, int]]:
    """Map real memory IDs to sequential integers for safe LLM prompting.

    Returns (real_to_seq, seq_to_real).
    """
    real_to_seq: dict[int, int] = {}
    seq_to_real: dict[int, int] = {}
    for i, real_id in enumerate(real_ids, start=1):
        real_to_seq[real_id] = i
        seq_to_real[i] = real_id
    return real_to_seq, seq_to_real


# ---------------------------------------------------------------------------
# Quality gates (heuristic-based)
# ---------------------------------------------------------------------------


def apply_quality_gates(
    *,
    statements: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Filter statements through persistence, specificity, utility,
    and independence tests.

    Uses heuristic rules (Option B from PRD) -- no LLM calls.
    """
    passed: list[dict[str, Any]] = []

    for stmt in statements:
        content = stmt.get("content", "")
        if not content or not isinstance(content, str):
            continue
        content_stripped = content.strip()
        if not content_stripped:
            continue

        content_lower = content_stripped.lower()

        # 1. PERSISTENCE: reject temporal markers
        if _fails_persistence(content_lower):
            continue

        # 2. SPECIFICITY: reject if < 5 words
        word_count = len(content_stripped.split())
        if word_count < _MIN_WORD_COUNT:
            continue

        # 3. UTILITY: reject conversational observations
        if _fails_utility(content_lower):
            continue

        # 4. INDEPENDENCE: reject context-dependent references
        if _fails_independence(content_stripped):
            continue

        passed.append(stmt)

    return passed


def _fails_persistence(content_lower: str) -> bool:
    """Return True if the content contains temporal markers."""
    return any(marker in content_lower for marker in _TEMPORAL_MARKERS)


def _fails_utility(content_lower: str) -> bool:
    """Return True if the content is a low-value conversational observation."""
    return any(content_lower.startswith(prefix) for prefix in _UTILITY_PREFIXES)


def _fails_independence(content: str) -> bool:
    """Return True if the content depends on conversation context."""
    return any(pattern.search(content) for pattern in _CONTEXT_DEPENDENT_PATTERNS)


# ---------------------------------------------------------------------------
# LLM-based prediction and delta extraction
# ---------------------------------------------------------------------------


async def predict_episode_knowledge(
    *,
    existing_facts: list[str],
    conversation_summary: str,
    prompt_loader: Any | None = None,
) -> str:
    """Predict what knowledge a conversation likely contains.

    Uses low temperature (0.3) for conservative predictions.
    """
    from anima_server.services.agent.llm import create_llm
    from anima_server.services.agent.messages import HumanMessage, SystemMessage
    from anima_server.services.agent.prompt_loader import PromptLoader

    if prompt_loader is None:
        prompt_loader = PromptLoader(agent_name="Anima")

    facts_text = (
        "\n".join(f"- {f}" for f in existing_facts) if existing_facts else "(no existing facts)"
    )

    prompt = prompt_loader.prediction(
        existing_facts=facts_text,
        conversation_summary=conversation_summary,
    )

    llm = create_llm()
    response = await llm.ainvoke(
        [
            SystemMessage(
                content="You predict what information a conversation likely contains based on existing knowledge. Be concise."
            ),
            HumanMessage(content=prompt),
        ]
    )
    content = getattr(response, "content", "")
    if not isinstance(content, str):
        content = str(content)
    return content


async def extract_knowledge_delta(
    *,
    user_message: str,
    assistant_response: str,
    prediction: str,
    prompt_loader: Any | None = None,
) -> list[dict[str, Any]]:
    """Extract only the delta between prediction and actual conversation.

    Returns list of dicts with content, category, confidence, reason,
    and detected_emotion fields.
    """
    from anima_server.services.agent.llm import create_llm
    from anima_server.services.agent.messages import HumanMessage, SystemMessage
    from anima_server.services.agent.prompt_loader import PromptLoader

    if prompt_loader is None:
        prompt_loader = PromptLoader(agent_name="Anima")

    prompt = prompt_loader.delta_extraction(
        prediction=prediction,
        user_message=user_message,
        assistant_response=assistant_response,
    )

    llm = create_llm()
    response = await llm.ainvoke(
        [
            SystemMessage(
                content="You extract only surprising, contradictory, or corrective information. Respond only with a JSON array."
            ),
            HumanMessage(content=prompt),
        ]
    )
    content = getattr(response, "content", "")
    if not isinstance(content, str):
        content = str(content)

    # Parse JSON array from response
    items = _parse_json_array(content)
    return items


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------

# Minimum number of user messages in conversation to use predict-calibrate
_MIN_CONVERSATION_LENGTH = 3


async def predict_calibrate_extraction(
    *,
    user_id: int,
    user_message: str,
    assistant_response: str,
    db: Any,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Full predict-calibrate pipeline:

    1. Retrieve relevant existing facts via hybrid_search
    2. If < 5 facts, fall back to direct extraction (cold-start)
    3. Predict expected knowledge
    4. Extract delta
    5. Apply quality gates
    6. Return filtered items plus any detected emotional signal from the
       raw delta extraction

    F3.9: If predict-calibrate fails, falls back to direct extraction.
    """
    from anima_server.services.agent.prompt_loader import PromptLoader

    # Create prompt loader with user's agent name
    prompt_loader = PromptLoader.from_db(db, user_id)

    # Step 1: Retrieve relevant existing facts
    try:
        search_result = await hybrid_search(
            db,
            user_id=user_id,
            query=user_message,
            limit=20,
        )
        existing_items = search_result.items  # list of (MemoryItem, score)
    except Exception:
        logger.debug("hybrid_search failed in predict_calibrate, falling back to cold-start")
        existing_items = []

    # Step 2: Cold-start check (F3.5)
    if len(existing_items) < _COLD_START_THRESHOLD:
        return await _cold_start_extraction(
            user_message=user_message,
            assistant_response=assistant_response,
        )

    # Extract content strings from existing items, with ID mapping (F3.11)
    real_ids = [item.id for item, _score in existing_items]
    id_map, _reverse_map = _build_id_map(real_ids)

    existing_facts: list[str] = []
    for item, _score in existing_items:
        content = df(user_id, item.content, table="memory_items", field="content")
        seq_id = id_map.get(item.id, 0)
        existing_facts.append(f"[{seq_id}] {content}")

    conversation_summary = f"User: {user_message}\nAssistant: {assistant_response}"

    # Steps 3-4: Predict then extract delta (F3.9: fallback on failure)
    try:
        prediction = await predict_episode_knowledge(
            existing_facts=existing_facts,
            conversation_summary=conversation_summary,
            prompt_loader=prompt_loader,
        )

        delta_items = await extract_knowledge_delta(
            user_message=user_message,
            assistant_response=assistant_response,
            prediction=prediction,
            prompt_loader=prompt_loader,
        )
    except Exception:
        logger.warning(
            "predict-calibrate LLM calls failed for user %s, falling back to direct extraction",
            user_id,
        )
        return await _cold_start_extraction(
            user_message=user_message,
            assistant_response=assistant_response,
        )

    emotion_data = next(
        (item["detected_emotion"] for item in delta_items if item.get("detected_emotion")),
        None,
    )

    # Step 5: Apply quality gates (F3.3)
    filtered = apply_quality_gates(statements=delta_items)

    # Step 6: Normalize output format for downstream consumption
    results: list[dict[str, Any]] = []
    for item in filtered:
        result: dict[str, Any] = {
            "content": item.get("content", ""),
            "category": item.get("category", "fact"),
            "importance": _confidence_to_importance(item.get("confidence", 0.5)),
        }
        # Preserve emotional signal (F3.12)
        if item.get("detected_emotion"):
            result["detected_emotion"] = item["detected_emotion"]
        results.append(result)

    return results, emotion_data


async def _cold_start_extraction(
    *,
    user_message: str,
    assistant_response: str,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Cold-start fallback: use direct LLM extraction and apply quality gates."""
    extraction = await extract_memories_via_llm(
        user_message=user_message,
        assistant_response=assistant_response,
    )

    items = extraction.memories
    # Apply quality gates even in cold-start mode
    filtered = apply_quality_gates(statements=items)

    results: list[dict[str, Any]] = []
    for item in filtered:
        result: dict[str, Any] = {
            "content": item.get("content", ""),
            "category": item.get("category", "fact"),
            "importance": item.get("importance", 3),
        }
        results.append(result)

    emotion_data = (
        extraction.emotion if extraction.emotion and extraction.emotion.get("emotion") else None
    )

    # Attach emotion from the extraction result if present
    if emotion_data and results:
        results[0]["detected_emotion"] = emotion_data

    return results, emotion_data


def _confidence_to_importance(confidence: float) -> int:
    """Map a 0.0-1.0 confidence score to a 1-5 importance level."""
    if confidence >= 0.9:
        return 5
    if confidence >= 0.75:
        return 4
    if confidence >= 0.5:
        return 3
    if confidence >= 0.3:
        return 2
    return 1
