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
from types import UnionType
from typing import Any, Literal, Union, get_args, get_origin, get_type_hints

from anima_server.services.agent.rules import ToolRule, build_default_tool_rules
from anima_server.services.data_crypto import df
from anima_server.services.user_timezone import (
    extract_timezone_value,
    normalize_timezone_spec,
    store_timezone_in_world_context,
)

logger = logging.getLogger(__name__)

CoreMemoryLabel = Literal["human", "persona"]

_CORE_MEMORY_LABELS = ("human", "persona")
_RUNTIME_BLOCK_LABEL_HINTS: dict[str, str] = {
    "self_working_memory": "Use note_to_self for session-only scratch context instead.",
    "self_inner_state": "This is a runtime self-state block, not a writable core-memory label.",
    "current_focus": "Use note_to_self for temporary focus, not core_memory_*.",
    "thread_summary": "This block is system-generated and should not be edited with core_memory_*.",
}


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
        hint = hints.get(name)
        prop = _schema_for_hint(hint)
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


def _schema_for_hint(hint: Any) -> dict[str, object]:
    if hint is None:
        return {"type": "string"}

    origin = get_origin(hint)
    if origin in (Union, UnionType):
        non_none_args = [arg for arg in get_args(
            hint) if arg is not type(None)]
        if len(non_none_args) == 1:
            return _schema_for_hint(non_none_args[0])

    if origin is Literal:
        values = [value for value in get_args(hint) if value is not None]
        if not values:
            return {"type": "string"}

        value_types = {type(value) for value in values}
        if len(value_types) == 1:
            value_type = value_types.pop()
            if value_type is str:
                return {"type": "string", "enum": list(values)}
            if value_type is int:
                return {"type": "integer", "enum": list(values)}
            if value_type is float:
                return {"type": "number", "enum": list(values)}
            if value_type is bool:
                return {"type": "boolean", "enum": list(values)}
        return {"type": "string", "enum": [str(value) for value in values]}

    if hint is str:
        return {"type": "string"}
    if hint is int:
        return {"type": "integer"}
    if hint is float:
        return {"type": "number"}
    if hint is bool:
        return {"type": "boolean"}
    return {"type": "string"}


def _core_memory_label_error(label: str) -> str:
    guidance = (
        "Valid labels are 'human' and 'persona'. Use 'human' for your evolving "
        "understanding of the user and 'persona' for your own voice and style."
    )
    runtime_hint = _RUNTIME_BLOCK_LABEL_HINTS.get(label)
    if runtime_hint is not None:
        guidance += f" '{label}' is a runtime block label, not a writable core-memory label. {runtime_hint}"
    guidance += " Use note_to_self for session-only scratch context and save_to_memory for durable facts or preferences."
    return f"Invalid label '{label}'. {guidance}"


def _read_self_model_block(*, ctx: Any, section: str) -> str:
    from anima_server.services.agent.memory_blocks import build_merged_block_content
    from anima_server.services.agent.self_model import (
        get_self_model_block,
        render_self_model_section,
    )

    if ctx.runtime_db is None:
        block = get_self_model_block(
            ctx.db, user_id=ctx.user_id, section=section)
        if block is None:
            return ""
        return render_self_model_section(block, user_id=ctx.user_id)

    return build_merged_block_content(
        ctx.db,
        ctx.runtime_db,
        user_id=ctx.user_id,
        section=section,
    )


def _read_merged_core_memory_block(*, ctx: Any, label: CoreMemoryLabel) -> str:
    return _read_self_model_block(ctx=ctx, section=label)


def _resolve_saved_user_timezone() -> tuple[str | None, Any | None]:
    from anima_server.services.agent.tool_context import get_tool_context

    with contextlib.suppress(RuntimeError, ValueError):
        ctx = get_tool_context()
        content = _read_self_model_block(ctx=ctx, section="world")
        timezone_value = extract_timezone_value(content)
        if timezone_value:
            normalized, tzinfo = normalize_timezone_spec(timezone_value)
            return normalized, tzinfo
    return None, None


def _format_time_snapshot(*, label: str, tzinfo: Any) -> str:
    local_now = datetime.now(UTC).astimezone(tzinfo)
    utc_now = local_now.astimezone(UTC)
    timezone_name = local_now.tzname() or label
    return (
        f"{label}: {local_now.isoformat()} ({timezone_name}). "
        f"UTC: {utc_now.isoformat()}"
    )


