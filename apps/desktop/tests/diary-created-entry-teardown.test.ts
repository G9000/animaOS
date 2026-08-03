import { describe, expect, test } from "bun:test";
import { JSDOM } from "jsdom";
import type { DiaryEntryData } from "@anima/api-client";

// DiaryWorkspace.tsx (and modules it imports, e.g. editor/DiaryEditor.tsx)
// reference `window` at module-evaluation time (sanitizer setup) — same
// real-jsdom-before-import pattern used throughout this suite.
const dom = new JSDOM("<!doctype html><html><body></body></html>");
(globalThis as any).window = dom.window;
(globalThis as any).document = dom.window.document;
(globalThis as any).navigator = dom.window.navigator;
(globalThis as any).DOMParser = dom.window.DOMParser;
(globalThis as any).Node = dom.window.Node;
(globalThis as any).HTMLElement = dom.window.HTMLElement;
(globalThis as any).customElements = dom.window.customElements;

const { finalizeCreatedEntry } = await import("../src/features/diary/DiaryWorkspace");

// PR #139 round 8, Finding 4: `startNewEntry`'s `await createEntry(...)`
// (a real network POST) can still be pending when the whole workspace
// unmounts (e.g. the user clicks "New entry" then immediately navigates
// away from /journal). `finalizeCreatedEntry` is the exact continuation
// that runs once that POST resolves — extracted out of the component so
// it can be exercised here directly against a real (mocked) `created`
// entry and a controllable `isMounted` flag, without mounting the whole
// workspace (which needs AuthContext, a live Tiptap editor, and more).

function untouchedEntry(overrides: Partial<DiaryEntryData> = {}): DiaryEntryData {
  return {
    id: 42,
    userId: 1,
    entryDate: "2026-01-01",
    title: null,
    // The literal BLANK_BODY_MARKER (U+200B) createEntry always seeds a
    // fresh entry with — see hooks/useDiaryEntries.ts.
    body: "​",
    mood: null,
    source: "app",
    coverAttachmentId: null,
    folderId: null,
    attachments: [],
    createdAt: null,
    updatedAt: null,
    ...overrides,
  };
}

describe("finalizeCreatedEntry (PR #139 round 8, Finding 4)", () => {
  test("still mounted: selects the entry normally, never touches discardEntrySilently", async () => {
    const created = untouchedEntry();
    const selected: number[] = [];
    const discarded: number[] = [];

    await finalizeCreatedEntry(created, () => true, {
      select: (id) => selected.push(id),
      discardEntrySilently: async (id) => {
        discarded.push(id);
        return true;
      },
    });

    expect(selected).toEqual([42]);
    expect(discarded).toEqual([]);
  });

  test("unmounted before the POST resolved: deletes the untouched entry, never selects it", async () => {
    const created = untouchedEntry();
    const selected: number[] = [];
    const discarded: number[] = [];

    await finalizeCreatedEntry(created, () => false, {
      select: (id) => selected.push(id),
      discardEntrySilently: async (id) => {
        discarded.push(id);
        return true;
      },
    });

    // Never calls the "select" handler — in the real component that is
    // `sessionCreatedEntryIdsRef.current.add(id)` plus `setSelectedId(id)`,
    // and the latter is a React state setter on a component that is gone.
    expect(selected).toEqual([]);
    // Does clean up the entry it just created and nobody will ever see.
    expect(discarded).toEqual([42]);
  });

  test("unmounted before the POST resolved, but the entry was NOT genuinely blank: kept, never deleted on a guess", async () => {
    // Same unmount race, but the created entry itself already carries
    // real content (e.g. a mood set through some other path, or a
    // non-null folder that differs from where a truly fresh entry would
    // land) — reuses isSessionDiscardable/isUntouchedCreatedEntry rather
    // than unconditionally deleting whatever comes back from an unmounted
    // create.
    const created = untouchedEntry({ mood: "hopeful" });
    const selected: number[] = [];
    const discarded: number[] = [];

    await finalizeCreatedEntry(created, () => false, {
      select: (id) => selected.push(id),
      discardEntrySilently: async (id) => {
        discarded.push(id);
        return true;
      },
    });

    expect(selected).toEqual([]);
    expect(discarded).toEqual([]);
  });

  test("BEFORE (bug reproduction, literal pre-fix shape): with no unmount check at all, the create's continuation always calls select — even when the workspace is already gone", async () => {
    // This is the literal shape `startNewEntry` had before this round's
    // fix: unconditionally act on `created` the instant the POST
    // resolves, with no notion of whether anything is still around to
    // receive the result. Mirrored inline (not calling the real,
    // now-fixed `finalizeCreatedEntry`) specifically to demonstrate what
    // it used to do — the "AFTER" behavior above is asserted against the
    // real, current export.
    async function preFixContinuation(
      created: DiaryEntryData,
      handlers: { select: (id: number) => void },
    ): Promise<void> {
      handlers.select(created.id);
    }

    const created = untouchedEntry();
    const selected: number[] = [];
    await preFixContinuation(created, { select: (id) => selected.push(id) });

    // BUG: selects (in the real component: calls setSelectedId, a state
    // setter) regardless of whether the workspace that started this is
    // still mounted — the exact defect this round's fix closes.
    expect(selected).toEqual([42]);
  });
});
