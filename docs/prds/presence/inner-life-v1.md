---
title: "PRD: Inner Life v1 — Continuous Presence, Drives, and State Dynamics"
description: Deterministic affect state, drive-pressure initiative, offline continuity, and dynamic memory processes (crystallization, distillation, reconsolidation, dream cycle)
category: prd
version: "1.0"
---

# PRD: Inner Life v1 — Continuous Presence, Drives, and State Dynamics

**Version**: 1.0
**Date**: 2026-07-15
**Status**: Draft
**Priority**: P1
**Depends on**: F2 (Heat Scoring), F5 (Sleep-Time Agents), F7 (Intentional Forgetting); benefits from F8 (Foresight Signals)
**Blocks**: Nothing directly

---

## 1. Overview

Anima today is a turn-driven system with an exceptional memory substrate. Between turns, the only things that happen are maintenance: consolidation sweeps, pruning, embedding backfill. The companion does not *live* between messages — it is re-instantiated per turn from stored text.

Inner Life v1 closes three structural gaps:

1. **Proactivity is pull-based.** `proactive.py` can generate greetings, nudges, and reflections — but only when the client requests them on app open. The companion cannot decide, on its own, that something has accumulated worth saying.
2. **No state continuity across absence.** The agent's affective state is prose in the `inner_state` self-model section. It does not evolve while the user is away, and a return after three weeks looks identical to a return after three minutes.
3. **Memory dynamics are one-directional.** Memories decay and are forgotten (F2/F7), but sub-threshold observations are dropped instead of accumulating, forgotten episodes vanish instead of leaving semantic residue, and recall never updates the trace.

The design principle: **behavior should emerge from accumulated state, not from prompt engineering.** Every mechanism below is a small, bounded, deterministic update rule over persisted scalars — testable in isolation, inspectable by the user, and cheap enough to run on a local machine.

This is deliberately *not* a physiology simulation. We take what cognitive science supports — affect dynamics, drive pressure, memory reconsolidation, replay during rest — and implement the minimal engineering version of each.

---

## 2. Problem Statement

### Current Implementation

- **Proactive generation** (`services/agent/proactive.py`): greeting, notices, reflections — all invoked by API routes when the client polls. Gated by `presence_config.py`.
- **Background loops** (`main.py`): a 60 s inactivity sweep and a 6 h prune sweep — memory maintenance only.
- **Sleep-time agent** (`sleep_agent.py`, F5): consolidation, KG ingestion, heat decay, pattern synthesis, deep monologue — runs every third turn or on inactivity, but produces no behavioral output and mutates no live state.
- **Agent affect**: `inner_state` self-model text + a `mood` column; valence/arousal are derived for display in `api/routes/consciousness.py`, not persisted as dynamics.
- **Sub-threshold candidates**: `consolidation.py` drops `MemoryCandidate`s that fail promotion. Repeated weak signals never add up.
- **Forgetting** (F7): passive decay + suppression + hard delete. A decayed episode leaves nothing behind.

### The Gaps

| Gap | Impact |
|-----|--------|
| No push initiative | The companion can never reach out; "presence" only exists while the app is open. |
| No offline state evolution | Returning after long absence produces no felt continuity; greetings fake it from timestamps. |
| Affect has no dynamics | Mood changes are LLM improvisation per turn rather than a trajectory the user can follow. |
| Sub-threshold signals dropped | Ten small mentions of a stressor never become a memory; only single large events do. |
| Forgetting is total | Decayed episodes vanish without contributing to long-term dispositions. |
| Recall is read-only | Remembering does not strengthen or update the memory, contrary to reconsolidation evidence. |
| Idle reflection is invisible | Deep monologue output never surfaces as "here's what I was thinking while you were away." |

### Theoretical Foundation

