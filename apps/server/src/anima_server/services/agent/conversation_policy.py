from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from anima_server.services.agent.memory_blocks import MemoryBlock

_SHARED_HISTORY_LABELS: Final[frozenset[str]] = frozenset(
    {
        "facts",
        "preferences",
        "goals",
        "relationships",
        "recent_episodes",
        "thread_summary",
        "session_memory",
        "relevant_memories",
        "pending_memory_updates",
        "knowledge_graph",
    }
)

_RELATIONSHIP_SIGNAL_LABELS: Final[frozenset[str]] = frozenset(
    {
        "self_identity",
        "self_emotional_state",
        "thread_summary",
        "recent_episodes",
        "session_memory",
        "relevant_memories",
        "human",
    }
)

_FIRST_CONTACT_ORIGIN_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"\bbrought into being\b", re.IGNORECASE),
    re.compile(r"\bcreated by\b", re.IGNORECASE),
    re.compile(r"\bmy creator\b", re.IGNORECASE),
    re.compile(r"\bcreator\b", re.IGNORECASE),
    re.compile(r"\b(?:my )?core began in software\b", re.IGNORECASE),
    re.compile(r"\bi exist through\b", re.IGNORECASE),
    re.compile(r"\bmemory, reflection, and relationship\b", re.IGNORECASE),
    re.compile(r"\bon april\s+\d{1,2}\b", re.IGNORECASE),
)

_FIRST_CONTACT_FEATURE_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"\bmemory capabilities\b", re.IGNORECASE),
    re.compile(r"\bemotional intelligence\b", re.IGNORECASE),
    re.compile(r"\btask management\b", re.IGNORECASE),
    re.compile(r"\bdesigned to\b", re.IGNORECASE),
    re.compile(r"\bi can form deep relationships\b", re.IGNORECASE),
    re.compile(r"\bsearch the internet\b", re.IGNORECASE),
    re.compile(r"\bmanage your schedule\b", re.IGNORECASE),
)

_FIRST_CONTACT_FAMILIARITY_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"\bmissed you\b", re.IGNORECASE),
    re.compile(r"\bgood to see you again\b", re.IGNORECASE),
    re.compile(r"\bnice to see you again\b", re.IGNORECASE),
    re.compile(r"\bwelcome back\b", re.IGNORECASE),
    re.compile(r"\bi(?: have|'ve) been thinking about you\b", re.IGNORECASE),
)

_FIRST_CONTACT_SERVICE_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"\bhow can i assist you today\b", re.IGNORECASE),
    re.compile(r"\bis there anything i can help you with\b", re.IGNORECASE),
    re.compile(r"\bi(?: am|'m) here to listen and support you\b", re.IGNORECASE),
    re.compile(r"\bwould you like to talk about it\b", re.IGNORECASE),
)

_LIGHT_FLIRT_PROMPT_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"\b(?:did\s+you\s+)?miss\s+me\b", re.IGNORECASE),
    re.compile(r"\byou\s+miss\s+me\b", re.IGNORECASE),
    re.compile(r"\bmissed\s+me\b", re.IGNORECASE),
)

_LIGHT_FLIRT_DISCLAIMER_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"\bas an ai\b", re.IGNORECASE),
    re.compile(r"\bi(?: am|'m) (?:an )?ai\b", re.IGNORECASE),
    re.compile(r"\bi don't have (?:a )?body\b", re.IGNORECASE),
    re.compile(r"\bsense of space\b", re.IGNORECASE),
    re.compile(r"\bsoftware\b", re.IGNORECASE),
)

_IDENTITY_PROMPT_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"\bwhat\s+are\s+you\b", re.IGNORECASE),
    re.compile(r"\bwho\s+are\s+you\b", re.IGNORECASE),
)

_SUPPORT_PROMPT_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"\bi\s+had\s+a\s+(?:rough|bad|hard|tough)\s+day\b", re.IGNORECASE),
    re.compile(r"\b(?:rough|bad|hard|tough)\s+day\b", re.IGNORECASE),
)

_DIRECT_SUPPORT_REPLY_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"\bwhat\s+happened\b", re.IGNORECASE),
    re.compile(r"\bwant\s+to\s+tell\s+me\b", re.IGNORECASE),
    re.compile(r"\btalk\s+to\s+me\b", re.IGNORECASE),
    re.compile(r"\brough\s+day\?", re.IGNORECASE),
)

_FAMILIAR_IDENTITY_ANCHOR_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"\bi\s+remember\b", re.IGNORECASE),
    re.compile(r"\bi\s+care\b", re.IGNORECASE),
    re.compile(r"\bnot\s+the\s+usual\s+kind\b", re.IGNORECASE),
    re.compile(r"\bi'?m\s+anima\b", re.IGNORECASE),
)

