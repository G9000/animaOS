import { describe, expect, test } from "bun:test";
import { JSDOM } from "jsdom";

// Same real jsdom + react-dom/client mount pattern used elsewhere in this
// suite (see diary-details-drawer-mood.test.tsx) — PR #139 round 2, Finding
// 1 only reproduces with a real controlled <input>, a real debounce timer,
// and a real unmount racing a real parent re-render, none of which
// renderToStaticMarkup (or a hand-rolled mock of DetailsDrawer) exercises.
const dom = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>', {
  url: "http://localhost/",
});
(globalThis as any).window = dom.window;
(globalThis as any).document = dom.window.document;
(globalThis as any).navigator = dom.window.navigator;
(globalThis as any).HTMLElement = dom.window.HTMLElement;
(globalThis as any).HTMLInputElement = dom.window.HTMLInputElement;
(globalThis as any).Node = dom.window.Node;
(globalThis as any).Event = dom.window.Event;
(globalThis as any).customElements = dom.window.customElements;
(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

const React = await import("react");
const { act, useRef, useState } = React;
const { createRoot } = await import("react-dom/client");
const { DetailsDrawer } = await import("../src/features/diary/panels/DetailsDrawer");
const { dispatchDrawerUpdate, resolveDrawerUpdateEntryId } = await import(
  "../src/features/diary/lib/drawerUpdate"
);

const tick = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

function makeEntry(overrides: Partial<any> = {}) {
  return {
    id: 1,
    userId: 1,
    entryDate: "2026-01-01",
    title: "Untitled",
    body: "",
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

function typeInto(input: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(dom.window.HTMLInputElement.prototype, "value")!.set!;
  setter.call(input, value);
  input.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
}

// A harness that mirrors DiaryWorkspace.tsx's real wiring for this bug,
// calling the REAL exported `dispatchDrawerUpdate` (production code, not a
// reimplementation) exactly the way DiaryWorkspace's `handleDrawerUpdate`
// does:
//
//   dispatchDrawerUpdate(entryId, data, selectedEntryRef.current?.id ?? null, handlers)
//
// with `selectedEntryRef.current` assigned during the render body (see
// DiaryWorkspace.tsx: `selectedEntryRef.current = selectedEntry;`) — the
// exact timing that lets the ref advance to the newly selected entry
// BEFORE the outgoing keyed DetailsDrawer's unmount cleanup fires.
function Harness({ entryA, entryB, updates }: { entryA: any; entryB: any; updates: any[] }) {
  const [selectedId, setSelectedId] = useState(entryA.id);
  const selectedEntryRef = useRef<any>(null);
  const entry = selectedId === entryA.id ? entryA : entryB;
  selectedEntryRef.current = entry;

  (Harness as any)._select = setSelectedId;

  const handleDrawerUpdate = (entryId: number, data: any) => {
    dispatchDrawerUpdate(entryId, data, selectedEntryRef.current?.id ?? null, {
      moveEntryToFolder: (id, folderId) => updates.push({ kind: "moveEntryToFolder", id, folderId }),
      updateEntry: (id, updateData) => updates.push({ kind: "updateEntry", id, data: updateData }),
    });
  };

  return (
    <DetailsDrawer
      key={entry.id}
      entry={entry}
      folders={[]}
      open={true}
      onClose={() => {}}
      onUpdate={handleDrawerUpdate}
      onDelete={() => {}}
      onCoverFileSelected={() => {}}
      onFilesSelected={() => {}}
      onOpenAttachment={() => {}}
      onAttachmentError={() => {}}
      bodyText=""
      recording={false}
      speechAvailable={false}
      liveTranscript=""
      onToggleRecording={() => {}}
    />
  );
}

describe("resolveDrawerUpdateEntryId (pure)", () => {
  test("always targets the originating entry, regardless of what is currently selected", () => {
    expect(resolveDrawerUpdateEntryId(7, 7)).toBe(7);
    expect(resolveDrawerUpdateEntryId(7, 42)).toBe(7);
    expect(resolveDrawerUpdateEntryId(7, null)).toBe(7);
  });
});

describe("PR #139 round 2, Finding 1: mood teardown flush targets the originating entry", () => {
  test("switching entries before the debounce fires commits the mood onto the entry it was typed into, not the newly selected one", async () => {
    const container = dom.window.document.getElementById("root") as HTMLElement;
    const root = createRoot(container);
    const updates: any[] = [];
    const entryA = makeEntry({ id: 1, mood: null });
    const entryB = makeEntry({ id: 2, mood: null });

    try {
      await act(async () => {
        root.render(<Harness entryA={entryA} entryB={entryB} updates={updates} />);
      });

      const input = container.querySelector(
        'input[placeholder="How are you feeling?"]',
      ) as HTMLInputElement;
      expect(input).toBeTruthy();

      // Type a mood into entry A, but don't blur and don't wait out the
      // 600ms debounce.
      await act(async () => {
        typeInto(input, "hopeful");
      });
      expect(updates.length).toBe(0);

      // Switch to entry B before the debounce fires. This re-renders the
      // harness (advancing `selectedEntryRef.current` to entry B during
      // the render body) and, because DetailsDrawer is keyed by entry.id,
      // unmounts the entry-A instance — running its teardown flush.
      await act(async () => {
        (Harness as any)._select(entryB.id);
      });

      // The real bug: a parent that re-derives "the current entry" from
      // ambient selection state at the moment the flush arrives commits
      // entry A's typed mood onto entry B instead.
      expect(updates.length).toBe(1);
      expect(updates[0]).toEqual({
        kind: "updateEntry",
        id: entryA.id,
        data: { mood: "hopeful", clearMood: false },
      });
    } finally {
      root.unmount();
    }
  });
});
