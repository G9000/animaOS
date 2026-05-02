# Long Memory Evidence Retrieval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve LongMemEval mixed-score performance by adding targeted wide evidence retrieval for aggregation, temporal, latest-update, and preference questions without changing the database schema.

**Architecture:** Keep the existing memory store and Rust retrieval index. Add a Python query-intent layer that decides when the normal narrow adaptive retrieval is insufficient, then use Rust/Python hybrid retrieval to collect a wider candidate pool, rerank it by task-specific evidence rules, compact the evidence, and inject it as a dedicated prompt block. This is a proof-oriented step before any bigger event-memory schema migration.

**Tech Stack:** Python 3.12, FastAPI server, SQLAlchemy, existing `anima-core` Rust retrieval bindings, pytest, ruff, Bun/Nx wrapper scripts.

---

## Current Context

Recent benchmark results:

- Mixed LongMemEval 20q, Haiku 4.5, temperature 0: `11/20 = 55%`
- Temporal-only first 20q, Haiku 4.5, temperature 0: `13/20 = 65%`
- Previous non-deterministic Haiku 20q: `15/20 = 75%`
- Previous Gemma4 20q: `16/20 = 80%`

Mixed failure pattern:

- `single-session-user`: `3/3`
- `single-session-assistant`: `3/3`
- `temporal-reasoning`: `3/4`
- `knowledge-update`: `1/3`
- `single-session-preference`: `1/3`
- `multi-session`: `0/4`

Root cause observed from result JSON:

- Direct single-session facts work.
- Multi-session aggregation fails because normal adaptive retrieval returns only a few high-scoring chunks and often misses the full set needed to count.
- Knowledge-update questions fail because session dates are plain text inside chunks, not structured metadata, so "latest value wins" is not strongly represented.
- Preference questions fail because raw eval imports classify everything as `fact`, so preference evidence is not promoted distinctly.
- A previous broad-context experiment that simply increased retrieval volume regressed score to `12/20`; do not repeat that approach.

Result files to use for debugging:

- `apps/server/eval/results/longmemeval_oracle_raw_20_haiku45_mixed_temp0.json`
- `apps/server/eval/results/longmemeval_oracle_raw_20_haiku45_temp0_latest.json`

---

## File Structure

Create:

- `apps/server/src/anima_server/services/agent/retrieval_intent.py`
  - Classify user questions into retrieval modes.
  - Own lightweight query term extraction.
  - No DB access.

- `apps/server/src/anima_server/services/agent/evidence_retrieval.py`
  - Fetch wide candidates through existing `hybrid_search`.
  - Rerank candidates by intent.
  - Compact verbose raw transcript chunks into short evidence snippets.
  - Return prompt-ready evidence plus retrieval trace fragments.

- `apps/server/tests/test_retrieval_intent.py`
  - Unit tests for query classification and term extraction.

- `apps/server/tests/test_evidence_retrieval.py`
  - Unit tests for reranking, date parsing, evidence compaction, and aggregation behavior.

Modify:

- `apps/server/src/anima_server/services/agent/service.py`
  - Wire the evidence retrieval path into pre-turn retrieval.
  - Preserve existing retrieval path for normal direct questions.

- `apps/server/src/anima_server/services/agent/memory_blocks.py`
  - Add an optional dedicated `evidence_memories` block or allow `_build_semantic_block` to use evidence-specific labeling.

- `apps/server/eval/run_longmemeval.py`
  - No major changes expected. Only modify if a small logging field is needed to compare retrieval modes.

- `apps/server/eval/README.md`
  - Add a short command section for the mixed benchmark once implementation is verified.

Do not modify in first pass:

- `apps/server/models/*` or Alembic migrations.
- `packages/anima-core` unless profiling shows the Python reranker is too slow.
- Long-term memory extraction prompts.

---

## Design Rules

- Keep default direct retrieval unchanged unless an intent specifically needs wide evidence.
- Do not increase prompt context globally.
- For wide retrieval, fetch more candidates but compact them aggressively before prompt injection.
- Prefer user-stated lines over assistant advice/filler.
- Preserve session date in every evidence snippet.
- For aggregation questions, preserve distinct evidence items across sessions.
- For latest-update questions, prefer newer session evidence when the same entity appears multiple times.
- For preference questions, boost chunks where the user expresses "I prefer", "I enjoy", "I am working in", "I use", "I want to learn", etc.
- All production behavior changes need failing tests first.

