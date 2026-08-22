"""Machine-local Telegram/Discord link registry excluded from Core transfer."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from threading import RLock

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from anima_server.config import settings
from anima_server.models import DiscordLink, TelegramLink

_lock = RLock()
_VERSION = 1
_PROVIDERS = frozenset({"telegram", "discord"})


class IntegrationRegistryError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class IntegrationLink:
    provider: str
    external_id: str
    user_id: int


def _registry_path() -> Path:
    if not settings.runtime_instance_data_dir:
        raise IntegrationRegistryError("Runtime instance is not bound.")
    return Path(settings.runtime_instance_data_dir) / "config" / "integration-links.json"


def _load(path: Path) -> list[IntegrationLink]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise IntegrationRegistryError("Integration registry is unreadable.") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("version") != _VERSION
        or not isinstance(payload.get("links"), list)
    ):
        raise IntegrationRegistryError("Integration registry has an unsupported format.")
    links: list[IntegrationLink] = []
    for raw in payload["links"]:
        if not isinstance(raw, dict):
            raise IntegrationRegistryError("Integration registry entry is invalid.")
        provider = raw.get("provider")
        external_id = raw.get("externalId")
        user_id = raw.get("userId")
        if (
            provider not in _PROVIDERS
            or not isinstance(external_id, str)
            or not external_id
            or isinstance(user_id, bool)
            or not isinstance(user_id, int)
            or user_id < 0
        ):
            raise IntegrationRegistryError(
                "Integration registry entry is invalid "
                f"(provider={provider!r}, external_type={type(external_id).__name__}, "
                f"user_id={user_id!r})."
            )
        links.append(IntegrationLink(provider, external_id, user_id))
    identities = {(item.provider, item.external_id) for item in links}
    user_bindings = {(item.provider, item.user_id) for item in links}
    if len(identities) != len(links) or len(user_bindings) != len(links):
        raise IntegrationRegistryError("Integration registry contains duplicate bindings.")
    return links


def _write(path: Path, links: list[IntegrationLink]) -> None:
    ordered = sorted(links, key=lambda item: (item.provider, item.external_id, item.user_id))
    payload = {
        "version": _VERSION,
        "links": [
            {
                "provider": item.provider,
                "externalId": item.external_id,
                "userId": item.user_id,
            }
            for item in ordered
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    if _load(path) != ordered:
        raise IntegrationRegistryError("Integration registry verification failed.")


def link_integration(*, provider: str, external_id: str, user_id: int) -> IntegrationLink:
    if provider not in _PROVIDERS or not external_id or user_id < 0:
        raise IntegrationRegistryError("Integration link is invalid.")
    path = _registry_path()
    with _lock:
        links = [
            item
            for item in _load(path)
            if item.provider != provider
            or (item.external_id != external_id and item.user_id != user_id)
        ]
        result = IntegrationLink(provider, external_id, user_id)
        links.append(result)
        _write(path, links)
        return result


def lookup_integration(*, provider: str, external_id: str) -> IntegrationLink | None:
    path = _registry_path()
    with _lock:
        return next(
            (
                item
                for item in _load(path)
                if item.provider == provider and item.external_id == external_id
            ),
            None,
        )


def unlink_integration(*, provider: str, external_id: str) -> None:
    path = _registry_path()
    with _lock:
        current = _load(path)
        retained = [
            item
            for item in current
            if item.provider != provider or item.external_id != external_id
        ]
        if retained != current:
            _write(path, retained)


def migrate_legacy_integration_links(db: Session, *, user_id: int) -> int:
    """Copy, verify, then delete this user's portable legacy link rows."""
    normalized_user_id = int(user_id)
    telegram = list(
        db.scalars(
            select(TelegramLink).where(TelegramLink.user_id == normalized_user_id)
        ).all()
    )
    discord = list(
        db.scalars(
            select(DiscordLink).where(DiscordLink.user_id == normalized_user_id)
        ).all()
    )
    if not telegram and not discord:
        return 0
    path = _registry_path()
    additions = [
        *(
            IntegrationLink("telegram", str(item.chat_id), normalized_user_id)
            for item in telegram
        ),
        *(
            IntegrationLink("discord", str(item.channel_id), normalized_user_id)
            for item in discord
        ),
    ]
    with _lock:
        links = _load(path)
        for addition in additions:
            conflict = next(
                (
                    item
                    for item in links
                    if item.provider == addition.provider
                    and (
                        item.external_id == addition.external_id
                        or item.user_id == addition.user_id
                    )
                ),
                None,
            )
            if conflict is not None and conflict != addition:
                raise IntegrationRegistryError(
                    "Legacy integration link conflicts with the machine-local registry."
                )
            if conflict is None:
                links.append(addition)
        _write(path, links)
        verified = _load(path)
        if any(item not in verified for item in additions):
            raise IntegrationRegistryError("Legacy integration link verification failed.")
    db.execute(
        delete(TelegramLink).where(TelegramLink.user_id == normalized_user_id)
    )
    db.execute(delete(DiscordLink).where(DiscordLink.user_id == normalized_user_id))
    db.commit()
    return len(additions)