def _invalidate_memory_cache(user_id: int) -> None:
    from anima_server.services.agent.companion import get_companion

    companion = get_companion(user_id)
    if companion is not None:
        companion.invalidate_memory()


@tool
def current_datetime() -> str:
    """Return the current date and time in the saved user timezone when available; otherwise local machine time. Always includes UTC."""
    saved_label, saved_tz = _resolve_saved_user_timezone()
    if saved_tz is not None:
        return _format_time_snapshot(label=f"Saved user timezone ({saved_label})", tzinfo=saved_tz)

    local_tz = datetime.now().astimezone().tzinfo or UTC
    return _format_time_snapshot(label="Local time", tzinfo=local_tz)


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
    ctx.memory_modified = True

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
        ctx.memory_modified = True
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
        ctx.memory_modified = True
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
    ctx.memory_modified = True
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
    ctx.memory_modified = True

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
        ctx.memory_modified = True
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
    ctx.memory_modified = True

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

    best_task = _find_matching_task(tasks, text)
    if best_task is None:
        return f"Could not find a matching task for: {text}"

    best_task.done = True
    best_task.completed_at = datetime.now(UTC)
    best_task.updated_at = datetime.now(UTC)
    ctx.db.flush()

    from anima_server.services.agent.companion import get_companion

    companion = get_companion(ctx.user_id)
    if companion is not None:
        companion.invalidate_memory()
    ctx.memory_modified = True

    return f"Completed: {best_task.text}"


def _find_matching_task(tasks: Sequence[Any], text: str) -> Any | None:
    text_lower = text.lower().strip()
    if not text_lower:
        return None

    best_task = None
    best_score = 0.0
    for t in tasks:
        task_lower = t.text.lower()
        if text_lower == task_lower:
            return t
        text_words = set(text_lower.split())
        task_words = set(task_lower.split())
        if text_words and task_words:
            overlap = len(text_words & task_words) / \
                max(len(text_words), len(task_words))
            if overlap > best_score:
                best_score = overlap
                best_task = t

    if best_task is None or (best_score < 0.3 and text_lower != best_task.text.lower()):
        return None
    return best_task


def _parse_task_done_value(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in ("true", "yes", "1", "done", "complete", "completed"):
        return True
    if normalized in ("false", "no", "0", "open", "todo", "pending"):
        return False
    raise ValueError(
        "done must be one of true/false, yes/no, done/open, or 1/0.")


@tool
def update_task(
    text: str,
    new_text: str = "",
    due_date: str = "",
    priority: str = "",
    done: str = "",
) -> str:
    """Update an existing task by text (or close match). Leave fields empty to keep them unchanged. due_date accepts YYYY-MM-DD to set, or none/clear/remove to clear. done accepts true/false, yes/no, or done/open."""
    from sqlalchemy import select

    from anima_server.models.task import Task
    from anima_server.schemas.task import normalize_due_date, normalize_task_text
    from anima_server.services.agent.tool_context import get_tool_context

    ctx = get_tool_context()
    tasks = list(
        ctx.db.scalars(
            select(Task).where(Task.user_id == ctx.user_id).order_by(
                Task.done, Task.created_at.desc())
        ).all()
    )
    if not tasks:
        return "No tasks found."

    task = _find_matching_task(tasks, text)
    if task is None:
        return f"Could not find a matching task for: {text}"

    updates: list[str] = []
    if new_text.strip():
        normalized_text = normalize_task_text(new_text)
        if normalized_text != task.text:
            task.text = normalized_text
            updates.append(f"text='{normalized_text}'")

    if priority.strip():
        try:
            parsed_priority = max(1, min(5, int(priority)))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "priority must be an integer between 1 and 5.") from exc
        if parsed_priority != task.priority:
            task.priority = parsed_priority
            updates.append(f"priority={parsed_priority}")

    if due_date.strip():
        normalized_due_date_input = due_date.strip().lower()
        if normalized_due_date_input in ("none", "clear", "remove"):
            if task.due_date is not None:
                task.due_date = None
                updates.append("due date cleared")
        else:
            normalized_due_date = normalize_due_date(due_date)
            if normalized_due_date != task.due_date:
                task.due_date = normalized_due_date
                updates.append(f"due={normalized_due_date}")

    if done.strip():
        parsed_done = _parse_task_done_value(done)
        if parsed_done != task.done:
            task.done = parsed_done
            task.completed_at = datetime.now(UTC) if parsed_done else None
            updates.append("marked done" if parsed_done else "reopened")

    if not updates:
        return f"No changes applied to task: {task.text}"

    task.updated_at = datetime.now(UTC)
    ctx.db.flush()

    from anima_server.services.agent.companion import get_companion

    companion = get_companion(ctx.user_id)
    if companion is not None:
        companion.invalidate_memory()
    ctx.memory_modified = True

    return f"Updated task '{task.text}': " + ", ".join(updates)


