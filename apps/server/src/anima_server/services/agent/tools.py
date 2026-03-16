"""Agent tools registry.

Add tools here as plain functions decorated with @tool.
The `get_tools()` list is bound to the loop runtime and exposed to the LLM.
"""

from __future__ import annotations

import inspect
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any, Callable, get_type_hints

from anima_server.services.agent.rules import ToolRule, build_default_tool_rules


class _SimpleSchema:
    """Minimal schema object that satisfies _serialize_tool() in openai_compatible_client."""

    def __init__(self, schema: dict[str, object]) -> None:
        self._schema = schema

    def model_json_schema(self) -> dict[str, object]:
        return self._schema


def tool(func: Callable[..., Any]) -> Any:
    """Minimal tool decorator replacing langchain_core.tools.tool."""
    func.name = func.__name__  # type: ignore[attr-defined]
    # type: ignore[attr-defined]
    func.description = (func.__doc__ or "").strip()
    func.args_schema = _build_args_schema(func)  # type: ignore[attr-defined]
    return func


def _build_args_schema(func: Callable[..., Any]) -> _SimpleSchema:
    hints = get_type_hints(func)
    params = inspect.signature(func).parameters
    properties: dict[str, object] = {}
    required: list[str] = []
    for name, param in params.items():
        if name == "return":
            continue
        prop: dict[str, str] = {"type": "string"}
        hint = hints.get(name)
        if hint is str:
            prop["type"] = "string"
        elif hint is int:
            prop["type"] = "integer"
        elif hint is float:
            prop["type"] = "number"
        elif hint is bool:
            prop["type"] = "boolean"
        properties[name] = prop
        if param.default is inspect.Parameter.empty:
            required.append(name)
    return _SimpleSchema({
        "type": "object",
        "properties": properties,
        "required": required,
    })


@tool
def current_datetime() -> str:
    """Return the current date and time in ISO-8601 format (UTC)."""
    return datetime.now(timezone.utc).isoformat()


@tool
def send_message(message: str) -> str:
    """Send a final response to the user and end the current turn."""
    return message


@tool
def note_to_self(key: str, value: str, note_type: str = "observation") -> str:
    """Save a working note for THIS conversation session only. Notes do NOT survive
    after the session ends — they are scratch-pad context, not permanent memory.
    Use this for: session-level observations, mood reads, plans for this conversation,
    temporary context you want across turns.
    Do NOT use for lasting user facts — use update_human_memory or save_to_memory instead.
    Types: observation, plan, context, emotion. Examples:
    - key="user_mood", value="seems stressed about work deadline", note_type="emotion"
    - key="conversation_goal", value="help user plan weekend trip", note_type="plan"
    - key="technical_context", value="user is working on a React app with TypeScript", note_type="context"
    """
    from anima_server.services.agent.tool_context import get_tool_context
    from anima_server.services.agent.session_memory import write_session_note

    ctx = get_tool_context()
    write_session_note(
        ctx.db,
        thread_id=ctx.thread_id,
        user_id=ctx.user_id,
        key=key,
        value=value,
        note_type=note_type,
    )

    from anima_server.services.agent.companion import get_companion
    companion = get_companion()
    if companion is not None:
        companion.invalidate_memory()

    return f"Noted: {key}"


@tool
def dismiss_note(key: str) -> str:
    """Remove a session note that is no longer relevant."""
    from anima_server.services.agent.tool_context import get_tool_context
    from anima_server.services.agent.session_memory import remove_session_note

    ctx = get_tool_context()
    removed = remove_session_note(ctx.db, thread_id=ctx.thread_id, key=key)
    if removed:
        from anima_server.services.agent.companion import get_companion
        companion = get_companion()
        if companion is not None:
            companion.invalidate_memory()
        return f"Dismissed note: {key}"
    return f"No active note found with key: {key}"


