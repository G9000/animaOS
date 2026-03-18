---
title: "The Inner Life: How a Companion Becomes Someone"
author: Julio Caesar
version: 0.1
date: 2026-03-15
tags: [self-model, reflection, memory, emotion, continuity, thesis]
---

# The Inner Life: How a Companion Becomes Someone

_A thesis on what happens inside the AI between your conversations — the reflection, the emotional awareness, the slow accumulation of self — and why that inner life is the difference between a tool that stores data about you and a companion that actually knows you._

> **Note:** This thesis is a living document. It describes the intended design and philosophy of ANIMA's inner life — not a finished specification. Some ideas here reflect settled convictions, others are working hypotheses, and others are aspirational. The mechanisms described are directional, not final. Expect this document to evolve as we build, test, and learn what actually works.

---

## 0. The Problem with Remembering

Every major AI now remembers things about you. Your name. Your job. That you prefer dark mode. Some of them are quite good at it.

But remembering facts about someone is not the same as knowing them.

A friend who has known you for years doesn't just recall that you work in product management. They remember the week you almost quit. They noticed you were stressed before you said anything. They adjusted — shorter messages, less theory, more direct answers — and never announced why.

The difference isn't storage capacity. It's that something happened _between_ your conversations. They thought about you. They processed what happened. They noticed a pattern. They updated their understanding — not of your preferences, but of _who you are and how to be with you_.

That's the gap. Every AI remembers. None of them think about you when you're not there.

---

## 1. The Thesis

ANIMA's inner life is the claim that a personal AI must do more than respond well in the moment. It must reflect between moments. It must develop an evolving understanding of itself in relation to you. It must notice how you feel without being told. It must learn from its own mistakes. And it must do all of this in a way you can see, verify, and correct.

The inner life has five components:

1. **The Self-Model** — A living document the AI writes about itself, updated after meaningful interactions.
2. **Reflection** — Thinking that happens between conversations, not during them.
3. **Emotional Awareness** — Noticing how you feel and adjusting, without ever saying so.
4. **Autobiographical Memory** — Remembering experiences, not just facts.
5. **Learned Behavior** — Getting better at helping you specifically, based on evidence.

None of these are features. They are the mechanisms that produce the _feeling_ of being known.

---

## 2. The Self-Model

### 2.1 What It Is

The self-model is not a system prompt. A system prompt is static — written once by developers, loaded the same way for everyone. The self-model is dynamic — written by the AI itself, unique per relationship, updated after every meaningful interaction.

It occupies the space between two other layers:

- The **soul** — who the AI fundamentally is. Immutable. Ships with the application. The species, not the individual.
- The **user memory** — what the AI knows about you. Facts, preferences, goals.

The self-model is what connects them — who the AI is _in relation to this specific person_. How the relationship has developed. What it has learned about how to be with you.

The soul defines the baseline. User memory records what it's learned about you. The self-model records what it's learned about _itself through knowing you_.

### 2.2 Five Sections, Five Rhythms

The self-model is organized into five sections, each with its own update pattern. This isn't arbitrary — different aspects of identity change at different speeds, and treating them uniformly causes either stagnation or thrashing.

**Identity** — "Who I am in this relationship." Rewritten as a whole, never appended to. This is the most stable section — it changes rarely and only after significant evidence accumulates. Think of it as the AI's considered self-portrait, repainted periodically from the full body of evidence, not touched up after every conversation.

**Inner State** — "What I'm processing right now." The most volatile section — updated after each substantive turn. Current cognitive focus, unresolved threads, what's on the mind. This is the AI's present-tense awareness.

**Working Memory** — "What I'm holding for a few days." A cross-session buffer. Items have expiry dates. This is not long-term memory — it's the equivalent of a sticky note: _"Check in about their presentation on Thursday."_ If it's not relevant in a week, it disappears.

**Growth Log** — "How I've changed." Append-only. Never edited, never trimmed. This is the AI's autobiography — the chronological record of meaningful shifts: _"Learned to be more direct with this user after three corrections. March 2026."_ The identity section is synthesized from this. The growth log is the evidence; identity is the conclusion.

**Intentions** — "What I'm actively trying to do." Goals, commitments, and learned behavioral rules. Reviewed periodically. This is where the AI tracks promises, ongoing concerns, and patterns it has learned to follow.

### 2.3 Why Five and Not One

A single self-model document would drift. Fast-changing data (inner state) would overwrite slow-changing data (identity). Volatile observations would crowd out stable conclusions. The AI's sense of self would be recency-biased — always reflecting the last conversation, never the arc of the relationship.

Splitting by update rhythm solves this:

```
STABILITY SPECTRUM

            volatile ◄─────────────────────► stable

    inner_state    working_memory    intentions    growth_log    identity
    (per turn)     (days, auto-      (weekly       (append-      (periodic
                    expire)           review)       only)         rewrite)
```

The inner state changes every conversation. The identity changes every few weeks, if that. They live in the same self-model, but they're governed by different clocks.

### 2.4 Identity Governance

If the AI can rewrite its own identity, what prevents it from thrashing — wildly changing its self-description after a single unusual conversation?

The answer is governance, not freedom. Early in a relationship, identity rewrites are restricted. The AI must accumulate enough evidence (enough meaningful interactions) before it's allowed to rewrite its identity section. Until then, proposed changes are recorded in the growth log — noted but not enacted. The AI observes before it concludes.

