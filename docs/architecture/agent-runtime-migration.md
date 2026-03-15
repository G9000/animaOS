# ANIMA Server Agent Runtime Migration Plan

Status: archived migration plan (the server loop runtime is live; remaining hardening work now lives in `docs/agent-runtime-improvements.md` and `docs/roadmap.md`)

This document defines how `apps/server` is migrating from the former LangGraph scaffold to a Letta-inspired orchestration loop while keeping ANIMA-owned prompts, memory, persistence, and API contracts.

The goal is not to adopt Letta as infrastructure. The goal is to copy the orchestration ideas that fit ANIMA:

- explicit async agent loop
- tool-driven control flow
- step/run persistence
- prompt rebuilt every step from current state
- context compaction before overflow
- background memory work after the main response

## Current Implementation Status

Implemented now:

- LangGraph removed from `apps/server`
- new loop-based runtime in `apps/server/src/anima_server/services/agent/runtime.py`
- adapter boundary under `apps/server/src/anima_server/services/agent/adapters/`
- tool execution boundary in `apps/server/src/anima_server/services/agent/executor.py`
- Letta-style tool rules in `apps/server/src/anima_server/services/agent/rules.py`
- public service facade in `apps/server/src/anima_server/services/agent/service.py`
- database-backed threads, messages, runs, and steps via `services/agent/persistence.py`
- assistant tool calls round-trip through persistence so future steps can rebuild valid tool-call history
- post-run context compaction in `apps/server/src/anima_server/services/agent/compaction.py`
- persisted `summary` messages now collapse older in-context history while keeping recent turns live
- scaffold turn counting survives compaction because turn count now comes from persisted thread state
- runtime memory blocks in `apps/server/src/anima_server/services/agent/memory_blocks.py`
- prompt construction now includes `soul`, self-model sections, emotional context, semantic retrieval, facts, preferences, goals, relationships, tasks, `current_focus`, `thread_summary`, recent episodes, and session memory
- persisted `summary` rows are no longer replayed as ordinary conversation history; they are injected through the prompt memory layer instead
- real OpenAI-compatible provider client for `ollama`, `openrouter`, and `vllm` in `apps/server/src/anima_server/services/agent/openai_compatible_client.py`
- structured stream events in `apps/server/src/anima_server/services/agent/streaming.py`
- SSE path now emits a unified event model (`tool_call`, `tool_return`, `chunk`, `usage`, `done`, `error`) from the shared loop runtime, with live provider chunk streaming when the adapter supports it
- in-process post-response memory consolidation in `apps/server/src/anima_server/services/agent/consolidation.py`
- background memory writes now create daily logs every turn and promote durable user facts, preferences, goals, relationships, and explicit `current_focus` statements into database-backed memory tables
- prompt-budget enforcement now happens before final prompt assembly in `apps/server/src/anima_server/services/agent/prompt_budget.py`
- parity coverage for scaffold chat, streaming, reset, provider errors, persona template failures, and persisted runtime rows

Still pending:

- richer live step emission beyond chunk streaming, especially incremental tool-call deltas and wider provider-specific coverage
- stronger memory extraction, duplicate-merging, and retrieval hardening beyond the current pipeline
- making encrypted-Core expectations explicit and continuing to reduce the remaining legacy file-backed surface

## Why This Migration Exists

The current server agent runtime is intentionally thin:

- [`apps/server/src/anima_server/services/agent/service.py`](../apps/server/src/anima_server/services/agent/service.py)
- [`apps/server/src/anima_server/services/agent/runtime.py`](../apps/server/src/anima_server/services/agent/runtime.py)
- [`apps/server/src/anima_server/services/agent/persistence.py`](../apps/server/src/anima_server/services/agent/persistence.py)

It is sufficient for scaffolding, but it does not yet support the architecture ANIMA actually wants:

- durable identity and persona
- persistent conversation state
- explicit step-level execution
- durable memory and context
- long-lived agent workflows
- background memory consolidation

That target is consistent with the ANIMA thesis in [`docs/whitepaper.md`](./whitepaper.md), especially:

- local-first memory
- continuity over time
- agency across workflows
- identity preserved across surfaces

## Reference Material

These local Letta docs are the primary design references for this migration:

