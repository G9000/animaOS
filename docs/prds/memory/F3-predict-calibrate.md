---
title: "PRD: F3 — Predict-Calibrate Consolidation"
description: Predictive consolidation with calibration feedback for memory quality
category: prd
version: "1.0"
---

# PRD: F3 — Predict-Calibrate Consolidation

**Version**: 1.0
**Date**: 2026-03-18
**Status**: Draft
**Roadmap Phase**: 10.3
**Priority**: P1
**Depends on**: F1 (Hybrid Search) — uses `hybrid_search()` to retrieve relevant existing facts
**Blocks**: None directly; F5 orchestrates this as a background task

---

## 1. Overview

Augment the LLM memory extraction pipeline with a predict-then-extract cycle inspired by the Free Energy Principle (Nemori). Instead of extracting facts from a conversation cold — producing many duplicates that downstream dedup catches — first predict what the conversation likely contains based on existing knowledge, then extract only the delta: surprises, corrections, and genuinely new information.

This produces higher-quality facts, reduces redundant LLM token usage, and catches contradictions at extraction time rather than downstream.

---

## 2. Problem Statement

### Current Implementation

`consolidation.py` pipeline:

1. `consolidate_turn_memory()` — regex fast path (patterns for age, birthday, occupation, etc.)
2. `consolidate_turn_memory_with_llm()` — calls `extract_memories_via_llm()` with `EXTRACTION_PROMPT`
3. `extract_memories_via_llm()` — prompts the LLM with the conversation, asks for all extractable facts
4. `resolve_conflict()` — LLM-based UPDATE/DIFFERENT classification for similar items
5. `store_memory_item()` — built-in duplicate/update/similar detection

### The Gap

`extract_memories_via_llm()` has **zero awareness of existing knowledge**. It extracts everything from scratch every time:

| Scenario | What happens now | What should happen |
|----------|------------------|--------------------|
| User mentions their job for the 5th time | LLM extracts "User works at Google" again; `store_memory_item()` detects the duplicate and skips | LLM knows it already has this fact, extracts nothing |
| User says "actually I moved to Berlin" (contradiction) | LLM extracts "User lives in Berlin"; `resolve_conflict()` catches the contradiction with "User lives in Munich" downstream | LLM's prediction says "User lives in Munich"; the delta extraction flags this as a contradiction immediately |
| User has a casual conversation with no new facts | LLM extracts 3-4 generic statements ("User likes chatting", "User is friendly"); `store_memory_item()` filters most | LLM predicts "nothing specific expected"; quality gates reject generic statements |

**Cost**: Wasted LLM tokens on redundant extraction. Missed opportunity for early contradiction detection. Low-quality facts passing initial extraction only to be caught by downstream filters.

### Evidence

| Source | Finding |
|--------|---------|
| Nemori `prediction_correction_engine.py` | Two-step: `_predict_episode()` → `_extract_knowledge_from_comparison()` produces higher-quality semantic memories |
| Nemori `semantic_generator.py` | `check_and_generate()` routes to prediction-correction when existing knowledge exists, direct extraction otherwise |
| Free Energy Principle (Friston) | Learning = prediction error minimization. Extract what surprises you, not what you already know. |

---

## 3. Goals and Non-Goals

### Goals

1. Predict expected conversation content from existing knowledge before extraction
2. Extract only the delta: genuinely new, surprising, or contradictory information
3. Apply quality gates (persistence, specificity, utility, independence) to filter low-value extractions
4. Cold-start fallback to direct extraction when < 5 existing facts
5. Reduce net LLM token usage per turn in steady state

### Non-Goals

- Changing the regex fast path — that stays as the quick, free extraction layer
- Modifying `store_memory_item()` or `resolve_conflict()` — those remain as safety nets
- Changing the data model — this is a pipeline change only
- Auto-tuning quality gate thresholds

---

## 4. Detailed Design

### 4.1 Pipeline Flow

