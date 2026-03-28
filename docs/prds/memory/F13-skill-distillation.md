---
title: "PRD: F13 — Skill Distillation (Procedural Learning)"
description: Distill reusable skills from clusters of similar agent experiences, enabling the AI to accumulate genuine expertise over time
category: prd
version: "1.0"
---

# PRD: F13 — Skill Distillation

**Version**: 1.0
**Date**: 2026-03-28
**Status**: Not Started (0%)
**Roadmap Phase**: 11.3
**Priority**: P2 — Medium
**Depends on**: F11 (Agent Experience Extraction), F12 (Experience Clustering)
**Blocked by**: F12 must be implemented first
**Blocks**: Nothing
**Inspired by**: Agent memory research (arXiv:2601.02163) — incremental skill extraction from clustered experiences

---

## 1. Overview

When the AI accumulates enough similar experiences in a cluster (minimum 3, per F12), distill them into a reusable **skill** — a generalized, optimized procedure for handling that type of task. Skills represent the AI's highest form of procedural learning: not "here's what I did once" (F11 experiences) but "here's the best way to handle this, based on everything I've learned."

Skills are:
- **Generalized**: Case-specific details (names, dates, specific errors) replaced with general descriptions
- **Quality-weighted**: Better approaches (higher quality_score) get more influence
- **Pitfall-aware**: Failed experiences inform a "what to avoid" section
- **Incrementally refined**: Each new experience in the cluster triggers a re-evaluation — the skill evolves
- **Confidence-scored**: Confidence increases with more confirming experiences, decreases when contradicted

This is the learning loop that makes the AI genuinely improve over its lifetime. A year-old AnimaOS instance with 500 experiences and 30 distilled skills is meaningfully better at recurring tasks than a fresh install.

### What This Means for the User

The user doesn't interact with skills directly. They experience an AI that:
- Handles familiar tasks faster and with fewer mistakes
- Shows confidence calibration ("I've done this many times" vs. "this is new for me")
- Avoids repeating past failures
- Gets better over time at the specific types of tasks *this particular user* asks for

---

## 2. Problem Statement

### Current State (After F11 + F12)

F11 stores individual experiences. F12 groups them into clusters. But the agent retrieves *individual* experiences — it sees "here are 3 past approaches to trip planning" as 3 separate items, each with their own approach and quality score.

The agent has no way to:
- Synthesize the 3 approaches into one best-practice procedure
- Learn from the failures in low-quality experiences without adopting their flawed approaches
- Compress its procedural knowledge — 3 experiences might repeat 60% of the same steps
- Know what *not* to do based on past mistakes

### The Gap

| Scenario | What F11 provides | What F13 provides |
|----------|-------------------|-------------------|
| 5 trip-planning experiences (quality: 0.9, 0.7, 0.8, 0.4, 0.9) | 5 separate approaches in the prompt (expensive, redundant) | 1 distilled skill: "Trip Planning — 5 steps, avoid X (failed in case 4), confidence 0.85" |
| New experience contradicts existing skill | Nothing — experiences are independent | Skill confidence decreases, approach may be revised |
| Agent has 50 experiences across 10 task types | 50 rows, retrieval returns top-3 individual cases | 10 skills, retrieval returns the most relevant skill as a compact procedure |

### The Information-Theoretic Argument

