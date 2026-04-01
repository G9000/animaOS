"""Agent tools registry.

Add tools here as plain functions decorated with @tool.
The `get_tools()` list is bound to the loop runtime and exposed to the LLM.
"""

from __future__ import annotations

import contextlib
import copy
import inspect
import logging
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any, get_type_hints

from anima_server.services.agent.rules import ToolRule, build_default_tool_rules
from anima_server.services.data_crypto import df

logger = logging.getLogger(__name__)


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
    return _SimpleSchema(
        {
            "type": "object",
            "properties": properties,
            "required": required,
        }
    )


@tool
def current_datetime() -> str:
    """Return the current date and time in ISO-8601 format (UTC)."""
    return datetime.now(UTC).isoformat()


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
    from anima_server.services.agent.session_memory import write_session_note
    from anima_server.services.agent.tool_context import get_tool_context

    ctx = get_tool_context()
    write_session_note(
        ctx.runtime_db,
        thread_id=ctx.thread_id,
        user_id=ctx.user_id,
        key=key,
        value=value,
        note_type=note_type,
    )

    from anima_server.services.agent.companion import get_companion

    companion = get_companion(ctx.user_id)
    if companion is not None:
        companion.invalidate_memory()

    return f"Noted: {key}"


@tool
def dismiss_note(key: str) -> str:
    """Remove a session note that is no longer relevant."""
    from anima_server.services.agent.session_memory import remove_session_note
    from anima_server.services.agent.tool_context import get_tool_context

    ctx = get_tool_context()
    removed = remove_session_note(ctx.runtime_db, thread_id=ctx.thread_id, key=key)
    if removed:
        from anima_server.services.agent.companion import get_companion

        companion = get_companion(ctx.user_id)
        if companion is not None:
            companion.invalidate_memory()
        return f"Dismissed note: {key}"
    return f"No active note found with key: {key}"


