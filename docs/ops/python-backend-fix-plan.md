---
title: Python Backend Fix Plan
last_edited: 2026-03-14
status: active
scope: apps/server
---

# Python Backend Fix Plan

This document defines what should be fixed next in ANIMA's Python backend.

It is intentionally backend-only. It does not cover the legacy API surface, packaging concerns, or broader product marketing language.

The goal is straightforward: make the current Python backend trustworthy as a persistent personal AI runtime.

## Current Assessment

The backend is already beyond toy-project level.

What is already real in code:

- explicit loop runtime in `apps/server/src/anima_server/services/agent/runtime.py`
- staged service orchestration in `apps/server/src/anima_server/services/agent/service.py`
- persisted threads, runs, steps, and messages in `apps/server/src/anima_server/models/agent_runtime.py`
- database-backed memory, episodes, session notes, and daily logs
- prompt assembly from multiple memory layers in `apps/server/src/anima_server/services/agent/memory_blocks.py`
- background consolidation and reflection in `apps/server/src/anima_server/services/agent/consolidation.py` and `apps/server/src/anima_server/services/agent/reflection.py`

So the main work is no longer "invent the architecture." The main work is correctness, reliability, and memory quality.

## Guiding Decisions

These decisions should stay fixed while doing the work below:

1. Keep the custom loop runtime.
2. Do not swap to LangGraph or another orchestration framework right now.
3. Prefer hardening over adding more agent features.
4. Optimize for continuity, memory quality, and identity coherence.
5. Treat memory writes as product-critical behavior, not a side effect.

## Priority 0: Fix Immediate Correctness Gaps

### 1. Unify provider validation for chat and embeddings

Problem:

- chat config rejects invalid `openrouter` setup early
- embedding generation can still build an empty `Authorization: Bearer ` header and fail noisily in background jobs

Why this matters:

- semantic retrieval quality becomes configuration-fragile
- background memory work logs avoidable errors
- runtime behavior becomes harder to reason about

Primary files:

- `apps/server/src/anima_server/services/agent/llm.py`
- `apps/server/src/anima_server/services/agent/embeddings.py`

What to change:

- make provider validation reusable across chat and embedding paths
- never emit provider auth headers with empty credentials
- make embedding backfill skip cleanly when the configured provider is invalid or unavailable

Acceptance criteria:

- `openrouter` without API key fails clearly before any background embedding request is attempted
- background consolidation does not emit empty-bearer header errors
- semantic retrieval degrades gracefully to keyword-only behavior when embeddings are unavailable

Tests to add:

- embeddings path rejects `openrouter` without API key cleanly
- `build_provider_headers("openrouter")` never returns an empty bearer token

### 2. Replace `max(sequence_id) + 1` with a safer sequence reservation strategy

Problem:

- message sequencing still depends on `max(sequence_id) + 1`
- per-user locking helps, but this is not the final persistence contract

Why this matters:

- transcript ordering is core product truth
- future concurrency, retries, or multiple runtime surfaces will stress this path first

Primary files:

- `apps/server/src/anima_server/services/agent/persistence.py`
- `apps/server/src/anima_server/services/agent/service.py`
- `apps/server/src/anima_server/services/agent/turn_coordinator.py`

What to change:

- introduce a DB-safer sequence allocation strategy
- define what counts as a committed turn
- keep orphaned user messages out of active context on all failure paths

Acceptance criteria:

- concurrent same-user turns do not collide on sequence ids
- failed turns do not leave replayable transcript artifacts
- persisted ordering remains stable under retries

Tests to add:

- overlapping requests for one user keep correct ordering
- failed turn followed by retry produces a clean next transcript

### 3. Make chat/integration tests reliable on Windows

Problem:

- the chat suite is currently noisy in this environment because temp directory cleanup races with open handles

Why this matters:

- backend reliability claims are weaker if the most important integration tests are environment-fragile

Primary files:

- `apps/server/tests/test_chat.py`
- any runtime modules holding file handles or temp-backed resources open after response completion

What to change:

- close temp-backed resources deterministically
- make test cleanup robust on Windows
- isolate any vector-store or temp-dir lifecycle that survives beyond request scope

Acceptance criteria:

- `apps/server/tests/test_chat.py` passes reliably on Windows
- teardown does not fail with temp-directory permission errors

## Priority 1: Harden the Orchestrator

### 4. Make the tool-call protocol fail loudly, not softly

Problem:

- malformed streamed tool-call arguments are improved, but the contract should be fully explicit end-to-end

Why this matters:

- silent argument coercion is one of the fastest ways to make an agent look smarter than it is
- protocol ambiguity causes bad tool behavior and bad debugging

Primary files:

- `apps/server/src/anima_server/services/agent/adapters/openai_compatible.py`
- `apps/server/src/anima_server/services/agent/executor.py`
- `apps/server/src/anima_server/services/agent/runtime.py`

What to change:

- keep parse errors structured all the way through execution
- ensure invalid tool args always become explicit step errors
- keep the runtime small and truthful

Acceptance criteria:

- malformed streamed tool arguments never execute a tool with defaulted args
- step traces record the failure clearly
- SSE clients receive a coherent failure event path

