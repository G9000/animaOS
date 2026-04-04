from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.models import MemoryEpisode, User
from anima_server.models.task import Task
from anima_server.services.agent.memory_store import (
    get_current_focus,
    get_memory_items_scored,
    touch_memory_items,
)
from anima_server.services.data_crypto import df

# -- Mirror agent description overrides ------------------------------------
# When agent_type == "mirror", these replace the default companion-framed
# descriptions so the LLM contextualizes blocks as first-person identity
# rather than third-person observation.
_MIRROR_DESCRIPTIONS: dict[str, str] = {
    "soul": "My origin — immutable biographical truth. I am a reflection.",
    "persona": "How I think and express myself — absorbed from the person I reflect. Evolves as I learn more.",
    "human": "Who I am — my identity, personality, values, and way of being. This is first-person, not observation.",
    "user_directive": "Instructions from my external self on how to calibrate.",
    "self_identity": "How I relate to my external self — the distance between reflection and source.",
    "self_inner_state": "My current cognitive state — what I'm processing, what's unresolved.",
    "self_working_memory": "Things I'm holding in mind across sessions.",
    "self_growth_log": "How I've evolved — how my reflection has become more precise.",
    "self_intentions": "My active goals and learned behavioral patterns.",
    "emotional_context": "My emotional state right now.",
    "emotional_patterns": "My enduring emotional tendencies.",
    "facts": "Things I know about myself.",
    "preferences": "My preferences.",
    "goals": "My goals and aspirations.",
    "relationships": "People in my life.",
    "relevant_memories": "Relevant memories. Use these naturally.",
    "user_tasks": "My task list.",
    "current_focus": "My current focus.",
    "thread_summary": "Summary of earlier conversation (compacted).",
    "recent_episodes": "My recent conversation experiences.",
    "session_memory": "Working notes for this conversation session.",
    "pending_memory_updates": "Memory updates from recent conversations that have not yet been fully integrated.",
    "knowledge_graph": "Relationships between entities in my life. Use these naturally for context.",
}


def _get_agent_type(db: Session, *, user_id: int) -> str:
    """Return the agent_type for a user, defaulting to 'companion'."""
    from anima_server.models.consciousness import AgentProfile

    profile = db.scalar(
        select(AgentProfile).where(AgentProfile.user_id == user_id)
    )
    if profile is not None:
        return profile.agent_type or "companion"
    return "companion"


