# Today User Context Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a user-editable, rest-of-today mood/energy context that the companion receives during chat without storing it in memory or chat history.

**Architecture:** The desktop owns the temporary state in `sessionStorage` and includes it in chat requests. The server validates the request-only payload and appends a read-only `today_user_context` memory block after cacheable memory blocks are built, so the runtime can adapt behavior without persisting or caching the raw context.

**Tech Stack:** FastAPI/Pydantic, SQLAlchemy runtime tests, Bun TypeScript API client, React/Vite desktop.

---

## File Structure

- Modify `apps/server/src/anima_server/schemas/chat.py`: add `TodayContext` schema and optional `todayContext` on `ChatRequest`.
- Modify `apps/server/src/anima_server/api/routes/chat.py`: pass `payload.todayContext` into blocking and streaming agent calls.
- Modify `apps/server/src/anima_server/services/agent/service.py`: thread `today_context` through turn setup and append an ephemeral `MemoryBlock`.
- Modify `apps/server/tests/test_agent_service.py`: prove prompt injection and no runtime persistence/cache leakage.
- Modify `apps/server/tests/test_chat.py`: prove request validation rejects stale dates.
- Modify `packages/api-client/src/types.ts`: add `TodayContext`.
- Modify `packages/api-client/src/client.ts`: allow `todayContext` in `chat.send` and `chat.stream`.
- Modify `packages/api-client/tests/client.test.ts`: prove request bodies include `todayContext`.
- Create `apps/desktop/src/lib/today-context.ts`: session storage, date expiry, and sanitization helper.
- Modify `apps/desktop/src/pages/chat/Chat.tsx`: load/update today context, render the editor near chat input, and send context with each turn.

## Task 1: Backend Schema and Prompt Injection

**Files:**
- Modify: `apps/server/src/anima_server/schemas/chat.py`
- Modify: `apps/server/src/anima_server/api/routes/chat.py`
- Modify: `apps/server/src/anima_server/services/agent/service.py`
- Test: `apps/server/tests/test_agent_service.py`
- Test: `apps/server/tests/test_chat.py`

- [ ] **Step 1: Write failing service test for ephemeral prompt block**

Add a test to `apps/server/tests/test_agent_service.py` near the existing `test_run_agent_includes_home_greeting_context_in_current_turn`:

```python
@pytest.mark.asyncio
async def test_run_agent_includes_today_context_without_persisting_or_caching(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_service.invalidate_agent_runtime_cache()
    runner = RecordingRunner()
    monkeypatch.setattr(agent_service, "get_or_build_runner", lambda: runner)
    monkeypatch.setattr(agent_service, "_run_post_turn_hooks", lambda **kwargs: None)

    try:
        with _soul_db_session() as soul_session, runtime_db_session() as runtime_session:
            user = User(
                username="today-context",
                password_hash="not-used",
                display_name="Today Context",
            )
            soul_session.add(user)
            soul_session.commit()

            today = agent_service.date.today().isoformat()
            await run_agent(
                "Can you help me plan this?",
                user.id,
                soul_session,
                runtime_session,
                today_context=agent_service.TodayContext(
                    date=today,
                    mood="tired",
                    energy="low",
                    note="keep replies direct",
                ),
            )

            messages = runtime_session.query(RuntimeMessage).order_by(RuntimeMessage.sequence_id).all()
            companion = agent_service.get_companion(user.id)
    finally:
        agent_service.invalidate_agent_runtime_cache()

    blocks = runner.requests[0]["memory_blocks"]
    today_blocks = [block for block in blocks if block[0] == "today_user_context"]
    assert len(today_blocks) == 1
    assert "Mood: tired" in today_blocks[0][1]
    assert "Energy: low" in today_blocks[0][1]
    assert "Note: keep replies direct" in today_blocks[0][1]
    assert [(message.role, message.content_text) for message in messages] == [
        ("user", "Can you help me plan this?"),
        ("assistant", "Reply to: Can you help me plan this?"),
    ]
    assert companion is not None
    cached = companion.get_cached_memory_blocks() or ()
    assert all(block.label != "today_user_context" for block in cached)
```

- [ ] **Step 2: Run service test and verify RED**

Run: `uv run pytest apps/server/tests/test_agent_service.py::test_run_agent_includes_today_context_without_persisting_or_caching -q`

Expected: FAIL because `today_context` / `TodayContext` is not implemented.

- [ ] **Step 3: Add backend schema and service plumbing**

In `schemas/chat.py`, add:

```python
from datetime import date

class TodayContext(BaseModel):
    date: str = Field(min_length=10, max_length=10)
    mood: str | None = Field(default=None, max_length=80)
    energy: str | None = Field(default=None, max_length=40)
    note: str | None = Field(default=None, max_length=280)

    @model_validator(mode="after")
    def validate_today_context(self) -> "TodayContext":
        if self.date != date.today().isoformat():
            raise ValueError("Today context date must match the current date.")
        if not any((self.mood or "").strip() or (self.energy or "").strip() or (self.note or "").strip()):
            raise ValueError("Today context requires mood, energy, or note.")
        return self
```

Then add `todayContext: TodayContext | None = None` to `ChatRequest`.

In `service.py`, import `date` and `TodayContext`, add `today_context: TodayContext | None = None` parameters to `run_agent`, `_execute_agent_turn`, `_execute_agent_turn_locked`, `_prepare_turn_context`, and `stream_agent`, and pass it through all calls.

Add a helper:

```python
def _build_today_context_block(today_context: TodayContext | None) -> MemoryBlock | None:
    if today_context is None:
        return None
    lines = ["Current user state for today:"]
    if today_context.mood and today_context.mood.strip():
        lines.append(f"- Mood: {today_context.mood.strip()}")
    if today_context.energy and today_context.energy.strip():
        lines.append(f"- Energy: {today_context.energy.strip()}")
    if today_context.note and today_context.note.strip():
        lines.append(f"- Note: {today_context.note.strip()}")
    return MemoryBlock(
        label="today_user_context",
        value="\n".join(lines),
        description=(
            "User-authored temporary context for today. Use it to adapt tone, "
            "pacing, and suggestions. Do not store it as memory, do not diagnose it, "
            "and do not repeat it unless useful or asked."
        ),
        read_only=True,
    )
```

After cacheable memory blocks are selected in `_prepare_turn_context`, append this block to the local `memory_blocks` tuple only. Do not include it in `companion.set_memory_cache()`.

In `routes/chat.py`, pass `today_context=payload.todayContext` to `run_agent` and `stream_agent`.

- [ ] **Step 4: Run service test and verify GREEN**

Run: `uv run pytest apps/server/tests/test_agent_service.py::test_run_agent_includes_today_context_without_persisting_or_caching -q`

Expected: PASS.

- [ ] **Step 5: Write failing HTTP validation test**

Add to `apps/server/tests/test_chat.py`:

```python
def test_chat_rejects_stale_today_context_date() -> None:
    with _scaffold_agent_settings(), _client() as client:
        user = _register_user(client, username="stale-context")
        headers = {"x-anima-unlock": str(user["unlockToken"])}
        response = client.post(
            "/api/chat",
            headers=headers,
            json={
                "message": "hello",
                "userId": int(user["id"]),
                "todayContext": {
                    "date": "1900-01-01",
                    "mood": "tired",
                },
            },
        )

    assert response.status_code == 422
```

- [ ] **Step 6: Run validation test and verify GREEN**

Run: `uv run pytest apps/server/tests/test_chat.py::test_chat_rejects_stale_today_context_date -q`

Expected: PASS after schema validation exists.

- [ ] **Step 7: Commit backend slice**

Run:

```bash
git add apps/server/src/anima_server/schemas/chat.py apps/server/src/anima_server/api/routes/chat.py apps/server/src/anima_server/services/agent/service.py apps/server/tests/test_agent_service.py apps/server/tests/test_chat.py
git commit -m "feat: inject ephemeral today context"
```

## Task 2: API Client Contract

**Files:**
- Modify: `packages/api-client/src/types.ts`
- Modify: `packages/api-client/src/client.ts`
- Test: `packages/api-client/tests/client.test.ts`

- [ ] **Step 1: Write failing API client tests**

In `packages/api-client/tests/client.test.ts`, extend the existing context-message test or add a new test:

```typescript
test("sends today context with chat requests", async () => {
  let requestBody: unknown = null;
  const api = createApiClient({
    baseUrl: "https://api.test/api",
    fetchImpl: async (_input, init) => {
      requestBody = JSON.parse(String(init?.body));
      return new Response(JSON.stringify({ response: "ok", model: "m", provider: "p", toolsUsed: [] }));
    },
  });

  await api.chat.send("Help me focus.", 7, undefined, [], [], {
    date: "2026-05-30",
    mood: "tired",
    energy: "low",
    note: "short replies",
  });

  expect(requestBody).toMatchObject({
    message: "Help me focus.",
    userId: 7,
    stream: false,
    todayContext: {
      date: "2026-05-30",
      mood: "tired",
      energy: "low",
      note: "short replies",
    },
  });
});
```

- [ ] **Step 2: Run API client test and verify RED**

Run: `bun run test packages/api-client/tests/client.test.ts`

Expected: FAIL because the extra `todayContext` parameter is ignored or not typed.

- [ ] **Step 3: Add client types and request fields**

