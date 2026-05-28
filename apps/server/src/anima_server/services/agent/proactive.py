"""Proactive greeting generation: the agent initiates with context-aware messages.

Generates personalized greetings when the user opens the app, drawing on:
- Self-model (identity, inner state, working memory)
- Emotional context (last known emotional state)
- Pending tasks and deadlines
- Time since last conversation
- Recent episodes and memories
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.config import settings
from anima_server.models import AgentMessage, AgentThread, MemoryEpisode, Task
from anima_server.services.data_crypto import df
from anima_server.services.presence_config import (
    PresenceConfigValues,
    get_presence_config_values,
)

logger = logging.getLogger(__name__)

_GREETING_LLM_TIMEOUT_SECONDS = 8.0
_PROACTIVE_NOTICE_LLM_TIMEOUT_SECONDS = 2.0
_GREETING_LLM_MAX_TOKENS = 64
_PROACTIVE_NOTICE_LLM_MAX_TOKENS = 64
_OLLAMA_NATIVE_KEEP_ALIVE = "10m"


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
    is_birthday: bool = False
    days_until_birthday: int | None = None


@dataclass(frozen=True)
class GreetingResult:
    message: str
    context: GreetingContext
    llm_generated: bool = False
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ProactiveNoticeResult:
    id: str
    message: str
    context: GreetingContext
    source: str = "proactive_notice"
    llm_generated: bool = False
    errors: list[str] = field(default_factory=list)


def _birthday_context(birthday_str: str | None, today: date) -> tuple[bool, int | None]:
    """Return (is_birthday, days_until_birthday) given a stored birthday string."""
    if not birthday_str:
        return False, None
    raw = birthday_str.strip()
    bday: date | None = None
    for fmt in ("%Y-%m-%d", "%m-%d", "%m/%d", "%m/%d/%Y"):
        try:
            parsed = datetime.strptime(raw, fmt).date()
            bday = parsed.replace(year=today.year)
            break
        except ValueError:
            continue
    if bday is None:
        return False, None

    if bday.month == today.month and bday.day == today.day:
        return True, 0

    if bday < today:
        bday = bday.replace(year=today.year + 1)
    days = (bday - today).days
    return False, days


def _normalize_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _latest_runtime_user_message(runtime_db: Session, user_id: int) -> object | None:
    try:
        from anima_server.models.runtime import RuntimeMessage

        return runtime_db.scalar(
            select(RuntimeMessage)
            .where(
                RuntimeMessage.user_id == user_id,
                RuntimeMessage.role == "user",
                RuntimeMessage.content_text.is_not(None),
                RuntimeMessage.content_text != "",
            )
            .order_by(RuntimeMessage.created_at.desc())
        )
    except Exception as exc:
        logger.debug("Runtime greeting history lookup failed: %s", exc)
        return None


def _ollama_native_base_url() -> str:
    base_url = settings.agent_base_url.strip() or "http://localhost:11434"
    base_url = base_url.rstrip("/")
    if base_url.endswith("/v1"):
        base_url = base_url[:-3].rstrip("/")
    return base_url


async def _invoke_ollama_native_chat(
    messages: list[dict[str, str]],
    *,
    timeout: float,
    max_tokens: int,
    transport: httpx.AsyncBaseTransport | None = None,
) -> str | None:
    options: dict[str, object] = {
        "num_predict": max_tokens,
    }
    if settings.agent_temperature is not None:
        options["temperature"] = settings.agent_temperature

    payload: dict[str, object] = {
        "model": settings.agent_model,
        "messages": messages,
        "stream": False,
        "think": False,
        "keep_alive": _OLLAMA_NATIVE_KEEP_ALIVE,
        "options": options,
    }

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(timeout, read=timeout),
        transport=transport,
    ) as client:
        response = await client.post(f"{_ollama_native_base_url()}/api/chat", json=payload)
    response.raise_for_status()

    body = response.json()
    if not isinstance(body, dict):
        return None
    message = body.get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if not isinstance(content, str):
        return None
    stripped = content.strip()
    return stripped or None


def gather_greeting_context(
    db: Session,
    user_id: int,
    runtime_db: Session | None = None,
) -> GreetingContext:
    """Collect context for greeting generation."""
    # Get tasks info
    now = datetime.now(UTC)
    tasks = db.scalars(
        select(Task).where(Task.user_id == user_id,
                           Task.completed_at.is_(None))
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
    last_message = (
        _latest_runtime_user_message(runtime_db, user_id)
        if runtime_db is not None
        else None
    )
    if last_message is None:
        last_message = db.scalar(
            select(AgentMessage)
            .join(AgentThread, AgentMessage.thread_id == AgentThread.id)
            .where(AgentThread.user_id == user_id, AgentMessage.role == "user")
            .order_by(AgentMessage.created_at.desc())
        )

    days_since = None
    if last_message and last_message.created_at:
        delta = now - _normalize_utc(last_message.created_at)
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
    from anima_server.services.agent.self_model import (
        get_identity_block,
        get_self_model_block,
        get_working_context,
        render_self_model_section,
    )

    identity_block = get_identity_block(db, user_id=user_id)
    identity_summary = (
        render_self_model_section(identity_block, user_id=user_id)
        if identity_block
        else None
    )

    inner_state_block = None
    working_memory_block = None
    try:
        from anima_server.db.runtime import get_runtime_session_factory

        with get_runtime_session_factory()() as runtime_db:
            working_context = get_working_context(runtime_db, user_id=user_id)
            inner_state_block = working_context.get("inner_state")
            working_memory_block = working_context.get("working_memory")
    except Exception:
        inner_state_block = get_self_model_block(
            db, user_id=user_id, section="inner_state")
        working_memory_block = get_self_model_block(
            db, user_id=user_id, section="working_memory")

    inner_state_summary = (
        render_self_model_section(inner_state_block, user_id=user_id)
        if inner_state_block
        else None
    )
    working_memory_summary = (
        render_self_model_section(working_memory_block, user_id=user_id)
        if working_memory_block
        else None
    )

    # Get emotional context (read from runtime PG where signals now live)
    from anima_server.services.agent.emotional_intelligence import (
        get_recent_signals,
    )

    emotion_db = db
    _own_emotion_session = None
    try:
        from anima_server.db.runtime import get_runtime_session_factory

        _own_emotion_session = get_runtime_session_factory()()
        emotion_db = _own_emotion_session
    except Exception:
        pass  # fall back to soul DB

    try:
        signals = get_recent_signals(emotion_db, user_id=user_id, limit=1)
    finally:
        if _own_emotion_session is not None:
            _own_emotion_session.close()
    emotional_summary = None
    if signals:
        s = signals[0]
        emotional_summary = f"{s.emotion} ({s.trajectory})"

    # Get birthday context from user profile
    is_birthday = False
    days_until_birthday: int | None = None
    try:
        from sqlalchemy import text as _text

        birthday_row = db.execute(
            _text("SELECT birthday FROM users WHERE id = :uid"),
            {"uid": user_id},
        ).fetchone()
        birthday_str = birthday_row[0] if birthday_row else None
        is_birthday, days_until_birthday = _birthday_context(birthday_str, now.date())
    except Exception:
        pass

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
        is_birthday=is_birthday,
        days_until_birthday=days_until_birthday,
    )


def build_static_greeting(ctx: GreetingContext) -> str:
    """Build a simple static greeting when LLM is unavailable."""
    parts: list[str] = []

    if ctx.is_birthday:
        parts.append("Happy birthday!")
    elif ctx.days_since_last_chat is None:
        parts.append("Hello! I'm glad to meet you.")
    elif ctx.days_since_last_chat == 0:
        parts.append("Hello again!")
    elif ctx.days_since_last_chat == 1:
        parts.append("Good to see you today.")
    else:
        parts.append(
            f"It's been {ctx.days_since_last_chat} days. Welcome back.")

    if ctx.overdue_task_count:
        s = "s" if ctx.overdue_task_count != 1 else ""
        parts.append(f"You have {ctx.overdue_task_count} overdue task{s}.")

    return " ".join(parts)


def build_static_proactive_notice(
    ctx: GreetingContext,
    *,
    instruction: str | None = None,
    config: PresenceConfigValues | None = None,
) -> str | None:
    """Build a quiet in-chat proactive notice without requiring an LLM."""
    allow_tasks = config.task_nudges_enabled if config is not None else True
    allow_memory = config.memory_nudges_enabled if config is not None else True
    allow_checkins = config.checkin_nudges_enabled if config is not None else True

    if ctx.is_birthday:
        return "It's your birthday today. Hope it's a good one."

    if ctx.days_until_birthday is not None and 1 <= ctx.days_until_birthday <= 7:
        return f"Your birthday is in {ctx.days_until_birthday} day{'s' if ctx.days_until_birthday != 1 else ''}."

    custom = (instruction or "").strip()
    if custom:
        return f"{custom[:1].upper()}{custom[1:]}. Want to start there?"

    if allow_tasks and ctx.overdue_task_count:
        s = "s" if ctx.overdue_task_count != 1 else ""
        return f"You have {ctx.overdue_task_count} overdue task{s}. Want to sort them together?"

    if allow_tasks and ctx.upcoming_deadlines:
        return f"{ctx.upcoming_deadlines[0]} is coming up soon. Want to look at it together?"

    if allow_checkins and ctx.days_since_last_chat and ctx.days_since_last_chat > 0:
        return "There is a thread from last time we can pick back up if you want."

    if allow_memory and ctx.working_memory_summary:
        return "I am holding a few open threads for you. Want to choose one?"

    return None


async def generate_greeting(
    db: Session,
    *,
    user_id: int,
    runtime_db: Session | None = None,
) -> GreetingResult:
    """Generate a personalized greeting, falling back to static if LLM unavailable."""
    from anima_server.services.agent.prompt_loader import get_prompt_loader

    prompt_loader = get_prompt_loader(db, user_id)

    ctx = gather_greeting_context(db, user_id=user_id, runtime_db=runtime_db)

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
        task_parts.append(
            f"Upcoming deadlines: {', '.join(ctx.upcoming_deadlines[:3])}")
    if ctx.open_task_count:
        task_parts.append(f"{ctx.open_task_count} open tasks total")
    if ctx.current_focus:
        task_parts.append(f"Current focus: {ctx.current_focus}")
    if task_parts:
        task_context = "Task context:\n" + \
            "\n".join(f"- {p}" for p in task_parts)

    memory_context_parts: list[str] = []
    if ctx.inner_state_summary:
        memory_context_parts.append(
            f"Your inner state:\n{ctx.inner_state_summary}")
    if ctx.working_memory_summary:
        memory_context_parts.append(
            f"Things you're holding in mind:\n{ctx.working_memory_summary}")
    if ctx.recent_episode_summary:
        memory_context_parts.append(
            f"Recent conversations:\n{ctx.recent_episode_summary}")
    memory_context = "\n\n".join(memory_context_parts)

    if ctx.is_birthday:
        time_context = (time_context + "\nToday is the user's birthday.").strip()
    elif ctx.days_until_birthday is not None and ctx.days_until_birthday <= 7:
        days = ctx.days_until_birthday
        time_context = (
            time_context
            + f"\nThe user's birthday is in {days} day{'s' if days != 1 else ''}."
        ).strip()

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

        system_content = (
            f"You are {prompt_loader.agent_name}, generating a brief greeting. "
            "Respond with ONLY the greeting text."
        )
        if settings.agent_provider == "ollama":
            content = await asyncio.wait_for(
                _invoke_ollama_native_chat(
                    [
                        {"role": "system", "content": system_content},
                        {"role": "user", "content": prompt},
                    ],
                    timeout=_GREETING_LLM_TIMEOUT_SECONDS,
                    max_tokens=_GREETING_LLM_MAX_TOKENS,
                ),
                timeout=_GREETING_LLM_TIMEOUT_SECONDS,
            )
        else:
            llm = create_llm()
            response = await asyncio.wait_for(
                llm.ainvoke(
                    [
                        SystemMessage(content=system_content),
                        HumanMessage(content=prompt),
                    ]
                ),
                timeout=_GREETING_LLM_TIMEOUT_SECONDS,
            )
            content = getattr(response, "content", "")
        if isinstance(content, str) and content.strip():
            return GreetingResult(message=content.strip(), context=ctx, llm_generated=True)
    except Exception as e:
        logger.debug("LLM greeting generation failed: %s", e)
        errors.append(str(e))

    # Fallback to static
    return GreetingResult(message=build_static_greeting(ctx), context=ctx, errors=errors)


async def generate_proactive_notice(
    db: Session,
    *,
    user_id: int,
    instruction: str | None = None,
    runtime_db: Session | None = None,
) -> ProactiveNoticeResult | None:
    """Generate a quiet proactive notice for the main chat surface."""
    from anima_server.services.agent.prompt_loader import get_prompt_loader

    config = get_presence_config_values(db, user_id)
    if not config.enabled or not config.main_chat_enabled:
        return None

    effective_instruction = (instruction or "").strip() or config.custom_instruction
    ctx = gather_greeting_context(db, user_id=user_id, runtime_db=runtime_db)
    fallback = build_static_proactive_notice(
        ctx,
        instruction=effective_instruction,
        config=config,
    )
    if fallback is None:
        return None

    if settings.agent_provider == "scaffold":
        return ProactiveNoticeResult(id="proactive_notice", message=fallback, context=ctx)

    prompt_loader = get_prompt_loader(db, user_id)
    prompt_parts = [
        "Write one short, quiet proactive notice for the main chat.",
        "It should feel like an optional thread, not a command or interruption.",
        "Do not mention that you are generating a notification.",
    ]
    if ctx.is_birthday:
        prompt_parts.append("Today is the user's birthday.")
    elif ctx.days_until_birthday is not None and ctx.days_until_birthday <= 7:
        days = ctx.days_until_birthday
        prompt_parts.append(
            f"The user's birthday is in {days} day{'s' if days != 1 else ''}."
        )
    if effective_instruction:
        prompt_parts.append(f"User customization: {effective_instruction}")
    if config.task_nudges_enabled and ctx.overdue_task_count:
        prompt_parts.append(f"Overdue tasks: {ctx.overdue_task_count}")
    if config.task_nudges_enabled and ctx.upcoming_deadlines:
        prompt_parts.append(f"Upcoming deadlines: {', '.join(ctx.upcoming_deadlines[:3])}")
    if config.checkin_nudges_enabled and ctx.days_since_last_chat is not None:
        prompt_parts.append(f"Days since last chat: {ctx.days_since_last_chat}")
    if config.memory_nudges_enabled and ctx.recent_episode_summary:
        prompt_parts.append(f"Recent conversation: {ctx.recent_episode_summary}")
    if config.memory_nudges_enabled and ctx.working_memory_summary:
        prompt_parts.append(f"Working memory: {ctx.working_memory_summary}")

    errors: list[str] = []
    try:
        from anima_server.services.agent.llm import create_llm
        from anima_server.services.agent.messages import HumanMessage, SystemMessage

        system_content = (
            f"You are {prompt_loader.agent_name}. Respond with ONLY the proactive "
            "notice text, one sentence."
        )
        prompt = "\n".join(prompt_parts)
        if settings.agent_provider == "ollama":
            content = await asyncio.wait_for(
                _invoke_ollama_native_chat(
                    [
                        {"role": "system", "content": system_content},
                        {"role": "user", "content": prompt},
                    ],
                    timeout=_PROACTIVE_NOTICE_LLM_TIMEOUT_SECONDS,
                    max_tokens=_PROACTIVE_NOTICE_LLM_MAX_TOKENS,
                ),
                timeout=_PROACTIVE_NOTICE_LLM_TIMEOUT_SECONDS,
            )
        else:
            llm = create_llm()
            response = await asyncio.wait_for(
                llm.ainvoke(
                    [
                        SystemMessage(content=system_content),
                        HumanMessage(content=prompt),
                    ]
                ),
                timeout=_PROACTIVE_NOTICE_LLM_TIMEOUT_SECONDS,
            )
            content = getattr(response, "content", "")
        if isinstance(content, str) and content.strip():
            return ProactiveNoticeResult(
                id="proactive_notice",
                message=content.strip(),
                context=ctx,
                llm_generated=True,
            )
    except Exception as e:
        logger.debug("LLM proactive notice generation failed: %s", e)
        errors.append(str(e))

    return ProactiveNoticeResult(
        id="proactive_notice",
        message=fallback,
        context=ctx,
        errors=errors,
    )
