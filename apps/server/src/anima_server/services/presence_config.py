from __future__ import annotations

import threading
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.models import PresenceConfig

_consent_locks: dict[int, threading.Lock] = {}
_consent_locks_guard = threading.Lock()


def presence_consent_lock(user_id: int) -> threading.Lock:
    """Per-user lock serializing presence-config CONSENT updates against
    initiative delivery (PR #123 review, P1).

    The presence config (soul store) and pending initiatives (runtime store)
    live in separate databases, so no DB transaction can make "check consent,
    then mark delivered" atomic — a freshness re-read only narrows the TOCTOU.
    This server runs as a single process (desktop deployment), so an in-process
    per-user lock CAN close it: the config PUT holds the lock through its
    commit, and ``list_and_mark_delivered`` holds it from its authoritative
    consent check through the delivered side effect. An opt-out therefore
    either commits before the check (poll serves nothing) or blocks until the
    delivery decision is made (the opt-out post-dates the delivery)."""
    with _consent_locks_guard:
        lock = _consent_locks.get(user_id)
        if lock is None:
            lock = _consent_locks[user_id] = threading.Lock()
        return lock


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
    # IL3 push initiative — off by default (non-negotiable opt-in).
    initiative_enabled: bool = False
    quiet_hours_start: int | None = None
    quiet_hours_end: int | None = None
    # IL7 dream surfacing gate: off | on_ask | ambient (default on_ask).
    dream_sharing: str = "on_ask"


def get_presence_config_values(db: Session, user_id: int) -> PresenceConfigValues:
    """Read portable presence from CoreFS; require unlock once authority is active."""
    from anima_server.services.corefs.authority import (
        AuthorityState,
        core_authority_state_or_none,
    )
    from anima_server.services.corefs.preferences import (
        active_preference_authority_session,
        read_canonical_presence_values,
    )

    authority_session = active_preference_authority_session(user_id)
    if authority_session is not None:
        return read_canonical_presence_values(session=authority_session)
    # An environment that never activated first-release authority (no manifest,
    # or a pre-release manifest that fails closed at unlock) keeps the legacy
    # row fallback; an activated Core requires the canonical read above, and a
    # damaged/unparseable manifest raises here so consent gates fail closed
    # instead of reverting to legacy defaults.
    if core_authority_state_or_none() is AuthorityState.AUTHORITATIVE:
        raise RuntimeError("Canonical presence preferences require an unlocked CoreFS session.")

    row = db.scalar(select(PresenceConfig).where(PresenceConfig.user_id == user_id))
    if row is None:
        return PresenceConfigValues(user_id=user_id)
    return _to_values(row)


def get_or_create_presence_config(db: Session, user_id: int) -> PresenceConfig:
    row = db.scalar(select(PresenceConfig).where(PresenceConfig.user_id == user_id))
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
        "initiativeEnabled": "initiative_enabled",
    }

    for payload_key, model_key in field_map.items():
        if payload_key in updates:
            setattr(row, model_key, bool(updates[payload_key]))

    if "customInstruction" in updates:
        row.custom_instruction = _normalize_instruction(updates.get("customInstruction"))

    for payload_key, model_key in (
        ("quietHoursStart", "quiet_hours_start"),
        ("quietHoursEnd", "quiet_hours_end"),
    ):
        if payload_key in updates:
            setattr(row, model_key, _normalize_quiet_hour(updates.get(payload_key)))

    if "dreamSharing" in updates:
        row.dream_sharing = _normalize_dream_sharing(updates.get("dreamSharing"))

    db.flush()
    return _to_values(row)


def apply_presence_config_updates(
    current: PresenceConfigValues,
    updates: Mapping[str, object],
) -> PresenceConfigValues:
    """Apply the API patch without touching either persistence backend."""
    changed: dict[str, Any] = {}
    field_map = {
        "enabled": "enabled",
        "mainChatEnabled": "main_chat_enabled",
        "homeGreetingContextEnabled": "home_greeting_context_enabled",
        "taskNudgesEnabled": "task_nudges_enabled",
        "memoryNudgesEnabled": "memory_nudges_enabled",
        "checkInNudgesEnabled": "checkin_nudges_enabled",
        "initiativeEnabled": "initiative_enabled",
    }
    for payload_key, value_key in field_map.items():
        if payload_key in updates:
            changed[value_key] = bool(updates[payload_key])
    if "customInstruction" in updates:
        changed["custom_instruction"] = _normalize_instruction(updates.get("customInstruction"))
    for payload_key, value_key in (
        ("quietHoursStart", "quiet_hours_start"),
        ("quietHoursEnd", "quiet_hours_end"),
    ):
        if payload_key in updates:
            changed[value_key] = _normalize_quiet_hour(updates.get(payload_key))
    if "dreamSharing" in updates:
        changed["dream_sharing"] = _normalize_dream_sharing(updates.get("dreamSharing"))
    return replace(current, **changed)


