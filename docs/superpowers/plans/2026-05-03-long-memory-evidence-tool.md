# Long Memory Evidence as Agent Tool

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the keyword-based intent classifier with an LLM-driven agent tool that fetches wide evidence on demand. Solves multilingual support and aligns with modern agentic-RAG patterns where the model decides when to retrieve, instead of a pre-turn classifier guessing.

**Architecture:** Keep the compaction/rerank/decryption logic from `evidence_retrieval.py`. Expose it as a single agent tool `search_long_memory(query, mode)` with a mode enum (`aggregate`, `latest_update`, `temporal`, `preference`). Tool returns compacted evidence text directly as its result; the model uses it to answer in the same turn. Remove the pre-turn `_run_wide_evidence_retrieval` path and the `retrieval_intent.py` keyword classifier. The system prompt is updated with one short paragraph instructing the model when to reach for the tool.

**Why this shape:**
- Language-agnostic for free — Haiku decides, in any language it speaks.
- Zero overhead on DIRECT questions — the tool only fires when the model reaches for it.
- Auditable — every wide-evidence fetch shows up as a tool call in the trace, with the mode the model chose.
- Less code than what's there now — no classifier, no pre-turn helper, no retrieval-trace plumbing for the wide path.

**Tech Stack:** Python 3.12, FastAPI server, SQLAlchemy, existing `@tool` infrastructure in `services/agent/tools.py`, `evidence_retrieval.py` from prior work, pytest, ruff, Bun/Nx wrapper scripts.

---

## Current State

After commit `bb29967`:

- `retrieval_intent.py` — keyword classifier with English-only triggers. **Will be removed.**
- `evidence_retrieval.py` — `extract_session_date`, `compact_evidence_text`, `rerank_evidence_texts`, `retrieve_wide_evidence`. **Compaction/rerank stays; the classifier-driven entry point goes.**
- `service.py` — `_run_wide_evidence_retrieval` helper, called pre-turn before adaptive retrieval, builds `evidence_memories` block. **Will be removed.**
- `memory_blocks.py` — `build_evidence_memory_block` + `evidence_results` parameter on `build_runtime_memory_blocks`. **Will be removed; evidence comes back as a tool result, not a memory block.**

LongMemEval mixed 20q baseline (with current keyword classifier): **13/20**, English-only. Multilingual users currently get DIRECT-only retrieval regardless of intent.

---

## Design Rules

- The tool is the only entry point for wide-evidence retrieval. No pre-turn classification.
- Tool returns a single compact text payload — session-dated user lines, one block per evidence item. No memory-block side-channels.
- The system prompt names the tool and gives the model a one-sentence rule for when to call it. Don't list intent keywords; describe situations ("when the user asks how many / when / which-was-first / what-do-you-recommend questions and the answer is not already in your visible memory").
- Tool calls and results flow through the existing trace. The retrieval trace's `retriever` field becomes `"tool:search_long_memory"` for these turns.
- Remove rather than deprecate: `retrieval_intent.py`, `_run_wide_evidence_retrieval`, `build_evidence_memory_block`, `_SkipAdaptiveRetrieval`, the `evidence_results` parameter on `build_runtime_memory_blocks`. Nothing outside the agent service consumes them.

---

### Task 1: Add `search_long_memory` tool

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/evidence_retrieval.py`
- Modify: `apps/server/src/anima_server/services/agent/tools.py`
- Test: `apps/server/tests/test_search_long_memory_tool.py`

- [ ] **Step 1: Refactor `evidence_retrieval.py` to expose mode-driven retrieval without the classifier**

Add a `RetrievalMode` enum (move from `retrieval_intent.py`). Change `retrieve_wide_evidence` so it accepts an explicit `mode: RetrievalMode` parameter instead of classifying internally. Keep candidate-limit / max-evidence sizing inside the function, keyed off the mode.

The `WideEvidenceResult` dataclass keeps the same shape minus `intent`; add `mode: RetrievalMode` directly.

- [ ] **Step 2: Write failing tool test**

```python
import pytest
from anima_server.services.agent.tools import search_long_memory
from anima_server.services.agent.tool_context import set_tool_context, ToolContext


@pytest.mark.asyncio
async def test_search_long_memory_aggregates_distinct_user_lines(monkeypatch) -> None:
    # Monkeypatch retrieve_wide_evidence to return a known WideEvidenceResult.
    # Set a tool context with stub db / user_id.
    # Invoke search_long_memory.invoke({"query": "How many model kits?", "mode": "aggregate"}).
    # Assert the returned string contains all three distinct session dates and
    # that "Weathering tips" (assistant noise) is excluded.
    ...
```

- [ ] **Step 3: Implement the tool**

```python
@tool
async def search_long_memory(query: str, mode: str = "aggregate") -> str:
    """Search long-term memory across sessions for evidence the user does not see in your visible memory blocks.

    Use this tool when:
      - The user asks "how many" / "how much" / "what's the total" — set mode="aggregate".
      - The user asks "where did X move to recently" / "what's my current Y" — set mode="latest_update".
      - The user asks "which came first" / "before X did Y happen" — set mode="temporal".
      - The user asks "recommend / suggest / what should I" and you need their stated preferences — set mode="preference".

    Returns a compact list of evidence lines, each prefixed with the session date.
    Each line is a verbatim user statement. Do not call this tool for questions
    you can already answer from your visible memory blocks.
    """
    ...