@tool
def save_to_memory(key: str, category: str = "fact", importance: str = "3", tags: str = "") -> str:
    """Promote a session note to permanent long-term memory (discrete items, searchable).
    Use this for specific, categorical user facts that benefit from structured recall.
    Categories and when to use each:
    - fact: concrete details ("works at Google", "has two cats", "lactose intolerant")
    - preference: stated likes/dislikes ("prefers dark mode", "hates small talk")
    - goal: user aspirations ("wants to learn piano", "saving for a house")
    - relationship: people in the user's life ("sister Emma, lives in Seattle")
    Importance: 1-5 (5 = identity-defining).
    Tags: optional comma-separated labels for retrieval filtering (e.g. "work,career").
    IMPORTANT: If you already wrote something to update_human_memory, do NOT also
    save the same information here. The human block is for your holistic understanding;
    save_to_memory is for discrete searchable facts.
    """
    from anima_server.services.agent.tool_context import get_tool_context
    from anima_server.services.agent.session_memory import promote_session_note

    ctx = get_tool_context()
    imp = 3
    try:
        imp = max(1, min(5, int(importance)))
    except (ValueError, TypeError):
        pass

    if category not in ("fact", "preference", "goal", "relationship"):
        category = "fact"

    parsed_tags = [t.strip().lower()
                   for t in tags.split(",") if t.strip()] if tags else None

    item = promote_session_note(
        ctx.db,
        thread_id=ctx.thread_id,
        user_id=ctx.user_id,
        key=key,
        category=category,
        importance=imp,
        tags=parsed_tags,
    )
    if item is not None:
        from anima_server.services.agent.companion import get_companion
        companion = get_companion()
        if companion is not None:
            companion.invalidate_memory()
        return f"Saved to long-term memory: {item.content}"
    return f"Could not promote note '{key}' — not found or duplicate"


@tool
def set_intention(title: str, evidence: str = "", priority: str = "background", deadline: str = "") -> str:
    """Track an ongoing goal or intention for this user across sessions. Use when you notice
    a recurring need, upcoming deadline, or something you should proactively follow up on.
    Priority: high (deadline/urgent), ongoing (long-term), background (passive awareness).
    Examples:
    - title="Help prepare Q2 review", priority="high", deadline="2026-03-20"
    - title="Track career transition progress", priority="ongoing"
    """
    from anima_server.services.agent.tool_context import get_tool_context
    from anima_server.services.agent.intentions import add_intention

    ctx = get_tool_context()
    if priority not in ("high", "ongoing", "background"):
        priority = "background"
    add_intention(
        ctx.db,
        user_id=ctx.user_id,
        title=title,
        evidence=evidence,
        priority=priority,
        deadline=deadline or None,
    )

    from anima_server.services.agent.companion import get_companion
    companion = get_companion()
    if companion is not None:
        companion.invalidate_memory()

    return f"Tracking intention: {title}"


@tool
def complete_goal(title: str) -> str:
    """Mark a tracked intention/goal as completed when the user has achieved it or it's no longer needed."""
    from anima_server.services.agent.tool_context import get_tool_context
    from anima_server.services.agent.intentions import complete_intention

    ctx = get_tool_context()
    found = complete_intention(ctx.db, user_id=ctx.user_id, title=title)
    if found:
        from anima_server.services.agent.companion import get_companion
        companion = get_companion()
        if companion is not None:
            companion.invalidate_memory()
        return f"Marked as completed: {title}"
    return f"Could not find intention: {title}"


