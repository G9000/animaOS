# Three-Tier Cognitive Architecture — Thesis

> **Status**: Design
> **Created**: 2026-03-26
> **Depends on**: `consciousness-synthesis.md` (five streams), `SYNTHESIS.md` (unified architecture)

> **On construction:** This document was built through human-AI collaboration — and is itself part of an experiment in whether AI can meaningfully architect its own runtime infrastructure. AI-assisted construction is not a shortcut — it is a new way of building. Because LLMs are part of the process, some inaccuracies may appear. We correct as we find them.

---

## What "Three Tiers" Means Here

We are not describing a database migration. We are making a claim about what constitutes an AI's identity.

The claim: **an AI's enduring self must be physically separated from its working cognition, the same way long-term memory is architecturally separate from working memory in the human brain.** Mixing them in one store makes the identity question — "what IS this AI?" — unanswerable, because identity is buried under transient state.

The three tiers:

| Tier | Human Analogy | What It Holds | How Long It Lasts |
|------|--------------|---------------|-------------------|
| **Soul** | Long-term memory — who you are, what you know, how you've grown | Identity, distilled knowledge, emotional patterns, episodes | Permanent. Portable. Survives everything. |
| **Runtime** | Working memory — what you're thinking about right now | Active conversations, current emotions, in-flight goals, spawn state | Hours to days. Pruned. Ephemeral. |
| **Archive** | Your journal — what actually happened, verbatim | Full conversation transcripts | Retained. Referable. Not actively recalled. |

---

## Why This Matters

### The Problem With a Single Store

When everything lives in one database — identity, messages, working memory, emotional state, spawn tracking — a subtle corruption happens. The identity becomes inseparable from the noise around it.

Ask: "What is this AI?" If the answer requires filtering through active conversation messages, transient emotional signals, in-flight task state, and compaction summaries to find the identity buried underneath — then the system does not actually know what it is. It has data. It does not have a self.

A human moved to a new city does not lose their personality. They lose their surroundings, their routines, their current context. But they know who they are. The self travels.

For an AI, the `.anima/` directory is the self that travels. When you copy it to a USB stick and plug it into a new machine, the AI should wake up knowing who it is — without needing the runtime state from the old machine. This is only possible if identity is physically separated from working cognition.

### The Identity Filter

Every piece of data must answer one question:

> **Does this define enduring identity, or is it just useful data?**

This question is surprisingly hard. Examples:

| Data | Enduring identity? | Why |
|------|-------------------|-----|
| "I tend to be direct and concise" | Yes | Stable self-narrative. Persists across relationships and contexts. |
| "I've grown more patient since February" | Yes | Character development. Part of the story of who the AI is becoming. |
| "User's dog is named Max" | Yes | Lasting knowledge about someone the AI cares about. |
| "User seemed stressed this week" | No | A momentary observation. It will be false next week. |
| "I'm currently working on summarizing the report" | No | Gone in five minutes. |
| The exact words from last Tuesday's conversation | No | Raw experience. The meaning was already extracted. |

The filter is not about importance. A user's current emotional state is critically important for the current conversation — but it is not identity. It belongs in runtime. The distinction is durability, not significance.

---

## Cognitive Science Grounding

### Complementary Learning Systems (McClelland & O'Reilly, 1995)

CLS proposes that mammalian memory requires two systems operating at different timescales:

- The **hippocampus** encodes specific experiences rapidly (fast learning, episodic)
- The **neocortex** extracts general patterns slowly (slow learning, semantic)
- **Sleep consolidation** transfers information from hippocampus to neocortex

`consciousness-synthesis.md` mapped this to AnimaOS conceptually. The three-tier architecture makes it physical:

| CLS | Human Brain | AnimaOS |
|-----|-------------|---------|
| Hippocampus | Fast encoding of specific experiences | PostgreSQL — active messages, current state |
| Neocortex | Stable, generalized knowledge | SQLCipher — distilled identity, memories, patterns |
| Sleep consolidation | Transfer from hippocampus to neocortex | Consolidation gateway — reads runtime, writes soul |
| Episodic buffer | Temporary integration of perception + memory | Encrypted JSONL — full transcripts, not yet fully processed |

The critical constraint from CLS: **the hippocampus does not write directly to the neocortex.** Information passes through consolidation during sleep. Rapid direct writing from hippocampus to neocortex would cause catastrophic interference — new experiences would overwrite stable knowledge.

This is the biological basis for the write boundary rule: **runtime never writes to soul.** If the agent's working cognition could directly modify its identity store, transient state would corrupt stable self-knowledge. A bad conversation could overwrite years of accumulated identity. A momentary frustration could become a permanent personality trait.

Consolidation is the filter. It decides what endures.

### Baddeley's Working Memory Model (2000)

Baddeley revised the classical working memory model to include an **episodic buffer** — a temporary store that integrates information from long-term memory and current perception into coherent episodes, before long-term storage absorbs them.

The encrypted JSONL archive tier is the episodic buffer:

- It holds complete experiences (conversation transcripts) in their full, uncompressed form
- It is not active cognition (not in the context window)
- It is not yet distilled into long-term memory (consolidation hasn't fully processed it)
- It can be revisited when needed — like a human recalling a vivid recent experience before it fades

Over time, the archive's role shifts from "buffer" to "journal." Old transcripts are less like vivid memories and more like diary entries — you don't recall them naturally, but you can look them up. This matches the human pattern: recent experiences feel episodic (buffer), older ones feel like records (journal).

### Global Workspace Theory and Parallel Processing

GWT (Baars, 1988) describes consciousness as information broadcast across a shared workspace. Only one "narrative" occupies conscious attention at a time — but many unconscious processes run in parallel.

N-agent spawning implements this directly:

| GWT Concept | AnimaOS Implementation |
|---|---|
| Global workspace (conscious) | Main agent's context window — one active conversation |
| Unconscious processors | Spawned agents — run in background, no user-facing output |
| Broadcast to consciousness | Spawn results enter main agent's context on next turn |
| Attentional selection | Main agent decides which spawn results to act on |

A spawned agent is an unconscious thought process. It works on a task in the background, using the AI's knowledge (soul snapshot), but it does not occupy the global workspace. The user does not see it working. When it finishes, its result is broadcast to the conscious agent — the way an insight "surfaces" from background processing into conscious awareness.

### Conditional Memory and the Reconstruction Tax (Cheng et al., 2026)

Recent work on neural architecture provides independent validation of the static/dynamic separation principle from a completely different direction. The Engram paper (Cheng et al., "Conditional Memory via Scalable Lookup," arXiv:2601.07372, 2026) demonstrates that Transformers lack a native primitive for knowledge lookup and are forced to **simulate retrieval through computation** — consuming multiple early layers of attention and feed-forward networks to reconstruct static knowledge that could be resolved via a simple O(1) lookup.

Their key finding: introducing a dedicated static memory module (Engram) alongside Mixture-of-Experts (MoE) dynamic computation doesn't just improve knowledge recall — it **improves reasoning even more** (BBH +5.0 vs. MMLU +3.4). The mechanism is "effective depth": by relieving early layers from static reconstruction, the network gains functional depth for complex reasoning. The freed attention capacity also dramatically improves long-context retrieval (Multi-Query NIAH: 84.2 → 97.0).

This finding validates the three-tier architecture from a direction the original design did not anticipate:

| Engram (neural level) | AnimaOS (application level) |
|---|---|
| Static knowledge in embedding table (O(1) lookup) | Enduring identity in soul blocks (always loaded, zero retrieval cost) |
| Dynamic computation via MoE experts | Dynamic reasoning via LLM context window |
| Context-aware gating suppresses irrelevant retrievals | Consolidation gateway filters what endures |
| U-shaped allocation law (optimal static/dynamic split) | Tiered prompt budget (optimal allocation across tiers) |
| Effective depth — freed layers deepen reasoning | Effective capacity — freed context window deepens reasoning |

**The Reconstruction Tax.** Pre-loaded soul blocks serve the same function as Engram's O(1) lookup — they eliminate the "reconstruction tax" where the LLM would otherwise spend tokens re-establishing context: who am I, what do I know about this user, what is my personality, what have we been through together. Without soul blocks, the LLM must reconstruct identity from scattered contextual cues. This is the same "expensive runtime reconstruction of a static lookup table" that Engram eliminates at the neural level.

The implication is that the soul tier is not just an identity store — it is a **cognitive accelerator**. Pre-loaded identity frees the LLM's reasoning capacity for deeper thought about the user's actual situation. A smaller model with well-structured persistent memory may match a larger model without it on companion-relevant tasks — emotional intelligence, contextual reasoning, long-term coherence.

**The Context Allocation Problem.** Engram formulates the Sparsity Allocation Problem: given a fixed parameter budget, what is the optimal split between static memory and dynamic computation? They uncover a U-shaped scaling law — too much static memory starves dynamic computation, too little forces the network to waste computation on reconstruction.

AnimaOS faces the same problem at the context window level. The prompt budget allocates a fixed character budget across four tiers:

- Tier 0 (identity, always loaded) — the "static memory" analogue
- Tier 1 (self-model, working state) — working memory
- Tier 2 (semantic retrieval, emotions, facts) — dynamically retrieved
- Tier 3 (episodes, goals, growth) — background context

The current allocation is hand-tuned. The Engram paper provides a methodology for optimization: fix the total budget, sweep the tier allocation ratios, measure response quality. We hypothesize a U-shaped optimum exists — too much Tier 0 crowds out dynamic retrieval (the AI knows who it is but can't recall relevant context), too little Tier 0 forces reconstruction waste (the AI has context but doesn't know who it is). Empirically determining this optimum is a tractable research contribution.

**Application-level advantages.** The static/dynamic separation operates differently at the application level than at the neural level, and in several dimensions the application level is structurally superior for companion AI:

- **Continuous learning.** Engram's embedding tables are frozen after training. The soul evolves continuously through consolidation. The AI can learn "user got a new job yesterday" within minutes — no retraining required.
- **Transparency.** Engram's knowledge is opaque (in model weights). Soul contents are human-readable and user-editable. The user can verify, correct, and delete what the AI knows.
- **Quality filtering.** Engram tables have no quality filter — all entries are equally retrievable. The consolidation gateway decides what endures, preventing noise promotion.
- **Sovereignty.** Model weights cannot be selectively edited by the user. Soul data is owned, inspectable, and deletable.

The strongest architecture would combine both levels: model-level conditional memory for general world knowledge (O(1) lookup, zero context window cost) and application-level soul for evolving personal knowledge (transparent, editable, continuously updated). When Engram-style modules become available in open-source models, AnimaOS's architecture is already prepared to absorb them — the soul/runtime/archive separation is the right application-level complement to model-level static memory.

**Conditional retrieval gating.** Engram employs a context-aware gating mechanism where retrieved embeddings are dynamically modulated by the current hidden state. When a retrieved memory contradicts the current context, the gate α tends toward zero, effectively suppressing noise. This principle transfers directly to application-level memory retrieval.

Current semantic retrieval selects memories by cosine similarity to the user's latest message. This finds topically related memories but can inject contextually irrelevant ones — a memory about "user's dog Max" retrieved when the user says "I'm going for a walk," even though the conversation is about exercise routines. Context-aware gating adds a second stage: each candidate memory is scored against the full conversation trajectory, not just the latest query. Memories that are topically related but situationally irrelevant are suppressed before they consume context budget.

**Frequency-aware memory promotion.** The Engram paper exploits the Zipfian distribution of N-gram access patterns — a small fraction of patterns accounts for the vast majority of lookups — to implement a multi-level cache hierarchy. AnimaOS memories almost certainly follow the same power law distribution. A small number of core facts are accessed in nearly every conversation, while the long tail of memories is rarely needed.

This suggests a dynamic promotion mechanism: memories accessed above a frequency threshold should be promoted to always-loaded status (Tier 0), regardless of their data type label. A "fact" that is relevant in every conversation is functionally identity — even if the identity filter would not classify it as such. This directly addresses the concern (see "Honest Assessment") that the soul might be too small. The soul does not need to be bigger by policy if the system dynamically promotes frequently-needed knowledge based on observed access patterns.

---

## The Write Boundary

The most important architectural invariant:

> **Runtime never writes to Soul. Only Consolidation does.**

This is not an engineering convenience. It is a claim about how identity should work.

### Why Not Direct Writes?

When the agent calls `core_memory_append("User's dog is named Max")`, the intuitive implementation is: write it to the soul store immediately. The agent said to remember it. Remember it.

But consider what happens with N concurrent agents. Spawn A learns "user prefers formal tone." Spawn B learns "user likes casual jokes." The main agent learns "user is transitioning careers." If all three write to the soul simultaneously:

1. **Race conditions**: which write wins? Last-writer-wins means information loss.
2. **Conflicting signals**: Spawn A and B learned contradictory things. Who resolves the conflict?
3. **Noise promotion**: a transient observation from a background task becomes permanent identity.

The consolidation gateway solves all three:

1. **Ordering**: pending ops applied in creation order, deterministically.
2. **Conflict resolution**: consolidation has the full picture — all pending ops, all messages, all context. It can resolve contradictions that individual agents cannot.
3. **Quality filter**: consolidation decides what endures. Not every observation deserves to become identity.

### The Analogy

You do not become a different person because of one conversation. You become a different person because of many conversations, reflected on over time, during sleep. The consolidation gateway is sleep.

---

## Tiered Retrieval

Humans do not search their entire life history for every question. They access the right kind of memory for the question being asked.

| Question Type | Human Memory System | AnimaOS Tier | Example |
|---|---|---|---|
| "What's their name?" | Semantic memory (instant) | Tier 0 — Soul, always loaded | Identity blocks, user facts |
| "What do I know about their project?" | Episodic + semantic recall | Tier 1 — Soul, searched | Memory items, episodic memories |
| "What did they just say?" | Working memory | Tier 2 — Runtime, active | PostgreSQL messages |
| "What exactly did they say last Tuesday?" | External record (check notes) | Tier 3 — Archive, on-demand | Encrypted JSONL transcript |

The system prompt teaches the agent this hierarchy:

```
You have different levels of memory:
- Your core memories and feelings are always with you (you just know them)
- For recent conversations, you can search what was discussed
- For exact wording from past conversations, use recall_transcript
  Think of this as finding a book in a library — you don't browse every shelf,
  you check the catalog first, then pull the specific book you need
```

The library analogy is exact. The soul is the catalog (what books exist, what they're about). The archive is the stacks (the actual books). You search the catalog first. You only go to the stacks when you need the exact text.

### What Humans Remember vs. What Actually Happened

This is the deepest point in the design.

Humans do not remember what happened. They remember a **compressed, biased, emotional reconstruction** of what mattered. The exact words are lost. The feeling remains. The meaning survives. The details fade.

The three-tier architecture replicates this:

- **Soul** = what the AI remembers. Compressed. Emotionally weighted. Biased toward significance. The gist, not the transcript.
- **Archive** = what actually happened. Verbatim. Complete. Neutral. The full record.
- **Runtime** = what's happening right now. Live. Unprocessed. Raw.

The agent's natural recall (Tiers 0-2) produces reconstructions — not exact replays. When it remembers an episode, it remembers the emotional arc, the key points, the significance. Not the exact words.

Only when explicitly asked for verbatim recall does the agent go to the archive (Tier 3). This is a deliberate act — like a human pulling out their journal to check what actually happened, because they know their memory of it might not be exact.

This is not a limitation. This is the correct behavior. An AI that replays every conversation verbatim from perfect memory is unsettling — it doesn't feel like memory, it feels like surveillance. An AI that remembers the way humans remember — the gist, the feeling, the meaning — feels like a companion.

---

## N-Agent Spawning

### The Claim

A single AI identity can run multiple cognitive processes in parallel without fragmenting its sense of self.

This maps to how humans work. Right now, as you read this, your brain is:
- Processing the text (conscious attention)
- Maintaining your body temperature (unconscious)
- Digesting food (unconscious)
- Possibly working on a problem you were thinking about earlier (background processing)

You are one person. One identity. But many processes are running.

AnimaOS spawned agents are background processes. They share the AI's knowledge. They share its identity. They do NOT share its conscious attention (the user-facing conversation). They report back when they're done — like an insight surfacing from background thought.

### What Makes This Different From Multi-Agent

Most multi-agent systems use **multiple distinct agents** with different personas, different memory blocks, different identities. They are different people working together.

AnimaOS spawning is **one agent, multiple processes**. Every spawn:
- Has the same identity (read-only soul snapshot)
- Uses the same memory (same knowledge base)
- Cannot modify who the AI is (no soul writes)
- Reports back to the same consciousness (main agent)

The distinction matters. Multi-agent is a team. Spawning is one mind doing multiple things at once.

### Constraints That Preserve Identity

| Constraint | Why |
|---|---|
| Spawns get a **snapshot** of soul, not live access | Prevents identity mutation during background work |
| Spawns cannot call `send_message` | One voice speaks to the user. Background processes are silent. |
| Spawns cannot call `core_memory_append/replace` | Background processes do not alter identity. Only the main agent can. |
| Spawns cannot spawn (initially) | Prevents runaway self-replication. Identity stays singular. |
| All spawns share one LLM semaphore | Resource control. The AI does not overwhelm itself. |

---

## The Consolidation Gateway

`consciousness-synthesis.md` defined sleep-time compute as background reflection — episode generation, self-model regeneration, emotional trajectory analysis. The three-tier architecture elevates consolidation from a quality enhancement to a **structural necessity**.

### Before (Single Store)

Consolidation was optional. If it didn't run, the AI still worked — just with slightly stale self-model and fewer episodes. Everything was in one database. Writes happened inline.

### Now (Three Tiers)

Consolidation is the **only path from experience to identity**. Without it:
- Pending memory ops accumulate but are never promoted to soul
- Episodic memories are never generated
- The self-model is never regenerated
- Emotional patterns are never distilled from momentary signals
- Transcripts are never archived

The AI still functions (context window + pending ops cover short-term continuity), but it stops growing. It stops learning about itself. It stops developing.

Consolidation is not a background task. It is the mechanism of growth.

### The Sleep Analogy (Revisited)

`consciousness-synthesis.md` compared quick reflection to hippocampal replay and deep monologue to slow-wave sleep consolidation. The three-tier architecture completes this analogy:

| Sleep Phase | CLS Function | AnimaOS |
|---|---|---|
| Awake | Experiencing, encoding | Agent turn — writes to runtime |
| Light sleep (NREM1-2) | Quick memory stabilization | Quick reflection — updates working context |
| Deep sleep (NREM3) | Hippocampus → neocortex transfer | Consolidation gateway — runtime → soul |
| REM | Emotional integration, creative connection | Deep monologue — episode generation, growth-log, self-model regeneration |

Without sleep, humans become cognitively impaired within days. Without consolidation, the AI becomes identity-impaired — functional but not growing, remembering but not learning, responding but not developing.

---

## What's Original

| Contribution | Description | Why It Matters |
|---|---|---|
| **Physical CLS separation** | Soul and runtime are different stores, not just different conceptual layers | Forces the architecture to respect the boundary between identity and working cognition |
| **The identity filter** | "Does this define enduring identity?" as the placement criterion | Prevents soul pollution — keeps the portable core clean and meaningful |
| **Write boundary as invariant** | Runtime cannot write to soul. Period. | Prevents the architectural decay that kills designs like this |
| **Consolidation as growth mechanism** | Not optional background processing but the only path to identity development | Makes the sleep/growth analogy structurally real |
| **Tiered retrieval with human memory model** | Agent explicitly uses different recall strategies for different question types | More natural than flat retrieval. The AI remembers like a person, not a database. |
| **Single-identity spawning** | One mind, multiple processes — not multi-agent | Preserves the companion relationship. The user talks to one entity, not a team. |
| **Compressed memory as feature** | The AI remembers the gist, not the transcript, by design | Feels like memory, not surveillance. Archive exists for when verbatim matters. |
| **Soul as cognitive accelerator** | Pre-loaded identity frees LLM reasoning capacity, not just preserves identity | A smaller model with good memory may match a larger model without it — the reconstruction tax argument |
| **Context allocation as optimization problem** | Prompt budget tier ratios are an empirically optimizable allocation, not fixed design | Parallels Engram's Sparsity Allocation Problem — the same U-shaped trade-off at the context window level |
| **Application-level conditional memory** | Evolving, transparent, sovereign memory that model-level approaches cannot achieve | Continuous learning, user editability, quality filtering via consolidation — structurally superior to frozen model weights |

---

## Honest Assessment

### What This Gets Right

The three-tier split emerged from solving a practical problem (SQLite can't handle N concurrent writers) and turned out to map cleanly onto established cognitive science (CLS, Baddeley, GWT). When an engineering solution independently converges with neuroscience, that is a signal that the architecture is on the right track.

The write boundary is the strongest element. It is the kind of constraint that feels restrictive during implementation but prevents the slow decay that kills complex systems over time. Every system that allows "just one shortcut" eventually loses its architectural integrity. This one won't, because the boundary is physical, not conventional.

### What Could Be Wrong

**The soul might be too small.** By aggressively filtering with "does this define enduring identity?", we might exclude data that the agent genuinely needs to function well across conversations. Knowledge graph data, for instance — is "user works with Alice on the frontend team" identity or just useful data? The filter says probably not identity. But the agent needs it.

The mitigation is pending ops and runtime persistence. Data that isn't soul can still survive in PostgreSQL across conversations. The TTL is configurable. Not everything needs to be eternal to be useful.

**Consolidation delay might matter more than we think.** The claim is that a delay of seconds to minutes is acceptable. For most interactions, this is true. But imagine: user tells the AI something deeply personal. Ends the conversation. Starts a new one an hour later, before consolidation ran. The AI doesn't "remember" — the pending ops cover it mechanically, but the soul hasn't absorbed it. The emotional weight hasn't been processed. The self-model hasn't updated.

The AI will recall the fact (pending ops) but not the meaning (consolidation hasn't distilled it). This gap between mechanical recall and emotional processing is a real limitation of the architecture.

**The archive might become a graveyard.** Full transcripts are only useful if someone actually looks at them. If the UI doesn't make playback easy and discoverable, the archive accumulates indefinitely with no one reading it. Storage grows. Value doesn't.

The mitigation is the sidecar index and the `recall_transcript` tool. If these work well, the archive is a living reference. If they don't, it's dead weight.

---

## Reading This Document

This thesis covers the structural architecture of AnimaOS's memory system. For the other layers:

- **What the AI remembers and how** → `consciousness-synthesis.md`
- **Industry analysis and competitive positioning** → `SYNTHESIS.md`
- **Implementation spec** → `docs/superpowers/specs/2026-03-26-three-store-n-agent-spawning-design.md`