@tool
def delete_task(text: str) -> str:
    """Delete a task by text (or close match). Use this when the user wants a task removed entirely."""
    from sqlalchemy import select

    from anima_server.models.task import Task
    from anima_server.services.agent.tool_context import get_tool_context

    ctx = get_tool_context()
    tasks = list(
        ctx.db.scalars(
            select(Task).where(Task.user_id == ctx.user_id).order_by(
                Task.done, Task.created_at.desc())
        ).all()
    )
    if not tasks:
        return "No tasks found."

    task = _find_matching_task(tasks, text)
    if task is None:
        return f"Could not find a matching task for: {text}"

    deleted_text = task.text
    ctx.db.delete(task)
    ctx.db.flush()

    from anima_server.services.agent.companion import get_companion

    companion = get_companion(ctx.user_id)
    if companion is not None:
        companion.invalidate_memory()
    ctx.memory_modified = True

    return f"Deleted task: {deleted_text}"


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
def get_user_timezone() -> str:
    """Return the saved user timezone if one has been set. Use this before answering local-time questions if you are unsure."""
    saved_label, saved_tz = _resolve_saved_user_timezone()
    if saved_tz is not None:
        return _format_time_snapshot(label=f"Saved user timezone ({saved_label})", tzinfo=saved_tz)

    local_tz = datetime.now().astimezone().tzinfo or UTC
    return (
        "No saved user timezone. "
        + _format_time_snapshot(label="Local time", tzinfo=local_tz)
    )


@tool
def set_user_timezone(timezone_name: str) -> str:
    """Save the user's timezone for future local-time answers. Prefer IANA names like Asia/Kuala_Lumpur or America/New_York. UTC offsets like UTC+08:00 also work."""
    from anima_server.services.agent.tool_context import get_tool_context

    normalized, tzinfo = normalize_timezone_spec(timezone_name)
    ctx = get_tool_context()
    existing_content = _read_self_model_block(ctx=ctx, section="world")
    existing_timezone = extract_timezone_value(existing_content)
    if existing_timezone:
        with contextlib.suppress(ValueError):
            existing_normalized, _existing_tz = normalize_timezone_spec(
                existing_timezone)
            if existing_normalized == normalized:
                return (
                    f"User timezone already set to {normalized}. "
                    + _format_time_snapshot(label=f"Saved user timezone ({normalized})", tzinfo=tzinfo)
                )

    store_timezone_in_world_context(
        ctx.db,
        user_id=ctx.user_id,
        existing_content=existing_content,
        timezone_value=normalized,
        updated_by="tool",
    )

    ctx.memory_modified = True
    _invalidate_memory_cache(ctx.user_id)
    return (
        f"Saved user timezone as {normalized}. "
        + _format_time_snapshot(label=f"Saved user timezone ({normalized})", tzinfo=tzinfo)
    )