In `types.ts`, add:

```typescript
export interface TodayContext {
  date: string;
  mood?: string | null;
  energy?: string | null;
  note?: string | null;
}
```

In `client.ts`, import/use `TodayContext`, add an optional final `todayContext?: TodayContext | null` parameter to `streamChat`, `chat.send`, and `chat.stream`, and include:

```typescript
...(todayContext ? { todayContext } : {}),
```

in both request bodies.

- [ ] **Step 4: Run API client test and verify GREEN**

Run: `bun run test packages/api-client/tests/client.test.ts`

Expected: PASS.

- [ ] **Step 5: Commit API client slice**

Run:

```bash
git add packages/api-client/src/types.ts packages/api-client/src/client.ts packages/api-client/tests/client.test.ts
git commit -m "api-client: send today context"
```

## Task 3: Desktop Today Context UI

**Files:**
- Create: `apps/desktop/src/lib/today-context.ts`
- Modify: `apps/desktop/src/pages/chat/Chat.tsx`

- [ ] **Step 1: Write helper module**

Create `apps/desktop/src/lib/today-context.ts`:

```typescript
import type { TodayContext } from "@anima/api-client";

const STORAGE_KEY = "anima_today_context";

export type TodayContextDraft = Omit<TodayContext, "date">;

export function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

export function normalizeTodayContext(
  draft: TodayContextDraft,
  date = todayIso(),
): TodayContext | null {
  const mood = draft.mood?.trim() || "";
  const energy = draft.energy?.trim() || "";
  const note = draft.note?.trim() || "";
  if (!mood && !energy && !note) return null;
  return {
    date,
    ...(mood ? { mood } : {}),
    ...(energy ? { energy } : {}),
    ...(note ? { note } : {}),
  };
}

export function loadTodayContext(): TodayContext | null {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as TodayContext;
    if (parsed.date !== todayIso()) {
      sessionStorage.removeItem(STORAGE_KEY);
      return null;
    }
    return normalizeTodayContext(parsed, parsed.date);
  } catch {
    return null;
  }
}

export function saveTodayContext(context: TodayContext | null): void {
  try {
    if (!context) {
      sessionStorage.removeItem(STORAGE_KEY);
      return;
    }
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(context));
  } catch {
    // Ignore storage failures.
  }
}
```

- [ ] **Step 2: Wire chat state and request sending**

In `Chat.tsx`:

- import `TodayContext`
- import `loadTodayContext`, `normalizeTodayContext`, `saveTodayContext`
- add `const [todayContext, setTodayContext] = useState<TodayContext | null>(() => loadTodayContext());`
- pass `todayContext ?? undefined` as the final argument to `api.chat.stream(...)`

- [ ] **Step 3: Render a compact editor above the chat input**

Add a small `TodayContextCard` component in `Chat.tsx` or a focused component file if the inline component gets large. It should show three compact inputs:

- mood
- energy
- note

On save/update:

```typescript
const next = normalizeTodayContext({ mood, energy, note });
setTodayContext(next);
saveTodayContext(next);
```

On clear:

```typescript
setTodayContext(null);
saveTodayContext(null);
```

Render it in `inputAccessory` before proactive notices. Keep it visually restrained and avoid nested cards.

- [ ] **Step 4: Run desktop typecheck/build**

Run: `bun run build:desktop`

Expected: PASS.

- [ ] **Step 5: Commit desktop slice**

Run:

```bash
git add apps/desktop/src/lib/today-context.ts apps/desktop/src/pages/chat/Chat.tsx
git commit -m "desktop: add today context input"
```

## Task 4: Final Verification

**Files:**
- No new files expected.

- [ ] **Step 1: Run focused backend tests**

Run:

```bash
uv run pytest apps/server/tests/test_agent_service.py::test_run_agent_includes_today_context_without_persisting_or_caching apps/server/tests/test_chat.py::test_chat_rejects_stale_today_context_date -q
```

Expected: PASS.

- [ ] **Step 2: Run API client tests**

Run: `bun run test packages/api-client/tests/client.test.ts`

Expected: PASS.

- [ ] **Step 3: Run active app build**

Run: `bun run build`

Expected: PASS for server and desktop.

- [ ] **Step 4: Inspect diffs for storage invariant**

Run: `git diff --check`

Expected: no whitespace errors.

Run: `rg -n "today_user_context|todayContext|runtime_session_notes|memory_items|emotional_signals|self_model_blocks" apps packages`

Expected: today context code appears only in request schemas, request transport, prompt block injection, tests, and desktop session helper. No writes to memory tables or session notes.

- [ ] **Step 5: Final commit if needed**

If verification produced cleanup changes:

```bash
git add <changed files>
git commit -m "test: verify today context flow"
```