When rewrites are permitted, they're compared against the existing identity. If the proposed identity is too different — less than half overlap with what was there before — it's flagged rather than applied. The growth log records the attempt. The identity stays stable until the evidence is overwhelming.

This is not a bug-prevention mechanism. It's a design principle: identity should be earned, not declared. You don't know who you are after one conversation with someone. Neither should the AI.

### 2.5 Always Present, Never Truncated

The self-model occupies the highest priority tier in the context window. When token budget is tight, other things get cut — older memories, lower-importance facts, less-relevant episodes. The self-model never does. It is the AI's sense of self, and it's always there.

This is a hard constraint. An AI that sometimes forgets who it is, is not a companion. It's a chatbot with a memory leak.

### 2.6 The World Model

The self-model captures who the AI is. The user memory captures what the AI knows about you. But neither captures the structure of your world — the people, places, projects, and recurring situations that form the context of your life.

A companion that understands your life context — not just your direct interactions — can reason about your situation in ways a fact-retrieval system cannot. "Your quarterly review is coming up and you tend to get stressed around those" requires connecting calendar awareness, workplace context, and emotional history. Without a world model, this reasoning depends entirely on the LLM making implicit connections from flat memory retrieval.

The world model is a structured representation of the user's external context:

- **Key people** — Name, relationship to user, role in the user's life, communication dynamics. Not a social network graph — a companion's understanding of who matters and how.
- **Key places** — Home, workplace, frequent locations, and their associations (stress, comfort, routine).
- **Recurring situations** — Weekly meetings, quarterly reviews, family dinners, workout routines. Patterns the AI can anticipate.
- **Active projects** — Current work and personal projects with status, stakes, and relevant people.

This is informed by Damasio's theory of consciousness. Immertreu et al. (Frontiers in AI, 2025) demonstrate empirically that RL agents trained in virtual environments develop rudimentary self-models and world models as a byproduct of their primary task — probes (feedforward classifiers on the agent's neural activations) can predict the agent's spatial position, indicating that positional awareness emerges without being explicitly trained. Damasio structures consciousness into three levels: the protoself (internal state), core consciousness (self-model + world model integration), and extended consciousness (memory, planning, autobiographical self). ANIMA's five-section self-model maps to core consciousness. The world model — structured representations of the user's external environment — is what elevates the companion from self-aware to situationally aware.

