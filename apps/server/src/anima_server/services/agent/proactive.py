"""Proactive greeting generation: the agent initiates with context-aware messages.

Generates personalized greetings when the user opens the app, drawing on:
- Self-model (identity, inner state, working memory)
- Emotional context (last known emotional state)
- Pending tasks and deadlines
- Time since last conversation
- Recent episodes and memories
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from anima_server.config import settings
from anima_server.models import AgentThread, MemoryEpisode, Task
from anima_server.services.data_crypto import df

logger = logging.getLogger(__name__)

GREETING_PROMPT = """You are Anima, greeting your user as they open the app. This is NOT a chat reply — you are initiating contact. Be natural, warm, and brief (1-3 sentences).

{identity_context}
{emotional_context}
{time_context}
{task_context}
{memory_context}

Guidelines:
- Speak as yourself, not as a system. You're a companion who's been thinking about them.
- Reference specific things you know — don't be generic.
- If it's been a while, acknowledge it gently. If it was recent, be casual.
- If they were stressed or struggling last time, check in. If they were excited, match the energy.
- If there are urgent tasks or deadlines, mention them naturally — don't list them.
- Keep it to 1-3 sentences. No bullet points. No "how can I help you today."
- If you don't have much context yet (new relationship), be genuine about that — a simple warm hello is fine.