```

The body resolves the tool context (`get_tool_context()`), calls `retrieve_wide_evidence(db=ctx.db, user_id=ctx.user_id, query=query, mode=...)`, formats the result as plain text, and returns it.

- [ ] **Step 4: Register the tool in `get_core_tools`**

- [ ] **Step 5: Run tests**

```powershell
uv run --project apps/server pytest apps/server/tests/test_search_long_memory_tool.py -q
```

- [ ] **Step 6: Commit**

```powershell
git add apps/server/src/anima_server/services/agent/evidence_retrieval.py apps/server/src/anima_server/services/agent/tools.py apps/server/tests/test_search_long_memory_tool.py
git commit -m "agent: expose wide evidence retrieval as a tool"
```

---

### Task 2: Update system prompt

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/system_prompt.py` (or whichever file owns the per-turn system prompt template)
- Test: extend `apps/server/tests/test_agent_system_prompt.py`

- [ ] **Step 1: Find where per-turn instructions are appended**

- [ ] **Step 2: Add a single short instruction**

Wording (keep tight; don't list intent keywords):

> When the user asks a question whose answer requires looking across multiple past sessions — counts, latest values, temporal ordering, or preference-driven recommendations — and the answer is not already in your visible memory blocks, call `search_long_memory(query, mode)` before answering. Pick the mode that matches the question shape. Do not ask the user for clarification first.

- [ ] **Step 3: Test the prompt contains the instruction**

- [ ] **Step 4: Commit**

```powershell
git commit -m "agent: prompt model to reach for search_long_memory"
```

---

### Task 3: Remove pre-turn classification path

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/service.py`
- Modify: `apps/server/src/anima_server/services/agent/memory_blocks.py`
- Delete: `apps/server/src/anima_server/services/agent/retrieval_intent.py`
- Delete: `apps/server/tests/test_retrieval_intent.py`
- Delete: `apps/server/tests/test_evidence_memory_blocks.py`
- Delete: `apps/server/tests/test_agent_wide_evidence_wiring.py`
- Modify: `apps/server/tests/test_evidence_retrieval.py` — drop tests tied to `RetrievalIntent`/classifier; keep tests that exercise compaction and rerank.

- [ ] **Step 1: Remove `_run_wide_evidence_retrieval`, `_WideEvidenceTurnContext`, `_SkipAdaptiveRetrieval` from service.py**

Restore the pre-turn block to its pre-Task-5 shape: hybrid_search → adaptive filter → relevant_memories. No wide-evidence detour. The new tool replaces it.

- [ ] **Step 2: Remove `evidence_results` param and `build_evidence_memory_block` from memory_blocks.py**

Strip `evidence_memories` from `_MIRROR_DESCRIPTIONS`. Remove the conditional block insertion. Remove the cache-eviction entry for `"evidence_memories"`.

- [ ] **Step 3: Delete `retrieval_intent.py` and its test**

- [ ] **Step 4: Verify nothing else imports the removed names**

```powershell
uv run --project apps/server ruff check apps/server/src apps/server/tests
```

- [ ] **Step 5: Run focused suites**

```powershell
uv run --project apps/server pytest apps/server/tests/test_evidence_retrieval.py apps/server/tests/test_search_long_memory_tool.py apps/server/tests/test_agent_memory_blocks.py apps/server/tests/test_agent_service.py -q
```

- [ ] **Step 6: Commit**

```powershell
git commit -m "agent: remove pre-turn intent classifier in favor of tool"
```

---

### Task 4: Verification

- [ ] **Step 1: Ruff on touched files**
- [ ] **Step 2: Full server test suite — `bun run test:server`**
- [ ] **Step 3: `bun run lint`**
- [ ] **Step 4: `bun run build`**

---

### Task 5: Mixed benchmark + spot-check multilingual

**English (regression check):**
- [ ] Run mixed 20q benchmark on the disposable server (same shape as commit `bb29967`).
- [ ] Score with qwen3.5 judge. Target: **≥ 13/20** (no regression vs. classifier baseline).

**Multilingual (the actual point):**
- [ ] Pick three of the failing-or-passing English questions, hand-translate to two languages (e.g. Spanish + Indonesian), feed them through the same disposable server.
- [ ] Verify in the trace that `search_long_memory` is called for the wide-evidence questions in both translated languages — that's the regression test for the multilingual fix. Score is secondary.

- [ ] Stop the eval server and confirm port 3032 is clear.

---

## If The Score Drops

The known regression risk is the model failing to call the tool on questions where the keyword classifier would have routed correctly. Two mitigations, in order:

1. Tighten the system-prompt instruction. Don't add intent keywords; do add a short "if you find yourself about to say 'I don't have that information' for a question that sounds historical, call the tool first" rule.
2. Reduce tool friction: lower the bar for calling it (the cost of a wasted call is one extra round-trip; the cost of a missed call is a wrong answer).

If the score still doesn't hit 13/20, the right next move is the structured event-memory schema parked in the prior plan's "Future Phase" — not more tool-prompt iteration.

---

## What This Replaces

After this plan ships, the entire keyword-intent surface (`retrieval_intent.py`, `_run_wide_evidence_retrieval`, the `evidence_results` parameter, the `evidence_memories` block, the `_SkipAdaptiveRetrieval` sentinel) is gone. The remaining long-memory surface is:

- `evidence_retrieval.py` — compaction + rerank + `retrieve_wide_evidence(mode=...)` (no classifier).
- `tools.py` — `search_long_memory` tool.
- A one-paragraph instruction in the system prompt.

That's the whole feature. Less code than the keyword version, language-agnostic, and aligned with how Anthropic's own agentic-RAG examples are structured.
