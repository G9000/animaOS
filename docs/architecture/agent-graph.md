# AI Graph Architecture

Note: this document describes the legacy `apps/api` LangGraph path. `apps/server` now uses a loop-based runtime, and the remaining migration work is documented in [agent-runtime-migration.md](./agent-runtime-migration.md).

This document explains how ANIMA processes a chat request in `apps/api/src/agent/graph.ts`.

## Flow Diagram
```mermaid
sequenceDiagram
    participant UI as Desktop/UI
    participant Chat as /api/chat route
    participant Graph as graph.ts
    participant DB as SQLite (Drizzle)
    participant Model as LLM Provider
    participant Tools as Tool Layer

    UI->>Chat: POST /api/chat { message, userId, stream }
    Chat->>Graph: runAgent() or streamAgent()

    Graph->>DB: Load agent_config + last 20 messages
    Note over Graph: systemPrompt priority:\nDB systemPrompt > soul.md > DEFAULT_SYSTEM_PROMPT
    Graph->>DB: Save user message

    Graph->>Model: createReactAgent(...) invoke/stream
    Model->>Tools: Optional tool call(s)
    Tools-->>Model: Tool results
    Model-->>Graph: Final AI response/chunks

    alt Graph/tool execution error
      Graph->>Model: Direct fallback invoke/stream (no tools)
      Model-->>Graph: Fallback response/chunks
    end

    Graph->>DB: Save assistant message (+ model/provider)
    alt stream=true
      Graph-->>Chat: Yield AI chunks
      Chat-->>UI: SSE events (chunk/done/error)
    else stream=false
      Graph-->>Chat: AgentResult JSON
      Chat-->>UI: JSON response
    end
```

## Entry Points
- HTTP route: `POST /api/chat` in `apps/api/src/routes/chat/index.ts`
- Non-streaming mode calls `runAgent(message, userId)`.
- Streaming mode calls `streamAgent(message, userId)` and returns SSE chunks.

## Runtime Configuration
For each request, the graph loads user config from `agent_config`:
- `provider` (`ollama`, `openai`, `anthropic`, `openrouter`)
- `model`
- optional `apiKey`, `ollamaUrl`, `systemPrompt`

If user config does not exist, defaults are:
- provider: `ollama`
- model: `qwen3:14b`
- ollama URL: `http://localhost:11434`

## Persona / System Prompt Resolution
Prompt priority is:
1. Per-user `systemPrompt` from DB (Settings page)
2. Per-user encrypted soul file (`users/<userId>/soul.md`)
3. Built-in `DEFAULT_SYSTEM_PROMPT`

## Graph Construction
`buildAgent(config, userId, systemPrompt?)` builds a LangGraph ReAct agent:
- LLM from `createModel(config)` in `apps/api/src/agent/models.ts`
- Toolset from `createTools(userId)` in `apps/api/src/agent/tools.langchain.ts`
- System message via `messageModifier`

`createReactAgent(...)` drives the reason-act loop internally:
- model decides when to call tools
- tool outputs are fed back into the loop
- final assistant message is produced

## Message Lifecycle
1. Load last 20 messages from `messages` table.
2. Save incoming user message immediately.
3. Invoke graph with history + new user message.
4. Extract final AI message.
5. Save assistant response with `model` + `provider` metadata.

## Streaming Path
`streamAgent` uses `agent.stream(..., { streamMode: "messages" })` and only emits:
- AI text chunks
- from `langgraph_node === "agent"`

Tool-call chatter is not sent to the client.

## Failure Behavior
If graph execution fails:
- log the error
- fall back to direct model invocation with the same system prompt + history
- continue saving assistant output to DB

This keeps chat available even if tool orchestration fails.
