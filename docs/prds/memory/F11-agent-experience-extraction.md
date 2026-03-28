---
title: "PRD: F11 — Agent Experience Extraction (Procedural Memory)"
description: Extract and store the agent's own problem-solving experiences for retrieval on similar future tasks
category: prd
version: "1.0"
---

# PRD: F11 — Agent Experience Extraction

**Version**: 1.0
**Date**: 2026-03-28
**Status**: Not Started (0%)
**Roadmap Phase**: 11.1
**Priority**: P1 — High
**Depends on**: None (uses existing consolidation pipeline, existing embedding infrastructure)
**Blocked by**: Nothing
**Blocks**: F12 (Experience Clustering), F13 (Skill Distillation)
**Inspired by**: Agent memory research (arXiv:2601.02163) — agent experience extraction pipeline

---

## 1. Overview

Teach the AI to learn from its own behavior. After each conversation turn that involves tool use or multi-turn problem-solving, the system extracts a structured experience record capturing **what the agent was trying to do**, **how it did it**, and **how well it worked**. These records are embedded and stored in the soul database. When a similar task arises in the future, the most relevant past experiences are surfaced in the system prompt so the agent can draw on proven approaches.

This is **procedural memory** — the AI remembers not just facts about the user (declarative memory, already implemented), but its own problem-solving strategies. Over time, the agent accumulates a growing library of "how I've handled situations like this before," becoming more effective without any external training.

### What This Is Not

