import { describe, expect, test } from "bun:test";
import { JSDOM } from "jsdom";

// Same real-jsdom-plus-react-dom/client pattern as
// diary-attachment-image-upload-teardown.test.tsx: createSlashRenderer
// mounts a real React root into a real `document.body` node, and this
// file's fix (Escape actually tearing that node down) can only be observed
// against a real DOM, not a hand-rolled stub.
const dom = new JSDOM('<!doctype html><html><body></body></html>', {
  url: "http://localhost/",
});
(globalThis as any).window = dom.window;
(globalThis as any).document = dom.window.document;
(globalThis as any).navigator = dom.window.navigator;
(globalThis as any).HTMLElement = dom.window.HTMLElement;
(globalThis as any).Node = dom.window.Node;
(globalThis as any).Text = dom.window.Text;
(globalThis as any).DocumentFragment = dom.window.DocumentFragment;
(globalThis as any).customElements = dom.window.customElements;
(globalThis as any).getSelection = () => dom.window.getSelection();
(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

const React = await import("react");
const { act } = React;

const { createSlashRenderer } = await import("../src/features/diary/editor/SlashMenu");
const { SLASH_COMMANDS } = await import("../src/features/diary/editor/slashCommands");

function fakeSuggestionProps(overrides: Partial<any> = {}) {
  return {
    items: SLASH_COMMANDS,
    command: () => {},
    clientRect: () => ({ top: 0, bottom: 20, left: 0, right: 100 } as DOMRect),
    ...overrides,
  };
}

function popupNode(): Element | null {
  return dom.window.document.querySelector(".diary-slash-menu");
}

describe("PR #139 round 6 (P2): Escape must actually dismiss the slash menu", () => {
  test("Escape removes the popup from the DOM (visually disappears), returns true, and does not resurrect it on the next keystroke of the same trigger", async () => {
    const renderer = createSlashRenderer();

    await act(async () => {
      renderer.onStart(fakeSuggestionProps());
    });
    expect(popupNode()).not.toBeNull();

    let handled = false;
    await act(async () => {
      handled = renderer.onKeyDown({
        event: { key: "Escape" } as KeyboardEvent,
      } as any);
    });

    // Escape still reports "handled" so the editor doesn't also act on it.
    expect(handled).toBe(true);
    // FIXED: the popup is actually gone, not just still there with a
    // return value of `true` and no visible effect.
    expect(popupNode()).toBeNull();

    // Still inside the same "/query" — Tiptap's suggestion plugin keeps
    // calling onUpdate on every subsequent keystroke as long as the query
    // still matches. The dismissed menu must not reappear.
    await act(async () => {
      renderer.onUpdate(fakeSuggestionProps());
    });
    expect(popupNode()).toBeNull();

    await act(async () => {
      renderer.onUpdate(fakeSuggestionProps());
    });
    expect(popupNode()).toBeNull();
  });

  test("a fresh trigger (a new '/') reopens the menu normally after a previous dismissal", async () => {
    const renderer = createSlashRenderer();

    await act(async () => {
      renderer.onStart(fakeSuggestionProps());
    });
    await act(async () => {
      renderer.onKeyDown({ event: { key: "Escape" } as KeyboardEvent } as any);
    });
    expect(popupNode()).toBeNull();

    // The suggestion plugin tears the session down (query no longer
    // matches — e.g. the user backspaced past the `/`) ...
    await act(async () => {
      renderer.onExit();
    });
    // ... and then starts a brand-new trigger session.
    await act(async () => {
      renderer.onStart(fakeSuggestionProps());
    });

    expect(popupNode()).not.toBeNull();
  });

  test("ArrowDown/ArrowUp/Enter are unaffected by the Escape fix", async () => {
    const renderer = createSlashRenderer();
    let selected: any = null;

    await act(async () => {
      renderer.onStart(
        fakeSuggestionProps({
          command: (item: any) => {
            selected = item;
          },
        }),
      );
    });

    let handled = false;
    await act(async () => {
      handled = renderer.onKeyDown({ event: { key: "ArrowDown" } as KeyboardEvent } as any);
    });
    expect(handled).toBe(true);
    expect(popupNode()).not.toBeNull();

    await act(async () => {
      handled = renderer.onKeyDown({ event: { key: "Enter" } as KeyboardEvent } as any);
    });
    expect(handled).toBe(true);
    expect(selected).toEqual(SLASH_COMMANDS[1]);
  });
});
