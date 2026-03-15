---
title: Python Agent Runtime Improvement Plan
last_edited: 2026-03-14
status: active
scope: apps/server
---

# Python Agent Runtime Improvement Plan

This document records the current assessment of the Python agent runtime in `apps/server` and the next improvements that would most increase durability, clarity, and product quality.

It is intentionally not a migration doc. The loop runtime already exists. The question now is how to harden it without losing the product direction that makes ANIMA distinctive.

Status note for 2026-03-14: several recommendations from the original review
have already landed in code. The runtime now has a prompt-budget planner
(`prompt_budget.py`), explicit staged execution in `service.py`, unified
provider/config truth around `scaffold`, `ollama`, `openrouter`, and `vllm`,
per-user reflection scheduling, and no longer carries `ContinueToolRule`.
Read any conflicting sections below as historical assessment context, not as
the latest implementation snapshot.

## Current State

The current runtime has crossed the line from "thin scaffold" into a real companion substrate.

Core strengths:

- explicit loop runtime in [`apps/server/src/anima_server/services/agent/runtime.py`](../apps/server/src/anima_server/services/agent/runtime.py)
- persisted threads, runs, steps, messages, memory items, episodes, and session notes in [`apps/server/src/anima_server/models/agent_runtime.py`](../apps/server/src/anima_server/models/agent_runtime.py)
- prompt composition owned by ANIMA, not by an external agent framework, in [`apps/server/src/anima_server/services/agent/system_prompt.py`](../apps/server/src/anima_server/services/agent/system_prompt.py)
- layered memory injection in [`apps/server/src/anima_server/services/agent/memory_blocks.py`](../apps/server/src/anima_server/services/agent/memory_blocks.py)
- session-memory tools in [`apps/server/src/anima_server/services/agent/tools.py`](../apps/server/src/anima_server/services/agent/tools.py)
- self-model, emotional context, semantic retrieval, and feedback-signal plumbing in the server runtime path

The current implementation is therefore directionally strong. The main work left is not "more agent features." It is enforcing invariants and controlling complexity.

## Verification Basis

This assessment is grounded in the live code and current tests, not in older architecture assumptions.

Relevant passing test subsets on 2026-03-14:

- `uv run --project apps/server pytest apps/server/tests/test_agent_runtime.py apps/server/tests/test_chat.py apps/server/tests/test_agent_memory_blocks.py apps/server/tests/test_session_memory.py -q`
- `uv run --project apps/server pytest apps/server/tests/test_consciousness.py -q`

## 1. Finish Hardening Turn Atomicity and Serialization

### Current state

The request path in [`apps/server/src/anima_server/services/agent/service.py`](../apps/server/src/anima_server/services/agent/service.py) now serializes turns per `user_id`, stages turn execution explicitly, and marks orphaned user messages out of active context on failure.

The remaining gap is that the current sequence allocator in [`apps/server/src/anima_server/services/agent/persistence.py`](../apps/server/src/anima_server/services/agent/persistence.py) is still `max(sequence_id) + 1`, while uniqueness is enforced only by the database constraint on `thread_id, sequence_id`.

### Why this matters

This remains one of the runtime's most important correctness gaps.

This is not primarily a multi-user concern. It is a same-user, same-thread concurrency concern.

A single user can still produce overlapping requests by:

- double-submitting
- retrying after a slow response
- having more than one client surface open
- hitting the API directly
- triggering another request while a previous stream is still live

If two same-user requests overlap:

- sequence allocation can race
- thread state can be read from a stale view
- failures can leave partial turn artifacts in active context

The current desktop UI reduces the likelihood of this in the primary chat surface by disabling input while streaming, but that is only a client-side guard. The backend still defines whether transcript order and continuity are actually trustworthy.

For a companion product built on continuity, this matters more than another tool or another memory source. If turn order is not trustworthy, memory quality and self-model quality become suspect downstream.

### What to change

- Move sequence allocation to a DB-safe primitive instead of `max + 1`.
- Treat a turn as one atomic unit with an explicit policy for failure.
- Ensure failed turns do not replay orphaned user messages as valid history.

### Recommended implementation shape

Add a turn coordinator layer that owns:

- per-user or per-thread async locking
- thread loading
- sequence reservation
- turn transaction boundaries

That keeps these concerns out of the service facade and gives the runtime a single place to define "what counts as a committed turn."

### Tests to add

- concurrent same-user submissions do not collide
- failed LLM invocation does not pollute live context
- retry after failure produces a clean next turn

## 2. Keep Hardening the Prompt-Budget Planner

### Current state

The runtime now injects many memory layers via [`apps/server/src/anima_server/services/agent/memory_blocks.py`](../apps/server/src/anima_server/services/agent/memory_blocks.py):

- `soul`
- five self-model blocks
- emotional context
- semantic retrieval hits
- facts
- preferences
- goals
- relationships
- current focus
- thread summary
- recent episodes
- session memory