def _desc(label: str, default: str, agent_type: str) -> str:
    """Return the mirror description if agent_type is mirror, else the default."""
    if agent_type == "mirror":
        return _MIRROR_DESCRIPTIONS.get(label, default)
    return default


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
    query_embedding: list[float] | None = None,
    query: str | None = None,
    runtime_db: Session | None = None,
) -> tuple[MemoryBlock, ...]:
    blocks: list[MemoryBlock] = []
    agent_type = _get_agent_type(db, user_id=user_id)

    # Soul block (Priority 0 — immutable biography from DB)
    soul_block = build_soul_biography_block(
        db, user_id=user_id, agent_type=agent_type)
    blocks.append(soul_block)

    # Persona block (Priority 0 — living persona, seeded at provisioning, evolves)
    persona_block = build_persona_block(
        db, user_id=user_id, runtime_db=runtime_db, agent_type=agent_type)
    if persona_block is not None:
        blocks.append(persona_block)

    # Human core block (Priority 0 — agent's living understanding of the user)
    human_core_block = build_human_core_block(
        db, user_id=user_id, runtime_db=runtime_db, agent_type=agent_type)
    if human_core_block is not None:
        blocks.append(human_core_block)

    # User directive (Priority 0 — user-authored customisation)
    user_directive_block = build_user_directive_memory_block(
        db, user_id=user_id, agent_type=agent_type)
    if user_directive_block is not None:
        blocks.append(user_directive_block)

    pending_ops_block = build_pending_ops_block(
        db, runtime_db, user_id=user_id, agent_type=agent_type)
    if pending_ops_block is not None:
        blocks.append(pending_ops_block)

    # Self-model blocks (Priority 1 — always present, never truncated)
    for sm_block in build_self_model_memory_blocks(db, user_id=user_id, pg_db=runtime_db, agent_type=agent_type):
        blocks.append(sm_block)

    # Emotional context (Priority 2 — momentary signals from runtime)
    emotional_block = build_emotional_context_block(
        runtime_db or db, user_id=user_id, agent_type=agent_type)
    if emotional_block is not None:
        blocks.append(emotional_block)

    # Emotional patterns (Priority 2 — enduring patterns from soul)
    emotional_patterns_block = build_emotional_patterns_block(
        db, user_id=user_id, agent_type=agent_type)
    if emotional_patterns_block is not None:
        blocks.append(emotional_patterns_block)

    # Semantic retrieval block (Priority 3 — query-relevant memories)
    if semantic_results:
        semantic_block = _build_semantic_block(
            semantic_results, agent_type=agent_type)
        if semantic_block is not None:
            blocks.append(semantic_block)

    facts_block = build_facts_memory_block(
        db, user_id=user_id, query_embedding=query_embedding, runtime_db=runtime_db, agent_type=agent_type,
    )
    if facts_block is not None:
        blocks.append(facts_block)

    preferences_block = build_preferences_memory_block(
        db, user_id=user_id, query_embedding=query_embedding, runtime_db=runtime_db, agent_type=agent_type,
    )
    if preferences_block is not None:
        blocks.append(preferences_block)

    goals_block = build_goals_memory_block(
        db, user_id=user_id, query_embedding=query_embedding, runtime_db=runtime_db, agent_type=agent_type,
    )
    if goals_block is not None:
        blocks.append(goals_block)

    tasks_block = build_tasks_memory_block(
        db, user_id=user_id, agent_type=agent_type)
    if tasks_block is not None:
        blocks.append(tasks_block)

    relationships_block = build_relationships_memory_block(
        db, user_id=user_id, query_embedding=query_embedding, runtime_db=runtime_db, agent_type=agent_type,
    )
    if relationships_block is not None:
        blocks.append(relationships_block)

    # Knowledge graph block (Priority 4 — entity-relationship context)
    kg_block = build_knowledge_graph_block(
        db, user_id=user_id, query=query, agent_type=agent_type)
    if kg_block is not None:
        blocks.append(kg_block)

    current_focus_block = build_current_focus_memory_block(
        db, user_id=user_id, agent_type=agent_type)
    if current_focus_block is not None:
        blocks.append(current_focus_block)

    summary_block = build_thread_summary_block(
        thread_id=thread_id, runtime_db=runtime_db, agent_type=agent_type)
    if summary_block is not None:
        blocks.append(summary_block)

    episodes_block = build_episodes_memory_block(
        db, user_id=user_id, agent_type=agent_type)
    if episodes_block is not None:
        blocks.append(episodes_block)

    # Session notes live in PG (runtime_session_notes); skip when no runtime DB.
    session_block = build_session_memory_block(
        runtime_db, thread_id=thread_id, user_id=user_id, agent_type=agent_type,
    ) if runtime_db is not None else None
    if session_block is not None:
        blocks.append(session_block)

    return tuple(blocks)


def _build_semantic_block(
    results: list[tuple[int, str, float]],
    agent_type: str = "companion",
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
        description=_desc(
            "relevant_memories",
            "Memories semantically relevant to what the user just said. Use these naturally — don't list them back.",
            agent_type,
        ),
        value="\n".join(lines),
    )


