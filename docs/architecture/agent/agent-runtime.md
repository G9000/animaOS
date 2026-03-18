---
title: Agent Runtime Deep Dive
description: Cognitive loop mechanics, step execution, tool orchestration, compaction, and approval flow
category: architecture
---

# Agent Runtime Deep Dive

[Back to Index](README.md)

This document traces a user message through the agent runtime end-to-end, explaining every layer, data structure, and decision point. It complements [Data Flow](data-flow.md) (high-level call chain) and [Services](services.md) (file-level reference) with the _why_ behind each stage.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Layer 1: HTTP Entry Point](#layer-1-http-entry-point)
3. [Layer 2: Service Orchestrator](#layer-2-service-orchestrator)
4. [Layer 3: AnimaCompanion (Cache Layer)](#layer-3-animacompanion-cache-layer)
5. [Layer 4: AgentRuntime (Cognitive Loop)](#layer-4-agentruntime-cognitive-loop)
6. [Layer 5: LLM Adapter](#layer-5-llm-adapter)
7. [Layer 6: Tool Execution](#layer-6-tool-execution)
8. [Tool Orchestration Rules](#tool-orchestration-rules)
9. [Context Window Management](#context-window-management)
10. [Approval Flow](#approval-flow)
11. [Cancellation](#cancellation)
12. [Post-Turn Background Work](#post-turn-background-work)
13. [Error Handling & Recovery](#error-handling--recovery)
14. [Key Data Structures](#key-data-structures)

---

## Architecture Overview

The runtime follows a layered, stateless-core design:

```
HTTP Request
    |
    v
+--------------------+
|  routes/chat.py    |  FastAPI endpoint (SSE or JSON)
+--------+-----------+
         |
         v
+--------------------+
|  service.py        |  Orchestrator: locks, context assembly,
|                    |  persistence, post-turn hooks
+--------+-----------+
         |
         v
+--------------------+
|  AnimaCompanion    |  Process-resident per-user cache
|  (companion.py)    |  memory blocks, history, emotional state
+--------+-----------+
         | feeds cached state
         v
+--------------------+
|  AgentRuntime      |  STATELESS cognitive loop
|  (runtime.py)      |  prompt build -> step loop -> result
+--------+-----------+
         |
    +----+----+
    |         |
    v         v
+--------+ +--------------+
| LLM    | | ToolExecutor |
| Adapter| | (executor.py)|
+--------+ +--------------+
```

Key principle: **the runtime is stateless**. It receives history, memory blocks, and tool definitions as arguments and returns an `AgentResult`. All state management (caching, persistence, compaction) lives in the layers above it.

---

## Layer 1: HTTP Entry Point

**File**: `api/routes/chat.py:52-100`

```python
@router.post("", response_model=ChatResponse)
async def send_message(payload: ChatRequest, request: Request, db: Session):
```

Two modes:
- **`stream=false`**: Calls `run_agent()`, blocks until complete, returns `ChatResponse` with `response`, `model`, `provider`, `toolsUsed`.
- **`stream=true`**: Calls `ensure_agent_ready()` (validates LLM config), then opens an SSE stream via `stream_agent()`. Each event is formatted as `event: <type>\ndata: <json>\n\n`.

Error handling at this layer catches `LLMConfigError`, `LLMInvocationError`, and `PromptTemplateError`, returning HTTP 503.

### Other Chat Endpoints

| Endpoint | Purpose |
|----------|---------|
| `GET /api/chat/history` | Load past messages (decrypted) |
| `DELETE /api/chat/history` | Clear thread |
| `POST /api/chat/reset` | Reset thread |
| `POST /api/chat/dry-run` | Assemble full prompt without calling LLM |
| `POST /api/chat/runs/{id}/cancel` | Cancel a running turn |
| `POST /api/chat/runs/{id}/approval` | Approve/deny a pending tool call |
| `GET /api/chat/brief` | Static context brief (no LLM) |
| `GET /api/chat/greeting` | LLM-generated personalized greeting |
| `POST /api/chat/consolidate` | Trigger memory consolidation |
| `POST /api/chat/sleep` | Trigger sleep-time maintenance |
| `POST /api/chat/reflect` | Trigger deep inner monologue |

---

## Layer 2: Service Orchestrator

**File**: `services/agent/service.py`

This is the largest file in the runtime (~1100 lines). It manages the full turn lifecycle in four stages.

### Turn Entry: `run_agent()` / `stream_agent()`

Both delegate to `_execute_agent_turn()`, which acquires a **per-user async lock** (via `turn_coordinator.py`) to serialize concurrent requests for the same user. This prevents race conditions in conversation history and memory state.

```python
async def _execute_agent_turn(user_message, user_id, db, *, event_callback=None):
    user_lock = get_user_lock(user_id)
    async with user_lock:
        return await _execute_agent_turn_locked(...)
```

### Stage 1: Prepare Turn Context (`_prepare_turn_context`)

This is the most complex preparation stage. It assembles everything the runtime needs:

1. **Get AnimaCompanion** -- `_get_companion(user_id)` returns the cached companion or creates one
2. **Get/create thread** -- `persistence.get_or_create_thread(db, user_id)` ensures a DB thread exists
3. **Load history** -- `companion.ensure_history_loaded(db)` returns cached conversation window or loads from DB
4. **Create run record** -- `persistence.create_run()` writes an `AgentRun` row (status, provider, model, mode)
5. **Reserve sequences** -- `sequencing.reserve_message_sequences()` allocates monotonic message IDs
6. **Persist user message** -- `persistence.append_user_message()` writes to `agent_messages`
7. **Semantic retrieval** -- `embeddings.hybrid_search()`:
   - Embeds the user message
   - Cosine similarity search against stored `MemoryItem.embedding_json`
   - Similarity threshold 0.25, limit 15 results
   - `adaptive_filter()` further ranks/filters results
8. **Build memory blocks** -- `memory_blocks.build_runtime_memory_blocks()` assembles 15+ block types:
   - Soul biography, persona, human core, user directive
   - Self-model (5 sections: identity, values, inner_state, working_memory, growth_log)
   - Emotional context
   - Semantic results (query-dependent)
   - Facts, preferences, goals, tasks, relationships
   - Current focus, thread summary, episodes, session notes
9. **Feedback signals** -- `feedback_signals.collect_feedback_signals()` detects re-asks and corrections
10. **Memory pressure warning** -- injects a warning block if estimated tokens exceed 80% of context window
11. **Update companion window** -- appends user message to conversation cache

Returns a `_TurnContext(history, conversation_turn_count, memory_blocks)`.

### Stage 1b: Proactive Compaction (`_proactive_compact_if_needed`)

Before calling the LLM, estimates total context tokens:

```python
estimated_tokens = (block_chars + history_chars) // 4
```

If over `agent_max_tokens * agent_compaction_trigger_ratio`, runs `compact_thread_context()` to summarize older messages _before_ the first LLM call. This prevents oversized prompts from being rejected by the provider.

### Stage 2: Invoke Runtime (`_invoke_turn_runtime`)

1. Sets `ToolContext` (contextvar) so tools can access `db`, `user_id`, `thread_id`
2. Creates a **memory refresher callback** for inter-step memory updates
3. Calls `runner.invoke()` on the `AgentRuntime`
4. On context overflow: runs `_emergency_compact()` (aggressive settings: keep fewer messages, no reserved tokens) and retries once
5. On failure: marks run failed, removes orphaned user message from active context
6. Always clears `ToolContext` in `finally` block

### Stage 3: Persist Result (`_persist_turn_result`)

1. Counts result messages, reserves sequence IDs
2. `persist_agent_result()` writes assistant + tool messages to `agent_messages`
3. Commits to DB
4. Tries LLM-powered compaction (richer summaries) -- best-effort
5. Falls back to fast text-based compaction
6. Commits again

### Stage 4: Post-Turn Hooks (`_run_post_turn_hooks`)

Schedules two fire-and-forget background tasks:
- **Memory consolidation** -- extracts facts, preferences, emotions from the conversation
- **Reflection** -- delayed self-model update and inner monologue

---

## Layer 3: AnimaCompanion (Cache Layer)

**File**: `services/agent/companion.py`

The `AnimaCompanion` is a process-resident, per-user singleton that caches state between turns. The runtime is stateless; the companion feeds it cached state.

### What it caches

| Cache | Invalidation | Reload trigger |
|-------|-------------|----------------|
| Static memory blocks | Version counter (`_memory_version` vs `_cache_version`) | `ensure_memory_loaded()` when stale |
| Conversation window | Cleared on compaction or reset | `ensure_history_loaded()` when empty |
| System prompt | Cleared when memory invalidated | Rebuilt by runtime per-turn |
| Emotional state | Set by consolidation callback | N/A |

### Version-counter cache pattern

```python
def invalidate_memory(self):
    self._memory_version += 1  # bump version
    # does NOT clear _memory_cache -- in-flight turn continues
    # next cache read sees version mismatch and reloads

@property
def memory_stale(self) -> bool:
    return self._cache_version < self._memory_version
```

This design allows an in-flight turn to complete with its starting data while ensuring the _next_ turn picks up changes.

### Conversation window trimming

The window is bounded by `agent_compaction_keep_last_messages`. When it overflows, `_trim_at_turn_boundary()` finds a safe cut point (user or assistant message, not tool) to avoid orphaning tool results from their paired assistant message.

### Lifecycle

- **`warm(db)`** -- pre-populates caches at startup or first request
- **`reset()`** -- clears everything on thread reset
- **`invalidate_memory()`** -- bumps version counter (tools call this when they modify memory)

### Singleton management

```python
_companions: dict[int, AnimaCompanion] = {}  # keyed by user_id

def get_or_build_companion(runtime, user_id) -> AnimaCompanion:
    # thread-safe with _companion_lock
```

---

## Layer 4: AgentRuntime (Cognitive Loop)

**File**: `services/agent/runtime.py`

This is the heart of the system -- a stateless step loop that runs the agent's cognitive cycle.

### Construction: `build_loop_runtime()`

```python
def build_loop_runtime() -> AgentRuntime:
    tools = get_tools()
    return AgentRuntime(
        adapter=build_adapter(),          # OpenAI-compatible LLM client
        tools=tools,                       # 17 tool definitions
        tool_rules=get_tool_rules(tools),  # orchestration rules
        tool_summaries=get_tool_summaries(tools),
        tool_executor=ToolExecutor(tools),
        max_steps=settings.agent_max_steps,  # default: 6
    )
```

The runtime is cached as a module-level singleton (`_cached_runner`) and rebuilt only when `invalidate_agent_runtime_cache()` is called.

### `invoke()` -- Main Entry Point

```python
async def invoke(self, user_message, user_id, history, *,
                 memory_blocks, event_callback, cancel_event,
                 memory_refresher) -> AgentResult:
```

**Phase 1: Prompt Assembly**
1. `build_system_prompt_with_budget(memory_blocks)`:
   - Calls `split_prompt_memory_blocks()` to extract dynamic identity and persona
   - `plan_prompt_budget()` allocates token budget across blocks
   - Renders Jinja2 templates: `system_prompt.md.j2`, `system_rules.md.j2`, `guardrails.md.j2`
   - Injects: persona, dynamic identity (from self-model), tool summaries, serialized memory blocks
2. `build_conversation_messages(history, user_message, system_prompt)`:
   - Builds the message array: `[system, ...history, user_message]`

**Phase 2: Step Loop**

```
for step_index in range(max_steps):    # default: 6
    1. Check cancellation event
    2. Snapshot messages for tracing
    3. Compute allowed tools via ToolRulesSolver
    4. _run_step() -> call LLM
    5. Process response:
       a. No tool calls? -> try coercion, or end turn
       b. Has tool calls? -> validate, execute, check terminal
    6. Refresh memory if tools modified it
    7. Continue or break
```

**Phase 3: Result Construction**

Returns `AgentResult` with:
- `response` -- final text to the user
- `model`, `provider` -- which LLM was used
- `stop_reason` -- why the loop stopped (`end_turn`, `terminal_tool`, `max_steps`, `awaiting_approval`, `cancelled`)
- `tools_used` -- list of tool names invoked
- `step_traces` -- detailed per-step diagnostics
- `prompt_budget` -- token allocation trace

### `_run_step()` -- Single LLM Invocation

Each step:
1. Creates a `StepContext` for timing/progression tracking
2. Builds an `LLMRequest` with messages, tools, force_tool_call flag
3. Calls `_invoke_llm_with_retry()`:
   - **Non-streaming**: `adapter.invoke(request)` wrapped in `asyncio.wait_for(timeout)`
   - **Streaming**: iterates `adapter.stream(request)`, emits chunk events, checks cancel between chunks
4. Appends assistant message to the message list
5. Returns `(StepExecutionResult, streamed_flag, StepContext)`

### Stop Reasons

| StopReason | Trigger | What happens |
|------------|---------|-------------|
| `END_TURN` | LLM returns text without tool calls | Response = assistant text |
| `TERMINAL_TOOL` | `send_message` tool executed | Response = tool output |
| `MAX_STEPS` | Loop exhausted `max_steps` | Default message returned |
| `AWAITING_APPROVAL` | Tool requires user approval | Checkpoint persisted, SSE event emitted |
| `CANCELLED` | Cancel event set | Empty response, run marked cancelled |

### Text Tool Call Coercion

Smaller models sometimes emit tool calls as plain text instead of structured calls. The runtime detects and executes these patterns:

```
inner_thought("thinking about...")    -> parsed and executed
send_message("hello user")           -> parsed and executed
tool_name {"key": "value"}           -> parsed and executed
tool_name({"key": "value"})          -> parsed and executed
```

If no recognized pattern is found but `send_message` is available, the entire text is coerced into a `send_message` call. This keeps the cognitive loop intact regardless of model capability.

### Memory Refresh Between Steps

When a tool sets `memory_modified=True` (detected via `ToolContext` contextvar), the runtime calls the `memory_refresher` callback between steps:

```python
if any(tr.memory_modified for tr in tool_results):
    fresh_blocks = await memory_refresher()
    if fresh_blocks is not None:
        system_prompt = rebuild_system_prompt(fresh_blocks)
        messages[0] = make_system_message(system_prompt)  # replace system message
```

This ensures that if the agent saves a memory in step 2, its system prompt in step 3 already reflects the change.

---

## Layer 5: LLM Adapter

**File**: `services/agent/adapters/`

```
adapters/
  base.py               # BaseLLMAdapter ABC
  openai_compatible.py   # Main adapter (Ollama, OpenRouter, vLLM)
  scaffold.py            # Test/mock adapter
  __init__.py            # build_adapter() factory
```

### `BaseLLMAdapter` Interface

```python
class BaseLLMAdapter(ABC):
    provider: str
    model: str

    def prepare(self) -> None: ...           # validate config
    async def invoke(self, request: LLMRequest) -> StepExecutionResult: ...
    async def stream(self, request: LLMRequest) -> AsyncGenerator[StepStreamEvent]: ...
```

The adapter normalizes provider-specific responses into `StepExecutionResult`:
- `assistant_text` -- the model's text output
- `tool_calls` -- structured tool call requests (parsed into `ToolCall` dataclass)
- `usage` -- token usage statistics
- `reasoning_content` / `reasoning_signature` -- extended thinking (if supported)

### Retry Logic

`_invoke_llm_with_retry()` implements exponential backoff for transient errors:

| Error type | Retryable? |
|-----------|-----------|
| Timeout (`asyncio.TimeoutError`) | Yes |
| Rate limit (429) | Yes |
| Server errors (500, 502, 503, 504) | Yes |
| Context overflow (`ContextWindowOverflowError`) | No |
| Config errors | No |
| Already streamed content | No (would cause duplicate output) |

Retry limit and backoff are configurable via `agent_llm_retry_limit`, `agent_llm_retry_backoff_factor`, `agent_llm_retry_max_delay`.

---

## Layer 6: Tool Execution

**File**: `services/agent/executor.py`

### `ToolExecutor`

Maintains a name-to-tool registry. For each tool call:

1. **Lookup** -- find tool by name, return error if unknown
2. **Parse check** -- if `tool_call.parse_error` is set (malformed JSON from LLM), return error without executing
3. **Invoke** with timeout (`agent_tool_timeout`):
   - `ainvoke(payload)` if async
   - `invoke(payload)` if sync (LangChain-style)
   - `tool(**arguments)` via `run_in_executor` for plain functions (runs in thread pool with `contextvars.copy_context()`)
4. **Memory flag check** -- reads `ToolContext.memory_modified` after execution
5. **Return** `ToolExecutionResult` with output, error flag, terminal flag, memory_modified flag

### ToolContext (contextvar)

```python
@dataclass
class ToolContext:
    db: Session
    user_id: int
    thread_id: int
    memory_modified: bool = False  # set by tools that change memory
```

Set before the runtime loop, cleared in `finally`. Tools access it via `get_tool_context()`. When a tool modifies memory, it sets `ctx.memory_modified = True`, which triggers the memory refresh callback between steps.

### Parallel Execution

`execute_parallel()` runs multiple independent tool calls concurrently via `asyncio.gather()`. Currently not used in the main loop (tools execute sequentially per step) but available for future use.

---

## Tool Orchestration Rules

**File**: `services/agent/rules.py`

The `ToolRulesSolver` enforces Letta-style orchestration rules that control tool availability and sequencing within a turn.

### Rule Types

| Rule | Effect |
|------|--------|
| `InitToolRule(tool_name)` | Tool must be called first in the turn. Only init tools are available at step 0. |
| `TerminalToolRule(tool_name)` | Tool ends the turn when executed (e.g., `send_message`). |
| `ChildToolRule(tool_name, children)` | After calling this tool, only the listed children are available next. |
| `PrerequisiteToolRule(prerequisite, dependent)` | Dependent tool is unavailable until prerequisite has been called. |
| `RequiresApprovalToolRule(tool_name)` | Tool call pauses the turn and waits for user approval. |
| `ConditionalToolRule(tool_name, output_mapping)` | Routes to different child tools based on the tool's output value. |

### Default Rules (from `build_default_tool_rules`)

The default configuration enforces a cognitive pattern:
1. **Start with `inner_thought`** (InitToolRule) -- forces the agent to reason before acting
2. **End with `send_message`** (TerminalToolRule) -- ensures a user-facing response
3. **Child rules** guide the flow: `inner_thought` -> any tool -> `send_message`

### Force Tool Call

When `force_tool_call=True`, the LLM is instructed to always return a tool call (never plain text). This is active when `send_message` is in the allowed tools, ensuring the agent uses the structured `send_message` tool rather than emitting raw text.

---

## Context Window Management

The system uses a three-tier compaction strategy to prevent context overflow:

### Tier 1: Proactive Compaction (Pre-Turn)

**When**: Before the first LLM call, if estimated context exceeds `agent_max_tokens * agent_compaction_trigger_ratio`.

**How**: `compact_thread_context()` summarizes older messages into a thread summary, marks them `is_in_context=False`, and keeps the `agent_compaction_keep_last_messages` most recent messages.

### Tier 2: Emergency Compaction (Mid-Turn)

**When**: The LLM returns a `ContextWindowOverflowError`.

**How**: `_emergency_compact()` uses aggressive settings (keep half the normal messages, no reserved tokens) and retries the LLM call once.

### Tier 3: Post-Turn Compaction

**When**: After every successful turn.

**How**: First tries `compact_thread_context_with_llm()` (LLM-powered summarization for richer summaries), falls back to `compact_thread_context()` (fast text-based).

### Memory Pressure Warning

At 80% of context capacity, a `memory_pressure_warning` block is injected into the system prompt:

> "Your conversation context is getting full. Consider using save_to_memory to persist important facts..."

This fires only once per pressure window (resets after compaction).

### Token Estimation

All estimates use the heuristic `len(text) // 4` (approximately 4 characters per token). The `PromptBudgetTrace` tracks exact allocations:
- System prompt tokens
- Dynamic identity tokens
- Per-block token counts
- Conversation history tokens

---

## Approval Flow

Some tools can require user approval before execution (configured via `RequiresApprovalToolRule`).

### Flow

```
1. Agent requests tool call
2. ToolRulesSolver.requires_approval(tool_name) -> True
3. Runtime injects error: "Approval required before running tool: {name}"
4. Runtime stops with StopReason.AWAITING_APPROVAL
5. service.py persists an approval checkpoint:
   - The step traces so far
   - The pending ToolCall (name, id, arguments)
   - Run status set to "awaiting_approval"
6. SSE event: approval_pending {runId, toolName, toolCallId, toolArguments}
7. Client shows approval UI
```

### Resume

```
POST /api/chat/runs/{id}/approval  {approved: true/false, reason?: string}

If approved:
  -> ToolExecutor.execute(pending_tool_call)
  -> If terminal: return result immediately
  -> If non-terminal: one follow-up LLM call for response

If denied:
  -> Inject tool error: "Tool {name} was denied by user. Reason: {reason}"
  -> One follow-up LLM call so agent can acknowledge
```

Approval checkpoints are persisted as a `role='approval'` message in `agent_messages`, allowing recovery across server restarts.

---

## Cancellation

### How it works

1. `POST /api/chat/runs/{id}/cancel` calls `cancel_agent_run()`
2. Sets `asyncio.Event` on the companion: `companion.set_cancel(run_id)`
3. The runtime checks `cancel_event.is_set()` at:
   - **Step boundaries** -- between loop iterations
   - **During streaming** -- between chunks from the LLM
4. When detected, the loop breaks with `StopReason.CANCELLED`
5. The run is marked cancelled in the DB

### Mid-stream cancellation

If the cancel event fires while streaming LLM output, `_CancelledDuringStream` is raised internally, which breaks out of the stream loop cleanly.

---

## Post-Turn Background Work

**File**: `services/agent/service.py:990-1019`

After every successful turn, two background tasks are scheduled (fire-and-forget, using separate DB sessions):

### Memory Consolidation (`consolidation.py`)

Runs immediately in a background task:
1. **Regex extraction** -- pattern-based extraction of facts, preferences, focus
2. **LLM extraction** -- sends conversation to LLM with `EXTRACTION_PROMPT` for deeper semantic understanding
3. **Emotional signal extraction** -- detects emotions from the conversation
4. **Claim upsert** -- structured claims with conflict resolution
5. **Embedding generation** -- creates vector embeddings for new memory items
6. **Episode creation** -- stores episodic memory
7. **Daily log** -- records the turn in `memory_daily_logs`

Inner thoughts from the agent's `inner_thought` tool calls are included in the consolidation input, so the extraction pipeline can learn from the agent's own reasoning.

### Reflection (`reflection.py`)

Scheduled with a delay (typically 5 minutes):
1. **Quick inner monologue** -- brief self-reflection
2. **Deep monologue** -- full self-model reflection updating identity, inner state, working memory
3. **Self-model updates** -- persisted to `self_model_blocks` table

---

## Error Handling & Recovery

### StepFailedError

The runtime wraps all step-level failures in `StepFailedError`, which carries:
- `cause` -- the original exception
- `progression` -- how far the step got (`START`, `LLM_REQUESTED`, `RESPONSE_RECEIVED`, `TOOLS_STARTED`, `TOOLS_COMPLETED`)
- `context` -- the `StepContext` with timing data

The service layer uses `progression` for intelligent cleanup:
- Early failures (before LLM responded): just remove orphaned user message
- Late failures (after tools ran): mark run failed with detail

### Orphaned user messages

If a turn fails after persisting the user message but before completing, the message is marked `is_in_context=False` so it doesn't replay as valid history on the next turn.

### Tool-level errors

Tool failures are caught by the executor and returned as `ToolExecutionResult(is_error=True)`, which is fed back into the conversation as a tool error message. The agent can then acknowledge the error or try a different approach. The loop continues.

---

## Key Data Structures

### AgentResult (`state.py`)

```python
@dataclass
class AgentResult:
    response: str                          # final text for the user
    model: str                             # LLM model used
    provider: str                          # LLM provider
    stop_reason: str                       # StopReason value
    tools_used: list[str]                  # tool names invoked
    step_traces: list[StepTrace]           # per-step diagnostics
    prompt_budget: PromptBudgetTrace | None
```

### StepTrace (`runtime_types.py`)

```python
@dataclass
class StepTrace:
    step_index: int
    request_messages: tuple[MessageSnapshot, ...]  # messages sent to LLM
    allowed_tools: tuple[str, ...]
    force_tool_call: bool
    assistant_text: str
    tool_calls: tuple[ToolCall, ...]
    tool_results: tuple[ToolExecutionResult, ...]
    usage: UsageStats | None
    timing: StepTiming | None
    reasoning_content: str | None          # extended thinking
    reasoning_signature: str | None
```

### StopReason (`runtime_types.py`)

```python
class StopReason(StrEnum):
    END_TURN = "end_turn"                  # LLM returned text, no tools
    TERMINAL_TOOL = "terminal_tool"        # send_message executed
    MAX_STEPS = "max_steps"                # loop limit hit
    AWAITING_APPROVAL = "awaiting_approval"# tool needs user approval
    CANCELLED = "cancelled"                # user cancelled
```

### StepProgression (`runtime_types.py`)

Tracks how far a step progressed before failure:

```python
class StepProgression(IntEnum):
    START = 0
    LLM_REQUESTED = 1
    RESPONSE_RECEIVED = 2
    TOOLS_STARTED = 3
    TOOLS_COMPLETED = 4
    PERSISTED = 5
    FINISHED = 6
```

### ToolCall / ToolExecutionResult

```python
@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    parse_error: str | None      # set if LLM sent malformed JSON
    raw_arguments: str | None    # original unparsed string

@dataclass
class ToolExecutionResult:
    call_id: str
    name: str
    output: str
    is_error: bool = False
    is_terminal: bool = False    # true for send_message
    memory_modified: bool = False
```

---

## SSE Event Types

When streaming, the runtime emits these event types:

| Event | Payload | When |
|-------|---------|------|
| `chunk` | `{content: string}` | LLM text token received |
| `reasoning` | `{content, signature}` | Extended thinking content |
| `tool_call` | `{step, name, id, arguments}` | LLM requests a tool call |
| `tool_return` | `{step, name, id, output, isError}` | Tool execution completed |
| `timing` | `{step, stepDurationMs, llmDurationMs, ttftMs}` | Step timing data |
| `usage` | `{promptTokens, completionTokens, totalTokens}` | Token usage summary |
| `approval_pending` | `{runId, toolName, toolCallId, toolArguments}` | Tool awaiting user approval |
| `cancelled` | `{runId}` | Turn was cancelled |
| `done` | `{response, model, provider, toolsUsed, stopReason}` | Turn complete |
| `error` | `{error: string}` | Unrecoverable error |

---

## File Reference

| File | Role |
|------|------|
| `service.py` | Turn orchestrator (stages 1-4) |
| `companion.py` | Per-user state cache |
| `runtime.py` | Stateless cognitive loop |
| `executor.py` | Tool call execution |
| `tools.py` | Tool definitions and registry |
| `rules.py` | Tool orchestration rules |
| `system_prompt.py` | Jinja2 prompt assembly |
| `prompt_budget.py` | Token budget planning |
| `messages.py` | Message construction |
| `persistence.py` | DB read/write for threads, runs, messages |
| `sequencing.py` | Monotonic message ordering |
| `streaming.py` | SSE event construction |
| `runtime_types.py` | Step-level data types |
| `state.py` | `AgentResult`, `StoredMessage` |
| `tool_context.py` | ContextVar for tool DB access |
| `turn_coordinator.py` | Per-user async locks |
| `compaction.py` | Context window compaction |
| `memory_blocks.py` | Memory block assembly (15+ types) |
| `adapters/base.py` | LLM adapter ABC |
| `adapters/openai_compatible.py` | Main adapter implementation |
| `consolidation.py` | Post-turn memory extraction |
| `reflection.py` | Post-turn self-model reflection |
| `embeddings.py` | Embedding generation + hybrid search |
