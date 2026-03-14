from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.models import AgentMessage, MemoryEpisode, User
from anima_server.services.agent.memory_store import (
    get_current_focus,
    get_memory_items_scored,
    touch_memory_items,
)


@dataclass(frozen=True, slots=True)
class MemoryBlock:
    label: str
    value: str
    description: str = ""
    read_only: bool = True


def build_runtime_memory_blocks(
    db: Session,
    *,
    user_id: int,
    thread_id: int,
    semantic_results: list[tuple[int, str, float]] | None = None,
) -> tuple[MemoryBlock, ...]:
    blocks: list[MemoryBlock] = []

    # Soul block (Priority 0 — user-authored personality directive)
    soul_block = build_soul_memory_block(db, user_id=user_id)
    if soul_block is not None:
        blocks.append(soul_block)

    # Self-model blocks (Priority 1 — always present, never truncated)
    for sm_block in build_self_model_memory_blocks(db, user_id=user_id):
        blocks.append(sm_block)

    # Emotional context (Priority 2)
    emotional_block = build_emotional_context_block(db, user_id=user_id)
    if emotional_block is not None:
        blocks.append(emotional_block)

    human_block = build_human_memory_block(db, user_id=user_id)
    if human_block is not None:
        blocks.append(human_block)

    # Semantic retrieval block (Priority 3 — query-relevant memories)
    if semantic_results:
        semantic_block = _build_semantic_block(semantic_results)
        if semantic_block is not None:
            blocks.append(semantic_block)

    facts_block = build_facts_memory_block(db, user_id=user_id)
    if facts_block is not None:
        blocks.append(facts_block)

    preferences_block = build_preferences_memory_block(db, user_id=user_id)
    if preferences_block is not None:
        blocks.append(preferences_block)

    goals_block = build_goals_memory_block(db, user_id=user_id)
    if goals_block is not None:
        blocks.append(goals_block)

    relationships_block = build_relationships_memory_block(db, user_id=user_id)
    if relationships_block is not None:
        blocks.append(relationships_block)

    current_focus_block = build_current_focus_memory_block(db, user_id=user_id)
    if current_focus_block is not None:
        blocks.append(current_focus_block)

    summary_block = build_thread_summary_block(db, thread_id=thread_id)
    if summary_block is not None:
        blocks.append(summary_block)

    episodes_block = build_episodes_memory_block(db, user_id=user_id)
    if episodes_block is not None:
        blocks.append(episodes_block)

    session_block = build_session_memory_block(db, thread_id=thread_id)
    if session_block is not None:
        blocks.append(session_block)

    return tuple(blocks)


def _build_semantic_block(
    results: list[tuple[int, str, float]],
) -> MemoryBlock | None:
    """Build a memory block from semantic search results.

    Each result is (item_id, content, similarity_score).
    """
    if not results:
        return None

    lines: list[str] = []
    for _item_id, content, score in results:
        lines.append(f"- {content} (relevance: {score:.2f})")

    if not lines:
        return None

    return MemoryBlock(
        label="relevant_memories",
        description="Memories semantically relevant to what the user just said. Use these naturally — don't list them back.",
        value="\n".join(lines),
    )


def build_human_memory_block(
    db: Session,
    *,
    user_id: int,
) -> MemoryBlock | None:
    user = db.get(User, user_id)
    if user is None:
        return None

    lines: list[str] = []
    if user.display_name.strip():
        lines.append(f"Display name: {user.display_name.strip()}")
    if user.username.strip():
        lines.append(f"Username: {user.username.strip()}")
    if user.gender:
        lines.append(f"Gender: {user.gender}")
    if user.age is not None:
        lines.append(f"Age: {user.age}")
    if user.birthday:
        lines.append(f"Birthday: {user.birthday}")

    if not lines:
        return None

    return MemoryBlock(
        label="human",
        description="Stable facts about the user for this thread.",
        value="\n".join(lines),
    )


def build_facts_memory_block(
    db: Session,
    *,
    user_id: int,
) -> MemoryBlock | None:
    items = get_memory_items_scored(db, user_id=user_id, category="fact", limit=30)
    if not items:
        return None
    touch_memory_items(db, items)
    value = "\n".join(f"- {item.content}" for item in items)
    if len(value) > 2000:
        value = value[:2000]
    return MemoryBlock(
        label="facts",
        description="Known facts about the user.",
        value=value,
    )


def build_preferences_memory_block(
    db: Session,
    *,
    user_id: int,
) -> MemoryBlock | None:
    items = get_memory_items_scored(db, user_id=user_id, category="preference", limit=20)
    if not items:
        return None
    touch_memory_items(db, items)
    value = "\n".join(f"- {item.content}" for item in items)
    if len(value) > 2000:
        value = value[:2000]
    return MemoryBlock(
        label="preferences",
        description="User preferences.",
        value=value,
    )


def build_goals_memory_block(
    db: Session,
    *,
    user_id: int,
) -> MemoryBlock | None:
    items = get_memory_items_scored(db, user_id=user_id, category="goal", limit=15)
    if not items:
        return None
    touch_memory_items(db, items)
    value = "\n".join(f"- {item.content}" for item in items)
    if len(value) > 1500:
        value = value[:1500]
    return MemoryBlock(
        label="goals",
        description="User's goals and aspirations.",
        value=value,
    )