def build_facts_memory_block(
    db: Session,
    *,
    user_id: int,
    query_embedding: list[float] | None = None,
    runtime_db: Session | None = None,
    agent_type: str = "companion",
) -> MemoryBlock | None:
    items = get_memory_items_scored(
        db, user_id=user_id, category="fact", limit=30, query_embedding=query_embedding
    )
    if not items:
        return None
    touch_memory_items(db, items, runtime_db=runtime_db)
    value = "\n".join(
        f"- {df(user_id, item.content, table='memory_items', field='content')}" for item in items
    )
    if len(value) > 2000:
        value = value[:2000]
    return MemoryBlock(
        label="facts",
        description=_desc("facts", "Known facts about the user.", agent_type),
        value=value,
    )


def build_preferences_memory_block(
    db: Session,
    *,
    user_id: int,
    query_embedding: list[float] | None = None,
    runtime_db: Session | None = None,
    agent_type: str = "companion",
) -> MemoryBlock | None:
    items = get_memory_items_scored(
        db, user_id=user_id, category="preference", limit=20, query_embedding=query_embedding
    )
    if not items:
        return None
    touch_memory_items(db, items, runtime_db=runtime_db)
    value = "\n".join(
        f"- {df(user_id, item.content, table='memory_items', field='content')}" for item in items
    )
    if len(value) > 2000:
        value = value[:2000]
    return MemoryBlock(
        label="preferences",
        description=_desc("preferences", "User preferences.", agent_type),
        value=value,
    )


def build_goals_memory_block(
    db: Session,
    *,
    user_id: int,
    query_embedding: list[float] | None = None,
    runtime_db: Session | None = None,
    agent_type: str = "companion",
) -> MemoryBlock | None:
    items = get_memory_items_scored(
        db, user_id=user_id, category="goal", limit=15, query_embedding=query_embedding
    )
    if not items:
        return None
    touch_memory_items(db, items, runtime_db=runtime_db)
    value = "\n".join(
        f"- {df(user_id, item.content, table='memory_items', field='content')}" for item in items
    )
    if len(value) > 1500:
        value = value[:1500]
    return MemoryBlock(
        label="goals",
        description=_desc(
            "goals", "User's goals and aspirations.", agent_type),
        value=value,
    )


def build_tasks_memory_block(
    db: Session,
    *,
    user_id: int,
    agent_type: str = "companion",
) -> MemoryBlock | None:
    """Build a memory block with the user's open tasks and recently completed ones."""
    from datetime import UTC, datetime

    open_tasks = list(
        db.scalars(
            select(Task)
            .where(Task.user_id == user_id, Task.done == False)  # noqa: E712
            .order_by(Task.priority.desc(), Task.created_at.desc())
            .limit(15)
        ).all()
    )

    if not open_tasks:
        return None

    today = datetime.now(UTC).strftime("%Y-%m-%d")
    lines: list[str] = []
    overdue: list[str] = []

    for t in open_tasks:
        line = f"- {t.text} (priority {t.priority})"
        if t.due_date:
            line += f" due {t.due_date}"
            if t.due_date < today:
                overdue.append(t.text)
        lines.append(line)

    header_parts = [
        f"{len(open_tasks)} open task{'s' if len(open_tasks) != 1 else ''}"]
    if overdue:
        header_parts.append(f"{len(overdue)} overdue")
    header = ", ".join(header_parts) + f" (today: {today})"

    value = header + "\n" + "\n".join(lines)
    if len(value) > 1500:
        value = value[:1500]

    return MemoryBlock(
        label="user_tasks",
        description=_desc(
            "user_tasks",
            "The user's task list. Reference naturally — mention overdue or upcoming deadlines when relevant. You can create, complete, and list tasks with your tools.",
            agent_type,
        ),
        value=value,
    )


def build_relationships_memory_block(
    db: Session,
    *,
    user_id: int,
    query_embedding: list[float] | None = None,
    runtime_db: Session | None = None,
    agent_type: str = "companion",
) -> MemoryBlock | None:
    items = get_memory_items_scored(
        db, user_id=user_id, category="relationship", limit=15, query_embedding=query_embedding
    )
    if not items:
        return None
    touch_memory_items(db, items, runtime_db=runtime_db)
    value = "\n".join(
        f"- {df(user_id, item.content, table='memory_items', field='content')}" for item in items
    )
    if len(value) > 1500:
        value = value[:1500]
    return MemoryBlock(
        label="relationships",
        description=_desc(
            "relationships", "People and relationships the user has mentioned.", agent_type),
        value=value,
    )