```
User message + Assistant response
         |
         v
[1] Retrieve relevant existing facts (via hybrid_search from F1)
         |
         v
[2] Predict: "Given these existing facts and this conversation topic,
              what new facts would you expect?"
         |
         v
[3] Extract delta: "Given the prediction vs actual conversation,
                    extract ONLY surprising/new/contradictory facts"
         |
         v
[4] Quality gates: persistence, specificity, utility, independence
         |
         v
[5] Output → store_memory_item() pipeline (existing dedup as safety net)
```

**Cold-start path** (< 5 existing facts): Skip steps 1-2, go directly to `extract_memories_via_llm()` (current behavior), then apply quality gates.

### 4.2 New File

```
apps/server/src/anima_server/services/agent/predict_calibrate.py
```

### 4.3 LLM Prompts

**PREDICTION_PROMPT** (Step 2):
```
Given these existing facts about the user:
{existing_facts}

And this conversation summary:
{conversation_summary}

Predict what new facts or information this conversation likely contains.
Focus on what would be EXPECTED given what you already know.
Be specific. If you expect nothing new, say "no new facts expected."
```

**DELTA_EXTRACTION_PROMPT** (Step 3):
```
PREDICTION (what was expected):
{prediction}

ACTUAL CONVERSATION:
User: {user_message}
Assistant: {assistant_response}

Extract ONLY statements that are:
- SURPRISING: not predicted, genuinely new information
- CONTRADICTORY: conflicts with or updates a prediction
- CORRECTIVE: the user explicitly corrects something

Do NOT extract:
- Information that matches the prediction (already known)
- Vague or generic statements
- Opinions about the conversation itself

Return as JSON array of {content, category, confidence, reason}.
```

**KNOWLEDGE_QUALITY_PROMPT** (Step 4):
```
For each statement, apply these tests. Remove any that fail:

1. PERSISTENCE: Will this still be true in 6 months?
   (Reject: "User is tired today", "User is working on a report")
2. SPECIFICITY: Does it contain concrete, searchable information?
   (Reject: "User likes food", "User mentioned something about work")
3. UTILITY: Can it help predict future needs or personalize responses?
   (Reject: "User said hello", "User asked a question")
4. INDEPENDENCE: Can it be understood without the conversation context?
   (Reject: "User agreed with that", "The thing we discussed")
```

### 4.4 Function Signatures

```python
async def predict_episode_knowledge(
    *,
    existing_facts: list[str],
    conversation_summary: str,
) -> str:
    """Predict what knowledge a conversation likely contains.
    Uses low temperature (0.3) for conservative predictions.
    """
    ...

async def extract_knowledge_delta(
    *,
    user_message: str,
    assistant_response: str,
    prediction: str,
) -> list[dict[str, Any]]:
    """Extract only the delta between prediction and actual conversation.
    Returns [{content, category, confidence, reason}, ...]
    """
    ...

async def apply_quality_gates(
    *,
    statements: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Filter statements through persistence, specificity, utility, independence tests.
    Can be done via LLM or via heuristic rules.
    """
    ...

async def predict_calibrate_extraction(
    *,
    user_id: int,
    user_message: str,
    assistant_response: str,
    db: Session,
) -> list[dict[str, Any]]:
    """Full predict-calibrate pipeline:
    1. Retrieve relevant existing facts (via hybrid_search)
    2. Predict expected knowledge
    3. Extract delta
    4. Apply quality gates
    Returns list of high-quality memory items to store.
    """
    ...
```

### 4.5 Modified Files

| File | Function | Change |
|------|----------|--------|
| `consolidation.py` | `consolidate_turn_memory_with_llm()` | When existing facts > 5: call `predict_calibrate_extraction()`. When < 5: keep current `extract_memories_via_llm()` (cold-start path). |
| `consolidation.py` | `EXTRACTION_PROMPT` | Keep as-is for cold-start mode. |

### 4.6 Integration Points