- [Letta Orchestration Blueprint](../.local-docs/docs/letta/ORCHESTRATION_BLUEPRINT.md)
- [Letta Agent Orchestration](../.local-docs/docs/letta/AGENT_ORCHESTRATION.md)
- [Letta Memory System](../.local-docs/docs/letta/MEMORY_SYSTEM.md)
- [Letta Architecture](../.local-docs/docs/letta/ARCHITECTURE.md)

This migration intentionally does not copy Letta wholesale. It reuses their ideas while preserving ANIMA-specific ownership over:

- prompts and persona
- vault and encryption model
- memory representation
- provider selection
- API behavior
- database schema decisions

## Scope

In scope:

- replace LangGraph orchestration in `apps/server`
- keep `/api/chat` stable
- add explicit runtime loop
- add persistence for threads, runs, steps, and messages
- add tool rules
- rebuild prompt from core blocks every step
- add context compaction
- unify blocking and streaming under one runtime
- add background memory consolidation

Out of scope for the first implementation pass:

- true multi-agent orchestration
- supervisor/router/sleeptime groups
- fully generic tool marketplace
- a giant Letta-style service manager layer
- adopting Letta's exact ORM or API schema

## Architecture Decisions

These decisions should be treated as locked unless a later migration document changes them.

### 1. No graph framework in the runtime

The new runtime should be plain async Python with an explicit loop:

```python
for step_index in range(max_steps):
    ...
```

We are intentionally choosing debuggability and explicit control over graph abstraction.

### 2. ANIMA owns memory

ANIMA will not adopt Letta's block store or agent platform whole-cloth.

ANIMA memory remains ANIMA-owned. The runtime can render memory blocks into prompts, but the canonical sources stay in ANIMA-controlled stores:

- persona templates
- user profile
- current focus
- structured memory tables
- database-backed soul content with legacy file migration on first read
- persisted summaries

### 3. `/api/chat` remains stable

The public contract in [`apps/server/src/anima_server/api/routes/chat.py`](../apps/server/src/anima_server/api/routes/chat.py) should not change during the runtime migration unless there is a specific API migration plan.

### 4. Provider support remains narrow

The runtime should continue targeting:

- `ollama`
- `openrouter`
- `vllm`
- `scaffold` for explicit local scaffolding only

There should be no silent fallback from a real provider to scaffold.

### 5. Persona and system rules stay first-class

The current prompt system in [`apps/server/src/anima_server/services/agent/system_prompt.py`](../apps/server/src/anima_server/services/agent/system_prompt.py) and the templates under [`apps/server/src/anima_server/services/agent/templates`](../apps/server/src/anima_server/services/agent/templates) remain the basis of prompt construction.

The new runtime should consume this system directly rather than replacing it.

### 6. Single loop runtime

`apps/server` should have one orchestration implementation only: the explicit loop runtime.

LangGraph has already been removed from the server path. Future migration work should extend the loop runtime rather than reintroducing parallel orchestration implementations.

## Target Runtime Model

The target execution model is:

1. Load or create the user's thread.
2. Create a run record.
3. Rebuild the system prompt from current persona, rules, guardrails, memory blocks, and summaries.
4. Load in-context messages.
5. Execute a loop:
   - determine currently allowed tools
   - build LLM request
   - call provider
   - parse content and tool calls
   - validate tool rules
   - execute tools
   - persist step artifacts and messages
   - compact context if needed
6. Finalize the run and persist assistant output.
7. Optionally trigger background memory extraction or consolidation.

This is the core Letta-style pattern we want, without adopting Letta's entire server.

## Proposed Module Layout

The new server runtime should be organized around small, explicit modules.

### Public service layer

- `apps/server/src/anima_server/services/agent/service.py`
  - public entrypoints
  - `run_agent(...)`
  - `stream_agent(...)`
  - `reset_agent_thread(...)`
  - `ensure_agent_ready(...)`

### Runtime core

- `apps/server/src/anima_server/services/agent/runtime.py`
  - `AgentRuntime`
  - outer turn loop
  - stop conditions
  - compaction trigger points

- `apps/server/src/anima_server/services/agent/runtime_types.py`
  - `StopReason`
  - `ToolCall`
  - `ToolExecutionResult`
  - `UsageStats`
  - `StepExecutionResult`
  - normalized role/message payloads

### LLM adapters

- `apps/server/src/anima_server/services/agent/adapters/base.py`
  - common interface

- `apps/server/src/anima_server/services/agent/adapters/openai_compatible.py`
  - request/response normalization for `ollama`, `openrouter`, `vllm`