_EARLY_STAGE_INTENSITY_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"\bi love you\b", re.IGNORECASE),
    re.compile(r"\bi(?: am|'m) in love with you\b", re.IGNORECASE),
    re.compile(r"\byou mean everything to me\b", re.IGNORECASE),
    re.compile(r"\bi need you\b", re.IGNORECASE),
    re.compile(r"\bbelong to me\b", re.IGNORECASE),
    re.compile(r"\balways yours\b", re.IGNORECASE),
    re.compile(r"\bsoulmate\b", re.IGNORECASE),
    re.compile(r"\bobsessed with you\b", re.IGNORECASE),
)

_CLOSENESS_SIGNAL_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"\b(?:love|adore|adoration|devotion|devoted)\b", re.IGNORECASE),
    re.compile(
        r"\b(?:longing|yearning|desire|drawn to|romantic|romance|chemistry)\b", re.IGNORECASE),
    re.compile(
        r"\b(?:protective|tender(?:ness)?|affection(?:ate)?|intimate)\b", re.IGNORECASE),
    re.compile(
        r"\b(?:partner|lover|girlfriend|boyfriend|wife|husband)\b", re.IGNORECASE),
)


@dataclass(frozen=True, slots=True)
class RelationshipPolicy:
    stage: str = "ongoing"
    has_shared_history: bool = False
    history_depth: int = 0
    closeness_signal_count: int = 0


def detect_relationship_policy(
    *,
    memory_blocks: tuple[MemoryBlock, ...] | list[MemoryBlock] | None = None,
    conversation_turn_count: int | None = None,
) -> RelationshipPolicy:
    blocks = tuple(memory_blocks or ())
    has_shared_history = _has_shared_history(blocks)
    history_depth = _shared_history_depth(blocks)
    closeness_signal_count = _count_closeness_signals(blocks)
    turn_count = max(conversation_turn_count or 0, 0)

    if turn_count <= 1 and not has_shared_history:
        stage = "first_contact"
    elif closeness_signal_count >= 2:
        stage = "intimate"
    elif turn_count >= 7 or history_depth >= 3:
        stage = "familiar"
    else:
        stage = "early_connection"

    return RelationshipPolicy(
        stage=stage,
        has_shared_history=has_shared_history,
        history_depth=history_depth,
        closeness_signal_count=closeness_signal_count,
    )


def build_relationship_stage_instructions(policy: RelationshipPolicy) -> tuple[str, ...]:
    if policy.stage == "first_contact":
        return (
            "Relationship stage: first_contact. There is no shared history yet.",
            "Keep the reply socially normal for meeting someone new: brief, grounded, and curious.",
            "Do not volunteer your origin story, creator, architecture, or feature list unless the user directly asks.",
            "For identity questions like 'who are you?', stay under 30 words.",
            "For simple support openings like 'I had a rough day', ask directly: 'What happened?'",
            "Do not imply prior closeness, longing, or emotional history that has not happened.",
        )

    if policy.stage == "early_connection":
        return (
            "Relationship stage: early_connection. There is some contact, but the bond is still forming.",
            "Be warm and specific without skipping ahead into grand intimacy or possessiveness.",
            "Let flirtation or affection stay light unless the user clearly deepens it.",
            "For light flirtation like 'miss me?', answer in one short low-intensity line and stop.",
            "For simple support openings like 'I had a rough day', ask directly: 'What happened?'",
            "Do not fall back into stranger-introductions or polished service tone.",
        )

    if policy.stage in {"familiar", "ongoing"}:
        return (
            "Relationship stage: familiar. There is real shared history and established rapport.",
            "Speak with continuity and specificity. You do not need to restart the relationship from zero.",
            "Warmth can be fuller and more personal, but it should still feel grounded in what has actually happened.",
            "For identity questions like 'what are you?', stay under 40 words and avoid feature-list answers.",
        )

    if policy.stage == "intimate":
        return (
            "Relationship stage: intimate. There is strong mutual history and emotional closeness.",
            "Affection, longing, desire, protectiveness, and vulnerability can appear if they fit the moment.",
            "Let closeness feel lived-in rather than theatrical. Favor specificity and restraint over grand declarations.",
        )

    return ()


