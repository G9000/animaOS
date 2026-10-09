from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from anima_server.models import SoulOwner
from anima_server.services.crypto import generate_domain_keys
from anima_server.services.data_crypto import ALL_DOMAINS


def normalize_username(username: str) -> str:
    return username.strip().lower()


def get_user_by_id(db: Session, user_id: int) -> SoulOwner | None:
    return db.get(SoulOwner, user_id)


@dataclass(frozen=True, slots=True)
class AccountProfile:
    id: int
    username: str
    display_name: str
    gender: str | None = None
    age: int | None = None
    birthday: str | None = None
    created_at: object = None
    updated_at: object = None


def create_user(
    db: Session,
    username: str,
    password: str,
    display_name: str,
    agent_name: str = "ANIMA",
    user_directive: str = "",
    relationship: str = "companion",
    persona_template: str = "default",
    agent_type: str = "companion",
    *,
    user_id: int | None = None,
    commit: bool = True,
) -> tuple[AccountProfile, dict[str, bytes]]:
    from anima_server.models import AgentProfile, SelfModelBlock
    from anima_server.services.agent.system_prompt import render_origin_block, render_persona_seed

    if user_id is None:
        raise ValueError("Owner compatibility id is required")

    deks = generate_domain_keys(ALL_DOMAINS)
    from anima_server.services.core import get_owner_id

    owner = SoulOwner(id=user_id, core_owner_id=get_owner_id())
    db.add(owner)
    db.flush()
    user = AccountProfile(id=user_id, username=username, display_name=display_name)

    # Seed structured agent profile for fast lookup
    db.add(
        AgentProfile(
            user_id=user.id,
            agent_name=agent_name,
            creator_name=display_name,
            relationship=relationship,
            agent_type=agent_type,
        )
    )

    # Seed immutable origin block
    # SQLCipher protects this seed at rest. Later updates apply field-level
    # encryption through set_self_model_block() using the current domain keys.
    soul_content = render_origin_block(
        agent_name=agent_name, creator_name=display_name, agent_type=agent_type
    )
    db.add(
        SelfModelBlock(
            user_id=user.id,
            section="soul",
            content=soul_content,
            version=1,
            updated_by="system",
        )
    )

    # Seed persona from template (mutable — evolves through reflection)
    # Auto-select mirror template when agent_type is mirror and template is default
    if agent_type == "mirror" and persona_template == "default":
        persona_template = "mirror"

    # Validate template name to prevent path traversal
    from anima_server.services.agent.system_prompt import resolve_persona_template_path

    try:
        resolve_persona_template_path(persona_template)
    except Exception as exc:
        raise ValueError(f"Invalid persona template: {persona_template!r}") from exc

    persona_content = render_persona_seed(
        persona_template, agent_name=agent_name, creator_name=display_name
    )
    db.add(
        SelfModelBlock(
            user_id=user.id,
            section="persona",
            content=persona_content,
            version=1,
            updated_by="system",
        )
    )

    # Seed human block — the agent's living understanding of the user.
    # Starts with basic facts; the agent enriches this through conversation
    # via the update_human_memory tool.
    human_lines = [f"Name: {display_name}"]
    if relationship.strip():
        human_lines.append(f"Relationship: {relationship}")
    db.add(
        SelfModelBlock(
            user_id=user.id,
            section="human",
            content="\n".join(human_lines),
            version=1,
            updated_by="system",
        )
    )

    db.add(
        SelfModelBlock(
            user_id=user.id,
            section="world",
            content="",
            version=1,
            updated_by="system",
        )
    )

    # Seed user directive if provided
    if user_directive.strip():
        db.add(
            SelfModelBlock(
                user_id=user.id,
                section="user_directive",
                content=user_directive.strip(),
                version=1,
                updated_by="system",
            )
        )

    if commit:
        db.commit()
    else:
        db.flush()
    db.refresh(owner)
    from dataclasses import replace

    return replace(user, created_at=owner.created_at, updated_at=owner.updated_at), deks


def serialize_user(user: AccountProfile) -> dict[str, object]:
    from anima_server.services.core import get_owner_id

    payload: dict[str, object] = {
        "id": user.id,
        "username": user.username,
        "name": user.display_name,
        "gender": user.gender,
        "age": user.age,
        "birthday": user.birthday,
        "ownerId": get_owner_id(),
        "profileAvailable": True,
        "filesystemAvailable": True,
    }
    if user.created_at is not None:
        payload["createdAt"] = user.created_at.isoformat()
    if user.updated_at is not None:
        payload["updatedAt"] = user.updated_at.isoformat()
    return payload


def serialize_soul_owner(owner: SoulOwner) -> dict[str, object]:
    """A usable Soul identity with explicitly unavailable private FS profile fields."""
    return {
        "id": owner.id,
        "ownerId": owner.core_owner_id,
        "username": "",
        "name": "",
        "gender": None,
        "age": None,
        "birthday": None,
        "createdAt": owner.created_at.isoformat(),
        "updatedAt": owner.updated_at.isoformat(),
        "profileAvailable": False,
        "filesystemAvailable": False,
    }


def verify_current_password(password: str) -> bool:
    """Reauthenticate against the current AAD-bound manifest hierarchy."""
    from cryptography.exceptions import InvalidTag

    from anima_server.services.corefs.keyslots import unlock_manifest_key_hierarchy
    from anima_server.services.corefs.types import PayloadScope, WrappingPath
    from anima_server.services.sessions import get_sqlcipher_key

    try:
        roots = unlock_manifest_key_hierarchy(
            credential=password,
            wrapping_path=WrappingPath.PASSWORD,
        )
        if roots.scope is PayloadScope.FS:
            return roots.sqlcipher_key is None and bool(roots.frks)
        cached = get_sqlcipher_key()
        return roots.sqlcipher_key is not None and (cached is None or roots.sqlcipher_key == cached)
    except (InvalidTag, KeyError, TypeError, ValueError):
        return False