- Not a task/goal tracker (that's the existing intentions system)
- Not episodic memory about user events (that's `MemoryEpisode`)
- Not skills or best practices (that's F13, which builds on top of this)
- Not autonomous self-modification — the agent doesn't change its own code or prompts; it retrieves relevant past reasoning as context

---

## 2. Problem Statement

### Current State

AnimaOS's memory system is excellent at remembering things about the **user** — facts, preferences, goals, emotional patterns, episodes of past conversations. But it remembers nothing about its **own behavior**.

The agent's inner reasoning is captured via the `thinking` kwarg on every tool call, extracted in `_extract_inner_thoughts()`, and fed into consolidation as part of `enriched_response`. But this thinking is only used to extract *user-facing* facts — it is never stored or indexed as a record of the agent's own problem-solving process.

### The Gap

| Scenario | What happens now | What should happen |
|----------|------------------|--------------------|
| User asks for help planning a trip (again) | Agent starts from scratch — no memory of how it helped plan the last trip | Agent retrieves: "Last time I helped plan a trip: 1) Asked about dates/budget, 2) Suggested destinations matching preferences, 3) Created a day-by-day itinerary. Quality: 0.9" |
| Agent tries a tool call that fails | Error is handled, response is generated, failure is forgotten | Experience record captures: "Tried X, failed because Y, recovered by Z" — next time, skip X |
| User asks the same complex multi-step question a second time | Agent may produce a different (possibly worse) approach | Agent retrieves the approach that worked well last time and follows it |
| Agent successfully handles a delicate emotional situation | The successful strategy is lost | Experience captures the approach for similar future situations |

### User-Visible Impact

Without procedural memory, the AI never gets better at tasks it has done before. Every interaction starts from zero. The user sees an AI that forgets how to do things it's already done successfully, making the same mistakes or taking different approaches to the same problem each time.

### Research Precedent

Prior work on agent memory systems (arXiv:2601.02163) implements an experience extraction pipeline that:
1. Pre-compresses tool call chains (LLM-based chunked compression for long trajectories)
2. Single LLM call extracts `{task_intent, approach, quality_score}` per conversation segment
3. Computes embedding on `task_intent` for semantic retrieval

The key insight: agent conversations with tool calls almost always represent task-solving processes worth remembering. Simple chitchat without tools is filtered out.

---

## 3. Design

### 3.1 Data Model

New table: `agent_experiences` (in the **soul database** — this is part of the AI's enduring identity, not ephemeral runtime state)

| Column | Type | Notes |
|--------|------|-------|
| id | int PK | auto-increment |
| user_id | int FK → users | |
| task_intent | text | What the agent was trying to do — retrieval key |
| approach | text | Numbered steps with decisions, results, and lessons |
| quality_score | float | 0.0–1.0, how well the task was completed |
| source_thread_id | int | Which conversation thread this was extracted from |
| source_run_id | int | Which agent run produced this experience |
| tool_names_json | JSON | List of tools used (for filtering/stats) |
| turn_count | int | Number of agent turns in the experience |
| embedding_json | JSON | Embedding vector of task_intent (nullable) |
| cluster_id | varchar | Assigned by F12 clustering (nullable until F12) |
| superseded_by | int FK → agent_experiences | If a newer, better experience obsoletes this one |
| created_at | datetime | |
| updated_at | datetime | |

Indexes:
- `ix_agent_experiences_user_id` on `(user_id)` — scope queries to the current user
- `ix_agent_experiences_user_cluster` on `(user_id, cluster_id)` — for F12 cluster lookups

### 3.2 Extraction Pipeline

Experience extraction plugs into the existing background consolidation path. No new LLM call is needed for simple turns; complex tool-using turns get an additional extraction call.

#### 3.2.1 Trigger Conditions

Extract an experience when a completed agent turn meets ANY of these criteria:
- **Tool use**: The turn involved at least one tool call (excluding `send_message`)
- **Multi-turn reasoning**: The turn had 2+ LLM steps (heartbeat-driven continuation)
- **Inner thought depth**: The combined `thinking` text across tool calls exceeds 200 characters (indicates non-trivial reasoning)

Skip extraction when:
- Single-turn `send_message` only (casual chat)
- Total inner thought < 200 characters AND no tool calls beyond `send_message`
- The turn was cancelled or failed before producing a response

This filter is adapted from prior research's single-turn filtering heuristic but refined for AnimaOS's richer inner-thought signal.

#### 3.2.2 Input Assembly

The extraction LLM receives a structured representation of the agent turn, assembled from data already available in the runtime DB after a turn completes:

```python
@dataclass
class ExperienceExtractionInput:
    """Assembled from AgentResult + RuntimeMessage records."""
    user_message: str              # The triggering user message
    tool_calls: list[dict]         # [{name, arguments_summary, result_summary, inner_thinking}]
    final_response: str            # The agent's final response to the user
    total_steps: int               # Number of LLM steps in the turn
```

Key difference from prior art: AnimaOS already has the agent's **private reasoning** via the `thinking` kwarg. Other implementations must reconstruct intent from raw tool calls alone. AnimaOS's extraction prompt can be simpler and more accurate because it has direct access to what the agent was thinking.

#### 3.2.3 LLM Extraction

Single LLM call using the existing `create_llm()` provider. The prompt is adapted from prior research's experience compression approach but tailored for AnimaOS's richer input:

```
You are extracting a problem-solving experience from an AI agent's conversation turn.

The agent was helping a user. Here is what happened:

User's request: {user_message}

Agent's reasoning and actions:
{tool_calls_formatted}

Agent's final response: {final_response}

Extract ONE experience record. If this was trivial (simple greeting, basic factual Q&A requiring
no problem-solving), return {"task_intent": null}.

Return JSON:
{
    "task_intent": "The specific task as a self-contained statement (retrieval key)",
    "approach": "1. <sub-problem>\n   - Tried: <what was attempted>\n   - Result: <outcome>\n2. ...\n\nOutcome: <final result>",
    "quality_score": 0.0-1.0
}
```

**Retry policy**: 3 attempts with JSON validation. On all-fail, skip extraction silently (no user impact).

**Token budget**: Extraction input is capped at 4000 tokens. Tool call arguments and results are truncated to 500 chars each. Inner thinking is included in full (typically short).

#### 3.2.4 Embedding

After successful extraction, compute an embedding on `task_intent` using the existing `backfill_embeddings()` infrastructure. Store in `embedding_json` on the `AgentExperience` row.

This reuses the same embedding model and in-memory vector index used for `MemoryItem` semantic search.

### 3.3 Retrieval

#### 3.3.1 When to Retrieve

At the start of each agent turn, before the first LLM call. Specifically, during `build_runtime_memory_blocks()` in `memory_blocks.py`.

Retrieve when the user's message suggests a task (not pure small talk). The existing embedding infrastructure computes a query embedding for the user message; use this same embedding to search the experience store.

#### 3.3.2 Retrieval Method

Cosine similarity search over `embedding_json` vectors, same as `get_memory_items_scored()` for facts. Return top-3 experiences with similarity > 0.6.

#### 3.3.3 System Prompt Integration

New memory block `past_approaches`:

```
[past_approaches]
Description: How I've handled similar situations before. Use these as reference — adapt, don't copy blindly.

1. [0.87 relevance] Helped plan a weekend trip to the mountains
   Approach: 1) Asked about dates and budget → user wanted 2 days, $200 budget
   2) Suggested 3 locations within budget → user picked Mt. Rainier
   3) Created packing list and itinerary → user was satisfied
   Quality: 0.9

2. [0.72 relevance] Helped organize a birthday party
   Approach: 1) Asked about guest count and venue preferences...
   Quality: 0.8
```

Budget: 1500 characters max for the block. Truncate approach text if needed.

#### 3.3.4 Positioning in Block Order

Insert after `self_working_memory` and before `emotional_context`. Rationale: the agent should see its working memory and identity before past approaches, but past approaches should inform the emotional read of the current situation.

### 3.4 Quality and Lifecycle

#### 3.4.1 Quality Scoring

The LLM assigns `quality_score` during extraction. The score represents how well the agent completed the task:
- 1.0: Task fully completed, user explicitly satisfied
- 0.7–0.9: Task completed with minor issues
- 0.4–0.6: Partial completion or unclear outcome
- 0.0–0.3: Failed attempt, error recovery needed

Experiences with `quality_score < 0.3` are still stored — they're valuable for F13 (Skill Distillation) as negative examples showing what NOT to do.

#### 3.4.2 Supersession

If a new experience covers the same task as an existing one (cosine similarity > 0.9 between their `task_intent` embeddings) and has a higher `quality_score`, the older experience is superseded:
- Set `superseded_by` on the old row
- Superseded experiences are excluded from retrieval but retained for F13 analysis
- This prevents the prompt from being cluttered with multiple versions of the same approach

#### 3.4.3 Growth Log Integration

When a new experience is stored, log a growth entry: "Learned from experience: {task_intent} (quality: {quality_score})". This feeds into the existing growth log visible in the self-model.

### 3.5 Encryption

All text fields (`task_intent`, `approach`) are encrypted at rest using the existing per-field encryption in the soul database, consistent with `MemoryItem.content`. The `embedding_json` column is stored as plaintext (embeddings are not reversible to source text and are needed for cosine similarity computation).

---

## 4. Implementation Plan

| Step | File | Change |
|------|------|--------|
| 1 | `alembic_core/versions/` | New migration: create `agent_experiences` table |
| 2 | `models/agent_runtime.py` or new `models/agent_experience.py` | Add `AgentExperience` SQLAlchemy model |
| 3 | `services/agent/experience_extraction.py` (new) | `extract_agent_experience()` — input assembly, LLM call, parsing, storage |
| 4 | `services/agent/consolidation.py` | Call `extract_agent_experience()` from `run_background_memory_consolidation()` |
| 5 | `services/agent/memory_blocks.py` | Add `build_past_approaches_block()` with semantic retrieval |
| 6 | `services/agent/self_model.py` | Log growth entry on experience creation |
| 7 | `templates/` | Add extraction prompt template (Jinja2, consistent with existing prompts) |
| 8 | `api/routes/consciousness.py` | GET endpoint for listing/viewing experiences |
| 9 | Tests | Unit tests for extraction, supersession, retrieval, block building |

### Step Dependencies

```
1 → 2 → 3 → 4 (core pipeline)
         3 → 5 (retrieval)
         3 → 6 (growth log)
         3 → 7 (prompt template)
         2 → 8 (API)
All → 9 (tests)
```

### Estimated LLM Cost

- Extraction: ~1000 input tokens + ~300 output tokens per qualifying turn
- At ~30% of turns qualifying (tool use or multi-turn): ~1 extraction per 3 conversations
- Negligible compared to the agent's main LLM calls

---

## 5. Interaction with Other Features

| Feature | Interaction |
|---------|-------------|
| F5 (Sleep Agents) | Experience extraction runs as part of the sequential consolidation group, after fact extraction, before embedding backfill |
| F8 (Foresight) | Independent — foresight captures user temporal events, experience captures agent behavior |
| F12 (Clustering) | F12 assigns `cluster_id` to experiences for grouping — F11 leaves the column nullable |
| F13 (Skill Distillation) | F13 reads experiences to generate skills — F11 provides the raw material |
| Phase 10 (Consciousness) | Experiences feed the growth log, enriching the agent's self-awareness narrative |
| Existing consolidation | Experience extraction is additive — runs alongside fact/emotion extraction, does not modify or replace anything |

---

## 6. Design Decisions

### 6.1 Soul Database, Not Runtime

Experiences are part of the AI's enduring identity — "I remember how to handle X" persists across conversations, across machines (via `.anima/` portability). Runtime DB stores ephemeral conversation state. Soul DB stores what makes this AI *this* AI.

### 6.2 One Experience Per Turn, Not Per Conversation

Prior research uses boundary detection to segment conversations, then extracts one experience record per segment. AnimaOS doesn't need this — its turn-based architecture already provides natural boundaries. Each qualifying agent turn (user message → tool calls → response) produces at most one experience.

### 6.3 task_intent as Retrieval Key, Not approach

The `task_intent` field is embedded for semantic search. The `approach` field is too detailed and varied for useful embedding similarity — two approaches to the same task might use completely different words. The intent ("help user plan a trip") is the stable retrieval anchor.

### 6.4 No Pre-Compression (Unlike Prior Art)

Some agent memory implementations need LLM-based tool content pre-compression because their tool call outputs can be enormous (search results, code execution output). AnimaOS tool outputs are already concise (memory operations, task management, note-taking). Simple truncation at 500 chars per tool result suffices. If tool outputs grow larger in the future, pre-compression can be added as a transparent preprocessing step.

### 6.5 Existing Embedding Infrastructure

No new embedding model, no new vector store, no new search implementation. Experiences use the same `embedding_json` pattern as `MemoryItem`, the same in-memory cosine similarity search, and the same `backfill_embeddings()` mechanism for async embedding computation.

---

## 7. Success Criteria

- [ ] Agent experiences are extracted from tool-using and multi-turn conversation turns
- [ ] Experiences are stored in the soul database with encryption
- [ ] Relevant past experiences appear in the system prompt when the user's query semantically matches
- [ ] The agent's responses show awareness of past approaches (referencing or adapting them)
- [ ] Supersession works: newer, higher-quality experiences replace older ones for the same task type
- [ ] Growth log entries are created for new experiences
- [ ] Experiences survive database portability (`.anima/` copy to new machine)
- [ ] No regression in response latency (extraction is background, retrieval is <50ms)

---

## 8. Future Considerations

- **Experience decay**: Old experiences with low quality scores could be pruned after N months, similar to the forgetting system for facts. Not needed initially.
- **User feedback signal**: If the user explicitly says "that was helpful" or "that didn't work," update the quality score of the most recent experience. Connects to the existing feedback_signals system.
- **Cross-user experiences**: In a hypothetical multi-user AnimaOS, shared experiences (anonymized) could accelerate learning. Out of scope — AnimaOS is single-user by design.

---

## 9. References

- Agent memory systems research (arXiv:2601.02163) — agent experience extraction and compression patterns
- AnimaOS `services/agent/consolidation.py` — existing consolidation pipeline (insertion point)
- AnimaOS `services/agent/memory_blocks.py` — existing block builder (retrieval integration)
- AnimaOS `services/agent/service.py:_extract_inner_thoughts()` — existing inner-thought extraction
- AnimaOS `services/agent/runtime_types.py:ToolExecutionResult` — `inner_thinking` field