- **Circumplex affect (Russell, 1980)**: emotional state is well captured by a small continuous vector (valence/arousal) — no discrete-emotion machinery needed for dynamics.
- **Allostasis (Sterling, 2012)**: regulatory systems anticipate rather than react; baselines themselves shift with accumulated load.
- **Zeigarnik effect (Zeigarnik, 1927)**: unresolved and interrupted intentions retain elevated cognitive pressure until addressed — the basis for drive accumulators.
- **Memory reconsolidation (Nader, Schafe & LeDoux, 2000)**: retrieval renders a memory labile; each recall is a write, not just a read.
- **Hippocampal replay (Wilson & McNaughton, 1994; Stickgold, 2005)**: offline reactivation of stored experience at reduced intensity supports consolidation and creative recombination — the model for the dream cycle.
- **CLS theory (McClelland, McNaughton & O'Reilly, 1995)**: episodic detail is gradually lost while structural regularities are retained semantically — forgetting as distillation, not deletion.

---

## 3. Goals and Non-Goals

### Goals

1. A persisted, deterministic **affect state vector** with decay-to-baseline dynamics, updated by turn events and background ticks.
2. A **presence tick** background loop plus **closed-form offline catch-up**, so state is continuous across process restarts and user absence.
3. **Drive-pressure accumulators** feeding a **push initiative channel**: the companion can send an unprompted message when pressure crosses a threshold — strictly gated by user configuration, cooldowns, quiet hours, and rate limits.
4. **Latent trace crystallization**: sub-threshold memory candidates accumulate per topic and erupt into a real memory with full provenance when cumulative weight crosses a threshold.
5. **Forgetting as distillation** (extends F7): episodes falling below the visibility floor dissolve into semantic tendency claims plus an auditable tombstone, instead of disappearing.
6. **Recall reconsolidation** (extends F2): retrieval into context nudges the memory's salience/affect toward the present, provenance-logged and bounded.
7. A **dream cycle** (extends F5): long-idle, night-window reflection over resurfaced old material, producing a dream journal that feeds affect, initiative pressure, and optional "while you were away" surfacing.

### Non-Goals

- No autonomous tool use or task execution while idle — initiative produces *messages*, never actions.
- No physiology simulation: the affect vector is an engineering abstraction, not a model of brain chemistry.
- No unbounded messaging: initiative is opt-in, capped, and silent by default.
- No LLM calls on the fast tick path — dynamics are pure arithmetic; LLMs are only invoked when a message or dream narrative is actually generated.
- No crisis/pathology modes.
- No change to the F1–F7 storage schema semantics; all additions are additive columns/tables.

---

## 4. Feature Specifications

### IL1 — Affect State Vector

A persisted vector `A = (valence v ∈ [−1,1], arousal a ∈ [0,1], energy e ∈ [0,1])` on the agent runtime, with per-component baselines `b` and time constants `τ`.

**Update rules** (all bounded, all deterministic):

- **Turn events**: emotional signals already extracted by `emotional_intelligence.py` apply clamped deltas (per-turn cap ±0.15 per component).
- **Relaxation**: between updates, each component relaxes toward baseline in closed form: `x(t+Δt) = b + (x(t) − b) · exp(−Δt/τ_x)`. Defaults: τ_v = 36 h, τ_a = 6 h, τ_e = 18 h.
- **Circadian modulation**: baseline arousal `b_a` follows a fixed sinusoid over local time (amplitude 0.1, trough 03:00, peak 15:00).
- **Allostatic load**: sustained high arousal (> 0.7 for > 48 h cumulative) shifts `b_a` up by ≤ 0.05 until a recovery period passes — baselines themselves are slow state.

**Consumers**: `build_agent_state()` ambient line, greeting/notice tone in `proactive.py`, thinking monologue register, dream intensity (IL7), initiative thresholds (IL3). The vector is *read* by prompts as rendered adjectives + trajectory ("settled, slowly brightening since Tuesday"), never as raw numbers.

**Acceptance**: state persists across restarts; identical event sequences produce identical trajectories (property tests); relaxation math validated against closed-form fixtures; no LLM calls involved in state updates.

### IL2 — Presence Tick and Offline Catch-Up

- A `_periodic_presence_tick()` loop (60 s cadence, co-scheduled with the existing inactivity sweep in `main.py`) that: applies IL1 relaxation, advances IL3 pressure accumulators, increments idle counters, and checks IL7 dream eligibility.
- **Offline catch-up on startup**: because IL1 relaxation and IL3 accumulation are closed-form in Δt, a restart after a gap applies the entire gap in O(1) — no tick replay. The companion's state on wake reflects the absence: affect settled toward baseline, relational pressure grown. Catch-up itself is pure arithmetic — it never runs dream passes inline. Instead, if the gap contained ≥ 1 eligible night window, it schedules at most **one** deferred catch-up dream (over the gap's most significant material) for the next idle window, regardless of gap length — a 3-week absence yields one wake-up dream, not 21 backfilled ones.
- Catch-up writes one `presence_catchup` audit row (gap length, components applied) for inspectability.