@tool
def save_to_memory(key: str, category: str = "fact", importance: str = "3", tags: str = "") -> str:
    """Save a fact to permanent long-term memory (discrete items, searchable).
    Use this for specific, categorical user facts that benefit from structured recall.
    If a matching session note exists it will be promoted; otherwise the key text
    is stored directly — no prior note_to_self is required.
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
    from anima_server.services.agent.candidate_ops import create_memory_candidate
    from anima_server.services.agent.session_memory import promote_session_note
    from anima_server.services.agent.tool_context import get_tool_context

    ctx = get_tool_context()
    imp = 3
    with contextlib.suppress(ValueError, TypeError):
        imp = max(1, min(5, int(importance)))

    if category not in ("fact", "preference", "goal", "relationship"):
        category = "fact"

    parsed_tags = [t.strip().lower() for t in tags.split(",") if t.strip()] if tags else None

    # Try session-note promotion first (the original two-step flow).
    promoted = promote_session_note(
        ctx.runtime_db,
        thread_id=ctx.thread_id,
        user_id=ctx.user_id,
        key=key,
        category=category,
        importance=imp,
        tags=parsed_tags,
    )
    if promoted:
        from anima_server.services.agent.companion import get_companion

        companion = get_companion(ctx.user_id)
        if companion is not None:
            companion.invalidate_memory()
        return f"Saved '{key}' to permanent memory (category: {category})"

    # No session note found — create a memory candidate directly from the key text.
    candidate = create_memory_candidate(
        ctx.runtime_db,
        user_id=ctx.user_id,
        content=key,
        category=category,
        importance=imp,
        importance_source="user_explicit",
        source="tool",
        tags=parsed_tags,
    )
    if candidate is None:
        return f"Already saved: '{key}' is a duplicate"

    from anima_server.services.agent.companion import get_companion

    companion = get_companion(ctx.user_id)
    if companion is not None:
        companion.invalidate_memory()
    return f"Saved '{key}' to permanent memory (category: {category})"


@tool
def set_intention(
    title: str, evidence: str = "", priority: str = "background", deadline: str = ""
) -> str:
    """Track an ongoing goal or intention for this user across sessions. Use when you notice
    a recurring need, upcoming deadline, or something you should proactively follow up on.
    Priority: high (deadline/urgent), ongoing (long-term), background (passive awareness).
    Examples:
    - title="Help prepare Q2 review", priority="high", deadline="2026-03-20"
    - title="Track career transition progress", priority="ongoing"
    """
    from anima_server.services.agent.intentions import add_intention
    from anima_server.services.agent.tool_context import get_tool_context

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

    companion = get_companion(ctx.user_id)
    if companion is not None:
        companion.invalidate_memory()

    return f"Tracking intention: {title}"


@tool
def complete_goal(title: str) -> str:
    """Mark a tracked intention/goal as completed when the user has achieved it or it's no longer needed."""
    from anima_server.services.agent.intentions import complete_intention
    from anima_server.services.agent.tool_context import get_tool_context

    ctx = get_tool_context()
    found = complete_intention(ctx.db, user_id=ctx.user_id, title=title)
    if found:
        from anima_server.services.agent.companion import get_companion

        companion = get_companion(ctx.user_id)
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
    from anima_server.models.task import Task
    from anima_server.schemas.task import normalize_due_date, normalize_task_text
    from anima_server.services.agent.tool_context import get_tool_context

    ctx = get_tool_context()
    pri = 2
    with contextlib.suppress(ValueError, TypeError):
        pri = max(1, min(5, int(priority)))

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

    companion = get_companion(ctx.user_id)
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
    from sqlalchemy import select

    from anima_server.models.task import Task
    from anima_server.services.agent.tool_context import get_tool_context

    ctx = get_tool_context()
    query = select(Task).where(Task.user_id == ctx.user_id)
    if include_done.lower() not in ("true", "yes", "1"):
        query = query.where(Task.done == False)  # noqa: E712
    query = query.order_by(Task.done, Task.priority.desc(), Task.created_at.desc())
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
    from sqlalchemy import select

    from anima_server.models.task import Task
    from anima_server.services.agent.tool_context import get_tool_context

    ctx = get_tool_context()
    tasks = list(
        ctx.db.scalars(
            select(Task).where(Task.user_id == ctx.user_id, Task.done == False)  # noqa: E712
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
            overlap = len(text_words & task_words) / max(len(text_words), len(task_words))
            if overlap > best_score:
                best_score = overlap
                best_task = t

    if best_task is None or (best_score < 0.3 and text_lower != best_task.text.lower()):
        return f"Could not find a matching task for: {text}"

    best_task.done = True
    best_task.completed_at = datetime.now(UTC)
    best_task.updated_at = datetime.now(UTC)
    ctx.db.flush()

    from anima_server.services.agent.companion import get_companion

    companion = get_companion(ctx.user_id)
    if companion is not None:
        companion.invalidate_memory()

    return f"Completed: {best_task.text}"


@tool
def recall_memory(
    query: str, category: str = "", tags: str = "", page: str = "0", count: str = "5"
) -> str:
    """Search your memory for information about the user. Use this when the user asks
    what you remember, or when you need to look up something specific about them.
    Returns matching memories ranked by relevance (semantic + keyword hybrid search).
    Optional category filter: fact, preference, goal, relationship (or empty for all).
    Optional tags filter: comma-separated labels to narrow results (e.g. "work,career").
    Optional page: 0-indexed page number for paginated results (default "0").
    Optional count: number of results per page (default "5").
    Examples:
    - "what do you remember about my sister?" -> query="sister"
    - "what are my goals?" -> query="goals", category="goal"
    - "work-related facts" -> query="work", tags="work,career"
    - "show me more memories" -> query="...", page="1"
    """
    import asyncio

    from sqlalchemy import select

    from anima_server.models import MemoryEpisode
    from anima_server.services.agent.tool_context import get_tool_context

    ctx = get_tool_context()
    query_stripped = query.strip()
    if not query_stripped:
        return "Please provide a search query."

    cat = category.strip().lower() if category else None
    if cat and cat not in ("fact", "preference", "goal", "relationship"):
        cat = None

    parsed_tags = [t.strip().lower() for t in tags.split(",") if t.strip()] if tags else None

    # Use hybrid search (semantic + keyword) via Phase 1 infrastructure
    scored: list[tuple[float, str, str]] = []
    search_paths: dict[str, int | str] = {}
    try:
        from anima_server.services.agent.embeddings import hybrid_search

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        coro = hybrid_search(
            ctx.db,
            user_id=ctx.user_id,
            query=query_stripped,
            limit=20,
            similarity_threshold=0.2,
            tags=parsed_tags,
        )
        if loop is not None:
            result = asyncio.run_coroutine_threadsafe(coro, loop).result(timeout=30)
        else:
            result = asyncio.run(coro)
        hybrid_count = 0
        for item, score in result.items:
            if cat and item.category != cat:
                continue
            hybrid_count += 1
            scored.append(
                (
                    score,
                    df(ctx.user_id, item.content, table="memory_items", field="content"),
                    item.category,
                )
            )
        search_paths["hybrid"] = hybrid_count
    except Exception as exc:
        logger.warning("hybrid_search failed for query=%r: %s", query_stripped, exc)
        search_paths["hybrid"] = f"error: {exc}"

    # Text-based fallback: used when hybrid fails OR returns no items
    keyword_count = 0
    if not scored:
        from anima_server.services.agent.memory_store import get_memory_items

        query_lower = query_stripped.lower()
        items = get_memory_items(
            ctx.db,
            user_id=ctx.user_id,
            category=cat,
            limit=100,
        )
        for item in items:
            plaintext = df(ctx.user_id, item.content, table="memory_items", field="content")
            content_lower = plaintext.lower()
            if query_lower in content_lower:
                keyword_count += 1
                scored.append((1.0, plaintext, item.category))
                continue
            query_words = set(query_lower.split())
            content_words = set(content_lower.split())
            if query_words and content_words:
                overlap = len(query_words & content_words) / len(query_words)
                if overlap >= 0.5:
                    keyword_count += 1
                    scored.append((overlap, plaintext, item.category))
        search_paths["keyword"] = keyword_count

    # Also search episodes
    episodes = list(
        ctx.db.scalars(
            select(MemoryEpisode)
            .where(MemoryEpisode.user_id == ctx.user_id)
            .order_by(MemoryEpisode.created_at.desc())
            .limit(50)
        ).all()
    )
    episode_count = 0
    query_lower = query_stripped.lower()
    for ep in episodes:
        ep_plaintext = df(ctx.user_id, ep.summary, table="memory_episodes", field="summary")
        summary_lower = ep_plaintext.lower()
        if query_lower in summary_lower:
            episode_count += 1
            scored.append((0.9, f"[Episode {ep.date}] {ep_plaintext}", "episode"))
            continue
        query_words = set(query_lower.split())
        summary_words = set(summary_lower.split())
        if query_words and summary_words:
            overlap = len(query_words & summary_words) / len(query_words)
            if overlap >= 0.5:
                episode_count += 1
                scored.append((overlap, f"[Episode {ep.date}] {ep_plaintext}", "episode"))
    search_paths["episodes"] = episode_count

    if not scored:
        paths_summary = ", ".join(f"{k}={v}" for k, v in search_paths.items())
        return f"No memories found matching: {query} [search: {paths_summary}]"

    # Parse pagination parameters
    try:
        page_num = max(0, int(page))
    except (ValueError, TypeError):
        page_num = 0
    try:
        per_page = max(1, int(count))
    except (ValueError, TypeError):
        per_page = 5

    scored.sort(key=lambda x: x[0], reverse=True)

    total = len(scored)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page_num = min(page_num, total_pages - 1)
    start = page_num * per_page
    end = start + per_page
    page_items = scored[start:end]

    lines: list[str] = []
    for _score, content, cat_label in page_items:
        lines.append(f"- [{cat_label}] {content}")

    header = f"Found {total} matching memories (showing page {page_num + 1} of {total_pages}):"
    result = header + "\n" + "\n".join(lines)
    if page_num + 1 < total_pages:
        result += f"\nUse page={page_num + 1} to see more results."
    return result


@tool
def recall_conversation(
    query: str, role: str = "", start_date: str = "", end_date: str = "", limit: str = "10"
) -> str:
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

    from anima_server.services.agent.conversation_search import search_conversation_history
    from anima_server.services.agent.tool_context import get_tool_context

    ctx = get_tool_context()

    max_results = 10
    with contextlib.suppress(ValueError, TypeError):
        max_results = max(1, min(20, int(limit)))

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None:
        future = asyncio.run_coroutine_threadsafe(
            search_conversation_history(
                ctx.runtime_db,
                ctx.db,
                user_id=ctx.user_id,
                query=query.strip(),
                role_filter=role.strip(),
                start_date=start_date.strip(),
                end_date=end_date.strip(),
                limit=max_results,
            ),
            loop,
        )
        hits = future.result(timeout=30)
    else:
        hits = asyncio.run(
            search_conversation_history(
                ctx.runtime_db,
                ctx.db,
                user_id=ctx.user_id,
                query=query.strip(),
                role_filter=role.strip(),
                start_date=start_date.strip(),
                end_date=end_date.strip(),
                limit=max_results,
            )
        )

    if not hits:
        return (
            f"No past conversations found matching: {query}"
            if query.strip()
            else "No conversations found in that date range."
        )

    lines: list[str] = []
    for hit in hits:
        lines.append(f"- [{hit.date}] {hit.role}: {hit.content}")

    return f"Found {len(hits)} conversation matches:\n" + "\n".join(lines)


@tool
def recall_transcript(query: str, days_back: int = 30) -> str:
    """Search past conversation transcripts for specific details.
    Use this when you need exact wording or verbatim recall from
    past conversations, not just general memory of what happened.
    Returns relevant snippets, not full conversations.
    """
    from anima_server.config import settings
    from anima_server.services.agent.tool_context import get_tool_context
    from anima_server.services.agent.transcript_search import format_snippets, search_transcripts
    from anima_server.services.data_crypto import get_active_dek

    ctx = get_tool_context()
    dek = get_active_dek(ctx.user_id, "conversations")
    try:
        parsed_days_back = int(days_back)
    except (TypeError, ValueError):
        parsed_days_back = 30

    snippets = search_transcripts(
        query=query,
        user_id=ctx.user_id,
        dek=dek,
        transcripts_dir=settings.data_dir / "transcripts",
        days_back=parsed_days_back,
    )
    return format_snippets(snippets)


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
    from anima_server.services.agent.pending_ops import create_pending_op
    from anima_server.services.agent.tool_context import get_tool_context

    ctx = get_tool_context()
    create_pending_op(
        ctx.runtime_db,
        user_id=ctx.user_id,
        op_type="full_replace",
        target_block="human",
        content=content.strip(),
        old_content=None,
        source_run_id=ctx.run_id,
        source_tool_call_id=ctx.current_tool_call_id,
    )
    ctx.memory_modified = True

    from anima_server.services.agent.companion import get_companion

    companion = get_companion(ctx.user_id)
    if companion is not None:
        companion.invalidate_memory()

    return "Human memory updated."


@tool
def core_memory_append(label: str, content: str) -> str:
    """Append new information to one of your in-context memory blocks. The change
    takes effect in the CURRENT conversation — you will see the updated block
    in your next reasoning step. Use this for incremental additions.
    Valid labels: human (your understanding of the user), persona (your own identity/style).
    Examples:
    - core_memory_append("human", "Mentioned they recently adopted a rescue dog named Biscuit.")
    - core_memory_append("persona", "I've noticed I tend to be more playful in evening chats.")
    """
    from anima_server.services.agent.pending_ops import create_pending_op
    from anima_server.services.agent.tool_context import get_tool_context

    if label not in ("human", "persona"):
        return f"Invalid label '{label}'. Use 'human' or 'persona'."

    ctx = get_tool_context()
    create_pending_op(
        ctx.runtime_db,
        user_id=ctx.user_id,
        op_type="append",
        target_block=label,
        content=content.strip(),
        old_content=None,
        source_run_id=ctx.run_id,
        source_tool_call_id=ctx.current_tool_call_id,
    )

    ctx.memory_modified = True
    from anima_server.services.agent.companion import get_companion

    companion = get_companion(ctx.user_id)
    if companion is not None:
        companion.invalidate_memory()

    return f"Appended to {label} memory. It will be visible in your next step."


@tool
def core_memory_replace(label: str, old_text: str, new_text: str) -> str:
    """Replace specific text in one of your in-context memory blocks. The change
    takes effect in the CURRENT conversation. Use this to correct outdated
    information or refine your understanding.
    Valid labels: human (your understanding of the user), persona (your own identity/style).
    The old_text must match exactly (case-sensitive) to be replaced.
    Examples:
    - core_memory_replace("human", "Works at Google", "Works at Apple (switched jobs March 2026)")
    - core_memory_replace("persona", "I prefer formal language", "I adapt my formality to match the user's style")
    """
    from anima_server.services.agent.memory_blocks import build_merged_block_content
    from anima_server.services.agent.pending_ops import create_pending_op
    from anima_server.services.agent.tool_context import get_tool_context

    if label not in ("human", "persona"):
        return f"Invalid label '{label}'. Use 'human' or 'persona'."

    ctx = get_tool_context()
    existing_text = build_merged_block_content(
        ctx.db,
        ctx.runtime_db,
        user_id=ctx.user_id,
        section=label,
    )
    if not existing_text:
        return f"No {label} memory block exists yet. Use core_memory_append to create one."

    if old_text not in existing_text:
        return f"Could not find the exact text to replace in {label} memory."

    create_pending_op(
        ctx.runtime_db,
        user_id=ctx.user_id,
        op_type="replace",
        target_block=label,
        content=new_text.strip(),
        old_content=old_text,
        source_run_id=ctx.run_id,
        source_tool_call_id=ctx.current_tool_call_id,
    )

    ctx.memory_modified = True
    from anima_server.services.agent.companion import get_companion

    companion = get_companion(ctx.user_id)
    if companion is not None:
        companion.invalidate_memory()

    return f"Replaced text in {label} memory. It will be visible in your next step."



@tool
def check_system_health() -> str:
    """Run system health checks and return a formatted report.

    Checks database integrity, LLM connectivity, and background task status.
    """
    import asyncio

    from anima_server.services.agent.tool_context import get_tool_context
    from anima_server.services.health.registry import get_default_registry

    ctx = get_tool_context()
    registry = get_default_registry()

    report = asyncio.run(registry.run_all(user_id=ctx.user_id))
    return registry.format_report(report)




def get_core_tools() -> list[Any]:
    """Return the minimal cognitive tool set.

    These 6 tools are the AI's core capabilities — communicate,
    remember, learn, persist.  Everything else is an extension.
    """
    return [
        send_message,
        recall_memory,
        recall_conversation,
        core_memory_append,
        core_memory_replace,
        save_to_memory,
    ]


def get_extension_tools() -> list[Any]:
    """Return optional extension tools (task management, intentions, etc.)."""
    return [
        create_task,
        list_tasks,
        complete_task,
        set_intention,
        complete_goal,
        note_to_self,
        dismiss_note,
        update_human_memory,
        current_datetime,
        recall_transcript,
        check_system_health,
    ]


def get_tools() -> list[Any]:
    """Return all tools available to the agent (core + extensions)."""
    return get_core_tools() + get_extension_tools()


def prepare_action_tool_schemas(
    schemas: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Convert client-registered action tool schemas into OpenAI function format.

    Returns dicts that LangChain's ``bind_tools`` accepts directly alongside
    native tool objects.
    """
    result: list[dict[str, Any]] = []
    for schema in schemas:
        s = copy.deepcopy(schema)
        params = s.get("parameters", {"type": "object", "properties": {}, "required": []})
        result.append(
            {
                "type": "function",
                "function": {
                    "name": s["name"],
                    "description": s.get("description", ""),
                    "parameters": params,
                },
            }
        )
    return result


def get_tool_summaries(tools: Sequence[Any] | None = None) -> list[str]:
    """Render tool names and descriptions for prompt construction."""
    resolved_tools = tools or get_tools()
    summaries: list[str] = []

    for agent_tool in resolved_tools:
        name = getattr(agent_tool, "name", "") or getattr(agent_tool, "__name__", "tool")
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
