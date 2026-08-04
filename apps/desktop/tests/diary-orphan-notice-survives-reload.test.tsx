import { afterEach, describe, expect, test } from "bun:test";
import { JSDOM } from "jsdom";

// Real jsdom + react-dom/client mount, same pattern as
// diary-upload-graduation.test.tsx: this exercises the REAL
// `useDiaryEntries` hook's `reload`/`setError` (not a stub that is
// incapable of clearing the error), because the whole bug lives in how
// `reload`'s genuine `setError(null)` on success interacts with the notice.
const dom = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>', {
  url: "http://localhost/",
});
(globalThis as any).window = dom.window;
(globalThis as any).document = dom.window.document;
(globalThis as any).navigator = dom.window.navigator;
(globalThis as any).HTMLElement = dom.window.HTMLElement;
(globalThis as any).Node = dom.window.Node;
(globalThis as any).customElements = dom.window.customElements;
(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

const React = await import("react");
const { act } = React;
const { createRoot } = await import("react-dom/client");
const { api } = await import("../src/lib/api");
const { useDiaryEntries } = await import("../src/features/diary/hooks/useDiaryEntries");
const {
  queueOrphanUploadNotice,
  drainAndShowOrphanUploadNotice,
  __resetOrphanUploadNoticesForTest,
} = await import("../src/features/diary/lib/orphanUploadNotices");

afterEach(() => {
  __resetOrphanUploadNoticesForTest();
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

async function mount(element: any): Promise<{ unmount: () => void }> {
  const container = dom.window.document.getElementById("root") as HTMLElement;
  const root = createRoot(container);
  await act(async () => {
    root.render(element);
  });
  return { unmount: () => root.unmount() };
}

// ---------------------------------------------------------------------------
// FINDING 1 (P2, round 7 — regression from round 6, commit 544fc43a): the
// orphan-upload notice queued by round 6's fix was cleared the instant the
// reload it triggers resolves, because `useDiaryEntries.reload`'s success
// path unconditionally does `setError(null)`. This drives the REAL
// `useDiaryEntries` hook (real `reload`, real `setError`) through
// `drainAndShowOrphanUploadNotice` — the actual function DiaryWorkspace.tsx
// now calls from its drain effect — so the assertion below can only pass if
// the notice genuinely outlives the reload it triggers.
// ---------------------------------------------------------------------------
describe("PR #139 round 7 (P2): orphan-upload notice must survive the reload it triggers", () => {
  test("the notice is still present after the triggered reload resolves", async () => {
    const originalList = api.diary.list;
    const originalFolders = api.diary.folders.list;
    // Resolve the initial mount-triggered reload (useDiaryEntries's own
    // useEffect) immediately, so it isn't what we're observing below.
    (api.diary as any).list = async () => [];
    (api.diary.folders as any).list = async () => [];

    let hookResult: ReturnType<typeof useDiaryEntries> | null = null;
    function Harness() {
      hookResult = useDiaryEntries(1);
      return null;
    }

    const handle = await mount(<Harness />);
    try {
      // Flush the initial reload's promise chain fully (microtasks alone
      // aren't enough to guarantee an async function's `await` plus its
      // `finally` have both settled; a macrotask tick is).
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 0));
      });
      expect(hookResult!.error).toBeNull();

      // Simulate an orphaned inline upload resolving while nothing is
      // subscribed, then the (re)mounted workspace draining it — this is
      // the exact call DiaryWorkspace.tsx's drain effect makes.
      queueOrphanUploadNotice({ entryId: 1, attachmentId: 42 });

      const secondList = deferred<any[]>();
      (api.diary as any).list = () => secondList.promise;

      await act(async () => {
        drainAndShowOrphanUploadNotice(hookResult!.setError, hookResult!.reload);
        // Give the drain's own reload call a chance to start (and hit the
        // still-pending `secondList.promise`) before we assert.
        await Promise.resolve();
      });

      // The reload triggered by the drain is deliberately still pending at
      // this point (that's the whole fix: `setError(message)` happens only
      // once `reload` settles, so it wins the race against `reload`'s own
      // `setError(null)`) — so the notice is not up yet.
      expect(hookResult!.error).toBeNull();

      // Now let that reload resolve.
      await act(async () => {
        secondList.resolve([]);
        await new Promise((resolve) => setTimeout(resolve, 0));
      });

      // FIXED: once the reload settles, the notice is the one that's up —
      // `reload`'s own `setError(null)` did not get the last word.
      expect(hookResult!.error).toContain("saved as an attachment");
    } finally {
      await act(async () => {
        handle.unmount();
      });
      (api.diary as any).list = originalList;
      (api.diary.folders as any).list = originalFolders;
    }
  });
});