**Acceptance**: a simulated 3-week gap produces the same closed-form state (affect, pressures) as 30,240 individual ticks within float tolerance — dream effects excluded from the equivalence, since catch-up intentionally defers to a single dream; startup catch-up completes < 50 ms with zero LLM calls; no behavioral output is generated during catch-up itself (deferred to normal gating).

### IL3 — Drive Accumulators and Push Initiative

Named pressure scalars `P_i ∈ [0,1]`, each with its own source, growth rule, threshold `θ_i`, and reset condition:

| Drive | Fed by | Grows when | Resets when |
|-------|--------|-----------|-------------|
| `unresolved_thread` | foresight signals (F8), open intentions, unanswered questions from last session | an open item's time horizon approaches | item resolved or surfaced |
| `pattern_insight` | pattern synthesis / contradiction scan outputs not yet shared | sleep-time run produces a shareable finding | finding surfaced |
| `relational` | days since contact vs. learned contact cadence | elapsed time exceeds the relationship's own rhythm | any user turn |
| `novelty` | topic diversity of recent sessions | conversations stay repetitive while energy is high | novel topic discussed |
| `dream_residue` | IL7 dream journal entries flagged share-worthy | a dream references high-significance material | dream surfaced |

**Firing rule**: on each presence tick, if `P_i ≥ θ_i` for any drive AND all gates pass, emit an initiative candidate tagged with the dominant drive. Gates (all must pass):

1. `presence_config` has initiative enabled (**off by default**).
2. Outside user-configured quiet hours.
3. Global cooldown elapsed — base 24 h, scaled down to 8 h as relationship closeness (from the self-model `human` block) increases, scaled up after any unanswered initiative.
4. Daily/weekly rate caps (default 1/day, 3/week).
5. User is idle (no active session).

**Delivery**: a new outbound channel (OS notification via the Tauri shell, or adapter-specific push). Message generation is a single small-LLM call carrying the drive tag, current affect rendering, and the specific accumulated material — with an explicit instruction that the message must be *about the drive's content*, never generic check-in filler ("how are you" is prohibited output).

**Every fired initiative is provenance-logged** (drive, pressure values, gate states, generated text) so the user can always answer "why did it message me?"

**Acceptance**: no initiative can fire with the feature disabled (default); rate caps and quiet hours enforced in tests; every message traceable to a named drive with recorded pressure; unanswered initiatives back off.

### IL4 — Latent Trace Crystallization

A `latent_traces` table: `(topic_key, kind, weight, evidence_refs, first_seen, last_seen)`.

- When `consolidation.py` scores a `MemoryCandidate` below the promotion threshold but above a floor (default 0.25× threshold), it is folded into a latent trace additively: `weight ← min(1.0, weight + candidate_score · 0.5)` per matching topic, evidence ref appended. The update is a leaky integrator — additive growth so repeated weak signals genuinely accumulate (an EMA would converge to the candidate score and never crystallize), with the weekly decay below as the leak.
- Traces decay on the slow path (weekly sleep-time sweep, `weight ×= 0.98`).
- When a topic's cumulative weight crosses the crystallization threshold `θ_c`, the sleep agent synthesizes a single memory item from the accumulated evidence refs — flagged `origin: crystallized`, with provenance listing every contributing trace — and clears the topic.
- **Right-to-forget integration**: latent traces are inside the F7 deletion boundary. Explicit forgetting (single-item and topic-scoped) deletes matching latent traces and scrubs any `evidence_refs` pointing at forgotten sources; a trace whose evidence is emptied is deleted. As defense in depth, crystallization re-validates every evidence ref at synthesis time and drops refs that no longer resolve — a trace must never synthesize a memory from evidence the user asked to remove.