---

### Task 1: Add Query Intent Classifier

**Files:**

- Create: `apps/server/src/anima_server/services/agent/retrieval_intent.py`
- Test: `apps/server/tests/test_retrieval_intent.py`

- [ ] **Step 1: Write failing tests**

Add tests that describe the desired API:

```python
from anima_server.services.agent.retrieval_intent import RetrievalMode, classify_retrieval_intent


def test_classifies_count_questions_as_aggregate() -> None:
    intent = classify_retrieval_intent("How many model kits have I worked on or bought?")

    assert intent.mode == RetrievalMode.AGGREGATE
    assert intent.needs_wide_evidence is True
    assert intent.candidate_limit >= 40


def test_classifies_latest_update_questions() -> None:
    intent = classify_retrieval_intent("Where did Rachel move to after her recent relocation?")

    assert intent.mode == RetrievalMode.LATEST_UPDATE
    assert intent.needs_wide_evidence is True


def test_classifies_preference_recommendation_questions() -> None:
    intent = classify_retrieval_intent(
        "Can you recommend some resources where I can learn more about video editing?"
    )

    assert intent.mode == RetrievalMode.PREFERENCE
    assert intent.needs_wide_evidence is True


def test_keeps_plain_chat_on_direct_mode() -> None:
    intent = classify_retrieval_intent("What is my dog's name?")

    assert intent.mode == RetrievalMode.DIRECT
    assert intent.needs_wide_evidence is False
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
uv run --project apps/server pytest apps/server/tests/test_retrieval_intent.py -q
```

Expected: fail because module does not exist.

- [ ] **Step 3: Implement minimal classifier**

Create `retrieval_intent.py` with:

```python
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class RetrievalMode(StrEnum):
    DIRECT = "direct"
    AGGREGATE = "aggregate"
    TEMPORAL = "temporal"
    LATEST_UPDATE = "latest_update"
    PREFERENCE = "preference"


@dataclass(frozen=True, slots=True)
class RetrievalIntent:
    mode: RetrievalMode
    candidate_limit: int = 15
    max_evidence: int = 6
    min_distinct_sessions: int = 1

    @property
    def needs_wide_evidence(self) -> bool:
        return self.mode is not RetrievalMode.DIRECT


def classify_retrieval_intent(query: str) -> RetrievalIntent:
    q = query.lower()

    if "how many" in q or "number of" in q or q.startswith("count "):
        return RetrievalIntent(
            mode=RetrievalMode.AGGREGATE,
            candidate_limit=50,
            max_evidence=10,
            min_distinct_sessions=3,
        )

    if "which" in q and ("first" in q or "earlier" in q or "before" in q):
        return RetrievalIntent(
            mode=RetrievalMode.TEMPORAL,
            candidate_limit=40,
            max_evidence=8,
            min_distinct_sessions=2,
        )

    if "recent" in q or "latest" in q or "after" in q or "move to" in q or "moved to" in q:
        return RetrievalIntent(
            mode=RetrievalMode.LATEST_UPDATE,
            candidate_limit=40,
            max_evidence=8,
            min_distinct_sessions=2,
        )

    if "recommend" in q or "resources" in q or "conference" in q or "publication" in q:
        return RetrievalIntent(
            mode=RetrievalMode.PREFERENCE,
            candidate_limit=35,
            max_evidence=6,
            min_distinct_sessions=1,
        )

    return RetrievalIntent(mode=RetrievalMode.DIRECT)
```

- [ ] **Step 4: Run tests and verify GREEN**

Run:

```powershell
uv run --project apps/server pytest apps/server/tests/test_retrieval_intent.py -q
```

Expected: pass.

- [ ] **Step 5: Commit**

```powershell
git add apps/server/src/anima_server/services/agent/retrieval_intent.py apps/server/tests/test_retrieval_intent.py
git commit -m "agent: classify retrieval intent"
```

---

### Task 2: Add Evidence Compaction And Reranking

**Files:**