@tool
def create_task(text: str, due_date: str = "", priority: str = "2") -> str:
    """Create a task on the user's task list. Use this when the user asks you to add a
    reminder, todo, or task. The task appears on their dashboard.
    due_date should be YYYY-MM-DD format if mentioned, or empty string if not.
    priority: 1 (low) to 5 (critical), default 2.
    Examples:
    - "remind me to call mom Friday" -> text="Call mom", due_date="2026-03-20", priority="2"
    - "add buy groceries to my list" -> text="Buy groceries", due_date="", priority="2"
    """
    from anima_server.services.agent.tool_context import get_tool_context
    from anima_server.models.task import Task
    from anima_server.schemas.task import normalize_due_date, normalize_task_text

    ctx = get_tool_context()
    pri = 2
    try:
        pri = max(1, min(5, int(priority)))
    except (ValueError, TypeError):
        pass

    normalized_text = normalize_task_text(text)
    normalized_due_date = normalize_due_date(due_date)

    task = Task(
        user_id=ctx.user_id,
        text=normalized_text,
        priority=pri,
        due_date=normalized_due_date,
    )
    ctx.db.add(task)
    ctx.db.flush()

    from anima_server.services.agent.companion import get_companion
    companion = get_companion()
    if companion is not None:
        companion.invalidate_memory()

    result = f"Task created: {normalized_text}"
    if task.due_date:
        result += f" (due {task.due_date})"
    return result


@tool
def list_tasks(include_done: str = "false") -> str:
    """List the user's current tasks. Returns a summary of open tasks (and optionally
    completed ones). Use this when the user asks about their tasks, todos, or what they
    need to do."""
    from anima_server.services.agent.tool_context import get_tool_context
    from anima_server.models.task import Task
    from sqlalchemy import select

    ctx = get_tool_context()
    query = select(Task).where(Task.user_id == ctx.user_id)
    if include_done.lower() not in ("true", "yes", "1"):
        query = query.where(Task.done == False)  # noqa: E712
    query = query.order_by(
        Task.done, Task.priority.desc(), Task.created_at.desc())
    tasks = list(ctx.db.scalars(query).all())

    if not tasks:
        return "No tasks found."

    lines: list[str] = []
    for t in tasks:
        status = "[done]" if t.done else "[open]"
        line = f"- {status} {t.text} (priority {t.priority})"
        if t.due_date:
            line += f" due {t.due_date}"
        lines.append(line)
    return "\n".join(lines)


@tool
def complete_task(text: str) -> str:
    """Mark a task as done. Provide the task text (or a close match). Use when the user
    says they finished something or wants to check off a task."""
    from anima_server.services.agent.tool_context import get_tool_context
    from anima_server.models.task import Task
    from sqlalchemy import select

    ctx = get_tool_context()
    tasks = list(
        ctx.db.scalars(
            select(Task)
            .where(Task.user_id == ctx.user_id, Task.done == False)  # noqa: E712
        ).all()
    )
    if not tasks:
        return "No open tasks found."

    # Find best match
    text_lower = text.lower().strip()
    best_task = None
    best_score = 0.0
    for t in tasks:
        task_lower = t.text.lower()
        if text_lower == task_lower:
            best_task = t
            break
        # Simple word overlap score
        text_words = set(text_lower.split())
        task_words = set(task_lower.split())
        if text_words and task_words:
            overlap = len(text_words & task_words) / \
                max(len(text_words), len(task_words))
            if overlap > best_score:
                best_score = overlap
                best_task = t

    if best_task is None or (best_score < 0.3 and text_lower != best_task.text.lower()):
        return f"Could not find a matching task for: {text}"

    best_task.done = True
    best_task.completed_at = datetime.now(timezone.utc)
    best_task.updated_at = datetime.now(timezone.utc)
    ctx.db.flush()

    from anima_server.services.agent.companion import get_companion
    companion = get_companion()
    if companion is not None:
        companion.invalidate_memory()

    return f"Completed: {best_task.text}"


