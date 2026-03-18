---
title: Agent Tools
description: The 17 tools available to the LLM agent during conversation
category: architecture
---

# Agent Tools

[Back to Index](README.md)

The LLM agent has access to 17 tools during conversation, defined in `services/agent/tools.py`.

## Tool List

| Tool | Signature | Purpose |
|------|-----------|---------|
| `inner_thought` | `(thought: str)` | Private reasoning (always first step). Never shown to user. |
| `current_datetime` | `()` | UTC timestamp |
| `send_message` | `(message: str)` | Final response to user, ends turn |
| `continue_reasoning` | `()` | Take another step without sending |
| `core_memory_append` | `(label: str, content: str)` | Append to a named memory block |
| `core_memory_replace` | `(label: str, old_text: str, new_text: str)` | Replace text in a memory block |
| `note_to_self` | `(key: str, value: str, note_type: str)` | Session-scoped scratch note |
| `dismiss_note` | `(key: str)` | Remove a session note |
| `save_to_memory` | `(key: str, category: str, importance: str, tags: str)` | Promote note to permanent memory |
| `set_intention` | `(title: str, evidence: str, priority: str, deadline: str)` | Track ongoing goal |
| `complete_goal` | `(title: str)` | Mark intention as done |
| `create_task` | `(text: str, due_date: str, priority: str)` | Add to user's task list |
| `list_tasks` | `(include_done: str)` | View open tasks |
| `complete_task` | `(text: str)` | Mark task as done (fuzzy match) |
| `recall_memory` | `(query: str, category: str, tags: str)` | Hybrid search (semantic + keyword) |
| `recall_conversation` | `(query: str, role: str, start_date: str, end_date: str, limit: str)` | Search conversation history |
| `update_human_memory` | `(content: str)` | Update holistic understanding of user (human core block) |

## Tool Orchestration Rules

Defined in `services/agent/rules.py` via `ToolRulesSolver`:

- `inner_thought` must be called first (before any other tool)
- `send_message` ends the turn (terminal tool)
- Maximum 6 steps per turn (`agent_max_steps` setting)
- If max steps reached without `send_message`, the runtime forces a final response