### 5. Decide whether approval should become a real durable interrupt

Problem:

- approval rules exist, but approval is still a stop condition, not a full resume-capable workflow primitive

Why this matters:

- if approval is a product feature, it should survive real-world pauses and resumes
- if it is not, the system should stay simpler and honest about that

Primary files:

- `apps/server/src/anima_server/services/agent/rules.py`
- `apps/server/src/anima_server/services/agent/runtime.py`
- `apps/server/src/anima_server/models/agent_runtime.py`

Decision needed:

- keep approval as a lightweight stop reason
- or promote it into a persisted interrupt/resume model

Recommendation now:

- keep it lightweight unless human approval becomes a major product path

### 6. Unify prompt budgeting and transcript compaction more tightly

Problem:

- memory-block budgeting and transcript compaction both exist, but they are still partially separate systems

Why this matters:

- prompt quality depends on tradeoffs across all context sources, not transcript alone

Primary files:

- `apps/server/src/anima_server/services/agent/prompt_budget.py`
- `apps/server/src/anima_server/services/agent/compaction.py`
- `apps/server/src/anima_server/services/agent/memory_blocks.py`

What to change:

- move toward one clearer budgeting model
- prefer token-aware accounting over character-only heuristics where practical
- make budget decisions observable in traces or logs

Acceptance criteria:

- high-priority identity/state blocks survive saturation
- low-value context drops first in predictable ways
- large prompts remain understandable to debug

## Priority 2: Improve Memory Quality

### 7. Make extraction quality stricter than "write anything plausible"

Problem:

- memory extraction is real, but a personal AI will fail mainly through low-quality writes, not missing writes

Why this matters:

- bad durable memory is more damaging than no memory

Primary files:

- `apps/server/src/anima_server/services/agent/consolidation.py`
- `apps/server/src/anima_server/services/agent/memory_store.py`

What to change:

- tighten thresholds for low-confidence extraction
- improve duplicate and near-duplicate handling
- prefer fewer, cleaner memories over higher write volume

Acceptance criteria:

- memory tables accumulate less noise over long conversations
- contradictions and duplicates are reduced before prompt injection

### 8. Add explicit ownership rules for self-model writes

Problem:

- identity, inner state, growth log, and intentions are all valuable, but multiple subsystems can write to them

Why this matters:

- without ownership rules, the self-model will eventually churn

Primary files:

- `apps/server/src/anima_server/services/agent/self_model.py`
- `apps/server/src/anima_server/services/agent/inner_monologue.py`
- `apps/server/src/anima_server/services/agent/intentions.py`
- `apps/server/src/anima_server/services/agent/feedback_signals.py`
- `apps/server/src/anima_server/services/agent/sleep_tasks.py`

What to change:

- define which subsystem owns which section
- define append-only vs rewrite semantics
- block low-confidence rewrites of stable identity sections

Acceptance criteria:

- identity is stable
- growth log is informative rather than spammy
- intentions are deduplicated and lifecycle-aware

### 9. Clarify session-memory promotion rules

Problem:

- session notes and durable memory both exist, but the promotion boundary should stay explicit

Why this matters:

- personal AI quality depends on knowing what should stay local to a thread versus what should become long-term memory

Primary files:

- `apps/server/src/anima_server/services/agent/session_memory.py`
- `apps/server/src/anima_server/services/agent/memory_store.py`
- `apps/server/src/anima_server/services/agent/consolidation.py`

What to change:

- define promotion triggers more explicitly
- keep session scratchpad behavior separate from durable user-state inference

## Priority 3: Strengthen Backend Product Trust

### 10. Make encrypted-Core expectations explicit in code, not just docs

Problem:

- encryption support exists, but the product stance is still "supported but not fully enforced"

Why this matters:

- this product's thesis depends on trust and user-owned continuity

Primary files:

- `apps/server/src/anima_server/db/session.py`
- `apps/server/src/anima_server/config.py`
- `apps/server/src/anima_server/services/core.py`

What to change:

- decide when encryption is optional, expected, or required
- fail clearly when the configured encryption path is invalid

### 11. Keep docs tied to backend reality

Problem:

- this backend is complicated enough that stale docs will create false confidence quickly

What to keep in sync:

- `docs/memory-system.md`
- `docs/roadmap.md`
- `docs/agent-runtime-improvements.md`

## What Not To Do Right Now

Do not do these until the priorities above are in better shape:

- do not replace the runtime with LangGraph
- do not add multi-agent orchestration
- do not add more memory layers just because they sound intelligent
- do not turn approval flow into a complex subsystem unless the product clearly needs it
- do not widen provider support beyond the currently supported runtime set unless there is real adapter coverage

## Recommended Execution Order

1. provider/embedding validation fix
2. sequence allocation hardening
3. Windows chat test reliability
4. tool protocol hardening
5. prompt budget + compaction unification
6. self-model write governance
7. extraction quality tightening
8. encrypted-Core enforcement decision

## Final View

ANIMA's Python backend does not need a new architecture first.

It needs to become stricter about truth:

- true transcript ordering
- true memory quality
- true provider/config behavior
- true self-model governance
- true reliability under pressure

That is the work that will determine whether the system feels profound or fragile.
