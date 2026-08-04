import { describe, expect, test } from "bun:test";
import { JSDOM } from "jsdom";
import {
  graduateSessionEntry,
  isDiscardablePage,
  isSessionDiscardable,
  resolveLiveMood,
} from "../src/features/diary/lib/pageLifecycle";

// Same real jsdom + react-dom/client mount pattern used elsewhere in this
// suite (see diary-drawer-update-target.test.tsx) — PR #139 round 3,
// Finding 1 only reproduces with a real controlled <input>, a real
// debounce timer, and a real unmount racing a real parent decision, none
// of which renderToStaticMarkup (or a hand-rolled mock of DetailsDrawer)
// exercises.
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
const { dispatchDrawerUpdate } = await import("../src/features/diary/lib/drawerUpdate");

const base = {
  title: null,
  bodyPlainText: "",
  attachmentCount: 0,
  coverAttachmentId: null,
  hasNonTextContent: false,
  mood: null,
  folderId: null,
  initialFolderId: null,
  entryDate: "2026-01-01",
  initialEntryDate: "2026-01-01",
};

// ---------------------------------------------------------------------------
// FINDING 2 (P1): the untitled-page cleanup must never discard an entry the
// workspace did not itself create this session, no matter how blank it is.
// ---------------------------------------------------------------------------
describe("PR #139 round 3, Finding 2: session-scoped discard eligibility", () => {
  test("BEFORE (bug reproduction): isDiscardablePage alone has no notion of session origin — a blank entry looks equally discardable whether it was just created or freshly loaded from the server", () => {
    // This IS the real, still-exported, unchanged predicate that
    // DiaryWorkspace.tsx's evaluateAndMaybeDiscard called directly, with
    // no session check, before this fix — on EVERY one of its three call
    // sites (unmount, startNewEntry, selectEntry). A pre-existing entry
    // the user loaded, saw was blank (e.g. one they cleared out in an
    // earlier session), and simply left produces the exact same inputs as
    // a scratch page fresh off "+ New entry" — isDiscardablePage cannot
    // and does not distinguish them.
    expect(isDiscardablePage({ ...base })).toBe(true); // BUG: would delete a server-loaded entry
  });

  test("AFTER (fixed): a server-loaded blank entry (createdThisSession: false) is never discardable", () => {
    expect(isSessionDiscardable({ ...base, createdThisSession: false })).toBe(false);
  });

  test("AFTER (fixed): a blank entry this session created (createdThisSession: true) is still discardable", () => {
    expect(isSessionDiscardable({ ...base, createdThisSession: true })).toBe(true);
  });

  test("createdThisSession: true does not bypass the ordinary content checks — a session-created entry with real content is still kept", () => {
    expect(isSessionDiscardable({ ...base, createdThisSession: true, title: "Monday" })).toBe(false);
  });

  test("graduateSessionEntry removes an id from the eligible set (idempotent, no-op if absent)", () => {
    const ids = new Set<number>([1, 2]);
    graduateSessionEntry(ids, 1);
    expect(ids.has(1)).toBe(false);
    expect(ids.has(2)).toBe(true);
    // Calling again (already graduated) must not throw or affect anything else.
    graduateSessionEntry(ids, 1);
    expect(ids.has(2)).toBe(true);
  });

  test("the compounding chain this fix must break: create -> type real content -> save -> clear back to blank -> leave is no longer silently deleted", () => {
    // Mirrors DiaryWorkspace.tsx's real sequence: startNewEntry() adds the
    // new id to the session-eligible set; a real, user-driven edit (body,
    // title, or drawer field) calls graduateSessionEntry and never adds
    // the id back, regardless of what the entry looks like afterward.
    const sessionCreatedIds = new Set<number>([1]);
    // User types a real title/body (handleTitleChange / handleEditorChange
    // both call graduateSessionEntry on a genuine edit).
    graduateSessionEntry(sessionCreatedIds, 1);
    // User then clears everything back to exactly the blank state a
    // freshly-created page starts in, and leaves.
    const finalState = { ...base, createdThisSession: sessionCreatedIds.has(1) };
    expect(isSessionDiscardable(finalState)).toBe(false); // kept: this graduated to a real entry
  });
});