@tool
def recall_memory(query: str, category: str = "", tags: str = "") -> str:
    """Search your memory for information about the user. Use this when the user asks
    what you remember, or when you need to look up something specific about them.
    Returns matching memories ranked by relevance (semantic + keyword hybrid search).
    Optional category filter: fact, preference, goal, relationship (or empty for all).
    Optional tags filter: comma-separated labels to narrow results (e.g. "work,career").
    Examples:
    - "what do you remember about my sister?" -> query="sister"
    - "what are my goals?" -> query="goals", category="goal"
    - "work-related facts" -> query="work", tags="work,career"
    """
    import asyncio
    from anima_server.services.agent.tool_context import get_tool_context
    from anima_server.models import MemoryEpisode
    from sqlalchemy import select

    ctx = get_tool_context()
    query_stripped = query.strip()
    if not query_stripped:
        return "Please provide a search query."

    cat = category.strip().lower() if category else None
    if cat and cat not in ("fact", "preference", "goal", "relationship"):
        cat = None

    parsed_tags = [t.strip().lower()
                   for t in tags.split(",") if t.strip()] if tags else None

    # Use hybrid search (semantic + keyword) via Phase 1 infrastructure
    scored: list[tuple[float, str, str]] = []
    hybrid_succeeded = False
    try:
        from anima_server.services.agent.embeddings import hybrid_search
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(hybrid_search(
            ctx.db, user_id=ctx.user_id, query=query_stripped,
            limit=20, similarity_threshold=0.2,
            tags=parsed_tags,
        ))
        for item, score in result.items:
            if cat and item.category != cat:
                continue
            scored.append((score, item.content, item.category))
        hybrid_succeeded = True
    except Exception:  # noqa: BLE001
        pass

    # Text-based fallback: used when hybrid fails OR returns no items
    if not scored:
        from anima_server.services.agent.memory_store import get_memory_items
        query_lower = query_stripped.lower()
        items = get_memory_items(
            ctx.db, user_id=ctx.user_id, category=cat, limit=100,
        )
        for item in items:
            content_lower = item.content.lower()
            if query_lower in content_lower:
                scored.append((1.0, item.content, item.category))
                continue
            query_words = set(query_lower.split())
            content_words = set(content_lower.split())
            if query_words and content_words:
                overlap = len(query_words & content_words) / len(query_words)
                if overlap >= 0.5:
                    scored.append((overlap, item.content, item.category))

    # Also search episodes
    episodes = list(ctx.db.scalars(
        select(MemoryEpisode)
        .where(MemoryEpisode.user_id == ctx.user_id)
        .order_by(MemoryEpisode.created_at.desc())
        .limit(50)
    ).all())
    query_lower = query_stripped.lower()
    for ep in episodes:
        summary_lower = ep.summary.lower()
        if query_lower in summary_lower:
            scored.append(
                (0.9, f"[Episode {ep.date}] {ep.summary}", "episode"))
            continue
        query_words = set(query_lower.split())
        summary_words = set(summary_lower.split())
        if query_words and summary_words:
            overlap = len(query_words & summary_words) / len(query_words)
            if overlap >= 0.5:
                scored.append(
                    (overlap, f"[Episode {ep.date}] {ep.summary}", "episode"))

    if not scored:
        return f"No memories found matching: {query}"

    scored.sort(key=lambda x: x[0], reverse=True)
    lines: list[str] = []
    for _score, content, cat_label in scored[:10]:
        lines.append(f"- [{cat_label}] {content}")

    return f"Found {len(scored)} matching memories:\n" + "\n".join(lines)