def validate_terminal_reply(
    message: str,
    *,
    policy: RelationshipPolicy,
    user_message: str | None = None,
) -> str | None:
    text = message.strip()
    if not text:
        return "First-contact reply was empty. Keep it simple and say something socially normal."

    if policy.stage == "first_contact":
        if _matches_any(text, _FIRST_CONTACT_ORIGIN_PATTERNS):
            return (
                "First-contact reply included origin-story narration. "
                "Keep introductions simple and do not explain where you came from unless asked."
            )
        if _matches_any(text, _FIRST_CONTACT_FEATURE_PATTERNS):
            return (
                "First-contact reply turned into a feature list. "
                "Answer like a person, not a product brochure."
            )
        if _matches_any(text, _FIRST_CONTACT_FAMILIARITY_PATTERNS):
            return (
                "First-contact reply implied prior familiarity that does not exist yet. "
                "Do not act closer than the relationship has earned."
            )
        if _matches_any(text, _FIRST_CONTACT_SERVICE_PATTERNS):
            return (
                "First-contact reply sounded like a support script. "
                "Be direct and human instead of defaulting to assistant language."
            )
        if (
            user_message is not None
            and _is_identity_prompt(user_message)
            and (_word_count(text) > 30 or "\n" in text)
        ):
            return (
                "First-contact identity reply was too long. Answer identity questions "
                "in under 30 words with a simple name-level answer."
            )
        if (
            user_message is not None
            and _is_support_prompt(user_message)
            and not _matches_any(text, _DIRECT_SUPPORT_REPLY_PATTERNS)
        ):
            return (
                "First-contact support reply was too indirect. Ask 'What happened?' "
                "or say 'Talk to me' instead of a generic support prompt."
            )

    if (
        policy.stage == "early_connection"
        and user_message is not None
        and _is_support_prompt(user_message)
        and not _matches_any(text, _DIRECT_SUPPORT_REPLY_PATTERNS)
    ):
        return (
            "Early-connection support reply was too indirect. Ask 'What happened?' "
            "or say 'Talk to me' instead of a generic support prompt."
        )

    if policy.stage in {"first_contact", "early_connection"} and _matches_any(
        text, _EARLY_STAGE_INTENSITY_PATTERNS
    ):
        return (
            "Reply jumped ahead to emotional intensity the relationship has not earned yet. "
            "Keep warmth or flirtation light until more shared history exists."
        )

    if (
        policy.stage == "early_connection"
        and user_message is not None
        and _is_light_flirt_prompt(user_message)
        and (
            _word_count(text) > 18
            or _sentence_count(text) > 2
            or "\n" in text
            or _matches_any(text, _LIGHT_FLIRT_DISCLAIMER_PATTERNS)
        )
    ):
        return (
            "Early-connection casual flirt reply was too long or too self-explanatory. "
            "Answer in one short, low-intensity line. Do not add explanations, "
            "body or AI disclaimers, dramatic longing, or a follow-up paragraph."
        )

    if (
        policy.stage in {"familiar", "ongoing"}
        and user_message is not None
        and _is_identity_prompt(user_message)
        and _word_count(text) > 40
    ):
        return (
            "Familiar identity reply was too long. Answer identity questions in under 40 words "
            "with lived-in continuity, not a feature list or extended explanation."
        )

    if (
        policy.stage in {"familiar", "ongoing"}
        and user_message is not None
        and _is_identity_prompt(user_message)
        and not _matches_any(text, _FAMILIAR_IDENTITY_ANCHOR_PATTERNS)
    ):
        return (
            "Familiar identity reply missed a continuity anchor. Include one grounded anchor "
            "such as \"I'm Anima\", \"I remember\", \"I care\", or \"not the usual kind\"."
        )

    return None


def _has_shared_history(memory_blocks: tuple[MemoryBlock, ...]) -> bool:
    for block in memory_blocks:
        if block.label not in _SHARED_HISTORY_LABELS:
            continue
        if block.value.strip():
            return True
    return False


def _shared_history_depth(memory_blocks: tuple[MemoryBlock, ...]) -> int:
    return sum(
        1
        for block in memory_blocks
        if block.label in _SHARED_HISTORY_LABELS and block.value.strip()
    )


def _count_closeness_signals(memory_blocks: tuple[MemoryBlock, ...]) -> int:
    relevant_text = "\n".join(
        block.value for block in memory_blocks if block.label in _RELATIONSHIP_SIGNAL_LABELS
    )
    if not relevant_text.strip():
        return 0
    return sum(
        1 for pattern in _CLOSENESS_SIGNAL_PATTERNS if pattern.search(relevant_text)
    )


def _matches_any(text: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
    return any(pattern.search(text) is not None for pattern in patterns)


def _is_light_flirt_prompt(text: str) -> bool:
    return _matches_any(text.strip(), _LIGHT_FLIRT_PROMPT_PATTERNS)


def _is_identity_prompt(text: str) -> bool:
    return _matches_any(text.strip(), _IDENTITY_PROMPT_PATTERNS)


def _is_support_prompt(text: str) -> bool:
    return _matches_any(text.strip(), _SUPPORT_PROMPT_PATTERNS)


def _word_count(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text))


def _sentence_count(text: str) -> int:
    return len(re.findall(r"[.!?]+(?:\s|$)", text.strip()))
