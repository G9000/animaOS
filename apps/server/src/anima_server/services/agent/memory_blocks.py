from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.models import AgentMessage, MemoryEpisode, User
from anima_server.services.agent.memory_store import get_current_focus, get_memory_items


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
) -> tuple[MemoryBlock, ...]:
    blocks: list[MemoryBlock] = []

    human_block = build_human_memory_block(db, user_id=user_id)
    if human_block is not None:
        blocks.append(human_block)

    facts_block = build_facts_memory_block(db, user_id=user_id)
    if facts_block is not None:
        blocks.append(facts_block)

    preferences_block = build_preferences_memory_block(db, user_id=user_id)
    if preferences_block is not None:
        blocks.append(preferences_block)

    current_focus_block = build_current_focus_memory_block(db, user_id=user_id)
    if current_focus_block is not None:
        blocks.append(current_focus_block)

    summary_block = build_thread_summary_block(db, thread_id=thread_id)
    if summary_block is not None:
        blocks.append(summary_block)

    episodes_block = build_episodes_memory_block(db, user_id=user_id)
    if episodes_block is not None:
        blocks.append(episodes_block)

    return tuple(blocks)


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
    items = get_memory_items(db, user_id=user_id, category="fact", limit=30)
    if not items:
        return None
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
    items = get_memory_items(db, user_id=user_id, category="preference", limit=20)
    if not items:
        return None
    value = "\n".join(f"- {item.content}" for item in items)
    if len(value) > 2000:
        value = value[:2000]
    return MemoryBlock(
        label="preferences",
        description="User preferences.",
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
