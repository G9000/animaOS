# Today User Context Design

Date: 2026-05-30
Status: Approved for planning

## Summary

Add a user-editable "today context" note for the current user's mood, energy, and optional short note. The companion receives this context during chat so it can adapt tone, pacing, and suggestions for the rest of the day. The context must not be stored as durable memory, runtime chat history, session notes, self-model state, or transcript content.

## Goals

- Let the user set and update mood and energy for the current day.
- Let the user add a short optional note, such as "keep replies direct today."
- Inject the context into companion prompt assembly for chat turns.
- Keep the context active until the local day changes, logout/reload clears session state, or the user updates/clears it.
- Make the companion reflect the context behaviorally: adjust tone, pacing, and suggestions without diagnosing the user.
- Preserve a hard no-storage invariant for the raw mood, energy, and note.

## Non-goals

- Do not create new long-term memory items.
- Do not write mood/energy to `runtime_messages`, `runtime_session_notes`, `memory_items`, emotional signals, self-model blocks, transcripts, or pending memory operations.
- Do not add a new database table or migration.
- Do not infer clinical or diagnostic emotional labels from this input.
- Do not force the assistant to mention the context in every reply.

## User Experience

Expose a small "Today" input surface near the main chat/dashboard prompt. The user can update:

- Mood: short free text.
- Energy: low, medium, high, or short free text if the existing UI pattern makes that simpler.
- Note: optional short text.

The control should show the currently active values and allow clearing them. When cleared, no today context is sent with chat requests.

The UI should avoid explaining that this becomes memory. Copy should frame it as temporary context for today.

## API Contract

Extend chat requests with an optional request-only field:

```json
{
  "todayContext": {
    "date": "2026-05-30",
    "mood": "anxious",
    "energy": "low",
    "note": "keep replies direct today"
  }
}
```

Validation:

- `date` is required when `todayContext` is present and must match the server's local current date.
- `mood`, `energy`, and `note` are optional strings, but at least one must be non-empty.
- Use tight max lengths, roughly 80 characters for `mood`, 40 for `energy`, and 280 for `note`.
- Reject invalid payloads with the normal chat request validation path.

The field is accepted by both streaming and non-streaming chat paths.

## Server Design

Add a request schema for `TodayContext`.

During `_prepare_turn_context`, after normal memory blocks are built, append a read-only ephemeral `MemoryBlock`:

```text
Label: today_user_context
Description: User-authored temporary context for today. Use it to adapt tone, pacing, and suggestions. Do not store it as memory, do not diagnose it, and do not repeat it unless useful or asked.
Value:
Current user state for today:
- Mood: anxious
- Energy: low
- Note: keep replies direct today
```

This block is passed only to the current runtime invocation. It must not be included in `companion.set_memory_cache()` so it cannot leak into later turns unless the client sends it again.

The block should also be preserved through proactive and emergency compaction retries within the same request because it is part of the current turn context.

## Frontend Design

Add a small today-context state helper in the desktop app:

- Store values in `sessionStorage` with the current date.
- Drop the values when the stored date no longer matches today's local date.
- Let the user update or clear the values.
- Send the active context to `api.chat.send` and `api.chat.stream`.

This is session storage only. It is not sent to any memory endpoint and is not represented as `contextMessages`, because `contextMessages` are persisted into runtime history.

## Companion Behavior

The prompt instruction should make the companion:

- Use low energy as a cue for shorter, lower-friction responses.
- Use high energy as a cue that more momentum and optional next steps may be welcome.
- Use fragile, anxious, tired, or overwhelmed moods as a cue for calmer pacing and fewer assumptions.
- Avoid saying "I notice you are..." unless the user directly asks about the mood/energy note or it is naturally helpful.
- Never save or summarize this context into memory on its own.

## Privacy and Storage Invariant

The raw today context may exist only in:

- Desktop in-memory React state.
- Desktop `sessionStorage`.
- The chat request body.
- The in-process prompt memory block for that one request.

It must not be written to:

- `runtime_messages`
- `runtime_session_notes`
- `memory_items`
- `memory_episodes`
- `emotional_signals`
- `self_model_blocks`
- transcript archive files
- pending memory operations or memory candidates

## Testing Plan

Backend tests:

- Chat request validation accepts a valid `todayContext`.
- Invalid lengths or wrong-date payloads are rejected.
- The runtime receives a `today_user_context` memory block when supplied.
- The block is not persisted into `RuntimeMessage`.
- The block is not included in companion memory cache.
- The block survives retry after compaction/context overflow within the same request.

API client tests:

- `chat.send` includes `todayContext` when passed.
- `chat.stream` includes `todayContext` when passed.
- Existing calls without the field are unchanged.

Frontend tests or focused type checks:

- Today context expires when the date changes.
- Clear removes it from session storage and future requests.
- Chat sends the current active context.

## Open Risks

- Server and client local dates can differ around midnight. The first implementation should use the `date` field to prevent stale context; if this becomes noisy, switch to server-side expiry metadata returned by a lightweight endpoint.
- Assistant responses are normal chat messages and can mention the mood/energy note if the model chooses to. The prompt should discourage unnecessary repetition, but cannot make that impossible.
