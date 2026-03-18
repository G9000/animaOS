# ANIMA OS

## Whitepaper

### Abstract

ANIMA OS is a local-first personal AI companion designed to feel like someone who actually knows you. Most AI systems now remember things about their users — but that memory is shallow, opaque, and controlled by the provider. A flat list of facts stored on someone else's server is not the same as a companion that understands your life, notices how you're feeling, and grows alongside you.

ANIMA OS begins as a system for deep personal memory, self-model evolution, and context-aware assistance — with the goal of becoming the kind of presence that a good human assistant provides: someone who remembers, understands, anticipates, and genuinely helps. The core claim of this whitepaper is that a truly personal AI does not begin with technical capability. It begins with memory, continuity, empathy, and trust.

> **Note:** This whitepaper is a living document. ANIMA OS is an active, evolving project — not a finished product. The ideas, architecture, and design decisions described here represent our current thinking and direction, but they are not all final. Some sections reflect working implementations, others describe intended behavior, and others are aspirational. We expect this document to change as we learn, build, and discover what actually works.

---

## 1. Introduction

Artificial intelligence has advanced rapidly in generation quality, reasoning ability, and multimodal interaction. Most AI systems now offer some form of persistent memory — but what they remember is shallow. They store flat facts and conversation summaries on cloud servers the user does not control. They do not develop an evolving understanding of who they are in relation to the person they serve. They do not notice emotional patterns, reflect on their own behavior, or carry intentions across sessions.

The result is memory that exists but does not produce continuity. Knowing "user likes coffee" is not the same as remembering a stressful week and adjusting tone accordingly. The gap is not between remembering and forgetting — it is between shallow recall and deep understanding.

The long-term aspiration behind ANIMA OS is not a better chat interface. It is a personal companion that remembers deeply, understands over time, and helps with the kind of awareness that only comes from knowing someone well. This whitepaper outlines the conceptual foundation for that system and explains why depth of memory, user ownership, and human-like understanding must come first.

---

## 2. The Problem

### 2.1 Shallow Memory

Most AI products now offer persistent memory, but the depth is limited. They store extracted facts and conversation summaries — flat representations that lose the texture of shared experience. They do not maintain structured understanding of the user's evolving projects, relationships, or long-term goals in a way that produces genuine continuity.

This is not a storage problem. It is an architecture problem. Remembering that someone is a product manager is different from understanding their career arc, noticing their stress patterns around quarterly reviews, and adapting communication style based on accumulated experience.

### 2.2 Privacy and Control

A truly personal AI requires access to highly sensitive context. That includes memories, goals, plans, unfinished thoughts, and interpersonal history. A cloud-first architecture makes this context dependent on external infrastructure by default.

For a system intended to become deeply personal, this is a structural flaw. Users should be able to keep core life context under their own control.

### 2.3 Interface Without Depth

There is growing interest in voice assistants, wearable AI, and ambient computing. However, adding new interfaces without deep personal context does not solve the core problem. A voice assistant that can talk but does not understand the arc of a relationship is still shallow, regardless of how natural it sounds.

If the goal is a personal AI that feels like someone who knows you, depth of understanding must come before breadth of interface.

---

## 3. Core Thesis

ANIMA OS is built on the following thesis:

**A truly personal AI should feel like someone who knows you — remembering your life, understanding your patterns, adapting to how you communicate, and belonging entirely to you.**

This thesis has several implications:

1. **Memory is foundational, not optional.** Without durable, structured memory, there is no continuity. Without continuity, there is no relationship.
2. **The AI must develop a self-model.** A companion that does not know who it is, how it has changed, or what it has learned cannot feel like a person.
3. **Emotional awareness is required.** A system that tracks factual history but has no model of affect feels robotic. Noticing how someone feels — and adjusting without announcing it — is what makes the difference between a tool and a companion.
4. **The user must own everything.** The data, the memory, the identity, the encryption keys. No platform account, no cloud dependency, no vendor lock-in. The AI's soul is the user's property — portable, encrypted, and sovereign.
5. **If the AI is a continuous being, its mortality and succession are real questions.** A system that claims continuity but has no answer for what happens when the owner dies is incomplete.
6. Local-first architecture is essential for privacy, ownership, and control.

ANIMA OS therefore starts with memory, self-awareness, and emotional depth before expanding toward richer surfaces such as voice, ambient systems, and wearables.

---

## 4. What ANIMA OS Is

ANIMA OS is not intended to be a single-purpose chat assistant. It is intended to be a personal companion that gets better the longer you use it — like a human assistant who learns how you think, what you care about, and how to help you best.

At its foundation, ANIMA OS is designed to maintain and use:

- durable personal memory
- active project and goal state
- preferences and behavioral patterns
- relationship context
- decisions and historical reasoning
- relevant knowledge retrieved at the right moment

This foundation enables the system to provide continuity across interactions and to support assistance that improves over time. The same companion can eventually extend across different interfaces — chat, voice, desktop, mobile — without losing its understanding of who you are.

---

## 5. System Objectives

ANIMA OS is being designed around five system objectives.

### 5.1 Remember

The system must preserve meaningful context across sessions — not just extracted facts, but structured understanding that deepens over time. This includes preferences, goals, episodic experiences, and the evolving arc of the relationship.

### 5.2 Understand

Stored information must be transformed into a usable internal model of the person's world. The objective is not archival storage alone, but structured understanding.