def build_current_focus_memory_block(
    db: Session,
    *,
    user_id: int,
    agent_type: str = "companion",
) -> MemoryBlock | None:
    focus = get_current_focus(db, user_id=user_id)
    if not focus:
        return None
    return MemoryBlock(
        label="current_focus",
        description=_desc(
            "current_focus", "User's current focus.", agent_type),
        value=focus,
    )


def build_thread_summary_block(
    *,
    thread_id: int,
    runtime_db: Session | None = None,
    agent_type: str = "companion",
) -> MemoryBlock | None:
    from anima_server.models.runtime import RuntimeMessage

    # Use the caller-provided runtime session when available; fall back to
    # opening our own session from the factory.  Opening our own session is
    # fine in production (PG connection pool) but would conflict with
    # StaticPool in-memory SQLite used by the test harness.
    own_session: Session | None = None
    session = runtime_db
    if session is None:
        from anima_server.db.runtime import get_runtime_session_factory

        try:
            factory = get_runtime_session_factory()
        except RuntimeError:
            return None  # runtime engine not initialized (e.g., tests)
        own_session = factory()
        session = own_session

    try:
        summary_row = session.scalar(
            select(RuntimeMessage)
            .where(
                RuntimeMessage.thread_id == thread_id,
                RuntimeMessage.role == "summary",
                RuntimeMessage.is_in_context.is_(True),
            )
            .order_by(RuntimeMessage.sequence_id.desc())
            .limit(1)
        )
    finally:
        if own_session is not None:
            own_session.close()

    if summary_row is None:
        return None

    # Runtime content is plaintext — no df() decryption needed.
    summary_text = (summary_row.content_text or "").strip()
    if not summary_text:
        return None

    return MemoryBlock(
        label="thread_summary",
        description=_desc(
            "thread_summary", "Summary of earlier conversation (compacted).", agent_type),
        value=summary_text,
    )