- Create: `apps/server/src/anima_server/services/agent/evidence_retrieval.py`
- Test: `apps/server/tests/test_evidence_retrieval.py`

- [ ] **Step 1: Write failing tests for date extraction and user-line compaction**

```python
from anima_server.services.agent.evidence_retrieval import compact_evidence_text, extract_session_date


def test_extracts_session_date_from_raw_chunk() -> None:
    text = "Session date: 2023/05/29 (Mon) 20:29\nUser: I bought a 1/72 scale B-29."

    assert extract_session_date(text) == "2023/05/29 (Mon) 20:29"


def test_compacts_to_relevant_user_lines_and_keeps_date() -> None:
    text = (
        "Session date: 2023/05/29 (Mon) 20:29\n"
        "User: I bought a 1/72 scale B-29 bomber model kit and a 1/24 scale Camaro.\n"
        "Assistant: Here are many long photo-etching tips that are not the answer."
    )

    compacted = compact_evidence_text(text, query_terms={"model", "kit", "bomber", "camaro"})

    assert "Session date: 2023/05/29" in compacted
    assert "B-29 bomber" in compacted
    assert "Camaro" in compacted
    assert "photo-etching tips" not in compacted
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
uv run --project apps/server pytest apps/server/tests/test_evidence_retrieval.py -q
```

Expected: fail because module does not exist.

- [ ] **Step 3: Implement minimal helpers**

Implement:

- `extract_session_date(text: str) -> str | None`
- `extract_user_lines(text: str) -> list[str]`
- `extract_question_terms(query: str) -> set[str]`
- `compact_evidence_text(text: str, query_terms: set[str], max_chars: int = 900) -> str`

Rules:

- Keep `Session date: ...` line.
- Prefer `User:` lines.
- Include assistant line only if no user line matches.
- Match query terms by lowercase substring.
- Fall back to first user line if no terms match.
- Truncate at sentence or line boundary where possible.

- [ ] **Step 4: Run tests and verify GREEN**

Run:

```powershell
uv run --project apps/server pytest apps/server/tests/test_evidence_retrieval.py -q
```

Expected: pass.

- [ ] **Step 5: Add failing rerank tests**

Add tests for aggregation and latest-update:

```python
from anima_server.services.agent.evidence_retrieval import rerank_evidence_texts
from anima_server.services.agent.retrieval_intent import RetrievalIntent, RetrievalMode


def test_aggregate_rerank_preserves_distinct_model_kit_evidence() -> None:
    texts = [
        "Session date: 2023/05/21\nUser: I finished a Revell F-15 Eagle kit.",
        "Session date: 2023/05/21\nAssistant: Weathering tips for models.",
        "Session date: 2023/05/27\nUser: I started a 1/16 scale German Tiger I tank diorama.",
        "Session date: 2023/05/29\nUser: I got a 1/72 scale B-29 bomber and a 1/24 scale Camaro.",
    ]
    intent = RetrievalIntent(mode=RetrievalMode.AGGREGATE, candidate_limit=50, max_evidence=3)

    ranked = rerank_evidence_texts(
        texts,
        query="How many model kits have I worked on or bought?",
        intent=intent,
    )

    joined = "\n".join(ranked)
    assert "Revell F-15" in joined
    assert "German Tiger" in joined
    assert "B-29 bomber" in joined
    assert "Camaro" in joined
    assert "Weathering tips" not in joined


def test_latest_update_rerank_prefers_newer_entity_evidence() -> None:
    texts = [
        "Session date: 2023/05/21\nUser: Rachel moved to a new apartment in the city.",
        "Session date: 2023/05/26\nUser: Rachel relocated to the suburbs.",
    ]
    intent = RetrievalIntent(mode=RetrievalMode.LATEST_UPDATE, candidate_limit=40, max_evidence=2)

    ranked = rerank_evidence_texts(
        texts,
        query="Where did Rachel move to after her recent relocation?",
        intent=intent,
    )

    assert "suburbs" in ranked[0]
```

- [ ] **Step 6: Run tests and verify RED**

Run:

```powershell
uv run --project apps/server pytest apps/server/tests/test_evidence_retrieval.py -q
```

Expected: fail because reranker does not exist or does not rank correctly.

