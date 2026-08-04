// Task 12 review, Finding 1 (CRITICAL): identity-aware teardown for a ref
// shared across a keyed remount (DiaryEditor is remounted fresh per
// entry.id — see editor/DiaryEditor.tsx and DiaryWorkspace.tsx's
// handleEditorReady/handleEditorDestroyed).
//
// Tiptap's own create/destroy ordering across such a remount is NOT
// guaranteed. The reviewer reproduced — against the installed
// @tiptap/react@3.29.2 + React 19 in jsdom — the INCOMING editor's
// `create` firing BEFORE the OUTGOING editor's `destroy`: `Editor.mount()`
// schedules emitting `create` via one timer registered during the new
// component's render, while `EditorInstanceManager.scheduleDestroy()`
// schedules the old instance's teardown via an independent timer
// registered during the old component's effect cleanup. Nothing guarantees
// which of those two timers fires first.
//
// A parent that reacted to teardown with an unconditional
// `ref.current = null` would have the outgoing (now-stale) instance's
// belated teardown null out a ref the incoming instance already
// repopulated — silently breaking every consumer of that ref (title
// autosave scheduling, the non-text-content snapshot used by the
// untitled-page cleanup, inline-image embed, voice-transcript insertion,
// focus-on-canvas-click) after every entry switch, regardless of which
// entry is now selected.
//
// This function is deliberately framework-free and pure, independent of
// React/Tiptap, so it is directly unit-testable against plain values (see
// tests/diary-editor-handoff.test.ts) without needing an interactive React
// test harness.
export interface EditorHandoffRef<T> {
  current: T | null;
}

/**
 * Only clears `ref.current` if it still points at the exact instance being
 * torn down. Correct regardless of which of the two independent async
 * teardown/create callbacks fires first — it does not depend on ordering
 * at all, only on object identity at the moment it runs.
 */
export function handleInstanceTornDown<T>(ref: EditorHandoffRef<T>, destroyed: T): void {
  if (ref.current === destroyed) {
    ref.current = null;
  }
}
