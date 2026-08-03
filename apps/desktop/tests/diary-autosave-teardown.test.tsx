import { describe, expect, test } from "bun:test";
import { JSDOM } from "jsdom";

// Same real jsdom + react-dom/client mount pattern as
// diary-attachment-blob-url.test.tsx — this bug (PR #139, Finding 1) only
// reproduces once a real effect cleanup actually runs on entry switch /
// unmount, which renderToStaticMarkup never exercises.
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
const { useAutosave } = await import("../src/features/diary/hooks/useAutosave");

const tick = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

interface Api {
  schedule: (payload: string) => void;
}

function Harness({
  entryId,
  save,
  api,
  onUnsavedOnTeardown,
}: {
  entryId: number | null;
  save: (payload: string) => Promise<void>;
  api: { current: Api | null };
  onUnsavedOnTeardown?: () => void;
}) {
  const { schedule } = useAutosave<string>({ entryId, save, delayMs: 5, onUnsavedOnTeardown });
  api.current = { schedule };
  return null;
}

describe("useAutosave teardown (PR #139, Finding 1)", () => {
  test("a failed autosave is retried on teardown instead of being silently discarded", async () => {
    let callCount = 0;
    const payloadsSeen: string[] = [];
    const save = async (payload: string) => {
      callCount += 1;
      payloadsSeen.push(payload);
      if (callCount === 1) {
        throw new Error("network down");
      }
      // Every retry after the first attempt succeeds.
    };

    const container = dom.window.document.getElementById("root") as HTMLElement;
    const root = createRoot(container);
    const api: { current: Api | null } = { current: null };

    try {
      await act(async () => {
        root.render(<Harness entryId={1} save={save} api={api} />);
      });

      api.current?.schedule("the user's last edit");
      // Let the debounce fire and the save fail.
      await act(async () => {
        await tick(40);
      });
      expect(callCount).toBe(1);
      expect(payloadsSeen).toEqual(["the user's last edit"]);

      // Simulate switching to a different entry: entryId changes, which
      // tears down the scheduler for entry 1 via useAutosave's own effect
      // cleanup (the exact path Finding 1 is about — no external
      // "flush the entry being left" call is needed to reproduce it).
      await act(async () => {
        root.render(<Harness entryId={2} save={save} api={api} />);
      });
      // The teardown's retry attempt is async; give it room to complete.
      await act(async () => {
        await tick(40);
      });

      // BEFORE the fix: teardown only calls flush(), which drains
      // `pending` (already null — the failed payload lives in `failed`,
      // not `pending`) and resolves immediately, so save() is never
      // called again and the edit is lost forever.
      //
      // AFTER the fix: teardown also retries the failed payload once, so
      // save() is called a second time with the SAME payload, and it
      // succeeds.
      expect(callCount).toBe(2);
      expect(payloadsSeen).toEqual(["the user's last edit", "the user's last edit"]);
    } finally {
      root.unmount();
    }
  });

  test("surfaces the failure when the teardown retry itself also fails", async () => {
    let callCount = 0;
    const save = async (_: string) => {
      callCount += 1;
      throw new Error("still down");
    };

    const container = dom.window.document.getElementById("root") as HTMLElement;
    const root = createRoot(container);
    const api: { current: Api | null } = { current: null };
    let surfaced = 0;

    try {
      await act(async () => {
        root.render(
          <Harness entryId={1} save={save} api={api} onUnsavedOnTeardown={() => (surfaced += 1)} />,
        );
      });

      api.current?.schedule("doomed edit");
      await act(async () => {
        await tick(40);
      });
      expect(callCount).toBe(1);

      await act(async () => {
        root.render(
          <Harness entryId={2} save={save} api={api} onUnsavedOnTeardown={() => (surfaced += 1)} />,
        );
      });
      await act(async () => {
        await tick(40);
      });

      // One teardown retry attempt (call #2), then give up and surface it
      // — never an unbounded retry loop against a persistently failing
      // server.
      expect(callCount).toBe(2);
      expect(surfaced).toBe(1);
    } finally {
      root.unmount();
    }
  });
});