[`apps/server/src/anima_server/services/agent/prompt_budget.py`](../apps/server/src/anima_server/services/agent/prompt_budget.py) now applies tiered budgets before final prompt assembly, but the planner is still character-based and the transcript compaction path in [`apps/server/src/anima_server/services/agent/compaction.py`](../apps/server/src/anima_server/services/agent/compaction.py) remains largely separate from that budget logic.

### Why this matters

This is now the main scalability issue inside the runtime.

The richest context increasingly lives outside the transcript. That means the system can exceed practical context budgets even if transcript compaction is working correctly.

The runtime now has real budget governance, but it still needs stronger enforcement, better observability, and clearer interaction between transcript compaction and memory-block budgeting.

### What to change

Keep iterating on one prompt-budget planner that decides:

- which blocks are mandatory
- which blocks are optional
- how much budget each block class can consume
- what gets summarized or dropped first
- how semantic hits compete with long-term fact blocks and session memory

### Recommended implementation shape

Use explicit tiers:

1. Never drop: system rules, guardrails, persona, soul
2. Strongly prefer: high-priority self-model slices, current focus, recent summary
3. Query-relevant: semantic retrieval hits, targeted facts/preferences
4. Nice-to-have: episodes, lower-priority self-model details, broad relationship context

Each tier should have a hard char/token budget. Do not rely on ad hoc truncation spread across block builders.

### Tests to add

- saturated prompt budget still preserves priority-0 and priority-1 blocks
- semantic hits displace lower-value generic blocks
- large self-model content is trimmed predictably rather than arbitrarily

## Identity Layering Recommendation

The runtime now has three distinct identity layers, and the docs should treat
them as different on purpose:

- `persona`: the thin seed voice and baseline temperament from the static
  template under `services/agent/templates/persona/`
- `soul`: the user-authored charter for who ANIMA should be in this
  relationship, stored in `self_model_blocks` with `section="soul"`
- `self_identity`: the evolving self-understanding that the system learns over
  time and injects dynamically into the prompt

These should not be collapsed into one concept.

Recommended role split:

- keep `persona` as a small fallback foundation
- treat `soul` as the canonical user-specific identity directive
- let `self_identity` evolve beneath that without contradicting the soul

Why this split is useful:

- a static persona template gives safe default behavior before the user shapes
  the companion
- the soul is personal and editable, so it should outrank the generic template
- self-identity should be learned and revisable, not frozen into the template

What should change over time is not the existence of three layers, but their
relative weight:

- `persona` should get thinner
- `soul` should become the main persistent charter
- `self_identity` should become the main adaptive layer

In prompt-budget terms, this means:

1. never drop: system rules, guardrails, thin persona seed, soul
2. strongly prefer: `self_identity`, current focus, recent thread summary
3. optional under budget pressure: lower-priority self-model sections and broad memory context

## 3. Keep the Turn Pipeline Explicit and Observable

### Current state

[`apps/server/src/anima_server/services/agent/service.py`](../apps/server/src/anima_server/services/agent/service.py) is now split into explicit stages:

- `_prepare_turn_context(...)`
- `_invoke_turn_runtime(...)`
- `_persist_turn_result(...)`
- `_run_post_turn_hooks(...)`

That split is a real improvement, but the service layer still owns many cross-cutting concerns and is the most likely place for future complexity to accumulate.

### Why this matters

The file still reads clearly, but it is already the point where otherwise-good features will start colliding.

The risk is not style. The risk is that turn semantics become implicit because too many concerns are interleaved in one function.

### What to change

Keep preserving stage boundaries as new features land, and avoid letting the service facade turn back into a god-function.

### Why this is worth doing

- easier to reason about failure boundaries
- easier to insert observability
- easier to test individual stages
- easier to add future memory layers without turning the entrypoint into a god-function

## 4. Keep Provider Truth Unified Between Runtime and Config

### Current state

The runtime provider list in [`apps/server/src/anima_server/services/agent/llm.py`](../apps/server/src/anima_server/services/agent/llm.py) supports:

- `scaffold`
- `ollama`
- `openrouter`
- `vllm`

The config route in [`apps/server/src/anima_server/api/routes/config.py`](../apps/server/src/anima_server/api/routes/config.py) now advertises the same real provider set and validates updates against it.

### Why this matters

This is one of the cleaner contracts in the runtime today, and it should stay that way:

- the UI cannot present providers that the runtime cannot actually load
- docs and behavior stay closer to implementation
- debugging is simpler because config values imply real runtime capability

### What to change

Keep deriving API-visible providers from the same source the runtime uses, and do not reintroduce legacy or fantasy providers without real adapter support behind them.

### Why this is worth doing

This is a small cleanup with disproportionate architectural value. A runtime that has a clear contract feels much more stable than one that accepts fantasy states.

## 5. Harden Tool Protocol Handling and Streaming Edge Cases

### Current state

The streaming path in [`apps/server/src/anima_server/services/agent/service.py`](../apps/server/src/anima_server/services/agent/service.py) now cancels the worker task when the client disconnects or the generator closes.

The OpenAI-compatible streaming adapter in [`apps/server/src/anima_server/services/agent/adapters/openai_compatible.py`](../apps/server/src/anima_server/services/agent/adapters/openai_compatible.py) also treats malformed streamed tool-call arguments as `{}`.