def presence_config_values_to_mapping(values: PresenceConfigValues) -> dict[str, object]:
    return {
        "enabled": values.enabled,
        "mainChatEnabled": values.main_chat_enabled,
        "homeGreetingContextEnabled": values.home_greeting_context_enabled,
        "taskNudgesEnabled": values.task_nudges_enabled,
        "memoryNudgesEnabled": values.memory_nudges_enabled,
        "checkInNudgesEnabled": values.checkin_nudges_enabled,
        "customInstruction": values.custom_instruction,
        "initiativeEnabled": values.initiative_enabled,
        "quietHoursStart": values.quiet_hours_start,
        "quietHoursEnd": values.quiet_hours_end,
        "dreamSharing": values.dream_sharing,
    }


def presence_config_values_from_mapping(
    *,
    user_id: int,
    values: Mapping[str, object],
) -> PresenceConfigValues:
    expected = {
        "enabled",
        "mainChatEnabled",
        "homeGreetingContextEnabled",
        "taskNudgesEnabled",
        "memoryNudgesEnabled",
        "checkInNudgesEnabled",
        "customInstruction",
        "initiativeEnabled",
        "quietHoursStart",
        "quietHoursEnd",
        "dreamSharing",
    }
    if set(values) != expected:
        raise ValueError("Canonical presence preference fields are invalid.")
    boolean_keys = expected - {
        "customInstruction",
        "quietHoursStart",
        "quietHoursEnd",
        "dreamSharing",
    }
    if any(not isinstance(values[key], bool) for key in boolean_keys):
        raise ValueError("Canonical presence preference booleans are invalid.")
    instruction = values["customInstruction"]
    if instruction is not None and (not isinstance(instruction, str) or not instruction.strip()):
        raise ValueError("Canonical presence custom instruction is invalid.")
    quiet_start = values["quietHoursStart"]
    quiet_end = values["quietHoursEnd"]
    if any(
        value is not None
        and (isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 23)
        for value in (quiet_start, quiet_end)
    ):
        raise ValueError("Canonical presence quiet hours are invalid.")
    dream_sharing = values["dreamSharing"]
    if dream_sharing not in _DREAM_SHARING_MODES:
        raise ValueError("Canonical dream-sharing preference is invalid.")
    return PresenceConfigValues(
        user_id=user_id,
        enabled=values["enabled"],
        main_chat_enabled=values["mainChatEnabled"],
        home_greeting_context_enabled=values["homeGreetingContextEnabled"],
        task_nudges_enabled=values["taskNudgesEnabled"],
        memory_nudges_enabled=values["memoryNudgesEnabled"],
        checkin_nudges_enabled=values["checkInNudgesEnabled"],
        custom_instruction=instruction,
        initiative_enabled=values["initiativeEnabled"],
        quiet_hours_start=quiet_start,
        quiet_hours_end=quiet_end,
        dream_sharing=dream_sharing,
    )


_DREAM_SHARING_MODES = ("off", "on_ask", "ambient")


def _normalize_dream_sharing(value: object) -> str:
    """Coerce to a valid dream-sharing mode; anything unrecognized falls back
    to the default ``on_ask`` rather than persisting a bad value."""
    if isinstance(value, str) and value in _DREAM_SHARING_MODES:
        return value
    return "on_ask"


def _normalize_quiet_hour(value: object) -> int | None:
    if value is None:
        return None
    try:
        hour = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return hour if 0 <= hour <= 23 else None


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
        initiative_enabled=row.initiative_enabled,
        quiet_hours_start=row.quiet_hours_start,
        quiet_hours_end=row.quiet_hours_end,
        dream_sharing=row.dream_sharing,
    )
