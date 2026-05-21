# Agent Runtime Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the highest-risk agent runtime audit findings around thread isolation, approval sequencing, cancellation state, delegated action tools, and LLM client configuration.

**Architecture:** Keep `AgentRuntime` mostly stateless and patch the stateful service boundary where thread/run lifecycle is coordinated. Isolate companion history by thread, serialize approval resume through the same thread lock as normal turns, make cancelled runs terminal-safe, and tighten delegated action result ownership.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0, pytest, runtime PostgreSQL/SQLite test fixtures, local tool schemas, OpenAI-compatible chat client.

---

## Tracking

- Workstream todo: `scratchboard/v2-memory-recall-reliability/todo.md`
- Audit source: agent runtime audit performed on 2026-05-18
- Primary package: `apps/server/src/anima_server/services/agent/`
- Test command: `uv run pytest -q tests/test_agent_service.py tests/test_approval_reentry.py tests/test_cancellation.py tests/test_client_action_tools.py tests/test_agent_llm.py tests/test_agent_openai_compatible_client.py`

## File Map

| Area | Files |
| --- | --- |
| Companion state | `apps/server/src/anima_server/services/agent/companion.py`, `service.py` |
| Approval lifecycle | `apps/server/src/anima_server/services/agent/service.py`, `persistence.py` |
| Cancellation lifecycle | `apps/server/src/anima_server/services/agent/service.py`, `persistence.py` |
| Delegated action tools | `apps/server/src/anima_server/services/agent/client_actions.py`, `api/routes/ws.py` |
| LLM client config | `apps/server/src/anima_server/services/agent/openai_compatible_client.py` |
| Prompt/schema alignment | `apps/server/src/anima_server/services/agent/templates/system_prompt.md.j2`, `templates/system_rules.md.j2` |
| Tests | `apps/server/tests/test_agent_service.py`, `test_approval_reentry.py`, `test_cancellation.py`, `test_client_action_tools.py`, `test_agent_openai_compatible_client.py` |

## Phase 1: State Isolation

### Ticket AR-001: Isolate Companion History Per Thread

**Problem:** The service now permits concurrent turns across different threads, but `AnimaCompanion` still owns a single mutable `thread_id` and history list per user. A turn for thread B can clear or replace thread A's in-flight history.

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/companion.py`
- Modify: `apps/server/src/anima_server/services/agent/service.py`
- Test: `apps/server/tests/test_agent_service.py`

- [x] Write a regression test showing cached histories for two thread IDs remain separate.
- [x] Add thread-keyed history cache helpers on `AnimaCompanion`.
- [x] Update service calls to load/invalidate/refresh history by explicit thread ID.
- [x] Return defensive history copies to each runtime invocation.
- [x] Run focused service tests.

### Ticket AR-002: Serialize Approval Resume With The Thread Lock

**Problem:** `approve_or_deny_turn()` persists resumed tool results without using `get_thread_lock(run.thread_id)`, so approval resume can interleave with a normal turn in the same thread.

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/service.py`
- Test: `apps/server/tests/test_approval_reentry.py`

- [x] Write a regression test that proves approval resume uses the target thread lock.
- [x] Split the current approval body into a locked helper.
- [x] Acquire `get_thread_lock(run.thread_id)` before clearing the checkpoint or reserving messages.
- [x] Run focused approval tests.

## Phase 2: Run Lifecycle Correctness

### Ticket AR-003: Preserve Cancelled Runs As Terminal

**Problem:** A cancel request can mark a run `cancelled`, but later persistence can overwrite it as `completed`. Streaming disconnects can also cancel the worker task without marking the run terminal.

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/persistence.py`
- Modify: `apps/server/src/anima_server/services/agent/service.py`
- Test: `apps/server/tests/test_cancellation.py`

- [x] Write a regression test that `persist_agent_result()` does not complete a cancelled run.
- [x] Add streaming-worker cleanup that marks the run cancelled when the worker task is cancelled after run creation.
- [x] Make finalization refuse to overwrite terminal `cancelled`/`failed` status.
- [x] Run focused cancellation tests.

## Phase 3: Delegated Tool Ownership

### Ticket AR-004: Scope Delegated Tool Results To Connection And User

**Problem:** Delegated action results are resolved by global `tool_call_id` only. Deterministic synthetic IDs or malicious/buggy clients can collide across connections.

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/client_actions.py`
- Modify: `apps/server/src/anima_server/api/routes/ws.py`
- Test: `apps/server/tests/test_client_action_tools.py`

- [x] Write a regression test with duplicate call IDs across two users/connections.
- [x] Key pending delegated calls by `(user_id, tool_call_id)` or verify the pending connection before resolving.
- [x] Make WebSocket result handling pass the sender connection into resolution.
- [x] Reject mismatched tool names or user IDs.
- [x] Run focused client action tests.

## Phase 4: Configuration And Prompt Alignment

### Ticket AR-005: Preserve Temperature Through Tool Binding

**Problem:** `OpenAICompatibleChatClient.bind_tools()` drops `_temperature`, so tool-enabled turns ignore configured temperature.

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/openai_compatible_client.py`
- Test: `apps/server/tests/test_agent_openai_compatible_client.py`

- [x] Write a regression test that bound clients retain `_temperature`.
- [x] Pass `temperature=self._temperature` into the bound client constructor.
- [x] Run OpenAI-compatible client tests.

### Ticket AR-006: Remove Impossible Required `thinking` Prompt Wording

**Problem:** System prompts tell models every tool call must include a `thinking` argument, while schemas intentionally omit `thinking` and strict schema mode is on by default.

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/templates/system_prompt.md.j2`
- Modify: `apps/server/src/anima_server/services/agent/templates/system_rules.md.j2`
- Test: `apps/server/tests/test_agent_system_prompt.py`

- [x] Write or update prompt tests to assert the prompt no longer requires schema-absent `thinking`.
- [x] Reword the cognitive loop around private reasoning without naming a required tool argument.
- [x] Keep executor defensive stripping of unexpected `thinking` args for backward compatibility.
- [x] Run prompt tests.

## Validation Gates

- [x] Run focused runtime test subset.
- [x] Run `bun run test:server` if service/persistence changes are broad.
- [x] Run `bun run lint` before merging.
- [x] Smoke-test chat streaming, approval resume, cancellation, and delegated action tools.
