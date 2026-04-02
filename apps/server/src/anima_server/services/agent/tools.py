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
    """Save a scratch-pad note for this session only (not permanent). Types: observation, plan, context, emotion. For lasting facts use save_to_memory instead."""
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
    removed = remove_session_note(
        ctx.runtime_db, thread_id=ctx.thread_id, key=key)
    if removed:
        from anima_server.services.agent.companion import get_companion

        companion = get_companion(ctx.user_id)
        if companion is not None:
            companion.invalidate_memory()
        return f"Dismissed note: {key}"
    return f"No active note found with key: {key}"


@tool
def save_to_memory(key: str, category: str = "fact", importance: str = "3", tags: str = "") -> str:
    """Save a personal fact, preference, or relationship to permanent memory. Use this for anything about the user that should be remembered across sessions (names, pets, occupation, preferences, goals). Categories: fact, preference, goal, relationship. Importance: 1-5. Tags: comma-separated."""
    from anima_server.services.agent.candidate_ops import create_memory_candidate
    from anima_server.services.agent.session_memory import promote_session_note
    from anima_server.services.agent.tool_context import get_tool_context

    ctx = get_tool_context()
    imp = 3
    with contextlib.suppress(ValueError, TypeError):
        imp = max(1, min(5, int(importance)))

    if category not in ("fact", "preference", "goal", "relationship"):
        category = "fact"

    parsed_tags = [t.strip().lower()
                   for t in tags.split(",") if t.strip()] if tags else None

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
    """Track a goal or intention across sessions. Priority: high, ongoing, or background. Deadline: YYYY-MM-DD or empty."""
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
    """Create a task on the user's task list. due_date: YYYY-MM-DD or empty. priority: 1 (low) to 5 (critical)."""
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
    """List the user's open tasks (set include_done=true to include completed)."""
    from sqlalchemy import select

    from anima_server.models.task import Task
    from anima_server.services.agent.tool_context import get_tool_context

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
    """Mark a task as done by providing its text (or close match)."""
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
            overlap = len(text_words & task_words) / \
                max(len(text_words), len(task_words))
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


def _search_candidates(
    runtime_db,
    *,
    user_id: int,
    query: str,
    category: str | None = None,
    limit: int = 10,
) -> list[tuple[float, str, str]]:
    """Search MemoryCandidate rows in PG by keyword. Used as fallback when
    no MemoryItem results are found.  Only searches non-promoted candidates."""
    from sqlalchemy import select

    from anima_server.models.runtime_memory import MemoryCandidate

    stmt = (
        select(MemoryCandidate)
        .where(
            MemoryCandidate.user_id == user_id,
            MemoryCandidate.status.in_(["extracted", "queued"]),
        )
        .order_by(MemoryCandidate.created_at.desc())
        .limit(30)
    )
    candidates = list(runtime_db.scalars(stmt).all())

    query_lower = query.lower()
    scored: list[tuple[float, str, str]] = []
    for c in candidates:
        content_lower = c.content.lower()
        if category and c.category != category:
            continue
        if query_lower in content_lower:
            scored.append((0.8, f"[pending] {c.content}", c.category))
            continue
        query_words = set(query_lower.split())
        content_words = set(content_lower.split())
        if query_words and content_words:
            overlap = len(query_words & content_words) / len(query_words)
            if overlap >= 0.4:
                scored.append(
                    (overlap * 0.8, f"[pending] {c.content}", c.category))

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[:limit]


@tool
def recall_memory(
    query: str, category: str = "", tags: str = "", page: str = "1", count: str = "5"
) -> str:
    """Search memory for user information (hybrid semantic + keyword). Filter by category (fact/preference/goal/relationship) and tags. Use one focused topic per query. Page 1 = first page."""
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

    parsed_tags = [t.strip().lower()
                   for t in tags.split(",") if t.strip()] if tags else None

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
            result = asyncio.run_coroutine_threadsafe(
                coro, loop).result(timeout=30)
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
                    df(ctx.user_id, item.content,
                       table="memory_items", field="content"),
                    item.category,
                )
            )
        search_paths["hybrid"] = hybrid_count
    except Exception as exc:
        logger.warning("hybrid_search failed for query=%r: %s",
                       query_stripped, exc)
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
            limit=40,
        )
        for item in items:
            plaintext = df(ctx.user_id, item.content,
                           table="memory_items", field="content")
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

    # Also search episodes (skip when filtering to a specific non-episode category)
    episode_count = 0
    if not cat or cat == "episode":
        episodes = list(
            ctx.db.scalars(
                select(MemoryEpisode)
                .where(MemoryEpisode.user_id == ctx.user_id)
                .order_by(MemoryEpisode.created_at.desc())
                .limit(20)
            ).all()
        )
        query_lower = query_stripped.lower()
        for ep in episodes:
            ep_plaintext = df(ctx.user_id, ep.summary,
                              table="memory_episodes", field="summary")
            summary_lower = ep_plaintext.lower()
            if query_lower in summary_lower:
                episode_count += 1
                scored.append(
                    (0.9, f"[Episode {ep.date}] {ep_plaintext}", "episode"))
                continue
            query_words = set(query_lower.split())
            summary_words = set(summary_lower.split())
            if query_words and summary_words:
                overlap = len(query_words & summary_words) / len(query_words)
                if overlap >= 0.5:
                    episode_count += 1
                    scored.append(
                        (overlap, f"[Episode {ep.date}] {ep_plaintext}", "episode"))
        search_paths["episodes"] = episode_count
    else:
        search_paths["episodes"] = "skipped (category filter)"

    # Candidate fallback: search PG for extracted-but-not-yet-promoted candidates
    candidate_count = 0
    if not scored:
        try:
            candidate_results = _search_candidates(
                ctx.runtime_db,
                user_id=ctx.user_id,
                query=query_stripped,
                category=cat,
            )
            for score, content, cat_label in candidate_results:
                candidate_count += 1
                scored.append((score, content, cat_label))
            search_paths["candidates"] = candidate_count
        except Exception:
            logger.debug("Candidate fallback search failed")
            search_paths["candidates"] = 0

    if not scored:
        paths_summary = ", ".join(f"{k}={v}" for k, v in search_paths.items())
        return f"No memories found matching: {query} [search: {paths_summary}]"

    # Parse pagination parameters (1-indexed input → 0-indexed internal)
    try:
        page_num = max(0, int(page) - 1)
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
        result += f"\nUse page={page_num + 2} to see more results."
    return result


@tool
def recall_conversation(
    query: str, role: str = "", start_date: str = "", end_date: str = "", limit: str = "10"
) -> str:
    """Search past conversations by topic. Filter by role (user/assistant), date range (YYYY-MM-DD), and limit."""
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
    """Search past transcripts for exact wording or verbatim recall. Returns relevant snippets."""
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
    """Replace your holistic model of the user (complete rewrite). Use for big-picture understanding. For discrete searchable facts use save_to_memory instead."""
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
    """Append to an in-context memory block (takes effect this conversation). Labels: human, persona."""
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
    """Replace exact text in an in-context memory block (takes effect this conversation). Labels: human, persona. old_text must match exactly."""
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
        params = s.get(
            "parameters", {"type": "object", "properties": {}, "required": []})
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
