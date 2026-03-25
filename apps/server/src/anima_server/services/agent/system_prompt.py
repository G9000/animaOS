from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from jinja2 import Environment, StrictUndefined, Template

from anima_server.services.agent.memory_blocks import MemoryBlock, serialize_memory_blocks


@dataclass(frozen=True, slots=True)
class SystemPromptContext:
    persona_template: str = "default"
    persona_content: str = ""
    tool_summaries: Sequence[str] = field(default_factory=tuple)
    memory_blocks: Sequence[MemoryBlock] = field(default_factory=tuple)
    dynamic_identity: str = ""
    user_context: str = ""
    additional_instructions: Sequence[str] = field(default_factory=tuple)
    now: datetime | None = None


class PromptTemplateError(RuntimeError):
    """Raised when prompt or persona templates are invalid or missing."""


TEMPLATES_DIR = Path(__file__).with_name("templates")
SYSTEM_PROMPT_TEMPLATE_PATH = TEMPLATES_DIR / "system_prompt.md.j2"
SYSTEM_RULES_TEMPLATE_PATH = TEMPLATES_DIR / "system_rules.md.j2"
GUARDRAILS_TEMPLATE_PATH = TEMPLATES_DIR / "guardrails.md.j2"
MEMORY_BLOCKS_TEMPLATE_PATH = TEMPLATES_DIR / "memory_blocks.md.j2"
ORIGIN_TEMPLATE_PATH = TEMPLATES_DIR / "origin.md.j2"
PERSONA_TEMPLATES_DIR = TEMPLATES_DIR / "persona"
_TEMPLATE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def build_system_prompt(
    context: SystemPromptContext | None = None,
) -> str:
    resolved = context or SystemPromptContext()
    now = resolved.now or datetime.now(UTC)
    system_rules = render_template(
        SYSTEM_RULES_TEMPLATE_PATH,
        {
            "now_iso": now.isoformat(),
        },
    )
    guardrails = render_template(
        GUARDRAILS_TEMPLATE_PATH,
        {
            "now_iso": now.isoformat(),
        },
    )
    persona = (
        resolved.persona_content.strip()
        if resolved.persona_content.strip()
        else build_persona_prompt(resolved.persona_template)
    )
    dynamic_identity = resolved.dynamic_identity.strip()
    filtered_blocks = tuple(resolved.memory_blocks)
    if dynamic_identity:
        filtered_blocks = tuple(
            block for block in filtered_blocks if block.label not in ("self_identity", "persona")
        )
    else:
        dynamic_identity, persona_from_blocks, filtered_blocks = split_prompt_memory_blocks(
            filtered_blocks
        )
        if persona_from_blocks:
            persona = persona_from_blocks

    memory_blocks = serialize_memory_blocks(filtered_blocks)
    template_context = {
        "system_rules": system_rules,
        "guardrails": guardrails,
        "persona": persona,
        "dynamic_identity": dynamic_identity,
        "persona_template": resolved.persona_template,
        "tool_summaries": [item.strip() for item in resolved.tool_summaries if item.strip()],
        "memory_blocks": memory_blocks,
        "memory_blocks_text": render_memory_blocks_template(memory_blocks),
        "user_context": resolved.user_context.strip(),
        "additional_instructions": [
            item.strip() for item in resolved.additional_instructions if item.strip()
        ],
        "now_iso": now.isoformat(),
    }
    return render_system_prompt_template(template_context)


def invalidate_system_prompt_template_cache() -> None:
    load_template.cache_clear()


def render_system_prompt_template(context: dict[str, Any]) -> str:
    return render_template(SYSTEM_PROMPT_TEMPLATE_PATH, context)


def render_memory_blocks_template(memory_blocks: list[dict[str, object]]) -> str:
    if not memory_blocks:
        return ""
    return render_template(
        MEMORY_BLOCKS_TEMPLATE_PATH,
        {
            "memory_blocks": memory_blocks,
        },
    )


def build_persona_prompt(template_name: str, agent_name: str = "Anima") -> str:
    template_path = resolve_persona_template_path(template_name)
    return render_template(template_path, {"agent_name": agent_name})


def render_persona_seed(template_name: str, agent_name: str = "Anima") -> str:
    """Render a persona template into content to be stored in the DB.

    Used once at provisioning time. The stored content becomes the living
    persona block that evolves through reflection.
    """
    return build_persona_prompt(template_name, agent_name=agent_name)


def render_origin_block(
    agent_name: str = "Anima",
    creator_name: str = "",
) -> str:
    """Render the immutable origin block from the seed template."""
    from anima_server.services.core import get_core_birth_date

    birth_date = get_core_birth_date()
    return render_template(
        ORIGIN_TEMPLATE_PATH,
        {
            "agent_name": agent_name,
            "birth_date": birth_date,
            "creator_name": creator_name or "their creator",
        },
    )


def split_prompt_memory_blocks(
    memory_blocks: Sequence[MemoryBlock],
) -> tuple[str, str, tuple[MemoryBlock, ...]]:
    """Extract dynamic_identity and persona content from memory blocks.

    Returns (dynamic_identity, persona_content, remaining_blocks).
    """
    dynamic_identity = ""
    persona_content = ""
    filtered_blocks: list[Any] = []

    for block in memory_blocks:
        if block.label == "self_identity" and not dynamic_identity:
            dynamic_identity = block.value.strip()
            continue
        if block.label == "self_identity":
            continue
        if block.label == "persona" and not persona_content:
            persona_content = block.value.strip()
            continue
        if block.label == "persona":
            continue
        filtered_blocks.append(block)

    return dynamic_identity, persona_content, tuple(filtered_blocks)


def render_template(path: Path, context: dict[str, Any]) -> str:
    try:
        return load_template(str(path.resolve())).render(**context).strip()
    except FileNotFoundError as exc:
        raise PromptTemplateError(f"Missing prompt template: {path.name}") from exc


def resolve_persona_template_path(template_name: str) -> Path:
    normalized = template_name.strip().lower()
    if not normalized or _TEMPLATE_NAME_RE.fullmatch(normalized) is None:
        raise PromptTemplateError(f"Invalid persona template name: {template_name!r}")

    path = PERSONA_TEMPLATES_DIR / f"{normalized}.md.j2"
    if not path.exists():
        raise PromptTemplateError(f"Unknown persona template: {normalized!r}")
    return path


@lru_cache(maxsize=32)
def load_template(path_str: str) -> Template:
    template_source = Path(path_str).read_text(encoding="utf-8")
    environment = Environment(
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    return environment.from_string(template_source)
