from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import re
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.models import AgentMessage, User
from anima_server.services.storage import get_user_data_dir


@dataclass(frozen=True, slots=True)
class MemoryBlock:
    label: str
    value: str
    description: str = ""
    read_only: bool = True


CURRENT_FOCUS_PATH = Path("memory") / "user" / "current-focus.md"
CURRENT_FOCUS_PLACEHOLDER = "Define your current focus"
_FRONTMATTER_RE = re.compile(r"^---\r?\n[\s\S]*?\r?\n---\r?\n?")
_CHECKBOX_LINE_RE = re.compile(r"^- \[(?: |x|X)\]\s+(?P<value>.+?)\s*$", re.MULTILINE)


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

    current_focus_block = build_current_focus_memory_block(user_id=user_id)
    if current_focus_block is not None:
        blocks.append(current_focus_block)

    summary_block = build_thread_summary_block(db, thread_id=thread_id)
    if summary_block is not None:
        blocks.append(summary_block)

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


def build_current_focus_memory_block(*, user_id: int) -> MemoryBlock | None:
    current_focus = load_current_focus_memory(user_id=user_id)
    if current_focus is None:
        return None

    return MemoryBlock(
        label="current_focus",
        description="User-declared current focus from local memory.",
        value=current_focus,
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


def load_current_focus_memory(*, user_id: int) -> str | None:
    path = get_user_data_dir(user_id) / CURRENT_FOCUS_PATH
    if not path.is_file():
        return None

    try:
        raw_text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None

    if not raw_text:
        return None

    body = strip_frontmatter(raw_text).strip()
    if not body:
        return None

    if is_placeholder_current_focus(body):
        return None

    return body


def strip_frontmatter(text: str) -> str:
    return _FRONTMATTER_RE.sub("", text, count=1)


def is_placeholder_current_focus(text: str) -> bool:
    match = _CHECKBOX_LINE_RE.search(text)
    if match is None:
        return False
    return match.group("value").strip() == CURRENT_FOCUS_PLACEHOLDER


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