def build_relationships_memory_block(
    db: Session,
    *,
    user_id: int,
) -> MemoryBlock | None:
    items = get_memory_items_scored(db, user_id=user_id, category="relationship", limit=15)
    if not items:
        return None
    touch_memory_items(db, items)
    value = "\n".join(f"- {item.content}" for item in items)
    if len(value) > 1500:
        value = value[:1500]
    return MemoryBlock(
        label="relationships",
        description="People and relationships the user has mentioned.",
        value=value,
    )


def build_current_focus_memory_block(
    db: Session,
    *,
    user_id: int,
) -> MemoryBlock | None:
    focus = get_current_focus(db, user_id=user_id)
    if not focus:
        return None
    return MemoryBlock(
        label="current_focus",
        description="User's current focus.",
        value=focus,
    )


def build_thread_summary_block(
    db: Session,
    *,
    thread_id: int,
) -> MemoryBlock | None:
    summary_row = db.scalar(
        select(AgentMessage)
        .where(
            AgentMessage.thread_id == thread_id,
            AgentMessage.role == "summary",
            AgentMessage.is_in_context.is_(True),
        )
        .order_by(AgentMessage.sequence_id.desc())
        .limit(1)
    )
    if summary_row is None:
        return None

    summary_text = (summary_row.content_text or "").strip()
    if not summary_text:
        return None

    return MemoryBlock(
        label="thread_summary",
        description="Compressed summary of earlier conversation context.",
        value=summary_text,
    )


def build_episodes_memory_block(
    db: Session,
    *,
    user_id: int,
) -> MemoryBlock | None:
    episodes = db.scalars(
        select(MemoryEpisode)
        .where(MemoryEpisode.user_id == user_id)
        .order_by(MemoryEpisode.created_at.desc())
        .limit(5)
    ).all()
    if not episodes:
        return None
    lines: list[str] = []
    for ep in reversed(episodes):
        topics = ", ".join(ep.topics_json or [])
        lines.append(f"- {ep.date}: {ep.summary} (Topics: {topics})")
    return MemoryBlock(
        label="recent_episodes",
        description="Recent conversation experiences with the user.",
        value="\n".join(lines),
    )


def build_session_memory_block(
    db: Session,
    *,
    thread_id: int,
) -> MemoryBlock | None:
    from anima_server.services.agent.session_memory import (
        get_session_notes,
        render_session_memory_text,
    )

    notes = get_session_notes(db, thread_id=thread_id, active_only=True)
    if not notes:
        return None

    text = render_session_memory_text(notes)
    if not text:
        return None

    return MemoryBlock(
        label="session_memory",
        description="Working notes for this conversation session. You can update these with the note_to_self tool.",
        value=text,
        read_only=False,
    )


def build_soul_memory_block(
    db: Session,
    *,
    user_id: int,
) -> MemoryBlock | None:
    """Build a memory block from the user's soul directive."""
    from anima_server.models import SelfModelBlock

    block = db.scalar(
        select(SelfModelBlock).where(
            SelfModelBlock.user_id == user_id,
            SelfModelBlock.section == "soul",
        )
    )
    if block is None or not block.content.strip():
        return None

    return MemoryBlock(
        label="soul",
        description="The user's personality directive — who they want me to be. This is their vision for our relationship.",
        value=block.content.strip(),
    )


def build_self_model_memory_blocks(
    db: Session,
    *,
    user_id: int,
) -> list[MemoryBlock]:
    """Build memory blocks from the agent's self-model sections."""
    from anima_server.services.agent.self_model import (
        ensure_self_model_exists,
        get_all_self_model_blocks,
        render_self_model_section,
    )

    ensure_self_model_exists(db, user_id=user_id)
    blocks_map = get_all_self_model_blocks(db, user_id=user_id)
    result: list[MemoryBlock] = []

    section_config = [
        ("identity", "self_identity", "Who I am in this relationship — my self-understanding."),
        ("inner_state", "self_inner_state", "My current cognitive state — what I'm thinking about, what's unresolved."),
        ("working_memory", "self_working_memory", "Things I'm holding in mind across sessions."),
        ("growth_log", "self_growth_log", "How I've evolved — my recent changes and why."),
        ("intentions", "self_intentions", "My active goals and learned behavioral rules."),
    ]

    for section, label, description in section_config:
        block = blocks_map.get(section)
        text = render_self_model_section(block)
        if text:
            result.append(MemoryBlock(
                label=label,
                description=description,
                value=text,
            ))

    return result


def build_emotional_context_block(
    db: Session,
    *,
    user_id: int,
) -> MemoryBlock | None:
    """Build a memory block with the agent's emotional read of the user."""
    from anima_server.services.agent.emotional_intelligence import synthesize_emotional_context

    text = synthesize_emotional_context(db, user_id=user_id)
    if not text:
        return None

    return MemoryBlock(
        label="emotional_context",
        description="My sense of how the user is doing emotionally. Guide tone, not verbal analysis.",
        value=text,
    )


def serialize_memory_blocks(
    blocks: Sequence[MemoryBlock],
) -> list[dict[str, object]]:
    serialized: list[dict[str, object]] = []
    for block in blocks:
        label = block.label.strip()
        value = block.value.strip()
        if not label or not value:
            continue
        serialized.append(
            {
                "label": label,
                "value": value,
                "description": block.description.strip(),
                "read_only": block.read_only,
            }
        )
    return serialized