@tool
def consolidate_pending_memory() -> str:
    """Force a Soul Writer pass now so pending core-memory writes and queued memory candidates are consolidated immediately."""
    import asyncio

    from anima_server.services.agent.pending_ops import count_pending_ops
    from anima_server.services.agent.soul_writer import run_soul_writer
    from anima_server.services.agent.tool_context import get_tool_context

    ctx = get_tool_context()
    ctx.db.commit()
    ctx.runtime_db.commit()
    pending_before = count_pending_ops(ctx.runtime_db, user_id=ctx.user_id)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None:
        result = asyncio.run_coroutine_threadsafe(
            run_soul_writer(ctx.user_id), loop
        ).result(timeout=60)
    else:
        result = asyncio.run(run_soul_writer(ctx.user_id))

    ctx.db.expire_all()
    ctx.runtime_db.expire_all()
    remaining_pending = count_pending_ops(ctx.runtime_db, user_id=ctx.user_id)
    ctx.memory_modified = True
    _invalidate_memory_cache(ctx.user_id)

    total_candidate_work = (
        result.candidates_promoted
        + result.candidates_rejected
        + result.candidates_superseded
        + result.candidates_failed
    )
    if pending_before == 0 and result.ops_processed == 0 and result.ops_skipped == 0 and result.ops_failed == 0 and total_candidate_work == 0:
        return "No pending memory ops or queued memory candidates required consolidation."

    return (
        "Soul Writer finished. "
        f"Ops processed={result.ops_processed}, skipped={result.ops_skipped}, failed={result.ops_failed}; "
        f"candidates promoted={result.candidates_promoted}, rejected={result.candidates_rejected}, "
        f"superseded={result.candidates_superseded}, failed={result.candidates_failed}; "
        f"remaining pending ops={remaining_pending}."
    )


@tool
def read_core_memory(label: CoreMemoryLabel) -> str:
    """Read the exact merged contents of the editable human or persona core-memory block, including unconsolidated pending ops. Use this before core_memory_replace when exact text matters."""
    from anima_server.services.agent.tool_context import get_tool_context

    ctx = get_tool_context()
    content = _read_merged_core_memory_block(ctx=ctx, label=label)
    if not content:
        return f"No {label} memory block exists yet."
    return content


@tool
def list_pending_memory_ops(label: CoreMemoryLabel | None = None) -> str:
    """List unconsolidated pending core-memory writes for human and persona. Use this to verify whether a memory update is queued but not yet consolidated."""
    from anima_server.services.agent.pending_ops import get_pending_ops
    from anima_server.services.agent.tool_context import get_tool_context

    ctx = get_tool_context()
    pending_ops = get_pending_ops(
        ctx.runtime_db,
        user_id=ctx.user_id,
        target_block=label,
    )
    core_ops = [
        op for op in pending_ops if op.target_block in _CORE_MEMORY_LABELS]
    if not core_ops:
        return (
            f"No pending {label} memory ops."
            if label is not None
            else "No pending core-memory ops."
        )

    lines: list[str] = []
    for op in core_ops:
        if op.op_type == "replace" and op.old_content is not None:
            lines.append(
                f"- [{op.target_block}] replace: {op.old_content} -> {op.content}"
            )
        else:
            lines.append(f"- [{op.target_block}] {op.op_type}: {op.content}")
    return f"Pending core-memory ops ({len(core_ops)}):\n" + "\n".join(lines)


@tool
def update_human_memory(content: str) -> str:
    """Replace the full human block in one shot. Use only when you already know the complete desired user model. For one-off facts or preferences, prefer save_to_memory or core_memory_append."""
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

    _invalidate_memory_cache(ctx.user_id)

    return "Human memory updated."


@tool
def core_memory_append(label: CoreMemoryLabel, content: str) -> str:
    """Append text to one of the two writable core memory blocks for this conversation. Use label='human' for user facts or evolving understanding, and label='persona' for your own voice/style. Never use runtime block names like self_working_memory, self_inner_state, current_focus, or thread_summary here. Use note_to_self for session-only scratch context and save_to_memory for durable discrete facts/preferences. If you do not know the exact existing text, append instead of replace."""
    from anima_server.services.agent.pending_ops import create_pending_op
    from anima_server.services.agent.tool_context import get_tool_context

    if label not in _CORE_MEMORY_LABELS:
        return _core_memory_label_error(label)

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
    _invalidate_memory_cache(ctx.user_id)

    return f"Appended to {label} memory. It will be visible in your next step."


@tool
def core_memory_replace(label: CoreMemoryLabel, old_text: str, new_text: str) -> str:
    """Replace exact text inside one writable core memory block for this conversation. Use label='human' for the user model and label='persona' for your own voice/style. Never use runtime block names like self_working_memory here. Only use this when the exact old_text is visible in current memory or prior tool output; otherwise use core_memory_append or update_human_memory."""
    from anima_server.services.agent.memory_blocks import build_merged_block_content
    from anima_server.services.agent.pending_ops import create_pending_op
    from anima_server.services.agent.tool_context import get_tool_context

    if label not in _CORE_MEMORY_LABELS:
        return _core_memory_label_error(label)

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
        return (
            f"Could not find the exact text to replace in {label} memory. "
            "Use core_memory_append if you are adding new information, or "
            "update_human_memory for a full rewrite."
        )

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
    _invalidate_memory_cache(ctx.user_id)

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


