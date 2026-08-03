import { describe, expect, test } from "bun:test";
import { JSDOM } from "jsdom";

// Same real jsdom + react-dom/client mount pattern used elsewhere in this
// suite — the mood-commit bug (PR #139, Finding 3) only reproduces with a
// real controlled <input>, a real onChange/onBlur event cycle, and a real
// unmount, none of which renderToStaticMarkup exercises.
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
const { act } = React;
const { createRoot } = await import("react-dom/client");
const { DetailsDrawer } = await import("../src/features/diary/panels/DetailsDrawer");

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

// Sets a controlled <input>'s value the way React needs to see it (bypasses
// the tracked-value guard so the subsequent native "input" event is not
// ignored) and dispatches a real "input" event — the same shape a user's
// keystroke produces, without ever firing "blur".
function typeInto(input: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(dom.window.HTMLInputElement.prototype, "value")!.set!;
  setter.call(input, value);
  input.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
}

describe("DetailsDrawer mood commit (PR #139, Finding 3)", () => {
  test("a typed mood commits without requiring blur", async () => {
    const container = dom.window.document.getElementById("root") as HTMLElement;
    const root = createRoot(container);
    const updates: any[] = [];

    try {
      await act(async () => {
        root.render(
          <DetailsDrawer
            key={1}
            entry={makeEntry({ mood: null })}
            folders={[]}
            open={true}
            onClose={() => {}}
            onUpdate={(data) => updates.push(data)}
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
          />,
        );
      });

      const input = container.querySelector(
        'input[placeholder="How are you feeling?"]',
      ) as HTMLInputElement;
      expect(input).toBeTruthy();

      await act(async () => {
        typeInto(input, "hopeful");
      });

      // BEFORE the fix: commitMood only ran from the input's onBlur handler
      // — typing alone (no blur, no unmount) never called onUpdate, so a
      // typed mood was silently dropped if the user navigated away by any
      // means other than blurring this exact field.
      //
      // AFTER the fix: a short debounce commits it on its own.
      expect(updates.length).toBe(0); // not yet — still within the debounce window
      await act(async () => {
        await tick(700);
      });

      expect(updates.length).toBe(1);
      expect(updates[0]).toEqual({ mood: "hopeful", clearMood: false });
    } finally {
      root.unmount();
    }
  });

  test("a typed mood commits on unmount even before the debounce fires", async () => {
    const container = dom.window.document.getElementById("root") as HTMLElement;
    const root = createRoot(container);
    const updates: any[] = [];

    try {
      await act(async () => {
        root.render(
          <DetailsDrawer
            key={1}
            entry={makeEntry({ mood: null })}
            folders={[]}
            open={true}
            onClose={() => {}}
            onUpdate={(data) => updates.push(data)}
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
          />,
        );
      });

      const input = container.querySelector(
        'input[placeholder="How are you feeling?"]',
      ) as HTMLInputElement;

      await act(async () => {
        typeInto(input, "relieved");
      });
      expect(updates.length).toBe(0);

      // Unmount immediately (e.g. the entry-switch/keying fix elsewhere in
      // this PR tears this instance down) — well before the debounce timer
      // would have fired on its own, and with no blur event ever having
      // occurred.
      act(() => {
        root.unmount();
      });

      expect(updates.length).toBe(1);
      expect(updates[0]).toEqual({ mood: "relieved", clearMood: false });
    } catch (e) {
      root.unmount();
      throw e;
    }
  });
});
