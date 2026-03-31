---
title: Agent Tools
description: The 17 tools available to the LLM agent during conversation
category: architecture
updated: 2026-03-31
---

# Agent Tools

[Back to Index](README.md)

The LLM agent has access to 17 tools during conversation, organized into 6 core tools and 11 extension tools. Defined in `services/agent/tools.py`.

## Tool Architecture

Every tool schema has one injected parameter that the tool functions never see:

1. **`thinking`** (required string, first parameter) -- The agent's private inner monologue. Injected by `inject_inner_thoughts_into_tools()`. Stripped by `unpack_inner_thoughts_from_kwargs()` in the executor before dispatch. Stored in `ToolExecutionResult.inner_thinking` for consolidation and tracing.

The `request_heartbeat` parameter has been removed. The runtime now uses V3-style loop continuation — the loop always continues after non-terminal tool calls.

## Core Tools (6)

These are the AI's fundamental capabilities -- communicate, remember, learn, persist.

| Tool | Signature | Purpose |
|------|-----------|---------|
| `send_message` | `(message: str)` | Final response to user, ends turn (terminal tool) |
| `recall_memory` | `(query: str, category?: str, tags?: str, page?: str, count?: str)` | Hybrid search (semantic + keyword) across memories and episodes, with pagination |
| `recall_conversation` | `(query: str, role?: str, start_date?: str, end_date?: str, limit?: str)` | Search past conversation history |
| `core_memory_append` | `(label: str, content: str)` | Append to human or persona memory block (immediate, in-context) |
| `core_memory_replace` | `(label: str, old_text: str, new_text: str)` | Replace text in human or persona memory block (immediate, in-context) |
| `save_to_memory` | `(key: str, category?: str, importance?: str, tags?: str)` | Promote a session note to permanent long-term memory |

## Extension Tools (11)

Optional tools for task management, intentions, session notes, transcript search, health, and utility.

| Tool | Signature | Purpose |
|------|-----------|---------|
| `create_task` | `(text: str, due_date?: str, priority?: str)` | Add to user's task list |
| `list_tasks` | `(include_done?: str)` | View open tasks |
| `complete_task` | `(text: str)` | Mark task as done (fuzzy match) |
| `set_intention` | `(title: str, evidence?: str, priority?: str, deadline?: str)` | Track ongoing goal across sessions |
| `complete_goal` | `(title: str)` | Mark intention as done |
| `note_to_self` | `(key: str, value: str, note_type?: str)` | Session-scoped scratch note (not permanent) |
| `dismiss_note` | `(key: str)` | Remove a session note |
| `update_human_memory` | `(content: str)` | Rewrite holistic user understanding (human core block) |
| `current_datetime` | `()` | UTC timestamp |
| `recall_transcript` | `(query: str, days_back?: int)` | Search encrypted JSONL transcript archives for verbatim past conversation content |
| `check_system_health` | `()` | Run system health checks (DB integrity, LLM connectivity, background tasks) and return formatted report |

## Tool Organization

```python
def get_tools() -> list[Any]:
    """Return all tools available to the agent (core + extensions)."""
    tools = get_core_tools() + get_extension_tools()
    inject_inner_thoughts_into_tools(tools)
    return tools
```

`get_core_tools()` and `get_extension_tools()` are separate functions, enabling a future OpenClaw-style pattern where the tool set can be configured per-user or per-session.

### Action Tool Schema Injection

When a connected client (Animus CLI) registers action tools, `prepare_action_tool_schemas()` converts their schemas into OpenAI function format with `thinking` injected. These are passed as `extra_tool_schemas` to the runtime.

## Tool Orchestration Rules

Defined in `services/agent/rules.py` via `ToolRulesSolver`:

- `send_message` ends the turn (terminal tool) -- the only default rule
- No `InitToolRule` -- the model can call any tool at step 0
- Maximum 6 steps per turn (`agent_max_steps` setting)
- V3-style: loop always continues after non-terminal tools (no heartbeat needed)
- Non-terminal tools execute in parallel; terminal tools execute sequentially
- If max steps reached without `send_message`, stop reason is `NO_TERMINAL_TOOL`

## Cognitive Loop

The system prompt instructs a 2-step cognitive pattern:

```
1. ACT: Call tools as needed -- every tool call includes a `thinking` argument
   with your private reasoning. The loop continues automatically after non-terminal tools.
2. RESPOND: Call send_message with your final reply (include `thinking`).
```

## Memory Tool Guidelines

The system prompt provides clear guidance on when to use which memory tool:

- **`core_memory_append/replace`**: For information that changes understanding (immediate, in-context). Labels: `human`, `persona`. Writes to `PendingMemoryOps` (PG) — promoted to soul DB by Soul Writer.
- **`save_to_memory`**: For discrete, searchable facts. Categories: `fact`, `preference`, `goal`, `relationship`. Importance 1-5. Promotes session notes to `MemoryCandidates` (PG).
- **`update_human_memory`**: For rewriting the entire user model. Writes to `PendingMemoryOps` (PG). Use sparingly.
- **`note_to_self`**: For session-only scratch notes (stored in PG `SessionNotes`). Types: `observation`, `plan`, `context`, `emotion`.
- **`recall_memory`**: Hybrid search with pagination (5 results per page by default).
- **`recall_conversation`**: Search past exchanges by query, role, and date range (searches runtime DB messages).
- **`recall_transcript`**: Search encrypted JSONL transcript archives for verbatim past conversation content.