Individual experiences contain redundant information. Three trip-planning experiences that all start with "ask about dates and budget" store that step three times. A skill stores it once with higher confidence. As experience count grows, the ratio of unique information to total storage shrinks. Skills are the compressed representation — they preserve the signal (what works, what doesn't) and discard the noise (case-specific details).

### Research Precedent

Prior art in agent memory systems (arXiv:2601.02163) implements a skill extraction pipeline that:
- Takes **only new** experience records + existing skills for a cluster
- Single LLM call merges new experience into existing skills
- Quality-weighted: high-quality experiences strengthen skills, low-quality ones add pitfalls
- Output is the complete updated skill set (not a delta)
- Confidence scoring: starts at 0.5 for single-case skills, increases with confirming experiences

Key prompt design: "Experience = concrete case. Skill = best-practice process." The extraction prompt explicitly distinguishes these — it asks the LLM to generalize, not copy.

---

## 3. Design

### 3.1 Data Model

New table: `agent_skills` (in the **soul database**)

| Column | Type | Notes |
|--------|------|-------|
| id | int PK | auto-increment |
| user_id | int FK → users | |
| cluster_id | varchar | FK → the cluster this skill belongs to (from F12) |
| name | varchar(100) | Short descriptive name ("Trip Planning", "Emotional Support") |
| description | text | When to apply this skill — the type of problem it solves |
| content | text | The procedure: numbered steps + pitfalls section |
| confidence | float | 0.0–1.0, increases with confirming experiences |
| experience_count | int | Number of experiences that contributed to this skill |
| last_refined_at | datetime | When the skill was last updated by a new experience |
| embedding_json | JSON | Embedding of `name + description` for retrieval |
| superseded_by | int FK → agent_skills | If this skill was replaced during a refinement |
| created_at | datetime | |
| updated_at | datetime | |

Indexes:
- `ix_agent_skills_user_cluster` on `(user_id, cluster_id)` — lookup skills for a cluster
- `ix_agent_skills_user_active` on `(user_id, superseded_by)` — retrieve only active skills

### 3.2 Skill Structure

A skill has three semantic components stored in the `content` field:

```
1. Ask about constraints (dates, budget, preferences)
   - Why: Setting boundaries early prevents wasted suggestions
2. Generate 2-3 options within constraints
   - Present with trade-offs, not just features
3. Help user narrow down with follow-up questions
   - Ask about deal-breakers, not open-ended preferences
4. Build detailed plan for the chosen option
   - Include logistics (travel time, parking, supplies)
5. Offer to revisit if priorities change

Pitfalls:
- Do not suggest options outside the stated budget — even if "slightly over."
  The user felt pressured and quality dropped (case quality: 0.4)
- Do not skip the narrowing step and jump to detailed planning.
  Users feel overwhelmed by premature detail.
```

The pitfalls section is the distillation of low-quality experiences — the failure modes the agent should avoid.

### 3.3 Extraction Pipeline

#### 3.3.1 Trigger

Skill distillation is triggered when:
1. A new experience is added to a cluster (via F12), AND
2. The cluster size >= `min_cluster_size_for_skill` (default: 3)

This means:
- First experience → no skill (only 1 case, nothing to generalize)
- Second experience → no skill (2 cases, insufficient pattern)
- Third experience → initial skill created (3 cases, enough to generalize)
- Fourth+ experience → skill incrementally refined

#### 3.3.2 Execution Context

Skill distillation runs as a **sleep-time task** (F5), not inline with consolidation. Rationale:
- Distillation requires an LLM call with potentially large input (all cluster experiences + existing skills)
- It is not time-sensitive — the skill doesn't need to exist before the next conversation turn
- Running it in the consolidation path would delay background fact extraction

Scheduling:

```python
# In experience_extraction.py, after clustering
if get_cluster_size(db, cluster_id) >= MIN_CLUSTER_SIZE_FOR_SKILL:
    schedule_skill_distillation(user_id, cluster_id)
```

The `schedule_skill_distillation()` function creates an entry in the sleep-time task queue. The next sleep-time run (every N turns, per F5) picks it up.

#### 3.3.3 Input Assembly

```python
@dataclass
class SkillDistillationInput:
    cluster_id: str
    new_experiences: list[AgentExperience]    # Only the NEW ones since last distillation
    existing_skills: list[AgentSkill]          # Current skills for this cluster
```

Key design point (from prior research): only the **new** experiences are sent to the LLM, along with the **existing** skills. This is incremental merging — the LLM updates existing skills based on new evidence, rather than regenerating from all experiences every time.

Token budget for the prompt:
- New experiences: up to 2000 tokens (truncate approach text if needed)
- Existing skills: up to 1500 tokens
- Prompt template: ~1000 tokens
- Total input: ~4500 tokens
- Expected output: ~500–1000 tokens

#### 3.3.4 LLM Prompt

Adapted from prior research's skill extraction prompt design:

```
You are distilling the best problem-solving process from concrete agent task experiences.

You will receive:
1. New experience(s) just added to a cluster of semantically similar tasks
2. Existing skills previously extracted for this cluster (may be empty)

Your job: merge new evidence into reusable Skills.

Experience = concrete case (specific task, specific steps, specific result)
Skill = best-practice process (generalized steps any agent can follow)

Guidelines:
- Generalize: replace case-specific details with general descriptions
- Quality-weighted: high quality_score (> 0.7) approaches are preferred
- Low quality_score (< 0.3) approaches go into the Pitfalls section
- Keep technology/tool names specific (they help with retrieval)
- Each skill must be self-contained: name + when to use + procedure + pitfalls
- Confidence = how certain you are that this process works well
  - 1 confirming experience: 0.5
  - 3 confirming, 0 contradicting: 0.7
  - 5+ confirming, < 20% contradicting: 0.85
  - Multiple contradicting or all low quality: lower confidence accordingly

New experience(s):
{new_experience_json}

Existing skills for this cluster:
{existing_skills_json}

If existing skills are empty, extract initial skills from the new experience.
If existing skills are present:
- Similar problem → refine the skill (update steps, adjust confidence)
- Different problem → add a new skill
- Contradicted → lower confidence or remove

Return the COMPLETE updated skill set (not a delta):
{
    "skills": [
        {
            "name": "Short name (max 10 words)",
            "description": "When to apply this skill",
            "content": "Numbered steps. Append Pitfalls section if failed cases exist.",
            "confidence": 0.0-1.0
        }
    ]
}
```

**Retry policy**: 3 attempts with JSON validation. On all-fail, keep existing skills unchanged.

#### 3.3.5 Post-Processing

After LLM returns skills:
1. Validate structure (name, content, confidence required)
2. Clamp confidence to [0.0, 1.0]
3. Compute embedding on `name + "\n" + description` for each skill
4. Replace existing skills for the cluster (soft delete old, insert new)
5. Update `experience_count` and `last_refined_at`
6. Log growth entry if confidence changed significantly

### 3.4 Retrieval

#### 3.4.1 When to Retrieve

During `build_runtime_memory_blocks()`, alongside F11 experience retrieval. Skills and experiences serve complementary roles:
- **Skills** are retrieved when a high-confidence skill matches the query (confidence > 0.5)
- **Experiences** are retrieved as fallback when no skill matches, or when the user's task is novel

Priority order:
1. If a skill with confidence ≥ 0.7 matches (cosine > 0.6) → show the skill
2. Else if individual experiences match → show top-3 experiences (F11 behavior)
3. Else → no procedural memory block

#### 3.4.2 Retrieval Method

Same cosine similarity search as F11, but over `agent_skills.embedding_json` instead of `agent_experiences.embedding_json`. Both searches run in the same pass — one query embedding, two searches.

#### 3.4.3 System Prompt Integration

New memory block `learned_skills` (higher priority than `past_approaches`):

```
[learned_skills]
Description: Best practices I've developed through repeated experience. These are proven approaches — follow them unless the situation clearly calls for something different.

Trip Planning (confidence: 0.85, based on 5 experiences)
1. Ask about constraints (dates, budget, preferences)
   - Setting boundaries early prevents wasted suggestions
2. Generate 2-3 options within constraints
   - Present with trade-offs, not just features
3. Help user narrow down with follow-up questions
4. Build detailed plan for the chosen option
5. Offer to revisit if priorities change

Pitfalls:
- Do not suggest options outside the stated budget
- Do not skip narrowing and jump to detailed planning
```

Budget: 2000 characters max for the block. If multiple skills match, show the highest-confidence one in full and summarize others.

#### 3.4.4 Block Order

Insert `learned_skills` before `past_approaches` in the memory block sequence. The agent should see its distilled skills before raw experiences — skills are more actionable and compact.

### 3.5 Skill Lifecycle

#### 3.5.1 Confidence Evolution

| Event | Confidence change |
|-------|-------------------|
| Initial creation (3 experiences, all quality > 0.7) | Start at 0.6 |
| New confirming experience (quality > 0.7) | +0.05 (cap at 0.95) |
| New contradicting experience (different approach, quality > 0.7) | -0.1 |
| New failure (quality < 0.3) with pitfall added | No confidence change (failure adds pitfall, doesn't invalidate the skill) |
| No new experiences for 6 months | Gradual decay: -0.05 per month (skills may become stale) |

Note: confidence changes are computed by the LLM (it receives the current confidence and new evidence). The rules above are guidelines embedded in the prompt, not hard-coded logic.

#### 3.5.2 Skill Supersession

When the LLM returns an updated skill set that removes a skill entirely (e.g., contradicted by multiple high-quality experiences), the removed skill is soft-deleted via `superseded_by`. It remains in the database for audit purposes but is excluded from retrieval.

#### 3.5.3 Staleness

Skills that haven't been refined in 6+ months and have low confidence (< 0.5) are candidates for pruning. This can be handled by the existing forgetting system (F7) via a sleep-time sweep — not implemented in F13 itself.

### 3.6 Encryption

Same as F11: text fields (`name`, `description`, `content`) encrypted at rest. `embedding_json` stored plaintext for computation.

---

## 4. Implementation Plan

| Step | File | Change |
|------|------|--------|
| 1 | `alembic_core/versions/` | New migration: create `agent_skills` table |
| 2 | `models/` | Add `AgentSkill` SQLAlchemy model |
| 3 | `services/agent/skill_distillation.py` (new) | `SkillDistiller` — input assembly, LLM call, post-processing, storage |
| 4 | `services/agent/experience_extraction.py` | Schedule skill distillation after clustering when threshold met |
| 5 | `services/agent/sleep_agent.py` | Add skill distillation to the sleep-time task queue |
| 6 | `services/agent/memory_blocks.py` | Add `build_learned_skills_block()` with semantic retrieval |
| 7 | `services/agent/self_model.py` | Log growth entries on skill creation/refinement |
| 8 | `templates/` | Add skill distillation prompt template |
| 9 | `api/routes/consciousness.py` | GET endpoint for listing skills; PUT for manual confidence adjustment |
| 10 | Tests | Unit tests for distillation, confidence evolution, retrieval priority, block building |

### Step Dependencies

```
1 → 2 → 3 (core distillation engine)
3 → 4 (trigger from F11/F12)
3 → 5 (sleep-time scheduling)
3 → 6 (memory block)
3 → 7 (growth log)
3 → 8 (prompt)
2 → 9 (API)
All → 10 (tests)
```

---

## 5. Design Decisions

### 5.1 Incremental Merging, Not Full Regeneration

The key insight from prior research: send only the **new** experiences to the LLM, along with existing skills. The LLM decides how to update. This avoids:
- Re-processing all experiences on every new case (token-expensive)
- Risk of the LLM "forgetting" lessons from earlier cases that aren't in the current context
- The skills accumulate knowledge over time, serving as a compressed representation of all past cases

### 5.2 Sleep-Time Execution, Not Inline

Skill distillation is not time-sensitive. The user won't notice if the skill exists 3 turns from now vs. right now. Running it in the sleep-time orchestrator (F5) keeps the per-turn consolidation path fast and avoids SQLite write contention.

### 5.3 Skills vs. Self-Model Sections

An alternative design would store skills as self-model sections (like `identity` or `inner_state`). This was rejected because:
- Self-model sections are free-form text with manual curation semantics
- Skills are structured (name/description/content/confidence) with automatic lifecycle
- Skills have a many-to-one relationship with clusters — they need their own table
- Self-model is about *who the agent is*; skills are about *what the agent knows how to do*

### 5.4 LLM-Driven Confidence, Not Formula-Based

The confidence score is determined by the LLM based on the evidence pattern, not by a hard-coded formula. This allows nuanced judgment:
- 3 high-quality experiences all using the same approach → high confidence
- 3 experiences with wildly different approaches → lower confidence (no convergence)
- 5 experiences where the approach keeps improving → the LLM can recognize the learning trajectory

The prompt provides guidelines (not rules) for how confidence should evolve.

### 5.5 One Cluster = One Skill Set (Usually 1–3 Skills)

A single cluster typically produces 1 skill. Occasionally 2–3 if the cluster spans related but distinct sub-tasks (e.g., a "travel planning" cluster might produce "Trip Research" and "Itinerary Building" as separate skills). The LLM decides the granularity.

### 5.6 Skill Priority Over Experience in Retrieval

When a high-confidence skill matches the query, it takes priority over individual experiences. The skill is a compressed, quality-weighted summary — it contains strictly more information per token than individual experiences. Individual experiences are shown only when no skill matches (novel task) or as supplementary context.

---

## 6. Success Criteria

- [ ] Skill distillation triggers when a cluster reaches 3+ experiences
- [ ] Distilled skills capture generalized steps, not case-specific details
- [ ] Low-quality experiences contribute pitfalls, not procedure steps
- [ ] Skills are incrementally refined when new experiences arrive (not regenerated from scratch)
- [ ] Confidence scores evolve correctly: increase with confirming, decrease with contradicting evidence
- [ ] Skills appear in the system prompt when the user's query matches
- [ ] Skills take priority over individual experiences when confidence is high
- [ ] Growth log records skill creation and significant refinements
- [ ] Skills survive database portability (`.anima/` copy)
- [ ] Skill distillation runs in the sleep-time queue without blocking the main conversation

---

## 7. Interaction with Other Features

| Feature | Interaction |
|---------|-------------|
| F11 (Experiences) | Provides the raw material — individual experiences are the input |
| F12 (Clustering) | Provides the grouping — cluster_id determines which experiences to distill together |
| F5 (Sleep Agents) | Distillation runs as a sleep-time task |
| F7 (Forgetting) | Stale, low-confidence skills are candidates for pruning |
| Phase 7 (Proactive) | Skills could inform proactive suggestions ("Based on my experience, here's how we should approach this") |
| Phase 10 (Consciousness) | Skills enrich the growth log and self-model — the agent knows what it's good at |

---

## 8. End-to-End Example

### Month 1: First Trip Planning Experience

User: "Can you help me plan a weekend trip?"
Agent: Uses tools, produces itinerary. Quality: 0.8.
→ F11 stores experience. F12 creates cluster_1_000. No skill yet (only 1 experience).

### Month 2: Second Trip Planning Experience

User: "I need to plan a hiking trip for next month."
Agent: Handles well. Quality: 0.9.
→ F11 stores experience. F12 assigns to cluster_1_000. Still only 2 experiences — no skill.

### Month 3: Third Trip Planning + First Failure

User: "Plan a birthday trip for my partner."
Agent: Suggests options over budget, user is unhappy. Quality: 0.4.
→ F11 stores experience. F12 assigns to cluster_1_000. Now 3 experiences — skill distillation triggers.

Sleep-time runs `SkillDistiller`:
- Input: 3 experiences (quality: 0.8, 0.9, 0.4), no existing skills
- Output: 1 skill "Trip Planning" with confidence 0.6
  - Steps generalized from the two good experiences
  - Pitfall added: "Do not suggest options outside the stated budget" (from the 0.4 experience)

### Month 5: Fourth Trip Planning

User: "Help me plan a camping trip."
Agent sees in prompt:
```
[learned_skills]
Trip Planning (confidence: 0.6, based on 3 experiences)
1. Ask about constraints (dates, budget, preferences)
2. Generate 2-3 options within constraints
3. Help narrow down with follow-up questions
4. Build detailed plan

Pitfalls:
- Do not suggest options outside stated budget
```
Agent follows the skill, stays within budget, great result. Quality: 0.9.
→ F11 stores experience. F12 assigns to cluster. F13 refines skill:
  - Confidence → 0.7 (confirmed by another high-quality case)
  - Steps unchanged (approach validated)

### Year 1: 10th Trip Planning

Skill confidence: 0.85. 8 confirming experiences, 2 failures with pitfalls captured. The agent is genuinely expert at trip planning for this specific user.

---

## 9. References

- Agent memory systems research (arXiv:2601.02163) — skill extraction from clustered experiences, incremental merging, cluster-to-skill trigger pattern
- AnimaOS F11 — experience storage and retrieval
- AnimaOS F12 — cluster management and size thresholds
- AnimaOS F5 — sleep-time task orchestration
- AnimaOS `services/agent/self_model.py` — growth log integration pattern