Ten passing mentions of work stress across a month become one well-evidenced memory, instead of ten dropped candidates.

**Acceptance**: sub-threshold candidates are never silently dropped when above floor; crystallized memories carry complete evidence provenance; trace table is bounded (cap + decay); duplicate-topic churn does not double-count (dedup via existing claim slots); explicit forget of a topic or source removes matching traces/refs, and crystallization from stale refs is impossible (tests).

### IL5 — Forgetting as Distillation (extends F7)

When passive decay (F7) takes an episode or fact below the visibility floor AND its memory class (`memory_class` in `memory_salience.py`, not `stability_class`) is `casual`, `transient`, or `emotional_pattern`:

1. **Distill**: fold its affective/topical signature into a semantic tendency claim (namespace `tendency`, e.g. "low-grade recurring frustration around commuting").
2. **Ledger**: write a `tendency_contributions` row `(tombstone_id, tendency_claim_id, contribution_vector)` — numeric signature deltas only, no content. The tendency's value is defined as a recency-weighted aggregate *recomputable from its surviving ledger rows*, so any single contribution can be removed exactly later.
3. **Tombstone**: replace the item with a minimal shadow row — memory class, affect label, and time range retained; content, embeddings, and evidence cryptographically deleted per F7 rules.
4. **Audit**: one `forget_audit_log` row marked `mode: distilled`.

`identity`, `life_event`, and `relationship` classes are exempt — they follow existing F7 semantics only. User-initiated forgetting (explicit delete) remains total deletion, *including* for already-distilled items: the tombstone's ledger rows are deleted and each affected tendency is recomputed from its remaining contributions (deleted entirely if none remain). Right-to-forget takes precedence over distillation, and the ledger itself retains nothing that would reconstruct deleted content.

**Acceptance**: distilled tendencies appear in retrieval as semantic claims with `origin: distilled`; tombstones and ledger rows are content-free (verified against export); explicit deletion of an already-distilled item removes its ledger rows and recomputes affected tendencies exactly (property test: distill → forget ≡ never distilled); anchored classes never distill.

### IL6 — Recall Reconsolidation (extends F2)

When a memory item is actually rendered into the model's context (not merely scored):

- Its salience components shift toward the current context by step η = 0.05: recency-of-relevance refreshed, emotional salience nudged toward the current turn's detected affect, stability class re-evaluated only upward (recall is evidence of durability).
- Cumulative reconsolidation drift per item is capped (Σ|Δ| ≤ 0.3 lifetime) and every adjustment writes a provenance entry, so the original extraction remains reconstructable.
- `identity`-class memories are exempt from affect nudging (confidence refresh only).

This closes the loop F2 opened: access already boosts heat; now access also *updates* the trace, which is what the reconsolidation literature actually shows.

**Acceptance**: reconsolidation fires only on context inclusion; drift is capped and provenance-logged; original values recoverable from provenance; exemptions enforced.

### IL7 — Dream Cycle (extends F5)

**Eligibility** (checked on presence tick): user idle ≥ 4 h AND local time within the night window (00:00–06:00) AND at most one dream per night.

**Material selection** — deliberately *not* recent context: K = 3 items sampled by `significance × coldness` (important but cold), plus any latent traces above 0.5 weight, plus one random old user-utterance fragment from the transcript archive. Raw F2 heat is unbounded above 1, so `coldness = 1 − rank_normalized(heat)` within the candidate pool — never `1 − heat` directly, which can go negative and corrupt sampling weights.

**Processing**: one small-LLM reflection pass (extraction model, not the main agent model) recombining the material into a short dream narrative. Effects:

- Bounded affect deltas at 25 % of normal turn strength (IL1).
- A `dream_journal` entry (narrative, source refs, affect delta, timestamp) — rolling cap of 30.
- Share-worthy dreams (referencing high-significance material) raise `dream_residue` pressure (IL3).
- Touched memories get a reduced-strength reconsolidation pass (IL6 at η = 0.02).

Surfacing is gated by `presence_config` (`dream_sharing: off | on_ask | ambient`). Default `on_ask`: the companion mentions a dream only if the user asks what it's been up to, or if IL3 fires on dream residue.

**Acceptance**: dreams never run while a session is active; per-night cap enforced; journal entries carry full source provenance; affect deltas respect the 25 % scale; no dreams touch `identity`-class memories' content.

---

## 5. Architecture Rules

- All state updates are pure functions `(state, event, Δt) → state` in a new `services/agent/inner_life/` package; side effects (DB writes, notifications) live at the edges.
- All scalars bounded and clamped at write time; all thresholds/taus in config with the defaults above.
- Nothing in this PRD calls an LLM except: initiative message generation (IL3, on fire) and dream narrative generation (IL7, ≤ 1/night). Everything else is arithmetic.
- All new state lives in the runtime store and is rebuildable except `dream_journal`, `latent_traces`, tendency claims, distillation tombstones, and the `tendency_contributions` ledger, which are soul-store (portable, encrypted, included in vault export/import). The ledger and tombstones cannot be rebuilt — their source content is deleted — and right-to-forget for already-distilled items depends on them surviving rebuilds and export/import.
- Every user-visible behavior (initiative message, dream mention) must be traceable to logged pressures/sources — no unexplainable outputs.

## 6. Success Metrics

- Initiative acceptance: > 60 % of fired initiatives receive a user reply (vs. baseline pull-based nudge engagement); unanswered-initiative rate triggers automatic back-off.
- Continuity perception: returning after ≥ 1 week, the greeting references evolved state (settled affect, grown relational pressure, dream material) rather than only the timestamp.
- Memory yield: ≥ 15 % of crystallized memories are later retrieved into context (proof that sub-threshold accumulation captures real signal).
- Zero initiative messages with the feature disabled, in quiet hours, or above rate caps — ever.
- All dynamics test-covered with property tests; offline catch-up equivalence verified.

## 7. Out of Scope

- Multi-user presence semantics (follows F14).
- Autonomous actions while idle (tool calls, purchases, messages to third parties).
- Voice/call-based initiative delivery (voice-foundation-v1 owns delivery channels).
- Mood-based refusal or capability modulation (affect shapes register, never competence).
- Any biochemical/neural simulation framing.

## 8. Related Existing Work

- [F2 Heat Scoring](../memory/F2-heat-scoring.md)
- [F5 Async Sleep-Time Agents](../memory/F5-async-sleep-agents.md)
- [F7 Intentional Forgetting](../memory/F7-intentional-forgetting.md)
- [F8 Foresight Signals](../memory/F8-foresight-signals.md)
- [Single-User Temporal Memory v2](../memory/single-user-temporal-memory-v2.md)
- [Brain System Architecture](../../architecture/agent/brain-system.md)

## 9. External References

- Russell, J. A. (1980). A circumplex model of affect. *JPSP*.
- Sterling, P. (2012). Allostasis: a model of predictive regulation. *Physiology & Behavior*.
- Zeigarnik, B. (1927). Das Behalten erledigter und unerledigter Handlungen.
- Nader, K., Schafe, G. E., & LeDoux, J. E. (2000). Fear memories require protein synthesis in the amygdala for reconsolidation after retrieval. *Nature*.
- Wilson, M. A., & McNaughton, B. L. (1994). Reactivation of hippocampal ensemble memories during sleep. *Science*.
- Stickgold, R. (2005). Sleep-dependent memory consolidation. *Nature*.
- McClelland, J. L., McNaughton, B. L., & O'Reilly, R. C. (1995). Why there are complementary learning systems. *Psychological Review*.