- [ ] **Step 7: Implement minimal reranker**

Implement:

- `rerank_evidence_texts(texts: Sequence[str], query: str, intent: RetrievalIntent) -> list[str]`

Scoring hints:

- Base score: term overlap between query terms and text.
- Add boost for `User:` lines.
- Aggregate mode:
  - Boost numeric tokens and item nouns from the query.
  - Prefer distinct session dates.
  - Do not collapse two different items in the same line.
- Latest-update mode:
  - Boost entity terms from query.
  - Boost newer `Session date` after parsing `YYYY/MM/DD`.
- Preference mode:
  - Boost user lines containing `prefer`, `enjoy`, `working in`, `use`, `learn`, `advanced`, `focus`.
- Temporal mode:
  - Boost explicit dates and compared entity terms.

Keep this deterministic and heuristic; do not call an LLM.

- [ ] **Step 8: Run tests and verify GREEN**

Run:

```powershell
uv run --project apps/server pytest apps/server/tests/test_evidence_retrieval.py -q
```

Expected: pass.

- [ ] **Step 9: Commit**

```powershell
git add apps/server/src/anima_server/services/agent/evidence_retrieval.py apps/server/tests/test_evidence_retrieval.py
git commit -m "agent: rerank long-memory evidence"
```

---

### Task 3: Add Wide Evidence Retrieval Function

**Files:**

- Modify: `apps/server/src/anima_server/services/agent/evidence_retrieval.py`
- Test: `apps/server/tests/test_evidence_retrieval.py`

- [ ] **Step 1: Write failing async test with monkeypatched hybrid search**

Test desired behavior without real DB/Rust:

```python
import pytest

from anima_server.services.agent import evidence_retrieval
from anima_server.services.agent.retrieval_intent import RetrievalMode


@pytest.mark.asyncio
async def test_retrieve_wide_evidence_uses_intent_candidate_limit(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    class FakeItem:
        id = 10
        category = "fact"
        content = "Session date: 2023/05/29\nUser: I got a 1/72 scale B-29 bomber."

    class FakeResult:
        query_embedding = [0.1, 0.2]
        items = [(FakeItem(), 0.9)]

    async def fake_hybrid_search(db, **kwargs):
        calls.append(kwargs)
        return FakeResult()

    monkeypatch.setattr(evidence_retrieval, "hybrid_search", fake_hybrid_search)
    monkeypatch.setattr(evidence_retrieval, "df", lambda user_id, value, **kwargs: value)

    result = await evidence_retrieval.retrieve_wide_evidence(
        db=object(),
        user_id=1,
        query="How many model kits have I worked on or bought?",
        runtime_db=None,
    )

    assert result.intent.mode == RetrievalMode.AGGREGATE
    assert calls[0]["limit"] >= 40
    assert result.query_embedding == [0.1, 0.2]
    assert result.semantic_results[0][0] == 10
    assert "B-29 bomber" in result.semantic_results[0][1]
```

- [ ] **Step 2: Run test and verify RED**

Run:

```powershell
uv run --project apps/server pytest apps/server/tests/test_evidence_retrieval.py::test_retrieve_wide_evidence_uses_intent_candidate_limit -q
```

Expected: fail because function/result class is missing.

- [ ] **Step 3: Implement retrieval wrapper**

In `evidence_retrieval.py`, add:

- `WideEvidenceResult` dataclass:
  - `intent: RetrievalIntent`
  - `semantic_results: list[tuple[int, str, float]]`
  - `trace_fragments: list[AgentContextFragment]` or plain intermediate data if importing state types causes cycles.
  - `query_embedding: list[float] | None`
  - `total_considered: int`

- `retrieve_wide_evidence(...)`
  - Classify intent.
  - If direct, return empty result.
  - Call existing `hybrid_search` with:
    - `limit=intent.candidate_limit`
    - `similarity_threshold=0.1`
  - Decrypt content with `df`.
  - Compact and rerank.
  - Return top `intent.max_evidence`.

Do not apply the normal adaptive filter here; the whole point is preserving enough evidence for cross-session reasoning while compacting text.

- [ ] **Step 4: Run test and verify GREEN**

Run:

```powershell
uv run --project apps/server pytest apps/server/tests/test_evidence_retrieval.py -q
```

