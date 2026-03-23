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

from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.config import settings
from anima_server.models import AgentMessage, AgentThread, MemoryEpisode, Task
from anima_server.services.data_crypto import df

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GreetingContext:
    current_focus: str | None = None
    open_task_count: int = 0
    overdue_task_count: int = 0
    upcoming_deadlines: list[str] = field(default_factory=list)
    days_since_last_chat: int | None = None
    identity_summary: str | None = None
    emotional_summary: str | None = None
    inner_state_summary: str | None = None
    working_memory_summary: str | None = None
    recent_episode_summary: str | None = None


@dataclass(frozen=True)
class GreetingResult:
    message: str
    context: GreetingContext
    llm_generated: bool = False
    errors: list[str] = field(default_factory=list)


def gather_greeting_context(db: Session, user_id: int) -> GreetingContext:
    """Collect context for greeting generation."""
    ctx = GreetingContext()

    # Get tasks info
    now = datetime.now(UTC)
    tasks = db.scalars(
        select(Task).where(Task.user_id == user_id, Task.completed_at.is_(None))
    ).all()

    open_count = 0
    overdue_count = 0
    deadlines: list[str] = []

    for task in tasks:
        open_count += 1
        if task.due_date:
            if task.due_date < now:
                overdue_count += 1
            elif (task.due_date - now).days <= 3:
                deadlines.append(task.title)

    # Get last conversation time
    last_message = db.scalar(
        select(AgentMessage)
        .join(AgentThread, AgentMessage.thread_id == AgentThread.id)
        .where(AgentThread.user_id == user_id, AgentMessage.role == "user")
        .order_by(AgentMessage.created_at.desc())
    )

    days_since = None
    if last_message and last_message.created_at:
        delta = now - last_message.created_at
        days_since = delta.days

    # Get recent episode summary
    recent_episode = db.scalar(
        select(MemoryEpisode)
        .where(MemoryEpisode.user_id == user_id)
        .order_by(MemoryEpisode.created_at.desc())
    )

    episode_summary = None
    if recent_episode:
        episode_summary = df(
            user_id, recent_episode.summary, table="memory_episodes", field="summary"
        )

    # Get self-model sections for context
    from anima_server.services.agent.self_model import get_self_model_block

    identity_block = get_self_model_block(db, user_id=user_id, section="identity")
    identity_summary = (
        df(user_id, identity_block.content, table="self_model_blocks", field="content")
        if identity_block
        else None
    )

    inner_state_block = get_self_model_block(db, user_id=user_id, section="inner_state")
    inner_state_summary = (
        df(user_id, inner_state_block.content, table="self_model_blocks", field="content")
        if inner_state_block
        else None
    )

    working_memory_block = get_self_model_block(db, user_id=user_id, section="working_memory")
    working_memory_summary = (
        df(
            user_id,
            working_memory_block.content,
            table="self_model_blocks",
            field="content",
        )
        if working_memory_block
        else None
    )

    # Get emotional context
    from anima_server.services.agent.emotional_intelligence import (
        get_recent_signals,
    )

    signals = get_recent_signals(db, user_id=user_id, limit=1)
    emotional_summary = None
    if signals:
        s = signals[0]
        emotional_summary = f"{s.emotion} ({s.trajectory})"

    return GreetingContext(
        current_focus=None,  # Could fetch from intentions
        open_task_count=open_count,
        overdue_task_count=overdue_count,
        upcoming_deadlines=deadlines,
        days_since_last_chat=days_since,
        identity_summary=identity_summary,
        emotional_summary=emotional_summary,
        inner_state_summary=inner_state_summary,
        working_memory_summary=working_memory_summary,
        recent_episode_summary=episode_summary,
    )


def build_static_greeting(ctx: GreetingContext) -> str:
    """Build a simple static greeting when LLM is unavailable."""
    parts: list[str] = []

    if ctx.days_since_last_chat is None:
        parts.append("Hello! I'm glad to meet you.")
    elif ctx.days_since_last_chat == 0:
        parts.append("Hello again!")
    elif ctx.days_since_last_chat == 1:
        parts.append("Good to see you today.")
    else:
        parts.append(f"It's been {ctx.days_since_last_chat} days. Welcome back.")

    if ctx.overdue_task_count:
        s = "s" if ctx.overdue_task_count != 1 else ""
        parts.append(f"You have {ctx.overdue_task_count} overdue task{s}.")

    return " ".join(parts)


async def generate_greeting(
    db: Session,
    *,
    user_id: int,
) -> GreetingResult:
    """Generate a personalized greeting, falling back to static if LLM unavailable."""
    from anima_server.services.agent.prompt_loader import get_prompt_loader

    prompt_loader = get_prompt_loader(db, user_id)

    ctx = gather_greeting_context(db, user_id=user_id)

    if settings.agent_provider == "scaffold":
        return GreetingResult(message=build_static_greeting(ctx), context=ctx)

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
            time_context = (
                f"It's been {ctx.days_since_last_chat} days since your last conversation."
            )
    else:
        time_context = "This is your first time meeting."

    task_context = ""
    task_parts: list[str] = []
    if ctx.overdue_task_count:
        s = "s" if ctx.overdue_task_count != 1 else ""
        task_parts.append(f"{ctx.overdue_task_count} overdue task{s}")
    if ctx.upcoming_deadlines:
        task_parts.append(f"Upcoming deadlines: {', '.join(ctx.upcoming_deadlines[:3])}")
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
        memory_context_parts.append(f"Things you're holding in mind:\n{ctx.working_memory_summary}")
    if ctx.recent_episode_summary:
        memory_context_parts.append(f"Recent conversations:\n{ctx.recent_episode_summary}")
    memory_context = "\n\n".join(memory_context_parts)

    # Use templated greeting prompt
    prompt = prompt_loader.greeting(
        identity_context=identity_context,
        emotional_context=emotional_context,
        time_context=time_context,
        task_context=task_context,
        memory_context=memory_context,
    )

    errors: list[str] = []
    try:
        from anima_server.services.agent.llm import create_llm
        from anima_server.services.agent.messages import HumanMessage, SystemMessage

        llm = create_llm()
        response = await llm.ainvoke(
            [
                SystemMessage(
                    content=f"You are {prompt_loader.agent_name}, generating a brief greeting. Respond with ONLY the greeting text."
                ),
                HumanMessage(content=prompt),
            ]
        )
        content = getattr(response, "content", "")
        if isinstance(content, str) and content.strip():
            return GreetingResult(message=content.strip(), context=ctx, llm_generated=True)
    except Exception as e:
        logger.debug("LLM greeting generation failed: %s", e)
        errors.append(str(e))

    # Fallback to static
    return GreetingResult(message=build_static_greeting(ctx), context=ctx, errors=errors)