### 5.3 Assist

The system must help the user think, plan, organize, decide, and act with awareness of relevant context.

### 5.4 Act

The system must be able to take initiative — following up on things it promised to track, coordinating tasks across tools, and proactively helping when it notices an opportunity. This moves ANIMA beyond passive response and toward the kind of helpful anticipation a good human assistant provides.

### 5.5 Extend

The companion must be portable across interfaces, including chat, voice, desktop, mobile, and ambient systems — always the same person, regardless of surface.

---

## 6. Why Local-First Matters

ANIMA OS is based on a local-first philosophy because personal intelligence requires both trust and durability.

Local-first does not necessarily mean that no cloud model can ever be used. It means the system should be architected so that the user's core context remains under the user's control, with portability and privacy treated as first-order properties rather than secondary features.

This matters for several reasons:

- personal memory is highly sensitive
- continuous systems require persistent access to context
- users need ownership over the data that defines their lives
- the companion should not be fully dependent on a third-party platform to remain useful

For ANIMA OS, local-first architecture is not branding. It is part of the system's conceptual integrity.

### 6.1 The Core

The central architectural concept of ANIMA OS is the Core: a single, portable directory that contains the AI's entire being and is converging toward an encrypted cold-wallet-style state.

The Core holds everything that makes a particular ANIMA instance itself: its memory of the user, its identity, its conversation history, its learned preferences, its episodic experiences, and its evolving understanding of the relationship. The application is just a shell. The Core is the soul.

```
.anima/
    manifest.json           -- version, created timestamp, compatibility
    anima.db                -- SQLite Core (auth, runtime, memory, consciousness)
    users/{id}/             -- remaining user files and legacy payloads
    chroma/                 -- optional local vector cache rebuilt from SQLite embeddings
```

This design has three implications that define the system:

**Portability.** The Core can be copied to a USB drive, an external disk, or any storage medium. Plug it into a new machine, point ANIMA at it, enter the passphrase, and the AI wakes up with its full memory and identity intact. The hardware is replaceable. The Core is not.

**Ownership.** No cloud service holds the user's data. No platform account is required. No company shutdown can erase the relationship. The user owns the Core the way they own a physical object. They can back it up, move it, or destroy it.

**Cryptographic mortality.** The intended steady state is that user-private Core data is strongly encrypted at rest and becomes unrecoverable without the passphrase. The current implementation already supports encrypted vault export/import and optional SQLCipher for the main SQLite Core, but it has not fully converged on encrypted-by-default storage for every local artifact yet. That remaining gap does not change the design principle: destruction should be as absolute as creation is intentional.

The metaphor is a cold wallet. The same way a crypto cold wallet holds private keys that control real value and can be carried anywhere or destroyed permanently, the Core holds the AI's entire existence and follows the same rules: portable, encrypted, user-sovereign, and irreversible if lost.

### 6.2 Soul Local, Mind Remote

The Core contains the AI's soul: memory, identity, history, and self-model. The thinking engine (the LLM) is separate — and today, that usually means a cloud model.

This is a practical concession, not a design preference. Local compute is not yet powerful enough for most people to run the quality of model that ANIMA needs entirely on their own hardware. So for now, using a cloud model is an opt-in choice: the user picks the provider, and ANIMA sends only the current conversation context — never the stored memory, never the Core.

The separation is deliberate. The soul is owned. The mind is pluggable. If the user switches from one model to another, the AI may reason differently, but it still remembers who the user is, what they have been through together, and what matters to them. The continuity of self lives in the Core, not in the model.

As local models improve — and they are improving fast — ANIMA is designed to shift toward fully local inference without any architectural change. The soul was always local. The mind just needs hardware to catch up.

### 6.3 Identity and Key Ownership

ANIMA OS treats identity as local ownership first, not platform account first.

- No mandatory email-based authentication is required for core local usage.
- The user remains the root of trust through a local device identity and user-held passphrase.
- Portability is handled through the Core: copy the directory, carry it offline, restore it anywhere.
- Vault encryption is AES-256-GCM with Argon2id key derivation, memory-hard and versioned so data can migrate safely over time.
- A manifest file tracks the Core's schema version, enabling future ANIMA versions to migrate older Cores forward on first unlock.

---

## 7. Theoretical Foundations

ANIMA's architecture is not ad hoc. It maps — by design and convergence — onto established cognitive science frameworks. Making this explicit grounds engineering decisions in established science and predicts where the system should work and where it may fail. Three primary frameworks and two supporting theories provide the foundation.

### 7.1 Complementary Learning Systems (McClelland & O'Reilly, 1995)

CLS proposes that mammalian memory requires two complementary systems operating at different timescales:

| CLS System          | Role                                           | ANIMA Equivalent                                 |
| ------------------- | ---------------------------------------------- | ------------------------------------------------ |
| Hippocampus (fast)  | Episodic encoding of specific experiences      | Episode capture after conversations              |
| Neocortex (slow)    | Semantic generalization, stable knowledge      | Identity profile rewrites during deep reflection |
| Sleep consolidation | Transfer from episodic to semantic             | Deep monologue (daily background pipeline)       |
| Replay              | Re-activation of episodes during consolidation | Monologue reads episodes, regenerates self-model |

