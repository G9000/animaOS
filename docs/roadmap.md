# ANIMA OS Lite — Roadmap

## Phase 0: Clean Foundation
**Goal:** Fix structural issues before building new features.

- [ ] Remove legacy `memories` SQLite table — memory is markdown-based now, the table is dead weight
- [ ] Fix default system prompt in `graph.ts` to align with `soul.md` (remove the "sardonic" fallback personality)
- [ ] Strip multi-user auth down to single-user — local PIN or passphrase at most
- [ ] Remove or disable email integration and translation feature (park for later)
- [ ] Add hash verification for passwords in auth route (if not already hashed)
- [ ] Lock down CORS to localhost only

## Phase 1: Automatic Memory
**Goal:** ANIMA remembers without being told to.

- [ ] Post-conversation memory extraction — after each exchange, run a secondary LLM pass that extracts facts, preferences, relationships, and goals from the conversation
- [ ] Dedup and merge — before writing a new memory, check existing memories for overlap and update rather than append
- [ ] Add timestamps to all memory entries (created, last referenced)
- [ ] Memory relevance at conversation start — on each new session, recall top memories related to recent topics and inject into system context

## Phase 2: Proactive Companion
**Goal:** ANIMA speaks first when it matters.

- [ ] Daily brief — on app launch, ANIMA generates a short briefing: current focus, open tasks, recent journal themes, anything time-sensitive
- [ ] Nudge system — background check loop (runs on app open or on interval):
  - Overdue tasks or stale focus
  - Journal gaps (hasn't journaled in N days)
  - Unfinished conversation threads ("you mentioned wanting to look into X")
- [ ] Nudges appear as a quiet banner or first message in chat, not as push notifications

## Phase 3: Ambient Presence
**Goal:** ANIMA lives beyond the chat window.

- [ ] Tray/menubar mode — Tauri system tray with quick actions:
  - Show current focus
  - Quick thought capture (one-line input that saves to journal or memory)
  - Open full chat
- [ ] Compact view — small floating window for the daily brief and current focus, not full chat
- [ ] Keyboard shortcut to summon ANIMA from anywhere (global hotkey via Tauri)

## Phase 4: Memory Depth
**Goal:** Memory becomes intelligence, not just storage.

- [ ] Weekly consolidation — summarize the week's journals and conversations into a "weekly digest" memory file
- [ ] Relationship graph — track people mentioned, how they relate to each other, last time referenced
- [ ] Pattern detection — "you tend to lose focus on Wednesdays" or "you've mentioned this project 12 times but never started a task for it"
- [ ] Memory decay — surface stale memories for review ("you said X 3 months ago — still true?")

## Phase 5: True Edge
**Goal:** Make local-first the real experience, not just a claim.

- [ ] Guided Ollama setup in onboarding (detect if installed, suggest model, test connection)
- [ ] Visible privacy indicator in UI — "running locally" vs "using cloud provider"
- [ ] Offline mode — graceful degradation when no model is available (queue messages, show cached daily brief)
- [ ] Optional local embeddings for semantic memory search (replace keyword grep)

---

## Guiding principle

Build depth before breadth. Every phase should make ANIMA feel more like a companion who knows you — not a tool that does more things.