// ---------------------------------------------------------------------------
// FINDING 1 (P1): the discard decision must never read entry.mood directly —
// it has to see what the user actually typed, even if not yet committed.
// ---------------------------------------------------------------------------
describe("PR #139 round 3, Finding 1: live mood draft, not stale server metadata", () => {
  test("resolveLiveMood prefers a same-entry draft over the committed value", () => {
    expect(resolveLiveMood({ entryId: 1, mood: "hopeful" }, 1, null)).toBe("hopeful");
  });

  test("resolveLiveMood falls back to the committed value when no draft has been recorded", () => {
    expect(resolveLiveMood(null, 1, "content")).toBe("content");
  });

  test("resolveLiveMood ignores a draft tagged for a different entry", () => {
    expect(resolveLiveMood({ entryId: 2, mood: "hopeful" }, 1, null)).toBeNull();
  });

  test("BEFORE (bug reproduction): feeding isDiscardablePage the stale, uncommitted-yet server mood lets a page with a typed-but-undebounced mood look discardable", () => {
    // This is exactly line 353's pre-fix shape: `mood: entry.mood ?? null`,
    // fed straight into the real, unchanged isDiscardablePage — the user
    // has typed "hopeful" into DetailsDrawer, but the 600ms debounce has
    // not fired, so entry.mood (server state) is still null.
    const staleEntryMood: string | null = null;
    expect(isDiscardablePage({ ...base, mood: staleEntryMood })).toBe(true); // BUG: would delete the page
  });

  test("AFTER (fixed): resolving the live draft first keeps the same page", () => {
    const liveMood = resolveLiveMood({ entryId: 1, mood: "hopeful" }, 1, null);
    expect(isDiscardablePage({ ...base, mood: liveMood })).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Integration-level regression: real DetailsDrawer + real dispatchDrawerUpdate
// + real pageLifecycle predicates, wired exactly the way DiaryWorkspace.tsx
// wires them, reproducing the full race from the round 3 review: type a mood,
// switch away before the debounce fires, and confirm neither an unwarranted
// delete NOR a PATCH-after-delete can occur.
// ---------------------------------------------------------------------------
function makeEntry(overrides: Partial<any> = {}) {
  return {
    id: 1,
    userId: 1,
    entryDate: "2026-01-01",
    title: null,
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

// `useStaleMood` toggles between the pre-fix wiring (reads entry.mood
// directly, the literal shape of DiaryWorkspace.tsx:353 before this round)
// and the fixed wiring (resolveLiveMood) — both branches call the SAME real
// exported isSessionDiscardable/isDiscardablePage, only the input they're
// fed differs, exactly mirroring what changed in DiaryWorkspace.tsx.
function Harness({
  entryA,
  entryB,
  events,
  useStaleMood,
}: {
  entryA: any;
  entryB: any;
  events: any[];
  useStaleMood: boolean;
}) {
  const [selectedId, setSelectedId] = useState(entryA.id);
  const selectedEntryRef = useRef<any>(null);
  const entriesRef = useRef<Record<number, any>>({ [entryA.id]: entryA, [entryB.id]: entryB });
  const sessionCreatedIdsRef = useRef<Set<number>>(new Set([entryA.id])); // A created this session; B is not (loaded from server)
  const liveMoodDraftRef = useRef<{ entryId: number; mood: string } | null>(null);

  const entry = entriesRef.current[selectedId];
  selectedEntryRef.current = entry;

  const handleDrawerUpdate = (entryId: number, data: any) => {
    sessionCreatedIdsRef.current.delete(entryId); // mirrors DiaryWorkspace's graduateSessionEntry call
    dispatchDrawerUpdate(entryId, data, selectedEntryRef.current?.id ?? null, {
      moveEntryToFolder: (id: number, folderId: number | null) =>
        events.push({ kind: "moveEntryToFolder", id, folderId }),
      updateEntry: (id: number, updateData: any) => {
        events.push({ kind: "updateEntry", id, data: updateData });
        // Simulate the server round trip updating the entry's mood, same
        // as useDiaryEntries' real setEntries mapper.
        const target = entriesRef.current[id];
        if (target && "mood" in updateData) target.mood = updateData.mood ?? null;
      },
    });
  };

  const handleMoodDraftChange = (entryId: number, mood: string) => {
    liveMoodDraftRef.current = { entryId, mood };
  };

  const evaluateAndMaybeDiscard = async (leavingEntry: any) => {
    const mood = useStaleMood
      ? (leavingEntry.mood ?? null) // pre-fix: reads server-committed state directly
      : resolveLiveMood(liveMoodDraftRef.current, leavingEntry.id, leavingEntry.mood ?? null); // fixed
    const discardable = isSessionDiscardable({
      createdThisSession: sessionCreatedIdsRef.current.has(leavingEntry.id),
      title: leavingEntry.title,
      bodyPlainText: "",
      attachmentCount: leavingEntry.attachments.length,
      coverAttachmentId: leavingEntry.coverAttachmentId,
      hasNonTextContent: false,
      mood,
      folderId: leavingEntry.folderId ?? null,
      initialFolderId: leavingEntry.folderId ?? null,
      entryDate: leavingEntry.entryDate,
      initialEntryDate: leavingEntry.entryDate,
    });
    if (discardable) {
      sessionCreatedIdsRef.current.delete(leavingEntry.id);
      events.push({ kind: "discard", id: leavingEntry.id });
    }
  };

  (Harness as any)._selectEntry = async (id: number) => {
    const leaving = selectedEntryRef.current;
    if (leaving) await evaluateAndMaybeDiscard(leaving);
    setSelectedId(id);
  };

  return (
    <DetailsDrawer
      key={entry.id}
      entry={entry}
      folders={[]}
      open={true}
      onClose={() => {}}
      onUpdate={handleDrawerUpdate}
      onMoodDraftChange={handleMoodDraftChange}
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

async function runScenario(useStaleMood: boolean) {
  const container = dom.window.document.getElementById("root") as HTMLElement;
  const root = createRoot(container);
  const events: any[] = [];
  const entryA = makeEntry({ id: 101, mood: null });
  const entryB = makeEntry({ id: 202, mood: null });

  try {
    await act(async () => {
      root.render(<Harness entryA={entryA} entryB={entryB} events={events} useStaleMood={useStaleMood} />);
    });

    const input = container.querySelector(
      'input[placeholder="How are you feeling?"]',
    ) as HTMLInputElement;
    expect(input).toBeTruthy();

    // Type a mood into entry A, but don't blur and don't wait out the
    // 600ms debounce — the exact race the round 3 review reproduced.
    await act(async () => {
      typeInto(input, "hopeful");
    });
    expect(events.length).toBe(0);

    // Switch to entry B before the debounce fires: evaluateAndMaybeDiscard
    // runs first (against entry A, still selected), THEN React re-renders
    // with the new selection, unmounting the keyed DetailsDrawer(A) and
    // running its own teardown flush.
    await act(async () => {
      await (Harness as any)._selectEntry(entryB.id);
    });

    return events;
  } finally {
    root.unmount();
  }
}

describe("PR #139 round 3: full race — mood typed, switched away before debounce, drawer unmount flush", () => {
  test("BEFORE (bug reproduction): stale entry.mood lets the page be discarded, then the drawer's own unmount flush PATCHes the now-deleted entry", async () => {
    const events = await runScenario(true);

    // The discard fires first (evaluateAndMaybeDiscard runs before the
    // re-render that unmounts DetailsDrawer(A)), and only afterward does
    // A's teardown flush its typed mood as an updateEntry PATCH — against
    // an entry that, by then, no longer exists server-side.
    expect(events).toEqual([
      { kind: "discard", id: 101 },
      { kind: "updateEntry", id: 101, data: { mood: "hopeful", clearMood: false } },
    ]);
  });

  test("AFTER (fixed): the page is never discarded, and the mood PATCH lands on a live entry", async () => {
    const events = await runScenario(false);

    expect(events).toEqual([{ kind: "updateEntry", id: 101, data: { mood: "hopeful", clearMood: false } }]);
    expect(events.some((e) => e.kind === "discard")).toBe(false);
  });
});