Generate ONLY the greeting text, nothing else."""


@dataclass(frozen=True, slots=True)
class GreetingContext:
    current_focus: str | None = None
    open_task_count: int = 0
    overdue_task_count: int = 0
    days_since_last_chat: int | None = None
    upcoming_deadlines: tuple[str, ...] = ()
    identity_summary: str = ""
    inner_state_summary: str = ""
    emotional_summary: str = ""
    recent_episode_summary: str = ""
    working_memory_summary: str = ""


@dataclass(slots=True)
class GreetingResult:
    message: str
    context: GreetingContext
    llm_generated: bool = False
    errors: list[str] = field(default_factory=list)


def gather_greeting_context(db: Session, *, user_id: int) -> GreetingContext:
    """Gather all context needed to generate a personalized greeting."""
    from anima_server.services.agent.memory_store import get_current_focus

    focus = get_current_focus(db, user_id=user_id)

    open_task_count = db.scalar(
        select(func.count(Task.id)).where(
            Task.user_id == user_id, Task.done.is_(False)
        )
    ) or 0

    overdue_count = db.scalar(
        select(func.count(Task.id)).where(
            Task.user_id == user_id,
            Task.done.is_(False),
            Task.due_date.isnot(None),
            Task.due_date < func.date("now"),
        )
    ) or 0

    # Upcoming deadlines (next 3 days)
    upcoming = list(
        db.scalars(
            select(Task.text)
            .where(
                Task.user_id == user_id,
                Task.done.is_(False),
                Task.due_date.isnot(None),
                Task.due_date >= func.date("now"),
                Task.due_date <= func.date("now", "+3 days"),
            )
            .limit(5)
        ).all()
    )

    # Days since last chat
    thread = db.scalar(
        select(AgentThread).where(AgentThread.user_id == user_id)
    )
    days_since: int | None = None
    if thread and thread.last_message_at:
        last = thread.last_message_at
        if last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        delta = datetime.now(UTC) - last
        days_since = delta.days

    # Self-model context
    identity_summary = ""
    inner_state_summary = ""
    working_memory_summary = ""
    try:
        from anima_server.services.agent.self_model import (
            get_self_model_block,
            render_self_model_section,
        )

        identity_block = get_self_model_block(db, user_id=user_id, section="identity")
        if identity_block and identity_block.version > 1:
            identity_summary = render_self_model_section(identity_block, budget=500)

        inner_block = get_self_model_block(db, user_id=user_id, section="inner_state")
        if inner_block:
            inner_state_summary = render_self_model_section(inner_block, budget=400)

        wm_block = get_self_model_block(db, user_id=user_id, section="working_memory")
        if wm_block:
            working_memory_summary = render_self_model_section(wm_block, budget=300)
    except Exception:  # noqa: BLE001
        pass

    # Emotional context
    emotional_summary = ""
    try:
        from anima_server.services.agent.emotional_intelligence import (
            synthesize_emotional_context,
        )

        emotional_summary = synthesize_emotional_context(db, user_id=user_id)
    except Exception:  # noqa: BLE001
        pass

    # Recent episodes
    recent_episode_summary = ""
    try:
        episodes = db.scalars(
            select(MemoryEpisode)
            .where(MemoryEpisode.user_id == user_id)
            .order_by(MemoryEpisode.created_at.desc())
            .limit(3)
        ).all()
        if episodes:
            recent_episode_summary = "\n".join(
                f"- {ep.date}: {df(user_id, ep.summary)}" for ep in reversed(episodes)
            )
    except Exception:  # noqa: BLE001
        pass

    return GreetingContext(
        current_focus=focus,
        open_task_count=open_task_count,
        overdue_task_count=overdue_count,
        days_since_last_chat=days_since,
        upcoming_deadlines=tuple(upcoming),
        identity_summary=identity_summary,
        inner_state_summary=inner_state_summary,
        emotional_summary=emotional_summary,
        recent_episode_summary=recent_episode_summary,
        working_memory_summary=working_memory_summary,
    )


def build_static_greeting(ctx: GreetingContext) -> str:
    """Build a simple static greeting from context (no LLM needed)."""
    parts: list[str] = []
    if ctx.current_focus:
        parts.append(f"Your current focus is: {ctx.current_focus}.")
    if ctx.open_task_count:
        s = "s" if ctx.open_task_count != 1 else ""
        parts.append(f"You have {ctx.open_task_count} open task{s}.")
    if ctx.days_since_last_chat is not None and ctx.days_since_last_chat > 1:
        parts.append(
            f"It's been {ctx.days_since_last_chat} days since we last chatted."
        )
    if not parts:
        parts.append("Welcome back! How can I help you today?")
    return " ".join(parts)


async def generate_greeting(
    db: Session,
    *,
    user_id: int,
) -> GreetingResult:
    """Generate a personalized greeting, falling back to static if LLM unavailable."""
    ctx = gather_greeting_context(db, user_id=user_id)
    result = GreetingResult(message="", context=ctx)

    if settings.agent_provider == "scaffold":
        result.message = build_static_greeting(ctx)
        return result

    # Build the LLM prompt with available context
    identity_context = ""
    if ctx.identity_summary:
        identity_context = f"Your self-understanding:\n{ctx.identity_summary}"
    else:
        identity_context = "You're still getting to know this person."

    emotional_context = ""
    if ctx.emotional_summary:
        emotional_context = f"Last emotional read:\n{ctx.emotional_summary}"

    time_context = ""
    if ctx.days_since_last_chat is not None:
        if ctx.days_since_last_chat == 0:
            time_context = "You chatted earlier today."
        elif ctx.days_since_last_chat == 1:
            time_context = "You last chatted yesterday."
        else:
            time_context = f"It's been {ctx.days_since_last_chat} days since your last conversation."
    else:
        time_context = "This is your first time meeting."

    task_context = ""
    task_parts: list[str] = []
    if ctx.overdue_task_count:
        s = "s" if ctx.overdue_task_count != 1 else ""
        task_parts.append(f"{ctx.overdue_task_count} overdue task{s}")
    if ctx.upcoming_deadlines:
        task_parts.append(
            f"Upcoming deadlines: {', '.join(ctx.upcoming_deadlines[:3])}"
        )
    if ctx.open_task_count:
        task_parts.append(f"{ctx.open_task_count} open tasks total")
    if ctx.current_focus:
        task_parts.append(f"Current focus: {ctx.current_focus}")
    if task_parts:
        task_context = "Task context:\n" + "\n".join(f"- {p}" for p in task_parts)

    memory_context_parts: list[str] = []
    if ctx.inner_state_summary:
        memory_context_parts.append(f"Your inner state:\n{ctx.inner_state_summary}")
    if ctx.working_memory_summary:
        memory_context_parts.append(
            f"Things you're holding in mind:\n{ctx.working_memory_summary}"
        )
    if ctx.recent_episode_summary:
        memory_context_parts.append(
            f"Recent conversations:\n{ctx.recent_episode_summary}"
        )
    memory_context = "\n\n".join(memory_context_parts)

    prompt = GREETING_PROMPT.format(
        identity_context=identity_context,
        emotional_context=emotional_context,
        time_context=time_context,
        task_context=task_context,
        memory_context=memory_context,
    )

    try:
        from anima_server.services.agent.llm import create_llm
        from anima_server.services.agent.messages import HumanMessage, SystemMessage

        llm = create_llm()
        response = await llm.ainvoke([
            SystemMessage(
                content="You are Anima, generating a brief greeting. Respond with ONLY the greeting text."
            ),
            HumanMessage(content=prompt),
        ])
        content = getattr(response, "content", "")
        if isinstance(content, str) and content.strip():
            result.message = content.strip()
            result.llm_generated = True
            return result
    except Exception as e:  # noqa: BLE001
        logger.debug("LLM greeting generation failed: %s", e)
        result.errors.append(str(e))

    # Fallback to static
    result.message = build_static_greeting(ctx)
    return result
