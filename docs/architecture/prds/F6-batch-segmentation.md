# PRD: F6 — Batch Episode Segmentation

**Version**: 1.0
**Date**: 2026-03-18
**Status**: Draft
**Roadmap Phase**: 10.7
**Priority**: P2
**Depends on**: None (standalone); benefits from F5 for orchestration
**Blocks**: Nothing

---

## 1. Overview

Replace fixed-size episode chunking (every N contiguous turns = one episode) with LLM-driven topic-coherent segmentation. When a conversation buffer exceeds a threshold (8 messages), the LLM groups messages into non-contiguous topic episodes. Messages about "work" scattered across positions 1-3 and 8-11 become one episode; messages about "cooking" at positions 4-7 become another.

This produces episodes that reflect actual conversation structure rather than arbitrary turn boundaries, improving episodic memory retrieval quality.

---

## 2. Problem Statement

### Current Implementation

`episodes.py`:
- `maybe_generate_episode()` — checks if `remaining_logs >= EPISODE_MIN_TURNS` (3)
- Takes up to `EPISODE_MIN_TURNS * 2` (6) contiguous logs
- Calls `_generate_episode_via_llm()` → LLM summarizes those turns → one episode
- `_create_fallback_episode()` — basic episode when LLM fails
- Episodes track `turn_count` and consume turns **sequentially** (offset-based)

### The Gap

Episodes always contain contiguous turns. If a conversation switches topics:

```
Message 1: "How's my project going?"    → work
Message 2: "Let me check..."           → work
Message 3: "Good progress on Aurora."   → work
Message 4: "What should I cook tonight?" → cooking
Message 5: "How about pasta..."         → cooking
Message 6: "Great idea."               → cooking
Message 7: "Back to work — any blockers?" → work
Message 8: "Yes, the API integration."  → work
```

**Current behavior**: Episode 1 = messages 1-6 (work + cooking mixed). Episode 2 = messages 7-8 (work only).

**Desired behavior**: Episode A = messages 1-3, 7-8 (all work). Episode B = messages 4-6 (all cooking).

### Impact

- Mixed-topic episodes produce incoherent summaries ("We discussed Project Aurora progress and pasta recipes")
- Episodic memory retrieval for "work" returns an episode that's 50% cooking content
- Episode significance scores are diluted by topic mixing

### Evidence

| Source | Finding |
|--------|---------|
| Nemori `batch_segmenter.py` | `segment_batch()` returns `List[List[int]]` — groups of message indices, non-continuous allowed |
| Nemori | Low temperature (0.2) for consistent segmentation |
| Nemori | Fallback to single group on LLM failure |
| Event Segmentation Theory (Zacks & Swallow, 2007) | Humans naturally segment continuous experience into discrete events at topic/context boundaries |

---

## 3. Goals and Non-Goals

### Goals

1. LLM-driven topic segmentation when message buffer exceeds threshold (8 messages)
2. Non-contiguous message grouping: messages from different positions can form one episode
3. Multiple episodes generated per segmentation batch (one per topic group)
4. Backward compatible: < 8 messages still use sequential method
5. Tracking: `segmentation_method` column records which method was used per episode

### Non-Goals