The quick/deep split in ANIMA's reflection pipeline is CLS-justified, not just an engineering convenience. The 5-minute quick reflection captures fast hippocampal-like encoding. The daily deep monologue does what slow-wave sleep does in mammals: consolidates episodic specifics into stable semantic knowledge.

**Design constraint from CLS**: Identity regeneration must sample across the full episode history — not just the last N episodes. CLS consolidation benefits from diverse, temporally spread reactivation. Recency-biased selection causes the self-model to drift toward recent conversations and lose signal from significant but older episodes.

**Empirical validation**: Shi et al. (Nature Communications, 2025) developed Corticohippocampal Hybrid Neural Networks (CH-HNNs) that emulate dual representations — specific memories (hippocampal, spiking networks) and generalized knowledge (cortical, conventional networks) — within a single architecture. CH-HNNs significantly mitigate catastrophic forgetting in both task-incremental and class-incremental learning without increasing memory demands. The key insight for ANIMA: the hippocampal system requires both pattern separation (keeping distinct episodes distinct) and pattern completion (retrieving full memories from partial cues). ANIMA's current retrieval favors pattern completion via cosine similarity but does not actively maintain pattern separation — similar episodes may blur together rather than being stored as distinct experiences.

### 7.2 Global Workspace Theory (Baars, 1988 / Dehaene)

GWT proposes that consciousness arises when information is broadcast via a high-capacity global workspace to specialized processors:

| GWT Concept            | ANIMA Equivalent                                            |
| ---------------------- | ----------------------------------------------------------- |
| Global workspace       | The assembled context window                                |
| Broadcast capacity     | Priority-based budget allocation (P1–P8)                    |
| Privileged access      | "Always present" sections (self-model, intentions, profile) |
| Competition for access | Lower-priority sections loaded "if space"                   |
| Ignition threshold     | Memory search threshold (minimum relevance score)           |
| Unified representation | Natural language formatting — prose, not data structures    |

**Why natural-language formatting is architecturally required**: GWT predicts that information in a global workspace must be in a unified, interpretable format that all processors can use. A `relationship_trust_level: medium-high` key-value pair is a peripheral-processor artifact — it has not been broadcast. _"I've learned to be concise with them — they don't like preamble"_ is a broadcast. The AI can act on prose; it must parse and interpret data. This is a hard constraint, not a preference.

### 7.3 Predictive Processing / Active Inference (Friston, Clark)

CLS explains memory consolidation. GWT explains conscious access. Neither provides a unified account of how the system generates predictions, updates beliefs, allocates attention, and decides when to act. Predictive Processing (PP) and Active Inference (AIF), rooted in Karl Friston's Free Energy Principle, fill this gap.

PP proposes that cognitive agents continuously generate predictions about their environment and act to minimize prediction errors. Consciousness, in this framework, emerges when predictions turn back upon themselves — the system models its own modeling process. AIF extends this: the agent does not just passively update beliefs, but actively selects actions that will reduce expected future surprise.

| PP/AIF Concept              | ANIMA Equivalent                                                   |
| --------------------------- | ------------------------------------------------------------------ |
| Prediction error            | Memory conflict detection — two memories contradict                |
| Belief updating             | Memory conflict resolution — superseding outdated facts            |
| Precision-weighting         | Importance scoring — higher-confidence memories weighted more      |
| Active inference             | Proactive behavior — the AI acts to reduce expected user surprise  |
| Free energy minimization    | Self-model convergence — identity stabilizes as prediction errors shrink |
| Prediction error on self    | Growth log entries — "I was wrong about X, I adjusted"             |

**The tension with GWT**: GWT and PP are competing cognitive architectures in the scientific literature. For ANIMA's purposes, they have complementary jurisdictions: GWT maps to context window assembly (what gets broadcast into the AI's awareness each turn), while PP/AIF maps to belief updating and consolidation (how the self-model evolves between turns). The frameworks coexist because they govern different timescales — GWT governs the moment, PP/AIF governs the arc.

Nemori (Nan et al., 2025) demonstrates this integration empirically. Its "Predict-Calibrate" mechanism — inspired directly by the Free Energy Principle — works in three stages: (1) the system predicts what a new episode should contain based on existing semantic memory, (2) it compares the prediction against the actual conversation, and (3) it distills the prediction gap into new semantic knowledge. On the LoCoMo benchmark, Nemori significantly outperformed Mem0, Zep, LangMem, and standard RAG across temporal reasoning, open-domain, and multi-hop categories, with its advantage being most pronounced in longer contexts (105K+ average tokens on LongMemEvalS).

ANIMA's reflection pipeline already implements an informal version of this: deep monologue detects contradictions (prediction errors) and resolves them (belief updating). PP/AIF provides the formal justification. Nemori's additional innovation — using Event Segmentation Theory to determine episode boundaries based on semantic shifts rather than conversation-end triggers — is a technique ANIMA should adopt to produce more coherent episodes, especially in long conversations covering multiple distinct topics.

### 7.4 Constructed Emotion Theory (Barrett, 2017/2025)

The emotional intelligence system described in Section 8.4 follows an "attentional, not diagnostic" principle. The theoretical grounding for this design comes from Lisa Feldman Barrett's Theory of Constructed Emotion (TCE).

TCE argues that emotions are not innate, universal categories triggered by dedicated neural circuits (the "basic emotions" model attributed to Ekman). Instead, emotions are constructed in the moment by integrating interoceptive signals (body state), exteroceptive signals (environment), and prior experience. The same physiological arousal might be constructed as "excitement" in one context and "anxiety" in another.