Expected: pass.

- [ ] **Step 5: Commit**

```powershell
git add apps/server/src/anima_server/services/agent/evidence_retrieval.py apps/server/tests/test_evidence_retrieval.py
git commit -m "agent: retrieve wide evidence for memory questions"
```

---

### Task 4: Add Evidence Prompt Block

**Files:**

- Modify: `apps/server/src/anima_server/services/agent/memory_blocks.py`
- Test: `apps/server/tests/test_agent_memory_blocks.py` or new focused tests in `apps/server/tests/test_evidence_memory_blocks.py`

- [ ] **Step 1: Write failing test for evidence block formatting**

Prefer a small unit test around a new helper:

```python
from anima_server.services.agent.memory_blocks import build_evidence_memory_block


def test_build_evidence_memory_block_uses_task_specific_description() -> None:
    block = build_evidence_memory_block(
        [
            (1, "Session date: 2023/05/29\nUser: I got a 1/72 scale B-29 bomber.", 0.98),
            (2, "Session date: 2023/05/21\nUser: I finished a Revell F-15 Eagle kit.", 0.91),
        ]
    )

    assert block is not None
    assert block.label == "evidence_memories"
    assert "Use this evidence to answer count" in block.description
    assert "B-29 bomber" in block.value
```

- [ ] **Step 2: Run test and verify RED**

Run:

```powershell
uv run --project apps/server pytest apps/server/tests/test_evidence_memory_blocks.py -q
```

Expected: fail because helper does not exist.

- [ ] **Step 3: Implement helper and wire optional argument**

In `memory_blocks.py`:

- Add `build_evidence_memory_block(results, agent_type="companion")`.
- Add optional parameter to `build_runtime_memory_blocks`:
  - `evidence_results: list[tuple[int, str, float]] | None = None`
- If `evidence_results` exists, append `evidence_memories` before ordinary `relevant_memories`.

Description should be explicit:

```text
Evidence retrieved for the current question. Use it to answer count, latest-update, temporal, or preference questions. Prefer user-stated lines and session dates. Do not ask for clarification if the answer is present here.
```

- [ ] **Step 4: Run test and verify GREEN**

Run:

```powershell
uv run --project apps/server pytest apps/server/tests/test_evidence_memory_blocks.py -q
```

Expected: pass.

- [ ] **Step 5: Commit**

```powershell
git add apps/server/src/anima_server/services/agent/memory_blocks.py apps/server/tests/test_evidence_memory_blocks.py
git commit -m "agent: add evidence memory prompt block"
```

---

### Task 5: Wire Evidence Retrieval Into Pre-Turn Agent Flow

**Files:**

- Modify: `apps/server/src/anima_server/services/agent/service.py`
- Test: `apps/server/tests/test_agent_service.py` or a new focused service test.

- [ ] **Step 1: Write failing service-level test**

Test at the smallest available boundary. If direct full `run_agent` setup is heavy, test an extracted helper. Preferred path:

- Extract a helper in `service.py`:
  - `_build_query_aware_memory_context(...)`
  - It returns memory blocks, retrieval trace, query embedding.
- Unit test this helper with monkeypatched `retrieve_wide_evidence` and `build_runtime_memory_blocks`.

Expected behavior:

- For aggregate intent, service passes `evidence_results` into `build_runtime_memory_blocks`.
- Retrieval trace uses `retriever="hybrid_wide_evidence"`.
- For direct intent/no evidence, existing path remains unchanged.

- [ ] **Step 2: Run test and verify RED**

Run the focused test:

```powershell
uv run --project apps/server pytest apps/server/tests/test_agent_service.py::test_pre_turn_uses_wide_evidence_for_aggregate_questions -q
```

Expected: fail because service does not call evidence retrieval.

- [ ] **Step 3: Implement minimal wiring**

In `service.py`, near the existing pre-turn `hybrid_search` block around the current `search_result = await hybrid_search(...)` call:

1. Import lazily inside the try block:

```python
from anima_server.services.agent.evidence_retrieval import retrieve_wide_evidence
```

2. Call `retrieve_wide_evidence` before normal adaptive filtering.

3. If the result has `semantic_results`:

- Set `semantic_results` to compacted evidence results.
- Set `query_embedding` from result.
- Build retrieval trace with evidence fragments.
- Skip normal adaptive filter for this turn.

4. If no wide evidence applies, use the existing code path unchanged.

Keep exception handling best-effort, matching current retrieval behavior.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run:

```powershell
uv run --project apps/server pytest apps/server/tests/test_agent_service.py::test_pre_turn_uses_wide_evidence_for_aggregate_questions apps/server/tests/test_evidence_retrieval.py apps/server/tests/test_retrieval_intent.py -q
```

Expected: pass.

- [ ] **Step 5: Commit**

```powershell
git add apps/server/src/anima_server/services/agent/service.py apps/server/tests/test_agent_service.py
git commit -m "agent: use wide evidence for complex memory questions"
```

---

### Task 6: Add Eval Debug Metadata For Retrieval Mode

**Files:**

- Modify: `apps/server/eval/run_longmemeval.py` only if needed.
- Test: `apps/server/tests/test_eval_harness.py`

- [ ] **Step 1: Inspect existing result JSON**

Check whether `/api/chat` retrieval trace already includes the new retriever string:

```powershell
$j = Get-Content apps/server/eval/results/longmemeval_oracle_raw_20_haiku45_mixed_temp0.json -Raw | ConvertFrom-Json
$j.results[0].retrieval.retriever
```

If the new trace will show `hybrid_wide_evidence`, no runner change is needed.

- [ ] **Step 2: Add test only if runner needs a new summary field**

Possible useful summary:

- `summary.retrievers.hybrid`
- `summary.retrievers.hybrid_wide_evidence`

This is optional. Do not add it unless useful for benchmark analysis.

- [ ] **Step 3: Commit if changed**

```powershell
git add apps/server/eval/run_longmemeval.py apps/server/tests/test_eval_harness.py
git commit -m "eval: summarize retrieval modes"
```

---

### Task 7: Run Verification

**Files:** no source edits.

- [ ] **Step 1: Run ruff on touched Python files**

```powershell
uv run --project apps/server ruff check apps/server/src/anima_server/services/agent/retrieval_intent.py apps/server/src/anima_server/services/agent/evidence_retrieval.py apps/server/src/anima_server/services/agent/service.py apps/server/src/anima_server/services/agent/memory_blocks.py apps/server/tests/test_retrieval_intent.py apps/server/tests/test_evidence_retrieval.py
```

Expected: `All checks passed!`

- [ ] **Step 2: Run focused tests**

```powershell
uv run --project apps/server pytest apps/server/tests/test_retrieval_intent.py apps/server/tests/test_evidence_retrieval.py apps/server/tests/test_evidence_memory_blocks.py apps/server/tests/test_agent_service.py -q
```

Expected: all selected tests pass.

- [ ] **Step 3: Run server suite**

```powershell
bun run test:server
```

Expected: full server test suite passes. Previous baseline was `1328 passed, 1 skipped`.

- [ ] **Step 4: Run repo lint**

```powershell
bun run lint
```

Expected: server ruff passes and desktop typecheck passes.

- [ ] **Step 5: Run build**

```powershell
bun run build
```

Expected: server wheel/sdist builds and desktop builds. Existing desktop chunk-size warning is acceptable.

---

### Task 8: Run Mixed Benchmark Again

**Files:** result JSON only.

Use a disposable eval server. Do not run eval reset against personal development data.

- [ ] **Step 1: Start a disposable server**

Use the same shape as the previous run:

```powershell
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$dataDir = Join-Path 'C:\tmp' "anima-haiku-eval-evidence-temp0-$stamp"
New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
$env:ANIMA_DATA_DIR = $dataDir
$env:ANIMA_DATABASE_URL = 'sqlite:///' + (($dataDir -replace '\\','/') + '/anima.db')
$env:ANIMA_RUNTIME_DATABASE_URL = ''
$env:ANIMA_PORT = '3032'
$env:ANIMA_EVAL_RESET_ENABLED = 'true'
$env:ANIMA_CORE_REQUIRE_ENCRYPTION = 'false'
$env:ANIMA_AGENT_BACKGROUND_MEMORY_ENABLED = 'false'
$env:ANIMA_AGENT_PROVIDER = 'anthropic'
$env:ANIMA_AGENT_MODEL = 'claude-haiku-4-5-20251001'
$env:ANIMA_AGENT_EMBEDDING_PROVIDER = 'ollama'
$env:ANIMA_AGENT_EMBEDDING_MODEL = 'nomic-embed-text'
$env:ANIMA_AGENT_MAX_TOKENS = '2048'
$env:ANIMA_AGENT_TEMPERATURE = '0'
uv run --project apps/server uvicorn anima_server.main:app --host 127.0.0.1 --port 3032
```