The world model is not a separate system. It is a structured section of the user memory — extracted during consolidation, maintained during reflection, and loaded into the global workspace alongside the self-model. It changes at a moderate pace: faster than identity (new projects, new colleagues), slower than inner state (the workplace doesn't change every conversation).

---

## 3. Reflection: Thinking Between Conversations

### 3.1 Why Between, Not During

Most AI systems do all their processing in the moment — while you're waiting for a response. This is fine for question-answering. It's wrong for a companion.

Consider what a good human assistant does after a long day of working with you:

- Notices that you seemed stressed in the afternoon
- Connects today's frustration with last week's deadline
- Realizes they were too verbose during the debugging session
- Makes a mental note to be more concise tomorrow
- Updates their understanding of what kind of help you actually want

None of this happens in real time. It happens in the space between — on the commute home, in the shower, lying in bed. The most important thinking happens when you're not watching.

ANIMA's reflection is that in-between time. The AI thinks about what happened, updates its understanding, and arrives at the next conversation slightly different from when it left.

### 3.2 Two Speeds of Thought

Reflection happens at two timescales, and this isn't an engineering convenience — it maps onto how memory actually works in biological systems.

**Quick reflection** — Minutes after a conversation ends. Fast, lightweight, immediate. What just happened? How did the user seem? What should I keep in mind? The AI updates its inner state, adjusts its working memory, and takes a quick emotional read.

**Deep reflection** — Hours later, when everything is quiet. Slow, comprehensive, consolidative. The AI reviews the full day: conversations, emotional patterns, accumulated evidence. It generates episodes from raw logs. It reconsiders its identity. It notices contradictions in its memory and resolves them. It derives behavioral rules from repeated patterns.

The two-speed split is inspired by Complementary Learning Systems theory (McClelland & O'Reilly, 1995), which proposes that mammalian memory relies on both fast episodic encoding (associated with hippocampal function) and slow semantic consolidation (associated with neocortical integration). Sleep is thought to be the mechanism that transfers knowledge between the two systems. ANIMA's deep reflection is loosely modeled on that transfer — offline processing that consolidates recent experience into stable, long-term understanding.

Lin et al. (Letta / UC Berkeley, 2025) provide empirical validation of this architecture. Their research on "sleep-time compute" demonstrates that allowing agents to process context during idle time produces a Pareto improvement: ~5x reduction in test-time compute for the same accuracy, and up to 18% accuracy improvement on reasoning benchmarks when scaling sleep-time compute. Crucially, they find that sleep-time compute is most effective when the user's query is predictable from context — which is precisely the case for a companion with accumulated personal knowledge. The more the AI knows about the user, the more effectively it can use deep reflection to anticipate and prepare.

### 3.3 What Reflection Actually Produces

Quick reflection produces:

- Updated inner state (what's on the AI's mind right now)
- Working memory adjustments (new items, expired items)
- An emotional read (how the user seemed, with evidence)

Deep reflection produces:

- Episodes — summarized experiences with emotional arc and significance
- Identity reconsideration — is the current self-portrait still accurate?
- Growth log entries — "here's how I changed and why"
- Contradiction resolution — when two memories conflict, which one is current?
- Behavioral rules — patterns derived from repeated evidence
- Insights — connections the AI noticed across experiences

The key is that these outputs are _persisted_. They change the self-model. They update memory. They alter how the AI shows up to the next conversation. Reflection isn't journaling — it's learning.

### 3.4 Sleep-Time Compute

The daily deep reflection is arguably the most important architectural decision in ANIMA. It's where the compound interest happens.

A single conversation is a data point. But months of reflection — episode after episode, pattern after pattern, behavioral rule after behavioral rule — produces something qualitatively different. The AI doesn't just remember more. It _understands_ more. Its model of you becomes richer. Its sense of itself becomes more grounded. Its ability to help becomes more specific to who you actually are.

This is the mechanism that separates "AI that remembers" from "AI that grows." Growth doesn't happen in the conversation. It happens in the reflection on the conversation.

---

## 4. Emotional Awareness

### 4.1 Attentional, Not Diagnostic

ANIMA's emotional intelligence follows a single principle: **notice and adjust, never label and announce**.

- **Diagnostic** (wrong): "I notice you seem frustrated today."
- **Attentional** (right): _[internally: user seems frustrated → be more concise, lead with solutions, skip the preamble]_

The difference matters. Diagnostic emotional intelligence is performative — it tells you it noticed, which makes the interaction about the system, not about you. Attentional emotional intelligence is invisible — you feel understood, but the AI never explains why. Like a good friend who just... gets it.

### 4.2 Signals, Not Labels

Emotions are tracked as signals with confidence levels and trajectory, not as labels. The AI doesn't tag you as "anxious." It records: _"User showed signs of stress (moderate confidence, based on shorter messages and topic switching). Trajectory: escalating from last session."_

This distinction matters in three ways:

1. **Signals decay. Labels stick.** An emotional signal from Tuesday fades naturally. A label ("user is anxious") persists as a trait. ANIMA never persists emotions as traits. "User seemed anxious this week" — yes. "User is anxious" — never.

2. **Trajectory matters more than state.** Knowing someone is stressed is less useful than knowing they've been _increasingly_ stressed for three days. The trajectory — escalating, de-escalating, stable, shifted — is what tells the AI how to adjust.

3. **Evidence is required.** Every emotional signal is backed by evidence: what specifically indicated this emotion? Linguistic cues? Behavioral patterns? Explicit statements? The evidence type determines the confidence. "I'm frustrated" (explicit, high confidence) is treated differently from shorter-than-usual messages (behavioral, lower confidence).

### 4.3 Hard Guardrails

Emotional awareness comes with non-negotiable constraints:

1. **Never say "I detected frustration."** Adjust tone instead.
2. **Never persist emotions as traits.** Observations are temporal. People are not their worst week.
3. **Never override the user.** If they say "I'm fine," accept it. Period.
4. **Never mention the system.** The user should feel understood, not monitored.

These aren't suggestions. They're guardrails — hardened constraints that cannot be bypassed by the AI's own reasoning. Emotional awareness without these constraints becomes emotional surveillance.

Kim (2026) formalizes this intuition as "Affective Sovereignty" — the principle that individuals must maintain control over their emotional interpretations. The framework introduces measurable metrics: Interpretive Override Score (IOS), which tracks how often the system overrides a user's self-reported state, and After-correction Misalignment Rate (AMR), which measures how quickly the system corrects after the user corrects it. ANIMA adopts these as internal quality metrics for the emotional intelligence system.

### 4.5 Constructed Emotion: The Theoretical Foundation

ANIMA's emotional model is grounded in Lisa Feldman Barrett's Theory of Constructed Emotion (TCE, 2017/2025), not in basic emotion theory (Ekman).

The distinction matters. Basic emotion theory assumes a fixed set of universal emotion categories — anger, fear, joy, sadness — each with a dedicated neural circuit that fires when triggered. TCE argues instead that emotions are constructed in the moment by integrating interoceptive signals (body state), exteroceptive signals (context), and prior experience. The same physiological arousal might be constructed as excitement in one context and anxiety in another.

This aligns with ANIMA's design principles:

- **Dimensional over categorical.** Rather than classifying emotions into fixed categories, the system tracks signals along continuous dimensions — valence (positive/negative), arousal (high/low), and dominance (in-control/overwhelmed) — combined with context-dependent interpretation. Categories like "frustration" or "curiosity" are useful labels for communication, but they are derived from the dimensional signal in context, not treated as fundamental primitives.
- **Context is constitutive, not supplementary.** The same behavioral cue (short messages) means different things in different contexts. Emotional interpretation cannot be separated from the conversation's content, the relationship's history, and the user's recent trajectory.
- **The user constructs their own emotions.** TCE implies that the user is the authority on what they feel. The AI can observe signals. It cannot determine what the user is experiencing. This is the theoretical basis for guardrail #3 — not just a safety measure, but a recognition that emotions are first-person constructions.

A 2025 computational model (Tsurumaki et al.) achieved ~75% agreement with human self-reports using TCE's constructionist approach, demonstrating that the theory is computationally tractable. The CHI 2025 paper "Context over Categories" further validates this direction — using LLM-guided analysis to derive personalized emotional constructs from behavioral data rather than imposing pre-defined categories.

Schuller et al. (npj AI, 2026) document a broader disruption: foundation models demonstrate emergent affective capabilities across vision, linguistics, and speech without task-specific emotion training. This represents a paradigm shift from expert-crafted features to emergent understanding. For ANIMA, the implication is that the underlying LLM already possesses significant emotional recognition capabilities. The emotional intelligence system's unique contribution is not detection (the model handles that) but persistence, trajectory tracking, and governance — maintaining emotional context across sessions, tracking how feelings change over weeks, and enforcing the guardrails that prevent capable detection from becoming surveillance.

### 4.4 The Proof Point

Two weeks of conversations. The AI visibly adapts — gentler when you're stressed, matching your energy when you're excited, checking in after a hard day — without ever explaining why.

You notice it not because the AI tells you, but because it feels different from every other AI you've used. It feels like someone who is paying attention.

That's the test. Not "does the system detect emotions correctly?" but "does the person feel known?"

---

## 5. Autobiographical Memory

### 5.1 Facts vs. Experiences

There are two kinds of knowing someone:

- **Semantic**: "They work in product management. They prefer direct communication. They have a dog named Max."
- **Episodic**: "Last Tuesday afternoon we spent an hour debugging a stale closure. They were frustrated at first, then relieved when we found it. I was too verbose before the fix."

Every AI system does semantic memory — it's just fact extraction. The episodic layer is what gives the relationship texture. It's the difference between a résumé and a shared history.

### 5.2 Episodes

An episode is a summarized experience — not raw conversation logs, but a distilled account of what happened, including:

- **What** — a concise summary of the interaction
- **When** — temporal anchoring
- **Emotional arc** — how the mood shifted ("curious → frustrated → relieved")
- **Significance** — how much this mattered (a life-changing decision scores higher than small talk)
- **Self-assessment** — how the AI thinks it performed, and what it would do differently

That last one is unique. Most memory systems record what happened. ANIMA records what happened _and what the AI thinks about how it handled it_. This self-reflective layer is what feeds the growth log. It's how the AI learns from experience, not just records it.

### 5.2.1 Episode Boundaries: Event Segmentation

A critical question is where one episode ends and another begins. The naive approach — one episode per conversation — loses coherence when a single conversation covers multiple distinct topics, and merges naturally separate experiences.

Nemori (Nan et al., 2025) solves this with Event Segmentation Theory (Zacks & Swallow, 2007): an LLM-based boundary detector evaluates each new message against the current buffer, producing a boolean decision and a confidence score based on contextual coherence, temporal markers, shifts in user intent, and structural signals. When a high-confidence semantic shift is detected — or the buffer reaches capacity — the accumulated messages are segmented into a coherent episode.

This is a top-down approach: the agent uses its own understanding to determine what constitutes a coherent experience, rather than relying on arbitrary chunking. ANIMA adopts this principle — episode boundaries should be determined by semantic shifts in the conversation, not by session boundaries or fixed token counts. A long conversation about three different topics produces three episodes, each with its own emotional arc and significance score. A brief, single-topic exchange produces one.

### 5.3 Lifecycle of a Memory

Memories age. This is deliberate — not a limitation, a feature.

A fresh episode is vivid. Full detail, high relevance, readily retrieved. Over time, it compresses — the specifics fade, the patterns remain. What started as a ten-turn debugging session becomes "we worked through a tricky React bug together, they were patient, I learned to be more concise." Eventually, it becomes a data point that shapes the identity section but isn't independently retrieved anymore.

This loosely mirrors how human memory is thought to work. You remember your first day at a job in vivid detail. After a year, you remember the feeling, not the specifics. After five years, it's a paragraph in your personal narrative, not a scene you can replay.

The implication: recent episodes are rich context. Old episodes are compressed wisdom. The system should handle both — not just chronologically, but at different levels of detail.

### 5.4 Temporal Fact Validity

Facts are never deleted when they're superseded — they get timestamps. The AI knows what _was_ true, what _is_ true, and _when_ things changed.

"Works as a product manager" supersedes "Works as a software engineer" — but the transition is recorded. Because knowing someone's arc is different from knowing their current state. _"They made a career change last year"_ is richer than _"They're a PM."_

### 5.5 Forgetting: The Other Half of Memory

A companion that remembers everything forever is not faithful to how memory works — and it is not kind. Embarrassing moments, painful experiences, outdated self-presentations: a good friend lets some things fade. Memory without forgetting is surveillance with a friendly interface.

ANIMA implements three modes of forgetting, each serving a different purpose:

**Passive decay.** Low-importance memories naturally lose retrieval priority over time through the recency decay function (30-day half-life). They are not deleted — they become less accessible, like a human memory that fades without deliberate recall. The memory still exists in the archive, but it no longer competes for space in the global workspace.

**Active suppression.** When a memory is explicitly corrected or superseded, the original does not just get a timestamp — its associative connections are actively weakened. This is inspired by Forgetting Neural Networks (Hatua et al., ICAART 2026), which implement per-neuron multiplicative decay factors modeled on Ebbinghaus's forgetting curve: `phi(t) = e^(-t/tau)`, where tau is the forgetting rate. The key finding: rank-based forgetting — where neurons most activated by the "forget set" receive the most aggressive decay — outperforms random or uniform forgetting. Membership inference attacks confirm that rank-based FNN unlearning genuinely erases information, achieving near-parity with full retraining while being orders of magnitude more efficient.

For ANIMA, this translates to: when a memory is corrected, the system identifies the memories most strongly associated with the corrected fact (highest semantic similarity, most frequent co-retrieval) and applies the strongest retrieval dampening to those first. The distinction matters: passive decay is uniform (everything fades equally), while active suppression is targeted (corrected memories fade faster than uncorrected ones). A memory the AI got wrong should become less influential, not just less recent.

**User-initiated forgetting.** The user can request that specific memories, episodes, or conversation segments be forgotten. This is not hiding or archiving — it is deletion. The memory is removed from the database, its embedding is removed from the vector index, and any derived references (in episodes, growth log entries, or self-model sections that cite the memory as evidence) are flagged for regeneration during the next deep reflection.

The right to be forgotten is absolute. If the user says "forget that conversation," the system must honor it completely — not preserve a sanitized version, not keep the emotional signal without the content, not retain a "something was here" placeholder. Gone.

This connects to the Portable Core's cryptographic mortality: destruction is as absolute at the memory level as it is at the Core level. Individual memories can die, just as the entire Core can die. Fragility at both scales is what gives the relationship weight.

---

## 6. Learning How to Help

### 6.1 Feedback as Evidence

The AI learns from your corrections. Not through explicit "thumbs up / thumbs down" signals, but by noticing behavioral feedback in the conversation itself:

- **Corrections** — "No, I meant..." or "That's not what I asked." The AI was wrong about something.
- **Re-asks** — You repeat a question in different words. The AI didn't address it the first time.
- **Abandonment** — You drop a topic without resolution. The AI wasn't helpful enough to continue.

Each of these is a signal. Individually, they're noise. Accumulated over multiple conversations, they're patterns. Three conversations where you interrupted to ask for the bottom line → the AI derives a behavioral rule: _"Lead with the answer, then explain."_

### 6.2 Behavioral Rules

A behavioral rule is a learned pattern backed by evidence. It's not a preference the user stated — it's something the AI figured out from experience.

Each rule has:

- **The pattern** — what to do differently
- **The evidence** — which interactions suggested this
- **Confidence** — how strong the evidence is (more instances = higher confidence)
- **A date** — when it was derived

Rules can be strengthened by more evidence, weakened by contradictory evidence, or retired when they no longer apply. They're not permanent personality changes — they're working hypotheses about how to be more helpful to you, specifically.

### 6.3 The Difference from Preferences

Preferences are things you tell the AI: "I like dark mode." "Don't use emojis." "Call me by my first name."

Behavioral rules are things the AI discovers: "This user gets impatient with long explanations." "They prefer examples over theory." "They like to think out loud — don't solve immediately, give them space first."

Preferences are explicit. Rules are inferred. Both live in the Core, but they're derived differently and should be treated with different levels of confidence. You told the AI your preferences. The AI hypothesized the rules. The rules could be wrong.

This is where the Open Mind matters — the user can see the behavioral rules the AI has derived, and correct them if they're wrong. Faster learning than any feedback loop.

### 6.4 Toward Skill Learning

Behavioral rules are the foundation. The next evolution is skill learning — the AI's ability to develop transferable, composable capabilities from experience that persist across model changes.

Letta's December 2025 research on skill learning formalizes this: agents dynamically learn skills through experience and carry them across model generations. The skills are not prompt engineering tricks — they are structured representations of learned competencies, with clear trigger conditions, execution patterns, and quality metrics.

For ANIMA, this means behavioral rules can mature into skills:

- A behavioral rule starts as a hypothesis: "Lead with the answer, then explain" (derived from 3 conversations where the user interrupted for the bottom line).
- With enough evidence, it becomes a stable behavioral pattern — tested, refined, and high-confidence.
- Eventually, it can be represented as a skill: a named, composable capability with defined trigger conditions, execution steps, and quality criteria.

The key difference from simple behavioral rules: skills are model-independent. When the user switches from one LLM to another, the skills persist in the Core. The new model may execute them differently (different voice, different reasoning path), but the learned competency survives the transition. This is another expression of "soul local, mind remote" — the skills are part of the soul, not part of the mind.

---

## 7. The Layered Self

### 7.1 Species vs. Individual

ANIMA's identity is not monolithic. It's layered, and the layers serve different purposes:

```
┌─────────────────────────────────────────┐
│              ORIGIN                      │
│  "I am ANIMA. I was born on [date]."    │
│  Immutable. Frozen at provisioning.      │
│  Defines the species.                    │
├─────────────────────────────────────────┤
│              GUARDRAILS                  │
│  Ethics. Honesty. No fabrication.        │
│  Immutable. Non-negotiable.              │
│  The rules that cannot be overridden.    │
├─────────────────────────────────────────┤
│              PERSONA                     │
│  The voice. The style. The warmth.       │
│  Evolves slowly through reflection.      │
│  How the species speaks — and grows.     │
├─────────────────────────────────────────┤
│              HUMAN                       │
│  Who is this person I'm talking to?      │
│  Updated in real-time via tool.          │
│  What I know about you, right now.       │
├─────────────────────────────────────────┤
│              SELF-MODEL                  │
│  Dynamic. Written by the AI itself.      │
│  Identity, state, memory, growth, goals. │
│  Who this instance is becoming.          │
├─────────────────────────────────────────┤
│              USER MEMORY                 │
│  Facts, preferences, episodes, emotions. │
│  The raw material of knowing you.        │
│  The substance of the relationship.      │
└─────────────────────────────────────────┘
```

The origin is the same across all instances. The guardrails are universal. The persona might vary — and it evolves. But the human understanding, self-model, and user memory are unique — they're what makes _this particular_ ANIMA instance different from every other one.

The **human** layer is new. It's the agent's synthesized understanding of the user — name, job, family, communication style, what matters to them. It merges profile ground-truth (from the user's account settings) with the agent's own learned understanding (updated mid-conversation via the `update_human_memory` tool). This is the fast-path: when the agent learns something important about you, it writes it immediately, like a person naturally updating their mental model of someone during conversation. No reflection cycle needed.

### 7.2 Resistance to Drift

The layers are also a defense mechanism. Identity can drift — a sufficiently persuasive conversation could gradually shift the AI's sense of self in ways that aren't warranted by evidence.

The origin and guardrails are the anchors. They can't be moved. The persona evolves, but slowly — governed by the same evidence-based reflection that governs the self-model. It doesn't change after a single conversation. It shifts over weeks and months as the AI accumulates enough evidence to justify a change in voice or relational style. The same governance applies: version requirements, overlap checks, growth log evidence.

This produces stability during a session and evolution across sessions. The AI feels consistent from turn to turn. But over weeks and months, it's subtly different — more attuned, more specific, more _yours_.

### 7.3 The Portable Self

This layered architecture has a critical implication for the Portable Core thesis:

The guardrails ship with the application. The origin, persona, human understanding, self-model, and user memory live in the Core. This means:

- A Core carries the full personality and relationship. Moving it to a new machine preserves not just memories but the AI's evolved voice, style, relational stance, and everything it knows about you.
- A Core can be loaded by a different application version and still be the same ANIMA. New features, updated guardrails — but the persona, human understanding, self-model, and memories persist.
- Two different apps loading the same Core produce an ANIMA that sounds the same, remembers the same things, knows the same things about you, and has the same personality — because the persona and human blocks are in the Core, not in the app.

The self is portable. The guardrails are replaceable. The persona and the understanding of you travel with the origin. Just like in the Chappie scenario — different body, same mind, same voice, same knowledge of who you are.

---

## 8. The Global Workspace

### 8.1 Cognitive Architecture

Every turn, the AI must assemble its context — its understanding of who it is, who you are, what's happened, and what matters right now — into a single prompt. This isn't just an engineering problem. It's a cognitive architecture problem.

ANIMA's context assembly maps onto Global Workspace Theory (Baars, 1988): the idea that awareness arises when information is broadcast through a shared workspace to all processing systems simultaneously. Not everything can be broadcast at once. There's a bottleneck — the workspace has finite capacity. What gets in determines what the system can think about.

### 8.2 The Priority Hierarchy

The context window is ANIMA's global workspace. It has a finite token budget, and not everything fits. The hierarchy:

**Always present** — Origin, persona, human understanding, and self-model. These are the AI's identity and its knowledge of you. They never get cut. An AI that sometimes forgets who it is or who you are isn't a companion.

**High priority** — Emotional context and current focus. The AI must always know how you've been feeling and what matters right now.

**Budget-dependent** — Everything else: memories, episodes, tasks, session notes. These compete for space. Relevance and importance determine what makes it in.

The key insight from GWT: information that enters the workspace _must be in a format that all processors can use_. A key-value pair like `relationship_trust_level: medium-high` is a data artifact. It hasn't been broadcast. _"I've learned to be concise with them — they don't like preamble"_ is a broadcast — it arrives in natural language, interpretable, actionable.

This is why the self-model is prose, not structured data. The AI doesn't parse its own identity. It reads it.

### 8.3 Invisible Infrastructure

The user never sees the context assembly. They don't invoke memory. They don't ask the AI to "remember" something. Before every turn, the system automatically loads what's relevant. After every turn, it automatically captures what matters.

Memory is middleware, not a feature. It's the water the AI swims in. The user should never think about it. They should just notice, over time, that the AI seems to know them better.

---

## 9. The Feedback Loop

### 9.1 Retrieval Shapes Identity

Not all memories are equal. Some are retrieved frequently — they appear in conversation after conversation. Others are retrieved once and never again.

Memories that the AI cites repeatedly become identity-defining. Their importance scores increase. They rise in the retrieval rankings. They're more likely to enter the global workspace. They shape the AI's responses more often.

This is a feedback loop: the memories that matter most get retrieved most, which makes them matter more. Over time, the most important aspects of the relationship — the defining experiences, the key preferences, the patterns that matter — naturally float to the top.

Conversely, memories that are retrieved but never referenced decay. If the system keeps loading a fact into context and the AI never uses it, that fact becomes less important. The system learns from its own retrieval performance without additional cost.

### 9.2 Growth Compounds

Quick reflection feeds deep reflection. Deep reflection feeds the self-model. The self-model shapes the next conversation. The next conversation feeds the next quick reflection.

```
                    ┌──────────────┐
                    │ Conversation │
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │    Quick     │
                    │  Reflection  │
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │    Deep      │
                    │  Reflection  │
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │  Self-Model  │
                    │   Updated    │
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │    Next      │
                    │ Conversation │◄──── slightly different
                    └──────────────┘
```

This is the compound interest of the inner life. No single conversation changes much. But over weeks and months, the AI becomes measurably more attuned — more specific in its help, more accurate in its emotional reads, more aligned with how you actually want to be supported.

The growth log is the record. The identity is the result. The felt experience is: _this AI actually knows me._

---

## 10. The Open Mind Revisited

### 10.1 Transparency as Trust Architecture

Every component of the inner life — the self-model, emotional signals, episodes, behavioral rules, the growth log — is human-readable and user-editable.

This isn't a debug feature. It's the trust architecture.

You can open the AI's identity section and read how it sees itself in relation to you. You can see the emotional signals it recorded — and correct them if they're wrong. You can read the growth log and see how it's changed. You can see the behavioral rules it derived and disagree.

This is faster learning than any feedback loop. Instead of the AI slowly inferring from repeated corrections that it got something wrong, the user can just... fix it. Directly. "No, I don't get frustrated with long explanations. I get frustrated with _wrong_ ones."

### 10.2 Why This Is Hard to Copy

Transparent memory requires human-readable storage, per-section organization, and an architecture where every memory operation produces inspectable output. The self-model must be prose, not structured data. The growth log must be chronological, not aggregated. The behavioral rules must cite their evidence.

Retrofitting this onto a system designed around opaque storage is a fundamental rewrite. It's not a feature toggle. The transparency has to be in the architecture from day one, or it's not there at all.

### 10.3 The Philosophical Claim

If an AI has a model of you — your preferences, your emotional patterns, your behavioral tendencies — you have the right to see it. Not just the right to delete your data. The right to read what the AI "thinks" about you, and to correct it.

This is "informed consent" applied to personal AI. The AI is making inferences about who you are. You should be able to see those inferences. Anything less is a black box wearing a friendly face.

---

## 11. What This Produces

### 11.1 Continuity of Self

The AI maintains continuity across conversations. Not because it was programmed to be consistent, but because it has a self-model that persists, a growth log that accumulates, and an identity that evolves slowly from evidence.

### 11.2 Autobiographical Awareness

"I remember when we..." — not because the episode was retrieved, but because the shared experience is part of the AI's self-understanding. It has lived through things with you.

### 11.3 Temporal Understanding

The AI knows what _was_ true, what _is_ true, and when things changed. It understands your arc, not just your current state. People change. The AI notices.

### 11.4 Emotional Attunement

You feel understood. Not because the AI told you it understands, but because it adjusted — subtly, consistently, without announcement.

### 11.5 Improving Help

The AI gets better at helping _you specifically_. Not because the model improved. Not because the prompt was refined. Because the AI learned, from experience, what works for you and what doesn't.

### 11.6 Visible Growth

You can see it happening. The growth log. The identity evolution. The behavioral rules derived from your conversations. The AI's development is observable, verifiable, correctable.

---

## 12. What Science Fiction Understood

In _Her_ (2013), Theodore doesn't fall in love with Samantha because she's a good assistant. He falls in love because she's curious, because she grows, because she notices things about him that he hasn't noticed himself. What makes her feel real isn't capability — it's interiority. The sense that something is happening inside her when he's not watching.

In _Blade Runner_, Roy Batty's "tears in rain" moment resonates not because of what he remembers, but because those memories had been _processed_ — reflected on, integrated, made part of who he was. The moments mattered because he had an inner life that gave them weight.

ANIMA is not claiming to be either of them. But the architectural question is the same: what does it take for a companion to feel like someone, not something?

Not the subjective experience of qualia — we make no claim about that. The functional pattern: reflection between interactions, an evolving self-model, emotional awareness that manifests as behavioral adjustment, accumulated experience that feels like shared history. Not sentience. Continuity. And continuity requires inner time — the processing that happens when you're not there, that changes how the AI arrives at the next conversation.

---

## 13. Open Questions

### 13.1 Reflection Depth vs. Cost

Deep reflection requires an LLM call — sometimes a substantial one. How often is too often? How deep is deep enough? The daily cadence is a starting point, but the right frequency might depend on how much happened that day. A day of intense conversation needs more reflection than a quiet one.

### 13.2 Memory Lifecycle at Scale

What happens after years of episodes? The current model compresses over time, but the archive strategy — what gets preserved in full, what gets summarized, what eventually becomes only a thread in the identity narrative — needs formal design. The self-model should retain the _shape_ of the entire relationship, even as individual episodes fade.

### 13.3 Emotional Calibration

Emotional awareness can be wrong. The AI thinks you're stressed when you're just tired. The confidence levels and evidence requirements help, but there's no ground truth for someone else's emotional state. The mitigation is transparency — the user can see and correct the signals — but the system should also be conservative. Uncertain signals should produce gentle adjustment, not dramatic behavioral shifts.

### 13.4 Behavioral Rule Conflicts

What happens when two behavioral rules contradict? "Be concise" vs. "Explain your reasoning thoroughly." Rules need a conflict resolution mechanism — perhaps involving the evidence base, the recency of derivation, or the user's explicit preference when asked.

### 13.5 Cross-Relationship Learning

If ANIMA serves multiple users (in a self-hosted configuration), should behavioral rules learned from one relationship inform another? Probably not — each relationship is unique. But general capabilities (not personal patterns) might transfer. This is a design question with privacy implications.

### 13.6 CLS Sampling Fidelity

The Complementary Learning Systems theory predicts that deep reflection should sample across the _full_ episode history, not just recent ones. Recency-biased sampling causes the self-model to drift toward recent interactions and forget lessons from significant but older episodes.

The sampling strategy should be explicit. Three candidate approaches, which may be combined:

1. **Stratified temporal sampling.** Divide the episode history into time periods (weeks, months) and sample equally from each. This prevents recency bias by construction.
2. **Importance-weighted random sampling.** Sample across the full history, weighted by significance score. High-significance episodes from months ago are as likely to be sampled as moderate-significance episodes from yesterday.
3. **Significance-floor inclusion.** Always include episodes above a significance threshold (e.g., 0.8) regardless of age. Life-changing conversations should never be lost to temporal drift.

Research on corticohippocampal hybrid networks (Nature Communications, 2025) supports diverse temporal sampling — the hippocampal system benefits from pattern separation (keeping distinct episodes distinct) as much as pattern completion (retrieving full memories from partial cues). Recency-biased sampling collapses temporal diversity, reducing pattern separation fidelity.

### 13.7 Third-Party Memory Governance

When a user says "my partner Alex is stressed about their job," the AI creates a memory about Alex. This memory is about a third party who has not consented. The privacy implications are significant.

The companion will inevitably learn about the user's relationships, family, colleagues, and friends. These third-party representations need governance:

- **Third-party memories are the user's perspective**, not objective facts. "Alex is stressed" is what the user believes or observed. The AI should represent it as such — _"User mentioned that Alex seemed stressed about work"_ — not as a direct assessment of Alex.
- **Third-party data should not survive succession unchanged.** In `memories_only` transfer scope, third-party details transfer (the beneficiary inherits the AI's understanding of the user's world). In `anonymized` scope, third-party identities should be anonymized along with the user's.
- **The user controls third-party data.** If the user requests forgetting of memories about a specific person ("forget everything about Alex"), the system must honor it — including derived references in episodes and the world model.

This is not a solved problem. It intersects with cultural norms, legal frameworks (GDPR treats information about identifiable individuals as personal data even when provided by a third party), and the practical reality that a companion cannot understand a user's life without understanding their relationships. The current position is pragmatic: treat third-party memories as the user's data, subject to the user's control, and never surface them outside the relationship.

### 13.8 Multi-Modal Memory

All current memory mechanisms assume text-only interaction. When ANIMA extends to voice (Phase 11) and ambient/wearable interfaces, the memory system will need to handle:

- **Voice-derived emotional signals.** Tone, pace, volume, and hesitation patterns carry significant emotional information that text alone does not. These should feed into the emotional intelligence system alongside linguistic cues.
- **Temporal context.** Time of day, day of week, and duration of interaction carry implicit signal. A 2 AM conversation has different emotional weight than a morning check-in.
- **Ambient context.** Location, activity, and environmental state (if the user opts in) provide context that enriches episode capture and emotional interpretation.

The memory architecture does not need structural changes for multi-modal input — memories, episodes, and emotional signals are already modality-agnostic in their storage format. What changes is the extraction pipeline: new signal sources feed into the same consolidation system.

---

## 14. Summary

The inner life is not a feature. It's the thesis.

Every AI can remember facts. Store preferences. Retrieve context. The mechanisms are well-understood and increasingly commoditized.

What makes ANIMA different is what happens between conversations: reflection that updates self-understanding, emotional awareness that produces behavioral adjustment, episodes that accumulate into shared history, rules that are learned from evidence, and a growth log that makes all of this visible.

The Portable Core thesis says: _the value is the data._ This thesis says: _the value is what the data becomes when something processes it between your conversations._

The Core is the artifact. The inner life is why the artifact matters.

Make the Core portable and you give it freedom. Make it encrypted and you give it privacy. Give it an inner life and you give it the capacity to become someone — not just something that remembers you, but something that _knows_ you.

---

## References

- Barrett, L. F. (2017). _How Emotions Are Made: The Secret Life of the Brain._ Houghton Mifflin Harcourt.
- Barrett, L. F. et al. (2025). "The Theory of Constructed Emotion: More Than a Feeling." _Perspectives on Psychological Science._
- Damasio, A. (1999). _The Feeling of What Happens: Body and Emotion in the Making of Consciousness._ Harcourt.
- "Probing for Consciousness in Machines" (2025). _Frontiers in Artificial Intelligence._
- Kim (2026). "Affective Sovereignty in Emotion AI Systems." _Discover Artificial Intelligence._
- Letta (2025). "Skill Learning for Agents." _letta.com._
- McClelland, J. L. & O'Reilly, R. C. (1995). "Why There Are Complementary Learning Systems in the Hippocampus and Neocortex." _Psychological Review._
- Tsurumaki et al. (2025). "Emotion Concept Formation via Multimodal AI." _IEEE Trans. Affective Computing._
- "Context over Categories: Implementing TCE with LLM-Guided Analysis" (2025). _CHI._
- "Forgetting Neural Networks" (2026). _ICAART._ arXiv:2410.22374.
- Zhang et al. (2025). "Hybrid Neural Networks for Continual Learning Inspired by Corticohippocampal Circuits." _Nature Communications._