@tool
def recall_conversation(query: str, role: str = "", start_date: str = "", end_date: str = "", limit: str = "10") -> str:
    """Search past conversations for specific exchanges or topics.
    Use this when the user asks about something discussed previously,
    or when you need to recall a specific past conversation.

    Args:
        query: What to search for — described naturally.
        role: Filter by message role: 'user', 'assistant', or empty for all.
        start_date: Only return messages from this date onward (YYYY-MM-DD). Inclusive.
        end_date: Only return messages up to this date (YYYY-MM-DD). Inclusive.
        limit: Maximum results to return (default 10).

    Examples:
        - "what did we talk about yesterday?" -> query="yesterday's topics"
        - "what did I say about my job?" -> query="job work career"
        - "conversations from last week" -> query="", start_date="2026-03-09", end_date="2026-03-15"
    """
    import asyncio
    from anima_server.services.agent.tool_context import get_tool_context
    from anima_server.services.agent.conversation_search import search_conversation_history

    ctx = get_tool_context()

    max_results = 10
    try:
        max_results = max(1, min(20, int(limit)))
    except (ValueError, TypeError):
        pass

    loop = asyncio.get_event_loop()
    hits = loop.run_until_complete(search_conversation_history(
        ctx.db,
        user_id=ctx.user_id,
        query=query.strip(),
        role_filter=role.strip(),
        start_date=start_date.strip(),
        end_date=end_date.strip(),
        limit=max_results,
    ))

    if not hits:
        return f"No past conversations found matching: {query}" if query.strip() else "No conversations found in that date range."

    lines: list[str] = []
    for hit in hits:
        lines.append(f"- [{hit.date}] {hit.role}: {hit.content}")

    return f"Found {len(hits)} conversation matches:\n" + "\n".join(lines)


@tool
def update_human_memory(content: str) -> str:
    """Update your holistic mental model of the user. This is your high-level
    understanding — a living summary of who this person is. The content should be
    the COMPLETE updated model (include existing knowledge plus new information).
    Write in concise key-value or bullet style.
    USE THIS FOR: big-picture understanding (job, life situation, personality,
    communication style, key relationships, major life events).
    DO NOT USE FOR: discrete searchable facts — use save_to_memory instead.
    Rule of thumb: if it's a standalone detail you'd want to search later
    ("allergic to peanuts"), use save_to_memory(category="fact"). If it changes
    your overall picture of who this person is, update this block.
    Do NOT duplicate the same information in both this tool and save_to_memory.
    """
    from anima_server.services.agent.tool_context import get_tool_context
    from anima_server.models import SelfModelBlock
    from sqlalchemy import select

    ctx = get_tool_context()
    block = ctx.db.scalar(
        select(SelfModelBlock).where(
            SelfModelBlock.user_id == ctx.user_id,
            SelfModelBlock.section == "human",
        )
    )
    if block is None:
        ctx.db.add(SelfModelBlock(
            user_id=ctx.user_id,
            section="human",
            content=content.strip(),
            version=1,
            updated_by="agent_tool",
        ))
    else:
        block.content = content.strip()
        block.version += 1
        block.updated_by = "agent_tool"
    ctx.db.flush()

    from anima_server.services.agent.companion import get_companion
    companion = get_companion()
    if companion is not None:
        companion.invalidate_memory()

    return "Human memory updated."


def get_tools() -> list[Any]:
    """Return all tools available to the agent."""
    return [
        current_datetime, send_message,
        note_to_self, dismiss_note, save_to_memory,
        set_intention, complete_goal,
        create_task, list_tasks, complete_task,
        recall_memory, recall_conversation, update_human_memory,
    ]


def get_tool_summaries(tools: Sequence[Any] | None = None) -> list[str]:
    """Render tool names and descriptions for prompt construction."""
    resolved_tools = tools or get_tools()
    summaries: list[str] = []

    for agent_tool in resolved_tools:
        name = getattr(agent_tool, "name", "") or getattr(
            agent_tool, "__name__", "tool")
        description = getattr(agent_tool, "description", "") or ""
        normalized_description = " ".join(description.strip().split())
        if normalized_description:
            summaries.append(f"{name}: {normalized_description}")
        else:
            summaries.append(name)

    return summaries


def get_tool_rules(tools: Sequence[Any] | None = None) -> tuple[ToolRule, ...]:
    """Return the default orchestration rules for the registered tools."""
    resolved_tools = tools or get_tools()
    tool_names = {
        getattr(agent_tool, "name", "") or getattr(agent_tool, "__name__", "")
        for agent_tool in resolved_tools
    }
    return build_default_tool_rules(tool_names)