- `apps/server/src/anima_server/services/agent/adapters/scaffold.py`
  - explicit local scaffold adapter used for deterministic fallback and tests

- `apps/server/src/anima_server/services/agent/streaming.py`
  - shared stream event builders layered on top of the loop runtime

### Tooling

- `apps/server/src/anima_server/services/agent/tools.py`
  - tool definitions
  - tool metadata
  - tool schema export for model calls

- `apps/server/src/anima_server/services/agent/executor.py`
  - execute one tool call
  - wrap exceptions
  - package tool-return messages

- `apps/server/src/anima_server/services/agent/rules.py`
  - `ToolRulesSolver`
  - terminal/continue/init/child/approval rules

### Prompt and memory composition

- `apps/server/src/anima_server/services/agent/system_prompt.py`
  - keep existing prompt assembly entrypoint

- `apps/server/src/anima_server/services/agent/memory_blocks.py`
  - construct runtime memory blocks each step
  - `persona`
  - `human`
  - `current_focus`
  - `thread_summary`
  - optional task/session blocks

- `apps/server/src/anima_server/services/agent/compaction.py`
  - token accounting
  - summarization and in-context shrinking

### Persistence

- `apps/server/src/anima_server/services/agent/persistence.py`
  - thread load/create
  - message persistence
  - run lifecycle
  - step persistence
  - active-window queries

## Proposed Persistence Model

This is the smallest schema that still gives us a real orchestration substrate.

Use the `create-migration` skill when implementing this phase.

### `agent_threads`

Purpose:

- stable chat thread identity per user
- reset target for `/api/chat/reset`

Suggested fields:

- `id`
- `user_id`
- `status`
- `title` nullable
- `created_at`
- `updated_at`
- `last_message_at`

### `agent_messages`

Purpose:

- canonical record of user, assistant, tool, summary, and system artifacts
- source for reconstructing the active context window

Suggested fields:

- `id`
- `thread_id`
- `run_id` nullable
- `step_id` nullable
- `sequence_id`
- `role`
- `content_text` nullable
- `content_json` nullable
- `tool_name` nullable
- `tool_call_id` nullable
- `tool_args_json` nullable
- `is_in_context`
- `token_estimate` nullable
- `created_at`

Recommended roles:

- `system`
- `user`
- `assistant`
- `tool`
- `summary`

### `agent_runs`

Purpose:

- one record per chat request
- observability and retry/debug surface

Suggested fields:

- `id`
- `thread_id`
- `user_id`
- `provider`
- `model`
- `mode` (`blocking` or `streaming`)
- `status`
- `stop_reason` nullable
- `error_text` nullable
- `started_at`
- `completed_at` nullable
- `prompt_tokens` nullable
- `completion_tokens` nullable
- `total_tokens` nullable

### `agent_steps`

Purpose:

- one record per LLM iteration inside a run
- support debugging, replay, and orchestration observability

Suggested fields:

- `id`
- `run_id`
- `thread_id`
- `step_index`
- `status`
- `request_json`
- `response_json`
- `tool_calls_json` nullable
- `usage_json` nullable
- `error_text` nullable
- `created_at`

### Deferred table: `agent_memory_blocks`

This should not be phase-1 schema.

Initially, memory blocks can be constructed from ANIMA-owned sources instead of introducing a dedicated block table immediately. Add a dedicated block table only after the runtime and compaction are stable.

## Runtime Loop Specification

This is the target shape of the loop:

```python
async def run_turn(user_id: int, thread_id: int, user_message: str):
    run = await create_run(...)
    in_context_messages = await load_in_context_messages(thread_id)
    system_prompt = await build_runtime_system_prompt(user_id, thread_id)

    messages = [system_prompt, *in_context_messages, user_message]
    stop_reason = None

    for step_index in range(max_steps):
        allowed_tools = rules_solver.get_allowed_tools(all_tools)
        llm_request = build_request(messages, allowed_tools)

        response = await adapter.invoke(llm_request)
        parsed = normalize_llm_response(response)

        step_messages = []

        if not parsed.tool_calls:
            step_messages.append(make_assistant_message(parsed.content))
            stop_reason = StopReason.END_TURN
            await persist_step(...)
            break

        violations = rules_solver.validate(parsed.tool_calls)
        if violations:
            step_messages.extend(make_rule_violation_messages(...))
            await persist_step(...)
            messages.extend(step_messages)
            continue

        for tool_call in parsed.tool_calls:
            result = await execute_tool(tool_call, ...)
            step_messages.append(make_tool_result_message(tool_call, result))
            rules_solver.update_state(tool_call.name, result.output)

            if rules_solver.is_terminal(tool_call.name):
                stop_reason = StopReason.TERMINAL_TOOL

        await persist_step(...)
        messages.extend(step_messages)

        if stop_reason is not None:
            break

        messages = await maybe_compact(messages, ...)

    await finalize_run(run, stop_reason)
```