If running in the background, capture the server PID and stop it after scoring.

- [ ] **Step 2: Run mixed 20q benchmark**

```powershell
uv run --project apps/server python apps/server/eval/run_longmemeval.py --base-url http://127.0.0.1:3032 --create-user --dataset oracle --sample mixed --limit 20 --config raw_20_haiku45_mixed_temp0_evidence --import-mode raw_chunks --output apps/server/eval/results/longmemeval_oracle_raw_20_haiku45_mixed_temp0_evidence.json
```

Expected runtime target: under 5 minutes for 20 questions.

- [ ] **Step 3: Score with local judge**

```powershell
uv run --project apps/server python apps/server/eval/score_results.py apps/server/eval/results/longmemeval_oracle_raw_20_haiku45_mixed_temp0_evidence.json --model qwen3.5:latest --force
```

Expected improvement target:

- Minimum useful target: `>= 13/20`
- Good first-pass target: `>= 14/20`
- Watch specifically:
  - `multi-session` should improve from `0/4`
  - `single-session-preference` should improve from `1/3`
  - `knowledge-update` should improve from `1/3`

- [ ] **Step 4: Compare against baseline**

Use:

```powershell
$baseline = Get-Content apps/server/eval/results/longmemeval_oracle_raw_20_haiku45_mixed_temp0.json -Raw | ConvertFrom-Json
$new = Get-Content apps/server/eval/results/longmemeval_oracle_raw_20_haiku45_mixed_temp0_evidence.json -Raw | ConvertFrom-Json
[pscustomobject]@{
  baseline = "$($baseline.summary.correct)/$($baseline.summary.scored)"
  new = "$($new.summary.correct)/$($new.summary.scored)"
  baselineAccuracy = $baseline.summary.accuracy
  newAccuracy = $new.summary.accuracy
  newCost = $new.summary.totalCostUsd
}
```

- [ ] **Step 5: Stop eval server**

```powershell
netstat -ano | Select-String ':3032'
Stop-Process -Id <LISTENING_PID> -Force
```

Verify port is clear:

```powershell
netstat -ano | Select-String ':3032'
```

Expected: no `LISTENING` row.

---

## If The Score Does Not Improve

Stop after one failed implementation attempt and inspect evidence before changing heuristics.

Debug checklist:

- Did failed multi-session questions use `hybrid_wide_evidence`?
- Did the retrieval trace include all expected evidence chunks?
- Did compaction remove the answer-bearing phrase?
- Did reranking over-favor assistant text?
- Did token usage rise too much?
- Are expected answer chunks absent from the Rust index, or present but ranked low?

If evidence is absent from candidate pool:

- Increase candidate limit only for `AGGREGATE` to 80 and retest one question.
- Consider adding a Rust query expansion function for noun/entity variants.

If evidence is present but model still answers wrong:

- Improve prompt block description.
- Add a final line like: `For count questions, enumerate evidence items before answering.`
- Keep this local to `evidence_memories`, not the global system prompt.

If latest-update remains weak:

- Add structured session date parsing in Python reranker.
- Sort same-entity evidence newest first.

If preference remains weak:

- Add a preference-signal boost for user lines containing domain/workflow indicators.

---

## Future Phase, Not In This Plan

A stronger long-term architecture is an event-memory schema:

- `event_date`
- `subject`
- `predicate`
- `object`
- `quantity`
- `source_session_date`
- `confidence`
- supersession/update links

That likely improves LongMemEval and LoCoMo more cleanly, but it needs a migration, extraction changes, and a backfill path. Do not start that until this migration-free evidence retrieval pass proves where the gains are.