_mod_tools_cache: list[Any] | None = None


def reload_mod_tools() -> None:
    """Bust the mod tools cache so the next agent turn re-fetches from anima-mod."""
    global _mod_tools_cache
    _mod_tools_cache = None


def _load_mod_tools() -> list[Any]:
    """Fetch tool schemas from the running anima-mod service and build @tool wrappers.

    Returns an empty list (without caching) if the service is unreachable, so the
    agent degrades gracefully and retries on the next turn.
    """
    global _mod_tools_cache
    if _mod_tools_cache is not None:
        return _mod_tools_cache

    import httpx
    from anima_server.config import settings

    try:
        resp = httpx.get(f"{settings.mod_url}/api/tools", timeout=5.0)
        resp.raise_for_status()
        schemas: list[dict[str, Any]] = resp.json()
    except Exception as exc:
        logger.debug("anima-mod tools unavailable: %s", exc)
        return []  # don't cache — retry next turn

    built: list[Any] = []
    for schema in schemas:
        try:
            built.append(_build_mod_tool(schema))
        except Exception as exc:
            logger.warning("Skipping mod tool %r: %s", schema.get("name"), exc)

    _mod_tools_cache = built
    if built:
        names = ", ".join(t.name for t in built)
        logger.info("Loaded %d mod tool(s) from anima-mod: %s", len(built), names)
    return built


def _build_mod_tool(schema: dict[str, Any]) -> Any:
    """Build a callable @tool from a ModToolSchema returned by GET /api/tools."""
    import httpx
    from anima_server.config import settings

    mod_id: str = schema["modId"]
    name: str = schema["name"]
    description: str = schema["description"]
    endpoint: str = schema["endpoint"]  # e.g. "/gmail/search"
    params_schema: dict[str, Any] = schema["parameters"]

    url = f"{settings.mod_url.rstrip('/')}/{mod_id}{endpoint}"

    def _fn(**kwargs: Any) -> str:
        from anima_server.services.agent.tool_context import get_tool_context
        ctx = get_tool_context()
        payload = {"userId": ctx.user_id, **kwargs}
        try:
            r = httpx.post(url, json=payload, timeout=30.0)
            r.raise_for_status()
            data = r.json()
            if "error" in data:
                return f"[{mod_id}] error: {data['error']}"
            return str(data.get("result", data))
        except httpx.ConnectError:
            return (
                f"[{mod_id}] unavailable — ensure anima-mod is running "
                f"and the {mod_id} mod is enabled."
            )
        except httpx.HTTPStatusError as exc:
            try:
                err = exc.response.json()
                msg = err.get("error") or err.get("message") or str(err)
            except Exception:
                msg = exc.response.text or f"HTTP {exc.response.status_code}"
            return f"[{mod_id}] error ({exc.response.status_code}): {msg}"
        except Exception as exc:
            logger.warning("Mod tool %s failed: %s", name, exc)
            return f"[{mod_id}] {name} failed: {exc}"

    # Override signature so the executor's kwarg-filtering logic sees the real
    # param names even though the underlying function uses **kwargs.
    sig_params = [
        inspect.Parameter(p, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        for p in params_schema.get("properties", {})
    ]
    _fn.__signature__ = inspect.Signature(sig_params)  # type: ignore[attr-defined]
    _fn.__name__ = name
    _fn.name = name  # type: ignore[attr-defined]
    _fn.description = description  # type: ignore[attr-defined]
    _fn.args_schema = _SimpleSchema(params_schema)  # type: ignore[attr-defined]
    return _fn


def get_extension_tools() -> list[Any]:
    """Return optional extension tools (task management, intentions, etc.)."""
    return [
        create_task,
        list_tasks,
        complete_task,
        update_task,
        delete_task,
        set_intention,
        complete_goal,
        note_to_self,
        dismiss_note,
        update_human_memory,
        read_core_memory,
        list_pending_memory_ops,
        consolidate_pending_memory,
        get_user_timezone,
        set_user_timezone,
        current_datetime,
        recall_transcript,
        check_system_health,
        *_load_mod_tools(),
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
