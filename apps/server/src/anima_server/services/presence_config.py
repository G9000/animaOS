from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.models import PresenceConfig


@dataclass(frozen=True)
class PresenceConfigValues:
    user_id: int
    enabled: bool = True
    main_chat_enabled: bool = True
    home_greeting_context_enabled: bool = True
    task_nudges_enabled: bool = True
    memory_nudges_enabled: bool = True
    checkin_nudges_enabled: bool = True
    custom_instruction: str | None = None


def get_presence_config_values(db: Session, user_id: int) -> PresenceConfigValues:
    row = db.scalar(
        select(PresenceConfig).where(PresenceConfig.user_id == user_id)
    )
    if row is None:
        return PresenceConfigValues(user_id=user_id)
    return _to_values(row)


def get_or_create_presence_config(db: Session, user_id: int) -> PresenceConfig:
    row = db.scalar(
        select(PresenceConfig).where(PresenceConfig.user_id == user_id)
    )
    if row is not None:
        return row

    row = PresenceConfig(user_id=user_id)
    db.add(row)
    db.flush()
    return row


def update_presence_config(
    db: Session,
    user_id: int,
    updates: dict[str, object],
) -> PresenceConfigValues:
    row = get_or_create_presence_config(db, user_id)
    field_map = {
        "enabled": "enabled",
        "mainChatEnabled": "main_chat_enabled",
        "homeGreetingContextEnabled": "home_greeting_context_enabled",
        "taskNudgesEnabled": "task_nudges_enabled",
        "memoryNudgesEnabled": "memory_nudges_enabled",
        "checkInNudgesEnabled": "checkin_nudges_enabled",
    }

    for payload_key, model_key in field_map.items():
        if payload_key in updates:
            setattr(row, model_key, bool(updates[payload_key]))

    if "customInstruction" in updates:
        row.custom_instruction = _normalize_instruction(
            updates.get("customInstruction")
        )

    db.flush()
    return _to_values(row)


def _normalize_instruction(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _to_values(row: PresenceConfig) -> PresenceConfigValues:
    return PresenceConfigValues(
        user_id=row.user_id,
        enabled=row.enabled,
        main_chat_enabled=row.main_chat_enabled,
        home_greeting_context_enabled=row.home_greeting_context_enabled,
        task_nudges_enabled=row.task_nudges_enabled,
        memory_nudges_enabled=row.memory_nudges_enabled,
        checkin_nudges_enabled=row.checkin_nudges_enabled,
        custom_instruction=row.custom_instruction,
    )