### Why this matters

The main issue that still remains is malformed streamed tool-call arguments degrading silently rather than failing explicitly.

For a local companion, these are acceptable in early development. For a durable runtime, they are weak contracts.

### What to change

- treat invalid streamed tool-call arguments as a step error, not as an empty dict
- add argument validation before tool execution

### Tests to add

- malformed tool-call JSON yields a structured step failure
- tools never run with silently-defaulted arguments after protocol corruption

## 6. Keep the Tool-Rule Surface Small and Truthful

### Current state

[`apps/server/src/anima_server/services/agent/rules.py`](../apps/server/src/anima_server/services/agent/rules.py) now keeps a smaller rule surface: terminal, init, child, and approval rules.

### Why this matters

Unused orchestration surface is expensive. It suggests capabilities the runtime does not actually guarantee.

That is especially risky in agent systems, where people start designing prompts and tools around semantics that do not really exist in code.

### What to change

Only add new rule types when the runtime has explicit semantics and tests behind them.

### Why this is worth doing

Keeping the runtime small and truthful is a major strength of this codebase. Preserve that.

## 7. Decide Whether Reflection Should Stay Per User or Move to Per Thread

### Current state

[`apps/server/src/anima_server/services/agent/reflection.py`](../apps/server/src/anima_server/services/agent/reflection.py) now scopes pending tasks and last-activity tracking per `user_id`, so one user's activity no longer cancels another user's reflection.

### Why this matters

This is acceptable if "conversation inactivity" is meant to be a user-level concept.

The open question is whether multiple active threads for the same user should share one inactivity timer or have separate reflection lifecycles.

### What to change

Decide explicitly whether per-user scope is the right product behavior or whether reflection should become thread-scoped.

### Tests to add

- repeated activity for one thread resets only that thread's timer

## 8. Clarify What Persistence Is Supposed to Guarantee

### Current state

The runtime has a well-shaped persistence schema, but `StepExecutionResult.raw_response` is not meaningfully persisted and step rows are written only after the in-memory turn completes.

### Why this matters

Right now the persistence layer is strong for:

- historical transcript continuity
- debugging completed turns
- compaction reuse

It is not yet strong for:

- crash-resilient mid-turn recovery
- replaying raw provider behavior
- full postmortem analysis of adapter normalization

That is not wrong. It just needs an explicit decision.

### What to change

Choose one of two directions:

- keep persistence normalized and lightweight, and simplify the runtime contract accordingly
- or persist richer step artifacts and possibly step-by-step writes if replay/debugging is a real product need

### Why this is worth doing

Ambiguous observability contracts create the worst kind of complexity: code that looks more durable than it really is.

## 9. Add Governance Around Self-Model Writes

### Current state

The self-model is now a serious part of the architecture. It can be read and written by multiple subsystems, including:

- inner monologue
- intentions
- feedback signals
- reflection/sleep-time work

### Why this matters

This is powerful, but it is also the next place the system can become chaotic.

Without policy, multiple writers can cause:

- oscillating identity text
- noisy growth logs
- unstable inner-state content
- accidental duplication of "what I learned"

### What to change

Add a self-model policy layer that decides:

- which subsystem owns which section
- which sections may be rewritten versus appended
- how often each section can change
- when a proposed change must become a growth-log entry instead of an overwrite

### Recommended ownership model

- `identity`: rare rewrite, high-threshold changes only
- `inner_state`: volatile, reflection/monologue owned
- `working_memory`: bounded mutable buffer with expiry
- `growth_log`: append-only
- `intentions`: mutable, but rule-based and deduplicated

### Tests to add

- repeated feedback signals do not spam growth-log entries
- intentions updates deduplicate rather than churn
- low-confidence monologue output cannot rewrite stable identity sections

## 10. Expand Tests from Feature Checks to Invariant Checks

### Current state

The runtime already has good feature coverage:

- chat/runtime behavior
- memory blocks
- session memory
- self-model and emotional context

### What is missing

The next test gap is not breadth. It is invariants:

- concurrent same-user turns
- failed-turn cleanup
- streaming disconnect cancellation
- prompt-budget saturation
- self-model write conflicts
- config/runtime provider mismatch rejection

### Why this matters

The codebase is leaving the phase where "feature exists" is enough. The next phase is "feature remains stable under pressure."

## Recommended Sequence

If improvement work needs to be staged, the highest-leverage order is:

1. atomic turns and sequence safety
2. prompt-budget planner hardening
3. tool-protocol hardening
4. persistence-contract clarification
5. self-model write governance
6. decide whether reflection stays per-user or moves per-thread
7. invariant-focused tests

## Closing View

The Python runtime is now distinctive for the right reasons:

- it is explicit
- it owns its own prompt and memory logic
- it is building toward continuity, not just task execution
- it has the beginnings of an actual inner architecture

That makes the next work more important, not less. The system now has enough depth that correctness, budgeting, and write governance matter more than adding another clever memory source.