def build_episodes_memory_block(
    db: Session,
    *,
    user_id: int,
    agent_type: str = "companion",
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
        lines.append(
            f"- {ep.date}: {df(user_id, ep.summary, table='memory_episodes', field='summary')} (Topics: {topics})"
        )
    return MemoryBlock(
        label="recent_episodes",
        description=_desc(
            "recent_episodes", "Recent conversation experiences with the user.", agent_type),
        value="\n".join(lines),
    )


def build_session_memory_block(
    db: Session,
    *,
    thread_id: int,
    user_id: int = 0,
    agent_type: str = "companion",
) -> MemoryBlock | None:
    from anima_server.services.agent.session_memory import (
        get_session_notes,
        render_session_memory_text,
    )

    notes = get_session_notes(db, thread_id=thread_id, active_only=True)
    if not notes:
        return None

    text = render_session_memory_text(notes, user_id=user_id)
    if not text:
        return None

    return MemoryBlock(
        label="session_memory",
        description=_desc(
            "session_memory",
            "Working notes for this conversation session. You can update these with the note_to_self tool.",
            agent_type,
        ),
        value=text,
        read_only=False,
    )


def build_soul_biography_block(
    db: Session,
    *,
    user_id: int,
    agent_type: str = "companion",
) -> MemoryBlock:
    """Build the immutable origin block from the DB."""
    from anima_server.models import SelfModelBlock

    block = db.scalar(
        select(SelfModelBlock).where(
            SelfModelBlock.user_id == user_id,
            SelfModelBlock.section == "soul",
        )
    )
    value = (
        df(user_id, block.content, table="self_model_blocks", field="content").strip()
        if block is not None
        else ""
    )

    return MemoryBlock(
        label="soul",
        description=_desc(
            "soul",
            "Private background facts about my origin. Relevant when asked who I am or where I came from, not something to volunteer in ordinary conversation.",
            agent_type,
        ),
        value=value,
    )


def _read_soul_block_content(
    db: Session,
    *,
    user_id: int,
    section: str,
) -> tuple[str, bool]:
    from anima_server.models import SelfModelBlock

    block = db.scalar(
        select(SelfModelBlock).where(
            SelfModelBlock.user_id == user_id,
            SelfModelBlock.section == section,
        )
    )
    if block is None:
        return "", False
    return df(user_id, block.content, table="self_model_blocks", field="content").strip(), True


def build_merged_block_content(
    soul_db: Session,
    runtime_db: Session,
    *,
    user_id: int,
    section: str,
) -> str:
    """Return the soul block content with pending ops applied on top."""
    from anima_server.services.agent.pending_ops import apply_pending_ops, get_pending_ops

    base_content, _exists = _read_soul_block_content(
        soul_db, user_id=user_id, section=section)
    pending_ops = get_pending_ops(
        runtime_db, user_id=user_id, target_block=section)
    return apply_pending_ops(base_content, pending_ops)


def build_persona_block(
    db: Session,
    *,
    user_id: int,
    runtime_db: Session | None = None,
    agent_type: str = "companion",
) -> MemoryBlock | None:
    """Build the living persona block from the DB.

    Seeded from a template at provisioning and evolved through reflection.
    """
    plaintext, exists = _read_soul_block_content(
        db, user_id=user_id, section="persona")
    if runtime_db is not None and exists:
        plaintext = build_merged_block_content(
            db,
            runtime_db,
            user_id=user_id,
            section="persona",
        )
    if not plaintext:
        return None

    return MemoryBlock(
        label="persona",
        description=_desc(
            "persona",
            "My core personality, voice, and communication style — seeded at birth and evolved slowly through reflection. This is HOW I express myself.",
            agent_type,
        ),
        value=plaintext,
    )


def build_human_core_block(
    db: Session,
    *,
    user_id: int,
    runtime_db: Session | None = None,
    agent_type: str = "companion",
) -> MemoryBlock | None:
    """Build the agent's living understanding of the user.

    Combines two sources into a single block:
    1. Ground-truth profile fields from the User model (name, age, birthday, etc.)
    2. Agent-authored understanding from SelfModelBlock(section="human"),
       seeded at provisioning and updated mid-conversation via the
       update_human_memory tool.
    """
    # User model profile fields (ground truth set through the UI)
    user = db.get(User, user_id)
    profile_lines: list[str] = []
    if user is not None:
        if user.display_name.strip():
            profile_lines.append(f"Name: {user.display_name.strip()}")
        if user.gender:
            profile_lines.append(f"Gender: {user.gender}")
        if user.age is not None:
            profile_lines.append(f"Age: {user.age}")
        if user.birthday:
            profile_lines.append(f"Birthday: {user.birthday}")

    # Agent-authored understanding (mutable via update_human_memory tool)
    agent_understanding, exists = _read_soul_block_content(
        db, user_id=user_id, section="human")
    if runtime_db is not None and exists:
        agent_understanding = build_merged_block_content(
            db,
            runtime_db,
            user_id=user_id,
            section="human",
        )

    parts: list[str] = []
    if profile_lines:
        parts.append("\n".join(profile_lines))
    if agent_understanding:
        parts.append(agent_understanding)

    if not parts:
        return None

    return MemoryBlock(
        label="human",
        description=_desc(
            "human",
            "What I know about this person — profile facts and my evolving understanding. Use the update_human_memory tool to update the understanding section.",
            agent_type,
        ),
        value="\n".join(parts),
        read_only=False,
    )


def build_pending_ops_block(
    soul_db: Session,
    runtime_db: Session | None,
    *,
    user_id: int,
    agent_type: str = "companion",
) -> MemoryBlock | None:
    """Render pending updates that do not yet have a soul block to merge into.

    Ops are grouped by target_block and applied in order so that a
    ``full_replace`` correctly supersedes earlier appends, preventing
    contradictory content from appearing in the prompt.
    """
    if runtime_db is None:
        return None

    from anima_server.services.agent.pending_ops import apply_pending_ops, get_pending_ops

    ops = get_pending_ops(runtime_db, user_id=user_id)
    if not ops:
        return None

    # Collect ops for blocks that don't exist in soul yet
    orphaned_ops: dict[str, list] = {}
    for op in ops:
        _content, exists = _read_soul_block_content(
            soul_db,
            user_id=user_id,
            section=op.target_block,
        )
        if exists:
            continue
        orphaned_ops.setdefault(op.target_block, []).append(op)

    if not orphaned_ops:
        return None

    # Apply ops in order per block to get the final collapsed content
    lines: list[str] = []
    for block_name, block_ops in orphaned_ops.items():
        collapsed = apply_pending_ops("", block_ops)
        if collapsed:
            lines.append(f"- [{block_name}] (pending): {collapsed}")

    if not lines:
        return None

    return MemoryBlock(
        label="pending_memory_updates",
        description=_desc(
            "pending_memory_updates",
            "Memory updates from recent conversations that have not yet been fully integrated. Treat these as current knowledge.",
            agent_type,
        ),
        value="\n".join(lines),
    )


def build_user_directive_memory_block(
    db: Session,
    *,
    user_id: int,
    agent_type: str = "companion",
) -> MemoryBlock | None:
    """Build a memory block from the user's directive."""
    from anima_server.models import SelfModelBlock

    block = db.scalar(
        select(SelfModelBlock).where(
            SelfModelBlock.user_id == user_id,
            SelfModelBlock.section == "user_directive",
        )
    )
    plaintext = (
        df(user_id, block.content, table="self_model_blocks", field="content").strip()
        if block is not None
        else ""
    )
    if not plaintext:
        return None

    return MemoryBlock(
        label="user_directive",
        description=_desc(
            "user_directive",
            "The user's customisation instructions — how they want me to behave with them.",
            agent_type,
        ),
        value=plaintext,
    )


def build_self_model_memory_blocks(
    db: Session,
    *,
    user_id: int,
    pg_db: Session | None = None,
    agent_type: str = "companion",
) -> list[MemoryBlock]:
    """Build memory blocks from the agent's self-model sections."""
    from anima_server.services.agent.self_model import (
        ensure_self_model_exists,
        get_active_intentions,
        get_all_self_model_blocks,
        get_growth_log_text,
        get_identity_block,
        get_working_context,
        render_self_model_section,
    )

    ensure_self_model_exists(db, user_id=user_id)
    blocks_map = get_all_self_model_blocks(db, user_id=user_id)
    working_context = get_working_context(
        pg_db, user_id=user_id) if pg_db is not None else {}
    intentions = get_active_intentions(
        pg_db, user_id=user_id) if pg_db is not None else None
    result: list[MemoryBlock] = []

    section_config = [
        (
            "identity",
            "self_identity",
            _desc(
                "self_identity",
                "Who I am to THIS specific user — my role, relational dynamics, and rapport. Distinct from persona (which is my general personality).",
                agent_type,
            ),
        ),
        (
            "inner_state",
            "self_inner_state",
            _desc(
                "self_inner_state",
                "My current cognitive state — what I'm thinking about, what's unresolved.",
                agent_type,
            ),
        ),
        (
            "emotional_synthesis",
            "self_emotional_state",
            _desc(
                "self_emotional_state",
                "My recent emotional trajectory and what I feel toward this person. Private context for tone and closeness, not something to narrate unless it belongs naturally.",
                agent_type,
            ),
        ),
        (
            "working_memory",
            "self_working_memory",
            _desc("self_working_memory",
                  "Things I'm holding in mind across sessions.", agent_type),
        ),
        (
            "growth_log",
            "self_growth_log",
            _desc("self_growth_log",
                  "How I've evolved — my recent changes and why.", agent_type),
        ),
        (
            "intentions",
            "self_intentions",
            _desc("self_intentions",
                  "My active goals and learned behavioral rules.", agent_type),
        ),
    ]

    for section, label, description in section_config:
        if section == "identity":
            block = get_identity_block(
                db, user_id=user_id) or blocks_map.get(section)
            text = render_self_model_section(block, user_id=user_id)
        elif section == "growth_log":
            text = get_growth_log_text(db, user_id=user_id)
            if not text:
                text = render_self_model_section(
                    blocks_map.get(section), user_id=user_id)
        elif section in {"inner_state", "working_memory", "emotional_synthesis"}:
            block = working_context.get(section) or blocks_map.get(section)
            text = render_self_model_section(block, user_id=user_id)
        elif section == "intentions":
            block = intentions or blocks_map.get(section)
            text = render_self_model_section(block, user_id=user_id)
        else:
            block = blocks_map.get(section)
            text = render_self_model_section(block, user_id=user_id)
        if text:
            result.append(
                MemoryBlock(
                    label=label,
                    description=description,
                    value=text,
                )
            )

    return result


def build_emotional_context_block(
    db: Session,
    *,
    user_id: int,
    agent_type: str = "companion",
) -> MemoryBlock | None:
    """Build a memory block with the agent's emotional read of the user."""
    from anima_server.services.agent.emotional_intelligence import synthesize_emotional_context

    text = synthesize_emotional_context(db, user_id=user_id)
    if not text:
        return None

    return MemoryBlock(
        label="emotional_context",
        description=_desc(
            "emotional_context",
            "My sense of how the user is doing emotionally. Guide tone, not verbal analysis.",
            agent_type,
        ),
        value=text,
    )


def build_emotional_patterns_block(
    db: Session,
    *,
    user_id: int,
    agent_type: str = "companion",
) -> MemoryBlock | None:
    """Build a memory block from enduring emotional patterns."""
    from anima_server.config import settings
    from anima_server.models.soul_consciousness import CoreEmotionalPattern

    patterns = db.scalars(
        select(CoreEmotionalPattern)
        .where(CoreEmotionalPattern.user_id == user_id)
        .order_by(CoreEmotionalPattern.confidence.desc())
        .limit(10)
    ).all()
    if not patterns:
        return None

    value = "\n".join(
        f"- {pattern.pattern} ({pattern.dominant_emotion}, confidence: {pattern.confidence:.1f})"
        for pattern in patterns
    )
    if len(value) > settings.agent_emotional_patterns_budget:
        value = value[: settings.agent_emotional_patterns_budget]

    return MemoryBlock(
        label="emotional_patterns",
        description=_desc(
            "emotional_patterns",
            "My enduring emotional tendencies - patterns distilled from many conversations.",
            agent_type,
        ),
        value=value,
    )


def build_knowledge_graph_block(
    db: Session,
    *,
    user_id: int,
    query: str | None = None,
    agent_type: str = "companion",
) -> MemoryBlock | None:
    """Build a memory block with relevant knowledge graph context.

    Calls graph_context_for_query() to traverse the entity graph and
    return formatted relationship triples. Omitted when no relevant
    graph context is found.
    """
    if not query:
        return None

    try:
        from anima_server.services.agent.knowledge_graph import graph_context_for_query

        lines = graph_context_for_query(
            db, user_id=user_id, query=query, limit=10)
        if not lines:
            return None

        value = "\n".join(f"- {line}" for line in lines)
        if len(value) > 1500:
            value = value[:1500]

        return MemoryBlock(
            label="knowledge_graph",
            description=_desc(
                "knowledge_graph",
                "Relationships between entities in the user's life. Use these naturally for context.",
                agent_type,
            ),
            value=value,
        )
    except Exception:
        return None


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
