# Scratchboard

## Phase 0: Clean Foundation — DONE
- [x] Fix default system prompt to align with soul.md
- [x] Remove legacy memories table, tools.ts, email, translation
- [x] Lock CORS, fix build script, verify password hashing

## Phase 1: Automatic Memory — DONE
- [x] `extract.ts` — post-conversation memory extraction (fire-and-forget)
- [x] `context.ts` — loads user memories into system prompt each request
- [x] Wired into all paths in graph.ts

## Phase 2: Proactive Companion — DONE
- [x] `brief.ts` — daily brief generator
- [x] `nudge.ts` — deterministic nudge checks (stale focus, overdue tasks, journal gaps, long absence)
- [x] `GET /chat/brief` + `GET /chat/nudges` endpoints
- [x] Dashboard shows brief + dismissable nudge banners

## Phase 3: Ambient Presence — DONE
- [x] System tray with "Open ANIMA" and "Quit" menu
- [x] Click tray icon to show/focus window
- [x] Global shortcut: Cmd+Shift+A (Ctrl+Shift+A on Windows/Linux) to summon from anywhere
- [x] `tauri-plugin-global-shortcut` added to both Rust and JS
- [x] App renamed to "ANIMA" in tauri.conf.json
- [x] Window label set to "main" for tray integration

## All new files
- `apps/api/src/agent/extract.ts` — auto memory extraction
- `apps/api/src/agent/context.ts` — memory context loader
- `apps/api/src/agent/brief.ts` — daily brief generator
- `apps/api/src/agent/nudge.ts` — nudge system

## Phase 4a: Soul Editor + Memory Consolidation — DONE
- [x] `GET/PUT /api/soul` — read and write `soul/soul.md` via API
- [x] Soul page (`/soul`) — full markdown editor with Cmd+S, reset, char/line count
- [x] Soul cache invalidation — editing soul.md takes effect on next conversation without restart
- [x] `consolidate.ts` — LLM-powered memory dedup, merge, and summarization
- [x] `POST /chat/consolidate` endpoint
- [x] Consolidate button in Memory Explorer sidebar
- [x] Safety check: rejects LLM output that drops >70% of entries
- [x] Soul nav link added to Dashboard
- [x] Route wired in App.tsx

## UI Overhaul — DONE
- [x] Shared `Layout.tsx` with slim icon sidebar (Home, Chat, Memory, Soul, Config)
- [x] Sidebar has tooltips on hover, active state, profile avatar + logout at bottom
- [x] Stripped per-page navigation headers from all pages (Dashboard, Chat, Memory, Settings, Soul, Profile)
- [x] All pages now render as content within the shared layout
- [x] Dashboard simplified — less dense, cleaner visual hierarchy, breathing room
- [x] Chat toolbar simplified — no redundant nav buttons
- [x] Memory explorer sidebar made more compact
- [x] Consistent `--color-*` variable syntax across all pages (no more mixed `bg-bg` vs `bg-(--color-bg)`)
- [x] Login/Register kept full-screen (no sidebar — public routes)

## Post-phase fixes
- [x] Restored translation feature (language picker + Google Translate API in chat)
- [x] Cached daily brief (in-memory, per-user per-day, 4h TTL)

## Still on the roadmap (Phases 4-5)
- Weekly memory consolidation
- Relationship graph
- Pattern detection + memory decay
- Guided Ollama onboarding
- Privacy indicator in UI
- Offline mode
- Local embeddings for semantic memory search
