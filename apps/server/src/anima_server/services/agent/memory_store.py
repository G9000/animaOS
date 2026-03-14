from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
import re
from pathlib import Path

from anima_server.services.storage import get_user_data_dir

MEMORY_ROOT = Path("memory")
CURRENT_FOCUS_PATH = Path("user") / "current-focus.md"
FACTS_PATH = Path("user") / "facts.md"
PREFERENCES_PATH = Path("user") / "preferences.md"
DAILY_PATH = Path("daily")
_BULLET_LINE_RE = re.compile(r"^- (?!\[)(?P<value>.+?)\s*$", re.MULTILINE)


def resolve_memory_path(user_id: int, relative_path: Path) -> Path:
    return get_user_data_dir(user_id) / MEMORY_ROOT / relative_path


def read_memory_text(user_id: int, relative_path: Path) -> str | None:
    path = resolve_memory_path(user_id, relative_path)
    if not path.is_file():
        return None

    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def write_memory_text(user_id: int, relative_path: Path, content: str) -> Path:
    path = resolve_memory_path(user_id, relative_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def append_memory_text(
    user_id: int,
    relative_path: Path,
    content: str,
    *,
    separator: str = "\n\n",
) -> Path:
    existing = read_memory_text(user_id, relative_path)
    if existing and existing.strip():
        merged = existing.rstrip() + separator + content.strip()
    else:
        merged = content.strip()
    return write_memory_text(user_id, relative_path, merged + "\n")


def append_unique_bullets(
    user_id: int,
    relative_path: Path,
    bullet_items: Sequence[str],
) -> list[str]:
    normalized_items: list[str] = []
    for item in bullet_items:
        normalized = normalize_bullet_item(item)
        if normalized:
            normalized_items.append(normalized)
    if not normalized_items:
        return []

    existing = read_memory_text(user_id, relative_path) or ""
    existing_keys = {normalize_bullet_key(item) for item in extract_bullet_items(existing)}

    additions: list[str] = []
    for item in normalized_items:
        key = normalize_bullet_key(item)
        if key in existing_keys:
            continue
        additions.append(item)
        existing_keys.add(key)

    if not additions:
        return []

    addition_text = "\n".join(f"- {item}" for item in additions)
    append_memory_text(user_id, relative_path, addition_text, separator="\n")
    return additions


def extract_bullet_items(text: str) -> list[str]:
    return [match.group("value").strip() for match in _BULLET_LINE_RE.finditer(text)]


def render_current_focus(focus: str, note: str | None = None) -> str:
    lines = ["# Current Focus", "", f"- [ ] {focus.strip()}"]
    if note and note.strip():
        lines.extend(["", "## Note", note.strip()])
    return "\n".join(lines).strip() + "\n"


def write_current_focus(user_id: int, focus: str, note: str | None = None) -> bool:
    content = render_current_focus(focus, note)
    existing = read_memory_text(user_id, CURRENT_FOCUS_PATH)
    if existing is not None and existing.strip() == content.strip():
        return False
    write_memory_text(user_id, CURRENT_FOCUS_PATH, content)
    return True


def append_daily_log_entry(
    user_id: int,
    *,
    user_message: str,
    assistant_response: str,
    now: datetime | None = None,
) -> Path:
    timestamp = now or datetime.now(UTC)
    filename = f"{timestamp.date().isoformat()}.md"
    entry = render_daily_log_entry(
        timestamp=timestamp,
        user_message=user_message,
        assistant_response=assistant_response,
    )
    return append_memory_text(user_id, DAILY_PATH / filename, entry)


def render_daily_log_entry(
    *,
    timestamp: datetime,
    user_message: str,
    assistant_response: str,
) -> str:
    return "\n".join(
        [
            f"## {timestamp.isoformat()}",
            "",
            "### User",
            to_blockquote(user_message),
            "",
            "### Assistant",
            to_blockquote(assistant_response),
        ]
    ).strip()


def to_blockquote(text: str) -> str:
    lines = [line.rstrip() for line in text.strip().splitlines() if line.strip()]
    if not lines:
        return "> "
    return "\n".join(f"> {line}" for line in lines)


def normalize_bullet_item(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" \t\r\n\"'`.,;:!?")


def normalize_bullet_key(value: str) -> str:
    return normalize_bullet_item(value).lower()