- Real-time event boundary detection during conversation (that's a different architecture)
- Changing episode content or summary format — only changing how turns are grouped before summarization
- Changing how episodes are displayed or retrieved — `build_episodes_memory_block()` reads by `created_at`, unchanged
- Sub-message segmentation (splitting individual messages) — grouping is at the message level

---

## 4. Detailed Design

### 4.1 Segmentation Flow

```
maybe_generate_episode() called (existing trigger)
       |
       v
  remaining_logs >= BATCH_THRESHOLD (8)?
       |
  No --+--> use current sequential method (take 6 contiguous turns)
       |
  Yes -+--> segment_messages_batch(logs)
              |
              v
         LLM returns [[1,2,3,7,8], [4,5,6]] (topic groups)
              |
              v
         validate_indices(groups, total_logs)
              |
              v
         for each group:
              generate_episodes_from_segments()
              → create one episode per group with message_indices_json
```

### 4.2 LLM Segmentation Prompt

```
You are grouping conversation messages by topic coherence.

Messages:
[1] User: "How's my project going?"
[1] Assistant: "Let me check..."
[2] User: "What should I cook tonight?"
[2] Assistant: "How about pasta..."
[3] User: "Back to work — any blockers?"
[3] Assistant: "Yes, the API integration."

Group these messages by topic. Messages about the same topic should be in the
same group, even if they are not consecutive. Return groups as a JSON array of
arrays of message numbers.

Example output: [[1, 3], [2]]

Rules:
- Every message number must appear in exactly one group
- Groups can contain non-consecutive numbers
- Each group should contain messages about a coherent topic
- Aim for 2-5 groups (don't over-segment)
```

**Temperature**: 0.2 (deterministic, consistent groupings)

### 4.3 New File

```
apps/server/src/anima_server/services/agent/batch_segmenter.py
```

### 4.4 Core Functions

```python
BATCH_SEGMENTATION_PROMPT: str = """..."""
BATCH_THRESHOLD: int = 8  # Min messages for batch segmentation

async def segment_messages_batch(
    messages: list[tuple[str, str]],  # (user_message, assistant_response) pairs
    *,
    user_id: int = 0,
) -> list[list[int]]:
    """Use LLM to group messages into topic-coherent episodes.

    Returns list of episode groups, each a list of 1-based message indices.
    Non-continuous indices are allowed: [[1,2,3], [4,5], [6,8], [7,9]]

    Falls back to single-group if LLM fails or output is unparseable.
    """
    ...

def should_batch_segment(buffer_size: int) -> bool:
    """True if buffer_size >= BATCH_THRESHOLD."""
    ...

def validate_indices(
    groups: list[list[int]],
    total_messages: int,
) -> bool:
    """Validate that:
    1. All indices from 1..total_messages appear exactly once
    2. No index is out of range
    3. No index appears in multiple groups
    Returns True if valid.
    """
    ...

def indices_to_0based(groups: list[list[int]]) -> list[list[int]]:
    """Convert 1-based LLM indices to 0-based Python indices."""
    ...

async def generate_episodes_from_segments(
    db: Session,
    *,
    user_id: int,
    thread_id: int | None,
    logs: list[MemoryDailyLog],
    segments: list[list[int]],
    today: str,
) -> list[MemoryEpisode]:
    """Generate one episode per segment group.
    Each episode:
    - Contains only the logs at the specified indices
    - Records message_indices_json (1-based)
    - Records segmentation_method='batch_llm'
    - Gets its own LLM-generated summary via existing _generate_episode_via_llm()
    """
    ...
```

### 4.5 Data Model Changes

**Add columns to `MemoryEpisode`:**

```python
message_indices_json: Mapped[list[int] | None] = mapped_column(
    JSON, nullable=True
)  # 1-based indices of included logs

segmentation_method: Mapped[str] = mapped_column(
    String(20), nullable=False, default="sequential"
)  # "sequential" or "batch_llm"
```

### 4.6 Modified Files

| File | Function | Change |
|------|----------|--------|
| `episodes.py` | `maybe_generate_episode()` | When `remaining_logs >= BATCH_THRESHOLD`: call `segment_messages_batch()`, then `generate_episodes_from_segments()`. Fall back to current sequential logic on failure. |
| `episodes.py` | `EPISODE_GENERATION_PROMPT` | Keep as-is — used for per-segment summary generation |
| `models/agent_runtime.py` | `MemoryEpisode` | Add `message_indices_json` and `segmentation_method` columns |

### 4.7 Backward Compatibility

| Scenario | Behavior |
|----------|----------|
| < 8 messages in buffer | Sequential method (current). `segmentation_method='sequential'`, `message_indices_json=null`. |
| >= 8 messages, LLM succeeds | Batch segmentation. Multiple episodes created. `segmentation_method='batch_llm'`. |
| >= 8 messages, LLM fails | Fallback: all messages become one episode. `segmentation_method='sequential'`. |
| >= 8 messages, index validation fails | Fallback: all messages become one episode. `segmentation_method='sequential'`. |
| Existing episodes (pre-migration) | `segmentation_method='sequential'` (column default), `message_indices_json=null`. No backfill needed. |

### 4.8 Index Off-by-One Safety

Nemori uses 1-based indices in LLM prompts (humans count from 1). Python uses 0-based. Explicit conversion:

```python
def indices_to_0based(groups: list[list[int]]) -> list[list[int]]:
    return [[i - 1 for i in group] for group in groups]
```

Validation runs **before** conversion (on 1-based indices matching 1..N). Conversion happens only when accessing `logs[index]`.

### 4.9 Turn Count Semantics

With batch segmentation, `turn_count` becomes the number of messages **in the segment group** (which may not be contiguous). The offset calculation in `maybe_generate_episode()` uses `sum(turn_count)` across all episodes generated in this batch, so the log pointer advances past all consumed messages.

Example: 10 messages → segments [[1,2,3,7,8], [4,5,6], [9,10]]
- Episode A: turn_count=5, message_indices_json=[1,2,3,7,8]
- Episode B: turn_count=3, message_indices_json=[4,5,6]
- Episode C: turn_count=2, message_indices_json=[9,10]
- Total consumed: 10 (pointer advances by 10)

---

## 5. Requirements

| ID | Requirement | Priority |
|----|-------------|----------|
| F6.1 | `segment_messages_batch()` sending numbered messages to LLM, receiving `list[list[int]]` of topic groups | Must |
| F6.2 | Non-contiguous index support: `[[1,2,3], [8,10,11], [4,5,6,7,9,12]]` is valid | Must |
| F6.3 | `BATCH_THRESHOLD` config (default: 8) gating when batch segmentation activates | Must |
| F6.4 | Low temperature (0.2) for deterministic segmentation | Must |
| F6.5 | Fallback to single-episode on LLM failure or parse error | Must |
| F6.6 | `validate_indices()`: all indices 1..N covered, no duplicates, no out-of-range | Must |
| F6.7 | `message_indices_json` column on `MemoryEpisode` (1-based indices) | Must |
| F6.8 | `segmentation_method` column on `MemoryEpisode` (`sequential` or `batch_llm`) | Must |
| F6.9 | `maybe_generate_episode()` routes to batch segmentation when buffer >= threshold | Must |
| F6.10 | Sequential method preserved for < 8 messages | Must |
| F6.11 | `indices_to_0based()` explicit conversion with validation | Must |
| F6.12 | `generate_episodes_from_segments()` creates one episode per segment group, each with own LLM summary | Must |
| F6.13 | Log pointer advances by total messages consumed across all segments | Must |
| F6.14 | Existing episodes (pre-migration) remain valid with default `segmentation_method='sequential'` | Must |

---

## 6. Data Model Changes

**Migration**: `20260322_0001_add_episode_segmentation_columns.py`

```python
def upgrade():
    op.add_column("memory_episodes", sa.Column("message_indices_json", sa.JSON, nullable=True))
    op.add_column("memory_episodes", sa.Column("segmentation_method", sa.String(20), nullable=False, server_default="sequential"))

def downgrade():
    op.drop_column("memory_episodes", "segmentation_method")
    op.drop_column("memory_episodes", "message_indices_json")
```

- New columns: **2** (`message_indices_json`, `segmentation_method`)
- New tables: **0**

---

## 7. Acceptance Criteria

| # | Criterion | Verification |
|---|-----------|--------------|
| AC1 | A 12-message conversation about "work, cooking, work" produces 2 episodes: one about work (non-contiguous messages) and one about cooking | Integration test with mock LLM |
| AC2 | Episodes with < 8 messages use sequential method | Unit test: 6 messages → sequential |
| AC3 | `segmentation_method` is `batch_llm` for batch-segmented episodes | Integration test |
| AC4 | `segmentation_method` is `sequential` for sequentially-created episodes | Integration test |
| AC5 | `message_indices_json` correctly stores 1-based indices for each episode | Integration test |
| AC6 | LLM timeout/failure falls back to single episode | Unit test: mock LLM timeout |
| AC7 | Malformed LLM response (wrong indices) falls back to single episode | Unit test: mock response with missing/duplicate indices |
| AC8 | No messages are dropped: `validate_indices()` rejects groups that don't cover all messages | Unit test |
| AC9 | Log pointer advances by total consumed messages (no re-processing or skipping) | Integration test |
| AC10 | Existing episodes (pre-migration) have `segmentation_method='sequential'` and `message_indices_json=null` | Migration test |
| AC11 | All 602 existing tests pass | CI |

---

## 8. Test Plan

| # | Type | Test | Details |
|---|------|------|---------|
| T1 | Unit | `should_batch_segment()` | 7 → false, 8 → true, 15 → true |
| T2 | Unit | `validate_indices()` — valid | `[[1,2,3], [4,5]]` with total=5 → true |
| T3 | Unit | `validate_indices()` — missing index | `[[1,2], [4,5]]` with total=5 → false (3 missing) |
| T4 | Unit | `validate_indices()` — duplicate | `[[1,2,3], [3,4,5]]` with total=5 → false (3 duplicated) |
| T5 | Unit | `validate_indices()` — out of range | `[[1,2,6]]` with total=5 → false |
| T6 | Unit | `indices_to_0based()` | `[[1,2,3], [4,5]]` → `[[0,1,2], [3,4]]` |
| T7 | Unit | `segment_messages_batch()` | Mock LLM with known groups, verify correct parsing |
| T8 | Unit | `segment_messages_batch()` — LLM failure | Mock LLM exception → returns `[[1,2,...,N]]` (single group) |
| T9 | Unit | Non-contiguous indices | `[[1,2,3], [4,5], [6,8], [7,9]]` → verify handling |
| T10 | Integration | `maybe_generate_episode()` with 10 logs | Verify multiple episodes created with correct `message_indices_json` |
| T11 | Integration | `segmentation_method` column | Verify batch episodes have `batch_llm`, sequential have `sequential` |
| T12 | Integration | Log pointer advancement | 10 messages → 3 episodes → offset advances by 10 |
| T13 | Regression | Episodes with < 8 logs | Verify still uses sequential method |
| T14 | Regression | Full suite | All 602 tests pass |

---

## 9. Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| **LLM segmentation quality** | Medium | Low temperature (0.2) for consistency. Fallback to single episode on parse error. Validation ensures no dropped messages. |
| **Index off-by-one** | Medium | Explicit `indices_to_0based()` conversion. Validation runs on 1-based indices before conversion. Clear separation between LLM-facing (1-based) and Python-facing (0-based) representations. |
| **LLM response parsing** | Medium | Expect JSON array of arrays. Multiple fallback parse attempts (strip markdown fences, try `json.loads`). On any failure, fall back to single group. |
| **Over-segmentation** | Low | Prompt instructs "aim for 2-5 groups". Very short conversations (< 8 messages) bypass segmentation entirely. |
| **Turn count semantics** | Low | `turn_count` = number of messages in the segment group. Log pointer advancement uses `sum(turn_count)` across all episodes in the batch. Documented and tested. |
| **Backward compatibility** | Low | Column defaults ensure existing episodes are valid. Sequential method preserved for < 8 messages. No existing behavior changes for short conversations. |

---

## 10. Rollout

1. Add `message_indices_json` and `segmentation_method` columns to `MemoryEpisode` model
2. Create Alembic migration
3. Create `batch_segmenter.py` with all functions
4. Write unit tests for validation, index conversion, segmentation
5. Modify `maybe_generate_episode()` in `episodes.py` to route to batch segmentation
6. Write integration tests for multi-episode generation
7. Run full test suite (602+ tests)
8. Ship as single PR

---

## 11. References

- Nemori `batch_segmenter.py` — `BatchSegmenter.segment_batch()`, non-continuous index grouping, temperature 0.2
- Zacks, J.M. & Swallow, K.M. (2007). Event Segmentation. Current Directions in Psychological Science.
- [Implementation Plan Phase 6](../memory-implementation-plan.md) — detailed function signatures and modified file list
