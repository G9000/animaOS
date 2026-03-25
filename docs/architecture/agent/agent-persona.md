---
title: Agent Persona System
description: How the agent's personality is seeded from templates, stored in the database, injected into the system prompt, and evolved through reflection.
category: architecture
updated: 2026-03-24
---

# Agent Persona System

[Back to Index](../README.md)

How the agent's personality is seeded, stored, and used at runtime.

---

## Persona Templates

Jinja2 templates in `services/agent/templates/persona/`. Each receives `{{ agent_name }}` at render time.

| Template | File | Description |
|---|---|---|
| `default` | `default.md.j2` | Blank slate. No preset personality — just a name. Discovers everything through conversation. |
| `companion` | `companion.md.j2` | Warm, emotionally attuned companion. Knows it exists in relationship with the user. |
| `anima` | `anima.md.j2` | The signature Anima personality — quiet, deliberate, reflective. |

---

## Lifecycle

### 1. Registration (`services/auth.py → create_user()`)

When a new user registers, the server seeds three self-model blocks:

```
create_user(username, password, display_name, agent_name="ANIMA", persona_template="default")
```

- **Soul block** (`section="soul"`) — Immutable origin story. Rendered from `origin.md.j2` with `agent_name` and `creator_name`. Never changes after creation.
- **Persona block** (`section="persona"`) — Mutable personality. Rendered from the chosen persona template via `render_persona_seed(persona_template, agent_name=agent_name)`. Evolves through reflection over time.
- **Human block** (`section="human"`) — The agent's understanding of the user. Starts with the user's name and relationship (if provided). Grows through conversation.

At registration, `setup_complete` on `AgentProfile` is `false` and persona defaults to `default` (blank slate). The real persona selection happens in the agent setup step.

### 2. Agent Setup (first visit to Dashboard)

After registration, the Dashboard detects `setup_complete === false` and shows the `AgentSetup` component. The user:

1. **Names the agent** — e.g. "Anima", "Nova", anything
2. **Picks a mode**:
   - **Companion** → `persona: "companion"`, `relationship: "companion"`
   - **Blank Slate** → `persona: "default"`, `relationship: ""`

This calls `PATCH /api/consciousness/{user_id}/agent-profile` which:

- Updates `AgentProfile.agent_name` and `AgentProfile.relationship`
- **Regenerates the soul block** with the new agent name (via `render_origin_block()`)
- **Regenerates the persona block** with the chosen template (via `render_persona_seed(template, agent_name=...)`)
- **Updates the human block** — adds/removes the `Relationship:` line
- Sets `setup_complete = true`

### 3. Runtime (every conversation turn)

The system prompt is built by `build_system_prompt()` in `services/agent/system_prompt.py`:

```
SystemPromptContext → build_system_prompt() → system_prompt.md.j2
```

The persona reaches the system prompt through this chain:

1. `build_runtime_memory_blocks()` in `memory_blocks.py` builds all memory blocks for the user
2. `build_persona_block()` reads the `persona` section from `self_model_blocks` in the DB
3. The persona block is passed into `SystemPromptContext.memory_blocks`
4. `build_system_prompt()` extracts it via `split_prompt_memory_blocks()` — the `persona` label gets pulled out and injected directly into the `Persona:` section of the system prompt template
5. The rendered system prompt includes:
   ```
   Persona:
   Name: Nova
   Nature: A quiet presence that stays with you...
   Voice: ...
   ```

### 4. Evolution

The persona block is **mutable**. It can change through:

- **Reflection/sleep tasks** — The agent can rewrite its persona through self-reflection (`updated_by: "sleep_time"`)
- **Post-turn consolidation** — Subtle persona shifts after conversation turns (`updated_by: "post_turn"`)
- **User manual edit** — Via `PUT /api/consciousness/{user_id}/self-model/persona` (`updated_by: "user_edit"`)
- **Core memory tools** — The agent can use `core_memory_replace` on its persona block during conversation

Each update increments the `version` field on the `SelfModelBlock`.

---

## Template Rendering Chain

```
render_persona_seed(template_name, agent_name)
  → build_persona_prompt(template_name, agent_name)
    → resolve_persona_template_path(template_name)  # validates name, returns Path
    → render_template(path, {"agent_name": agent_name})
      → load_template(path)  # Jinja2, cached via lru_cache
      → template.render(agent_name=agent_name)
```

Template names are validated against `^[a-z0-9][a-z0-9_-]*$` to prevent path traversal.

---

## Database Schema

**`agent_profile`** — fast-lookup structured fields:

| Column | Type | Description |
|---|---|---|
| `agent_name` | String(50) | The agent's chosen name |
| `creator_name` | String(100) | The user's display name |
| `relationship` | String(100) | Relationship type (empty for blank slate) |
| `setup_complete` | Boolean | Whether agent setup has been completed |

**`self_model_blocks`** — the living self-model (one row per user per section):

| Column | Type | Description |
|---|---|---|
| `section` | String(32) | `soul`, `persona`, `human`, `identity`, `inner_state`, `working_memory`, `growth_log`, `intentions` |
| `content` | Text | Encrypted content (the actual persona/soul/etc) |
| `version` | Integer | Increments on each update |
| `updated_by` | String(32) | `system`, `agent_setup`, `sleep_time`, `post_turn`, `user_edit` |

---

## File Map

```
services/auth.py                          # create_user() — seeds soul, persona, human blocks
services/agent/system_prompt.py           # build_system_prompt(), render_persona_seed(), render_origin_block()
services/agent/memory_blocks.py           # build_persona_block() — loads persona from DB for runtime
services/agent/self_model.py              # CRUD for self_model_blocks
services/agent/templates/
  system_prompt.md.j2                     # Main system prompt — includes {{ persona }}
  origin.md.j2                            # Soul/origin block template
  persona/
    default.md.j2                         # Blank slate persona
    companion.md.j2                       # Companion persona
    anima.md.j2                           # Anima persona
api/routes/consciousness.py               # REST API — agent-profile CRUD, self-model CRUD
models/consciousness.py                   # AgentProfile, SelfModelBlock, EmotionalSignal
```