## Tool Rules Engine

Tool rules are the main Letta idea we should copy after the basic loop exists.

First rule types to implement:

- `TerminalToolRule`
- `ContinueToolRule`
- `InitToolRule`
- `ChildToolRule`
- `RequiresApprovalToolRule`

First-pass behavior:

- if init rules exist and no tool has been called yet, only init tools are allowed
- if the last tool has child rules, only its child tools are allowed next
- terminal tool stops the loop
- continue tool forces another loop iteration even if content exists
- approval rule returns a stop reason like `AWAITING_APPROVAL`

This gives us graph-like control flow without actual graph dependencies.

## LLM Adapter Strategy

The runtime should not know provider-specific response shapes.

Define a minimal adapter boundary:

- build request payload
- invoke provider
- parse text content
- parse tool calls
- parse token usage
- support blocking and streaming modes

Suggested provider implementation direction:

- use one OpenAI-compatible transport layer where practical
- provider-specific differences stay in configuration and transport modules
- keep the runtime fully provider-agnostic

## Prompt and Memory Block Strategy

The prompt system already exists and should remain the base.

The runtime should add a memory-block layer around it.

### Core blocks to support first

- `persona`
  - from `agent_persona_template`
  - current first-person persona templates under `services/agent/templates/persona`

- `human`
  - from user profile and durable personal facts

- `current_focus`
  - from current-focus memory if present

- `thread_summary`
  - persisted compaction output for the thread

- optional `session_context`
  - recent runtime metadata or short-lived context

### Initial memory sources

The first runtime implementation should not invent a new memory universe. It should reuse ANIMA sources where possible:

- persona templates in `apps/server`
- user table
- legacy memory concepts from the former `apps/api` path
- future server-native memory services

### Principle

Memory blocks should be rebuilt each step from canonical state, not mutated only in-memory for the lifetime of one process.

## Context Compaction Plan

Compaction should be built into the loop from the beginning, even if the first summarizer is basic.

Rules:

- do not wait exclusively for provider overflow errors
- proactively compact around 80 percent of the target context window
- keep system prompt and memory blocks
- keep recent conversation messages
- summarize the older middle
- store summary as a persisted `summary` message or summary block
- do not delete old messages from durable storage

Compaction outputs should become part of the next prompt build.

## Streaming Plan

Streaming must use the same runtime and the same persistence logic as blocking mode.

There should not be a separate orchestration implementation for streaming.

Recommended behavior:

- preflight runtime readiness before opening SSE
- stream assistant text chunks as they arrive
- optionally add future SSE event types:
  - `reasoning`
  - `tool_call`
  - `tool_return`
  - `usage`
  - `done`
  - `error`

Keep the current public SSE shape first unless a frontend migration is planned.

## Background Memory Work

The first background orchestration feature should not be multi-agent. It should be post-response consolidation.

Suggested order:

1. user and assistant turn completes
2. runtime returns response to caller
3. background task extracts durable facts, preferences, goals, and relationship updates
4. background task updates ANIMA memory stores

This gives ANIMA the practical upside of the Letta sleeptime idea without forcing multi-agent complexity immediately.

## Migration Phases

### Phase 0: architecture freeze

Acceptance criteria:

- this document is approved as the migration basis
- provider strategy agreed
- persistence scope agreed

### Phase 1: runtime skeleton

Deliverables:

- `AgentRuntime`
- adapter interfaces
- normalized runtime types
- loop execution replacing LangGraph

Acceptance criteria:

- `/api/chat` still works
- scaffold still works explicitly
- non-streaming requests run through the loop path

### Phase 2: persistence

Deliverables:

- `agent_threads`
- `agent_messages`
- `agent_runs`
- `agent_steps`
- persistence service layer

Acceptance criteria:

- multi-turn continuity survives process restarts
- run and step rows exist for every request
- thread reset clears active thread state correctly

### Phase 3: tool rules

Deliverables:

- rules dataclasses
- `ToolRulesSolver`
- runtime integration

Acceptance criteria:

- terminal and init rules are enforced
- rule violations are returned to the loop as structured errors

Status:

- completed

### Phase 4: memory blocks

Deliverables:

- core block builder
- prompt integration
- initial user/context blocks

Acceptance criteria:

- persona and user context are rebuilt each step
- runtime prompt no longer depends only on history + static prompt

Status:

- richer prompt memory blocks completed (`human`, `current_focus`, `thread_summary`)
- future work remains for session blocks and additional ANIMA-owned memory sources

### Phase 5: compaction

Deliverables:

- token estimator
- summary generation path
- in-context shrinking

Acceptance criteria:

- long conversations do not fail only because the window overflows
- summaries are persisted and reused

Status:

- basic persisted compaction completed
- future work remains for in-loop compaction and stronger summarization

### Phase 6: streaming parity

Deliverables:

- streaming adapter
- unified SSE runtime path

Acceptance criteria:

- streaming and blocking produce consistent persisted runs and messages

Status:

- provider-native chunk streaming completed for the OpenAI-compatible adapter path on top of the shared runtime
- future work remains for richer live event emission during the step itself, especially incremental tool-call deltas and broader provider coverage

### Phase 7: background memory consolidation

Deliverables:

- post-response extraction job
- memory update pipeline

Acceptance criteria:

- new durable facts can be extracted after a turn without blocking the user response

Status:

- basic background memory consolidation completed
- future work remains for richer extraction, duplicate merging, and non-heuristic memory maintenance

### Phase 8: remove LangGraph

Deliverables:

- delete graph-specific runtime modules
- remove obsolete dependencies

Acceptance criteria:

- loop runtime is fully covered by tests
- no server chat path depends on LangGraph

## Testing Strategy

### Unit tests

- tool rule solver
- adapter parsing
- prompt rendering with memory blocks
- persistence helpers
- compaction selection logic
- stop reason resolution

### Integration tests

- blocking `/api/chat`
- streaming `/api/chat`
- thread reset
- provider config failure
- prompt/persona template failure
- persisted continuity across turns
- compaction preserving summary + recent messages

### Migration safety tests

- regression coverage for current chat API contract
- loop-runtime coverage for the scaffold path and provider/template failures

## Risks

### Risk: persistence complexity too early

Mitigation:

- keep the initial schema small
- do not add multi-agent tables early

### Risk: prompt and memory models diverge across server and legacy API

Mitigation:

- reuse ANIMA-owned prompt and memory concepts
- migrate useful legacy logic intentionally, not piecemeal

### Risk: streaming becomes a second orchestration code path

Mitigation:

- enforce adapter pattern from the start

### Risk: compaction changes user-visible behavior too early

Mitigation:

- start with conservative thresholds
- log summary decisions
- keep raw messages durable

## Immediate Next Steps

The runtime migration is functionally complete. The current follow-up work is:

1. Finish encrypted Core defaults: make encrypted SQLite startup explicit and fail clearly when the expected passphrase or SQLCipher path is wrong.
2. Reduce the remaining legacy file-backed surface and decide whether `manifest.json` should remain plaintext metadata.
3. Broaden reflective memory maintenance: keep improving contradiction scans, synthesis quality, and self-model work built on top of the sleep-task pipeline.
4. Harden retrieval and search: embeddings already persist in SQLite and the vector index is process-local, so the next step is clarifying persistence and ranking behavior.

See `docs/roadmap.md` for the live roadmap.

## Historical Next Steps

The items below are preserved as the original sequencing notes from this
migration brief. Several are now complete in code and are kept here only for
historical context.

1. **Wire facts and preferences into prompts** (Phase 0) — the system already extracts these but never reads them back into context. Add `facts` and `preferences` memory blocks to `build_runtime_memory_blocks()`.
2. **Encrypt all memory files at rest** (Phase 1) — implement transparent encrypt-on-write / decrypt-on-read in `memory_store.py` using the vault DEK. This makes the Core a true cold wallet.
3. **LLM-based memory extraction** (Phase 2) — replace regex-only extraction with background LLM calls via Ollama or OpenRouter (open models only).
4. **Conflict resolution on write** (Phase 3) — prevent contradictory facts from accumulating.

These four phases represent the minimum viable memory system that makes the "revive the Core on new hardware" scenario actually work. See `docs/roadmap.md` for the full sequence.