- **F1 (Hybrid Search)**: `predict_calibrate_extraction()` calls `hybrid_search()` to find relevant existing facts for the prediction step. Without F1, it could fall back to `get_memory_items_scored()` but with worse relevance.
- **Storage**: Output feeds into the existing `store_memory_item()` + `upsert_claim()` pipeline. Dedup, conflict resolution, and claims dual-write all still apply as safety nets. The predict-calibrate output must return items in the same `list[dict]` format (with `content`, `category`, `importance` keys) consumed by the downstream pipeline.
- **Emotional signal extraction**: The current `consolidate_turn_memory_with_llm()` extracts emotional signals alongside memories (lines 244-263). When predict-calibrate replaces `extract_memories_via_llm()`, emotional signal extraction must be preserved. The predict-calibrate path must either (a) include emotion extraction in its delta extraction prompt, or (b) run the existing emotion extraction as a separate call. Recommendation: include a `detected_emotion` field in the delta extraction prompt output, matching the existing `LLMExtractionResult.emotion` shape.
- **Background execution**: This runs inside `run_background_memory_consolidation()` — invisible to the user.
- **Token budget**: Net reduction in steady state. Two smaller, focused LLM calls instead of one broad extraction call, producing fewer but higher-quality items.

### 4.7 Quality Gate Implementation Options

Two approaches for the quality gates:

**Option A — LLM-based** (more accurate, costs tokens):
Send extracted statements through `KNOWLEDGE_QUALITY_PROMPT` for the LLM to filter.

**Option B — Heuristic-based** (free, less nuanced):
- Persistence: reject if content contains temporal markers ("today", "right now", "currently")
- Specificity: reject if content < 5 words or contains no proper nouns / numbers
- Utility: reject if content starts with "User said" or "User asked"
- Independence: reject if content contains pronouns without antecedents ("that", "it", "the thing")

**Recommendation**: Start with Option B (heuristics) and upgrade to Option A if precision is insufficient. The downstream `store_memory_item()` dedup catches anything that slips through.

---

## 5. Requirements

| ID | Requirement | Priority |
|----|-------------|----------|
| F3.1 | `predict_episode_knowledge()` generating predictions from existing facts + conversation summary | Must |
| F3.2 | `extract_knowledge_delta()` extracting only novel/contradictory/surprising statements vs prediction | Must |
| F3.3 | `apply_quality_gates()` filtering through 4 tests: persistence, specificity, utility, independence | Must |
| F3.4 | `predict_calibrate_extraction()` orchestrating the full pipeline with `hybrid_search()` retrieval | Must |
| F3.5 | Cold-start fallback when existing facts < 5 (use current `extract_memories_via_llm()`) | Must |
| F3.6 | `consolidate_turn_memory_with_llm()` routes to predict-calibrate when facts > 5 | Must |
| F3.7 | Low temperature (0.3) for prediction prompt | Should |
| F3.8 | JSON-structured output from delta extraction prompt | Must |
| F3.9 | Error handling: if predict-calibrate fails, fall back to direct extraction | Must |
| F3.10 | Skip predict-calibrate for short conversations (< 3 user messages) | Should |
| F3.11 | UUID/ID hallucination protection — when sending existing facts to LLM prompts, map real memory IDs to sequential integers and map back after response (adopted from Mem0) | Must |
| F3.12 | Emotional signal extraction must be preserved — predict-calibrate path must output `detected_emotion` alongside extracted facts, matching the existing `LLMExtractionResult.emotion` format | Must |

---

## 6. Data Model Changes

**None.** This is a pipeline-only change.

- Migration count: **0**
- New tables: **0**
- Modified tables: **0**

---

## 7. Acceptance Criteria