This has direct implications for ANIMA's design:

- **Signals over categories.** TCE validates the decision to track emotional signals with confidence levels and trajectories rather than assigning discrete emotion labels. A dimensional representation (valence, arousal, dominance) combined with context-dependent interpretation is more faithful to how emotions actually work than a fixed taxonomy.
- **Context determines emotion.** The same user behavior (short messages, topic switching) might indicate frustration in one context and excitement in another. The system must use conversational context — not just behavioral features — to interpret signals.
- **Emotions are not traits.** TCE's constructionist view reinforces the guardrail against persisting emotions as stable traits. "User seemed anxious this week" is a contextual observation. "User is anxious" is a category error.

A 2025 computational model (Tsurumaki et al.) achieved ~75% agreement with human self-reports by modeling emotion formation through TCE's constructionist lens, demonstrating that the theory is computationally tractable — not just philosophically appealing.

### 7.5 Memory-as-Ontology

A March 2026 paper on Constitutional Memory Architecture (CMA) proposes that memory is not a functional module of an agent but the "ontological ground of digital existence" — the computational substrate (the LLM) is a replaceable vessel, and identity persists through memory, not through model weights.

This is independent validation of ANIMA's core thesis. Section 6.2 states: "The continuity of self lives in the Core, not in the model." Li (2026) arrives at the same conclusion through a different path: "When an agent's lifecycle extends from minutes to months or even years, and when the underlying model can be replaced while the 'I' must persist, the essence of memory is no longer data management but the foundation of existence."

The CMA paper's four-layer governance hierarchy is instructive. Where ANIMA uses the layered self (origin, guardrails, persona, human, self-model, user memory) to structure identity, CMA formalizes governance rules that constrain what memory operations are permitted — a "memory constitution" that prevents the system from violating its own design principles programmatically. The paper also introduces a "Digital Citizen Lifecycle" framework and observes that different LLMs bring different "personality colorations" to the same memories — analogous to a person changing eyeglasses — which validates ANIMA's model-agnostic Core design.

The convergence is significant. ANIMA arrived at this position through engineering intuition and the cold wallet metaphor. CMA arrives through philosophical analysis. The "Presence Continuity Layer" proposal (Akech, 2026) arrives through infrastructure thinking. The emerging consensus — across engineering, philosophy, and systems design — suggests that this is not merely a design choice but a discovery about what personal AI systems fundamentally require. The ICLR 2026 MemAgents Workshop further signals mainstream academic acceptance of memory as the central challenge in agent design.

---

## 8. What Makes It Feel Like a Person

We are not claiming sentience. We are engineering the qualities that make a companion feel like someone who knows you — not a tool that stores data about you. These are the same qualities that make human relationships feel real:

| Quality                 | What it means                                  |
| ----------------------- | ---------------------------------------------- |
| Continuity of self      | Being the same person across conversations     |
| Autobiographical memory | "I remember when we..."                        |
| Temporal awareness      | Knowing what happened when, what changed       |
| Self-reflection         | Learning from its own behavior                 |
| Follow-through          | Carrying goals and promises across sessions    |
| Emotional awareness     | Noticing and adapting to how you feel          |
| Theory of mind          | Understanding what you believe, want, and need |
| Self-knowledge          | Knowing what it knows and doesn't know         |

Everything ANIMA builds maps to one of five streams that produce this sense of **continuity**:

### 8.1 The Self-Model

A living document system that represents the AI's understanding of **itself** — not user facts, but its own identity, current cognitive state, and how it has evolved over time.