| # | Criterion | Verification |
|---|-----------|--------------|
| AC1 | When the user mentions their job for the 5th time, the system extracts zero new facts | Integration test: store "User works at Google", repeat in conversation, verify empty delta |
| AC2 | When the user contradicts a previous fact ("I moved to Berlin"), the system extracts the correction as delta | Integration test: store "User lives in Munich", contradiction in conversation, verify "User lives in Berlin" extracted with reason "contradictory" |
| AC3 | Quality gates reject "we talked about food" (not specific) | Unit test for `apply_quality_gates()` |
| AC4 | Quality gates accept "User's favorite restaurant is Sushi Dai in Tokyo" | Unit test for `apply_quality_gates()` |
| AC5 | Cold-start mode (< 5 facts) uses direct extraction | Unit test: mock empty memory store, verify `extract_memories_via_llm()` is called |
| AC6 | Net LLM token usage per turn decreases in steady state (after 20+ facts) | Measurement: compare token counts before/after with same conversation set |
| AC7 | All 602 existing tests pass | CI |

---

## 8. Test Plan

| # | Type | Test | Details |
|---|------|------|---------|
| T1 | Unit | `predict_episode_knowledge()` | Given known facts + topic, verify prediction is reasonable (mock LLM) |
| T2 | Unit | `extract_knowledge_delta()` | Given prediction + conversation with surprises, verify delta contains only new info |
| T3 | Unit | `apply_quality_gates()` — heuristic mode | Feed temporal statements, vague statements, specific statements; verify correct filtering |
| T4 | Unit | Cold-start path | When no existing facts, verify fallback to direct extraction |
| T5 | Unit | Error handling | When predict-calibrate LLM call fails, verify fallback to direct extraction |
| T6 | Integration | Full pipeline | Run `consolidate_turn_memory_with_llm()` with predict-calibrate, verify extracted facts are novel |
| T7 | Integration | Contradiction detection | Store known fact, send contradicting conversation, verify correction extracted |
| T8 | Integration | Redundant mention | Store known fact, send conversation mentioning same fact, verify no new extraction |
| T9 | Regression | Existing consolidation tests | All 602 tests pass |

---

## 9. Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| **LLM prompt sensitivity** | Medium | Low temperature (0.3) for conservative predictions. Downstream `store_memory_item()` dedup catches errors. |
| **Latency** (two sequential LLM calls instead of one) | Low | Runs in background (`run_background_memory_consolidation()`), invisible to user. Can skip for short conversations (< 3 exchanges). |
| **Over-filtering** (quality gates too aggressive) | Medium | Start with heuristic gates (less aggressive). `store_memory_item()` dedup is the real safety net. Monitor extraction counts and loosen if needed. |
| **Integration with existing consolidation** | Medium | `consolidation.py` has careful error handling and fallback paths. The predict-calibrate call must be wrapped in the same try/except patterns. Fallback to direct extraction on any error. |
| **Prediction hallucination** | Low | Predictions that hallucinate facts will show those facts as "already known" → they won't be extracted. This is a false negative (missed fact), not a false positive (wrong fact). Safer direction to err. |
| **LLM ID hallucination** | Medium | LLMs may hallucinate or corrupt memory IDs when existing facts are listed in prompts. Mitigated by mapping real IDs to sequential integers before LLM calls (F3.11), adopted from Mem0. |

---

## 10. Rollout

1. Create `predict_calibrate.py` with all functions and prompts
2. Write unit tests for prediction, delta extraction, and quality gates
3. Modify `consolidate_turn_memory_with_llm()` in `consolidation.py` to route through predict-calibrate
4. Write integration tests for the full pipeline
5. Run full test suite (602+ tests)
6. Ship as single PR

No migration needed. No feature flag needed — cold-start fallback ensures the old path is preserved for new users or fresh databases.

---

## 11. References

- Nemori `prediction_correction_engine.py` — `learn_from_episode_simplified()`, `_predict_episode()`, `_extract_knowledge_from_comparison()`
- Nemori `semantic_generator.py` — cold-start mode routing
- Friston, K. (2010). The free-energy principle: a unified brain theory?
- [Implementation Plan Phase 3](../memory-implementation-plan.md) — detailed function signatures