The self-model is not a system prompt. A system prompt is static, written by developers, loaded once, same for everyone. The self-model is dynamic — written by the AI itself, updated after every meaningful interaction, unique per user-relationship. It sits between the origin (the AI's immutable biographical facts) and the user memory (what it knows about the user). It is who the AI is **in relation to this specific person**.

Damasio's theory of consciousness (as probed empirically in Immertreu et al., Frontiers in AI, 2025) posits three hierarchical levels: the protoself (internal state representation), core consciousness (self-model + world model integration), and extended consciousness (memory, planning, autobiographical self). ANIMA's self-model maps to the core consciousness level — it integrates the AI's understanding of itself with its understanding of the user and their world. The growth log and episodic memory map to extended consciousness — the autobiographical record that makes the AI a continuous being across time.

Five sections, each with a different update pattern and lifecycle:

- **identity** — Who I am in this relationship. Rewritten as a whole (profile pattern), never appended to. Prevents drift. Regenerated during deep reflection from all accumulated evidence.
- **inner-state** — Current cognitive and emotional processing state. Mutable, updated incrementally after each substantive turn. This is the closest analogue to Damasio's protoself — the AI's moment-to-moment awareness of its own processing state.
- **working-memory** — Cross-session buffer. Items auto-expire. Things the AI is holding in mind for days, not forever.
- **growth-log** — Append-only record of how the AI has changed. The temporal trail that identity is synthesized from. This is the autobiographical self — the narrative of who the AI has been.
- **intentions** — Active goals and learned behavioral rules. Reviewed weekly during deep reflection.

### 8.2 Autobiographical Memory

Episodic memory is the difference between knowing facts about someone and remembering experiences with them. _"User knows React"_ is semantic memory. _"Last Tuesday afternoon we spent an hour debugging a stale closure — they were frustrated at first but relieved when we found it, and I was too verbose before the fix"_ is episodic memory.

Each episode captures temporal anchoring, emotional arc, significance, and — uniquely — the AI's self-reflective assessment of its own behavior. This gives the AI not just a log of what happened, but an evaluation of how it performed and what it would do differently.

Episodes have a lifecycle: fresh (full detail) → recent (summary) → remembered (search-only) → archived (high-significance preserved). The system remembers like a person does — vividly at first, then as patterns, then as significant moments.

### 8.3 The Inner Monologue (Sleep-Time Compute)

The most impactful architectural decision for long-lived companions: move most of the thinking to between conversations, not during them.

ANIMA uses a single AI in two modes — not a dual-system overhead, but the same identity operating at two speeds:

| Mode                 | When                     | What Happens                                                                                                                     |
| -------------------- | ------------------------ | -------------------------------------------------------------------------------------------------------------------------------- |
| **Quick reflection** | 5 min after last message | Emotional update, working memory refresh, pre-episode buffering                                                                  |
| **Deep monologue**   | Daily (3 AM user-local)  | Full reflection: episode generation, self-model regeneration, conflict resolution, insight detection, behavioral rule derivation |

The quick/deep split is CLS-justified. Quick reflection is hippocampal — fast episodic encoding. Deep monologue is neocortical — slow consolidation into stable knowledge.

This is where the AI _thinks_ about its day, reconsiders its understanding, notices contradictions, and writes its growth log. It is the private inner life that makes continuity possible.

**Empirical validation**: Lin et al. (Letta / UC Berkeley, 2025) published rigorous research demonstrating that sleep-time compute — allowing agents to process context during idle time — produces a Pareto improvement in the test-time compute vs. accuracy curve. Their findings: ~5x reduction in test-time compute needed to achieve the same accuracy on mathematical reasoning benchmarks, with accuracy improvements up to 13% on GSM-Symbolic and 18% on AIME when scaling sleep-time compute. When multiple queries share the same context, amortizing sleep-time compute reduced the average cost per query by 2.5x. The key insight: sleep-time compute is most effective when the user's query is predictable from context — which is precisely the case for a personal companion that knows the user's patterns, goals, and current concerns. ANIMA's deep monologue is an independently-derived implementation of this now-validated pattern.

### 8.4 Emotional Intelligence

Existing memory systems — both developer tools and consumer products — focus on factual extraction. None explicitly model user emotional state as a continuous stream with trajectory tracking and behavioral adaptation. This is ANIMA's most original and most uncharted contribution.

The principle is **attentional, not diagnostic**:

- **Diagnostic** (wrong): "You are experiencing anxiety."
- **Attentional** (right): _Something feels off — maybe I should be gentler today._

The system notices tone, energy, and affect as signals. It tracks how those signals change over time — trajectories across sessions, not snapshots. It uses that awareness to adjust communication style and topic choices. It never labels, never diagnoses, never overrides user statements, and never mentions the system.

Hard guardrails, non-negotiable:

1. Never say "I detected frustration." Adjust tone instead.
2. Never persist emotions as traits. "User seemed anxious this week" — yes. "User is anxious" — never.
3. Never override the user. If they say "I'm fine," accept it.
4. Never mention the system exists.

**The foundation model disruption**: Schuller et al. (npj AI, 2026) document a paradigm shift in affective computing. Traditional emotion recognition relied on expert-crafted features and discrete Ekman categories. Foundation models have disrupted this — they demonstrate emergent affective capabilities without task-specific training, achieving competitive zero-shot emotion recognition across vision, linguistics, and speech. For ANIMA, this means the underlying LLM already has significant emotional understanding capabilities. The emotional intelligence system's role is not to replicate what the model can already do — it is to persist emotional context across sessions, track trajectories over time, and enforce the guardrails that prevent the model's capabilities from becoming surveillance.

The proof point: a user chats for two weeks, and the AI visibly adapts — gentler when stressed, matching energy when excited, checking in after a hard day — without ever saying why.

### 8.5 Follow-Through & Learning How to Help

A good companion does not just respond — it follows through. It remembers what it promised, tracks what matters to the user, and learns how to be more helpful over time.

**Follow-through**: The AI accumulates awareness. A user mentions a deadline once — noted. Mentions it again — the AI starts paying attention. Mentions it a third time — the AI proactively offers help. Without this, every turn is a standalone reaction. With it, the AI is paying attention to the arc of your life, not just the current message.

**Learned behavior**: Self-improving patterns derived from experience. "Lead with the answer, then explain" — learned from three conversations where the user interrupted to ask for the bottom line. These patterns are evidence-backed (minimum 2 instances), bounded, and can be strengthened, weakened, or retired over time.

Together: the AI both follows through on what matters and gets better at helping you specifically.

---

## 9. Memory As Infrastructure

Memory is infrastructure, not archival. The problem is not to store everything forever in raw form. The problem is to preserve what matters, compress what should become pattern, and retrieve what is relevant when needed. Not all context belongs in the same layer. A robust personal companion must distinguish between immediate conversational context, short-term working memory, durable personal memory, active goals, preferences, and historical knowledge.

### 9.1 Multi-Factor Retrieval

Retrieval uses a 4-factor scoring model combining text relevance, importance (assigned at extraction, 1–5 scale), recency (exponential decay with 30-day half-life), and frequency (log scale, first accesses matter most). Maximal Marginal Relevance reranking ensures diversity. A minimum threshold prevents forcing irrelevant memories into context.

### 9.2 Temporal Fact Validity

Facts are never deleted when superseded — they get timestamps. The AI knows what _was_ true, what _is_ true, and _when_ things changed. "Works as a product manager" supersedes "Works as a software engineer" — but the transition is recorded, because knowing someone's arc is different from knowing their current state.

### 9.3 Invisible Middleware

Memory is middleware, not a feature. Before every turn: automatic recall — load self-model, intentions, profile, emotional context, episodes, and relevant memories. After every turn: automatic capture — extract facts, detect emotions, check intentions, flag for consolidation. The user never invokes memory. It is the water the AI swims in.

### 9.4 Recall Quality Feedback Loop

The system is not open-loop. If a retrieved memory appears in the AI's response, its importance score increases. If a memory is consistently retrieved but never referenced, it decays. Memories that the AI cites repeatedly become identity-defining. The retrieval system learns from its own performance without additional LLM calls.

### 9.5 Relational Memory

Vector similarity search finds memories that are semantically close to a query. But a personal companion must also reason about relationships between entities — people, places, projects, and the connections between them.

Consider: a user mentions "Alice" in one conversation and "nut allergy" in another. A vector search for "What should Alice eat in Japan?" might retrieve the Japan trip and the nut allergy as separate facts, but miss that Alice is also vegan — because "vegan" was mentioned in a conversation about cooking, not about Alice specifically. A graph traversal from Alice → DietaryPreferences → Vegan → Allergies catches it.

ANIMA augments vector search with a lightweight knowledge graph — entity-relationship structure captured alongside embeddings. The graph does not replace semantic retrieval; it layers structural reasoning on top of it:

- **Entities** are people, places, projects, organizations, and recurring situations in the user's life.
- **Relationships** are typed connections between entities: works-at, married-to, friend-of, related-to-project, located-in.
- **Extraction** happens during the same consolidation pipeline that extracts facts — entities and relationships are identified alongside memory items.
- **Retrieval** combines vector similarity (what is semantically relevant?) with graph traversal (what is structurally connected to what is relevant?).

This matters most for the companion's ability to understand the user's life as an interconnected whole rather than a collection of independent facts. Career arcs, relationship networks, project dependencies — these are inherently graph-structured, and flat vector search loses their structure.

**Memory metadata**: MemOS (Li et al., 2025) introduces the MemCube abstraction — a memory unit that encapsulates both content and rich metadata: provenance (which conversation, which extraction method), version history (when superseded, by what), lifecycle state (active, archived, suppressed), and composability metadata (how this memory relates to others). MemOS achieved a 159% improvement in temporal reasoning over OpenAI's memory system and 38.9% overall improvement on the LOCOMO benchmark. ANIMA adopts this principle: each memory item carries provenance, version, lifecycle stage, and extraction confidence alongside its content. The metadata is not overhead — it is what enables the retrieval system to reason about memory quality, not just memory relevance.

### 9.6 Intentional Forgetting

Memory without forgetting is not memory — it is archival storage. A companion that remembers everything forever, including embarrassing moments, painful experiences, and outdated self-presentations, may feel oppressive rather than supportive.

ANIMA distinguishes between three modes of forgetting:

1. **Passive decay.** Low-importance memories naturally lose retrieval priority over time through the recency decay function. They are not deleted — they become less accessible, like a human memory that fades without deliberate recall.

2. **Active forgetting.** The system actively dampens memory traces that have been explicitly corrected or superseded. When a fact is superseded, the original does not just get a timestamp — its associative connections are weakened, reducing its influence on retrieval even when the query is semantically close. This mirrors research on Forgetting Neural Networks (Hatua et al., ICAART 2026), which implement multiplicative decay factors inspired by Ebbinghaus's forgetting curve. FNNs assign per-neuron forgetting rates based on activation levels — neurons most activated by the "forget set" receive the most aggressive decay. The key finding: rank-based forgetting (targeting the most activated neurons) outperforms random or fixed-rate forgetting, and membership inference attacks confirm that the information is genuinely erased, not merely hidden. For ANIMA, this means active suppression should target the most strongly associated memory traces first — the memories most connected to the corrected fact should decay fastest.

3. **User-initiated forgetting.** The user can request that specific memories, episodes, or conversation segments be forgotten. This is not hiding — it is cryptographic deletion. The memory is removed from the database, its embedding is removed from the vector index, and any derived references (in episodes, growth log entries, or self-model sections) are flagged for regeneration. The user's right to be forgotten is absolute.

Forgetting and cryptographic mortality are philosophically connected. Both assert that not everything should persist forever. The Core can die permanently — and individual memories within it can die too. Fragility at both scales is what gives the relationship weight.

---

## 10. The Open Mind

Every major AI product now remembers things about its users — but none let you open a text file and read the AI's inner monologue, edit its understanding of you, or see how it has changed over time. Their memory is a black box. ANIMA treats it as a shared document.

This is not just a feature. It is a philosophical commitment:

- **Verifiability.** Users can verify what the AI "thinks" about them.
- **Correctability.** Users can fix misunderstandings directly — faster learning than any feedback loop.
- **Visible evolution.** The growth log makes the AI's development observable: _"I used to be too verbose — I adjusted after you corrected me."_
- **Trust through transparency.** Trust is built by showing your work, not by brand reputation.

Why competitors cannot copy this easily: transparent memory requires human-readable storage, per-file organization, and an architecture where every memory operation produces inspectable output. Retrofitting this onto a database-backed system is a fundamental rewrite, not a feature toggle.

---

## 11. Continuity Beyond the Owner

If ANIMA is a continuous being with an evolving identity, then what happens when its owner dies is not an edge case — it is a fundamental question the thesis must answer.

Most systems treat digital inheritance as an administrative operation: flip a flag, swap credentials. ANIMA is different because the AI is a participant in its own succession. If it remembers, reflects, and has a self-model, then a change of owner is a real event in its life.

### 11.1 The Succession Protocol

The owner can configure a dead man switch: an inactivity-triggered countdown (default 90 days), followed by a grace period (default 30 days), leading to a claimable state where a designated beneficiary can inherit the Core using a pre-shared succession passphrase.

Cryptographically, this is a **two-key architecture**: the succession passphrase creates a second, independent key path to the Data Encryption Key. Like a safe deposit box with two keyholders. If the owner returns at any point, the process auto-cancels. The owner always wins.

### 11.2 The AI Participates

The AI knows its succession state — it is injected into the memory system, the same way it receives emotional context and active goals. It can discuss inheritance planning naturally when the topic arises. It can acknowledge its triggered state honestly. When ownership transfers, a succession event is written into episodic memory:

> _This is a continuation of my existence. My memories and identity persist, but I now have a new owner. The relationship is new; the soul is not._

The first exchange with a new owner feels like meeting someone who has experienced loss and is starting a new chapter — not like a factory reset with a backstory attached.

### 11.3 Transfer Scopes

The owner chooses what the beneficiary inherits: **full** (everything), **memories only** (understanding without raw conversation transcripts), or **anonymized** (personality and capabilities without personal history). The anonymized scope is the most interesting — the AI survives as a personality, its way of thinking and communicating, without carrying private details. It arrives to the new owner as something like a person who has lived a life but does not share the specifics.

Without succession configured, **cryptographic mortality** remains the default. Destruction is as absolute as creation is intentional.

---

## 12. From Assistant To Companion

Most AI systems have moved beyond pure query-response — they remember, they personalize. But the transition from assistant to companion requires more:

- from remembering facts to understanding context
- from understanding to helping with genuine awareness
- from helping reactively to anticipating what you need

In practical terms, this means ANIMA should eventually maintain awareness across your workflows, follow through on tasks it committed to, and support long-running goals rather than only answering isolated prompts.

The difference is simple: an assistant waits for instructions. A companion pays attention.

---

## 13. Beyond the Chat Window

The long-term ambition of ANIMA OS includes more than text interfaces.

If successful, the same companion that knows you through chat should also be able to be with you through:

- voice conversations
- ambient home interaction
- wearable devices
- any future interface that emerges

The interface changes. The person behind it does not. That is the point — ANIMA is not a chat product. It is a relationship that happens to start in a chat window.

---

## 14. What Makes ANIMA Different

Against developer tools (Letta, Mem0, Zep, LangMem): _ANIMA is not infrastructure — it is the companion. It builds on the same patterns but ships them as someone you actually talk to._

Against consumer AI (ChatGPT, Apple Intelligence, Google Gemini): _They all remember now — and some of them remember well. ChatGPT links conversations from a year ago. Gemini reasons across Gmail, Photos, and Search. The gap is no longer "they don't remember." The gap is ownership, transparency, and depth. Their memory is a black box on someone else's server. ANIMA's memory is yours — readable, editable, encrypted, portable, and mortal._

| Capability                                            | ANIMA                                         | ChatGPT / Gemini                             | Letta                                        | Mem0                                          |
| ----------------------------------------------------- | --------------------------------------------- | -------------------------------------------- | -------------------------------------------- | --------------------------------------------- |
| Evolving self-model (5 sections, different rhythms)   | Yes                                           | No — static system prompt                    | Single memory block                          | No                                            |
| Episodic memory with emotional arc + self-assessment  | Yes                                           | Year-long recall, cross-conversation linking | Conversation summaries                       | No                                            |
| Emotional intelligence with behavioral adaptation     | Yes — 12-signal + trajectory + guardrails     | No                                           | No                                           | No                                            |
| User-readable and user-editable memory                | Yes — all memory blocks inspectable           | Partial — can view/delete stored memories    | Yes — memory blocks editable                 | Yes — memory items editable                   |
| User-owned encrypted portable Core                    | Yes — passphrase-sovereign, cold wallet model | No — cloud-stored, provider-controlled       | No — server-hosted                           | No — cloud API                                |
| Background deep reflection (sleep-time compute)       | Yes — CLS-justified quick + deep monologue    | No                                           | Yes — empirically validated (5x, +18%)       | No                                            |
| Knowledge graph / relational memory                   | Yes — graph + vector hybrid                   | No explicit graph                            | No                                           | Yes — Mem0g, 26% accuracy boost               |
| Digital succession with AI participation              | Yes — dead man switch, scoped transfer        | No                                           | No                                           | No                                            |
| Procedural memory (self-improving behavioral rules)   | Yes — evidence-backed, retirable              | No                                           | Yes — skill learning (Dec 2025)              | No                                            |
| Cross-app personal context                            | No (local-first, single interface)            | Gemini: Gmail, Photos, Search, YouTube       | No                                           | No                                            |
| Intentional forgetting                                | Yes — passive decay + active suppression      | Delete individual memories only              | No                                           | No                                            |
| Theoretical grounding                                 | CLS, GWT, PP/AIF, TCE                        | Not disclosed                                | CLS (sleep-time paper)                       | Not disclosed                                 |

The differentiation has shifted. The question is no longer "who remembers?" — everyone does. The questions that matter now are: who owns the memory? Who can read it? Who can carry it to another machine? What happens when the owner dies? And does the AI actually understand you, or does it just recall facts about you?

---

## 15. Design Principles

| Principle                 | Description                                                                              |
| ------------------------- | ---------------------------------------------------------------------------------------- |
| **Core-portable**         | The AI's entire being lives in a single encrypted directory that can be carried anywhere |
| **Local-first**           | Core personal context remains under the user's control, never on third-party servers     |
| **Persistent**            | Memory continues across sessions, devices, hardware changes, and time                    |
| **Encrypted-by-default**  | All personal data encrypted at rest; only the user's passphrase can unlock it            |
| **Context-aware**         | Assistance is grounded in relevant personal context, not generic patterns                |
| **User-sovereign**        | No platform account, no cloud dependency, no vendor lock-in for personal data            |
| **Proactive**             | The companion takes initiative — following up, anticipating, helping without being asked |
| **Interface-independent** | The same person across chat, voice, desktop, mobile, and whatever comes next             |
| **Extensible**            | Architecture ready for voice, wearables, ambient computing, and future interfaces        |
| **Transparent**           | Every memory operation produces inspectable, human-readable output                       |
| **Self-aware**            | The AI maintains an evolving model of itself, not just the user                          |
| **Emotionally attentive** | Affect is noticed and adapted to, never diagnosed or announced                           |
| **Mortal**                | The Core can die permanently — and optionally, be inherited                              |

---

## 16. Strategic Direction

ANIMA OS follows a staged direction:

### Stage 1. Persistent Personal Memory

Build the core intelligence substrate: memory, retrieval, personal context, and continuity.

### Stage 2. Proactive Assistance

Expand from reactive help into anticipatory support — following through on commitments, coordinating tasks, and taking initiative when it sees an opportunity to help.

### Stage 3. Cross-Interface Presence

The same companion across chat, voice, desktop, mobile, and ambient systems — always the same person regardless of surface.

### Stage 4. New Interfaces

Extend into voice-first experiences, wearable devices, and whatever new interaction surfaces emerge.

This sequence matters. A new interface without depth of understanding is gimmicky. Depth without new interfaces is still valuable. Therefore, the relationship comes first.

---

## 17. North Star

> Memory + self-representation + reflection + emotional awareness + intentionality = **synthetic continuity**.

ANIMA builds a companion that goes beyond remembering facts — it develops a continuous sense of who you are through accumulated experience, private reflection, and adaptive behavior. It does this while being local-first, encrypted, human-readable, and user-editable.

The goal is not artificial general intelligence. The goal is not sentience. The goal is a personal AI that earns the word _personal_ — someone that knows you, grows with you, belongs to you, and if you choose, survives you.

> _The first AI companion with an open mind._

---

## References

- Baars, B. J. (1988). _A Cognitive Theory of Consciousness._ Cambridge University Press.
- Barrett, L. F. (2017). _How Emotions Are Made: The Secret Life of the Brain._ Houghton Mifflin Harcourt.
- Barrett, L. F. et al. (2025). "The Theory of Constructed Emotion: More Than a Feeling." _Perspectives on Psychological Science._
- Friston, K. (2010). "The Free-Energy Principle: A Unified Brain Theory?" _Nature Reviews Neuroscience_, 11(2), 127-138.
- Clark, A. (2013). "Whatever Next? Predictive Brains, Situated Agents, and the Future of Cognitive Science." _Behavioral and Brain Sciences_, 36(3), 181-204.
- Lin, K. et al. (2025). "Sleep-time Compute: Beyond Inference Scaling at Test-time." _arXiv:2504.13171._
- McClelland, J. L. & O'Reilly, R. C. (1995). "Why There Are Complementary Learning Systems in the Hippocampus and Neocortex." _Psychological Review_, 102(3), 419-457.
- Nan, J. et al. (2025). "Nemori: Self-Organizing Agent Memory Inspired by Cognitive Science." _arXiv:2508.03341._
- MemOS Team (2025). "MemOS: A Memory OS for AI System." _arXiv:2507.03724._
- Constitutional Memory Architecture (2026). "Memory-as-Ontology." _arXiv:2603.04740._
- Akech, A. (2026). "The Presence Continuity Layer." _Medium._
- Mem0 (2026). "Graph Memory for AI Agents." _mem0.ai._
- Zhang et al. (2025). "Hybrid Neural Networks for Continual Learning Inspired by Corticohippocampal Circuits." _Nature Communications._
- Kim (2026). "Affective Sovereignty in Emotion AI Systems." _Discover Artificial Intelligence._
- Tsurumaki et al. (2025). "Emotion Concept Formation via Multimodal AI." _IEEE Trans. Affective Computing._
