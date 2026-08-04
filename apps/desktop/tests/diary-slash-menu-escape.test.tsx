import { beforeEach, describe, expect, test } from "bun:test";
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

// ---------------------------------------------------------------------------
// FINDING (P2, round 7 — regression from round 6): round 6 tore down
// `root`/`container` on Escape but never cleared `current`, so `onKeyDown`
// kept consuming ArrowUp/ArrowDown/Enter for the rest of the trigger
// session even though the popup was already invisible — worst of all,
// Enter still silently invoked the last-selected command instead of
// inserting a newline. This is worse than the pre-round-6 bug (Escape
// doing nothing visibly) because the menu now LOOKS closed while still
// BEHAVING open.
// ---------------------------------------------------------------------------
describe("PR #139 round 7 (P2): onKeyDown must stop handling keys after Escape dismisses the menu", () => {
  // Earlier tests in this file (and this describe block's own prior runs)
  // intentionally leave stray `.diary-slash-menu` nodes in the shared jsdom
  // `document.body` — nothing in this file unmounts between tests. That's
  // harmless for them (they only check their own renderer's return values),
  // but `popupNode()`'s `document.querySelector` would otherwise match a
  // leftover node from an earlier test instead of this test's own, so start
  // each test here from a clean DOM.
  beforeEach(() => {
    dom.window.document.querySelectorAll(".diary-slash-menu").forEach((node) => node.remove());
  });

  test("after Escape, ArrowDown and Enter return false (editor handles them) and Enter does not invoke a command", async () => {
    const renderer = createSlashRenderer();
    let commandInvoked: any = null;

    await act(async () => {
      renderer.onStart(
        fakeSuggestionProps({
          command: (item: any) => {
            commandInvoked = item;
          },
        }),
      );
    });

    await act(async () => {
      renderer.onKeyDown({ event: { key: "Escape" } as KeyboardEvent } as any);
    });
    expect(popupNode()).toBeNull();

    let arrowHandled = true;
    await act(async () => {
      arrowHandled = renderer.onKeyDown({ event: { key: "ArrowDown" } as KeyboardEvent } as any);
    });
    // FIXED: false means "not handled" — the editor gets the arrow key
    // normally (e.g. to move the cursor), instead of the popup silently
    // eating it while invisible.
    expect(arrowHandled).toBe(false);

    let enterHandled = true;
    await act(async () => {
      enterHandled = renderer.onKeyDown({ event: { key: "Enter" } as KeyboardEvent } as any);
    });
    // FIXED: false means the editor inserts a newline as normal.
    expect(enterHandled).toBe(false);
    // FIXED: the actual user-facing harm — no command was silently
    // invoked on behalf of an invisible, dismissed menu.
    expect(commandInvoked).toBeNull();
  });

  test("a fresh onStart after a dismissal resets full key handling (ArrowDown/Enter go back to being handled)", async () => {
    const renderer = createSlashRenderer();
    let commandInvoked: any = null;

    await act(async () => {
      renderer.onStart(fakeSuggestionProps());
    });
    await act(async () => {
      renderer.onKeyDown({ event: { key: "Escape" } as KeyboardEvent } as any);
    });
    await act(async () => {
      renderer.onExit();
    });

    // A brand-new trigger session ("/" typed again).
    await act(async () => {
      renderer.onStart(
        fakeSuggestionProps({
          command: (item: any) => {
            commandInvoked = item;
          },
        }),
      );
    });
    expect(popupNode()).not.toBeNull();

    let arrowHandled = false;
    await act(async () => {
      arrowHandled = renderer.onKeyDown({ event: { key: "ArrowDown" } as KeyboardEvent } as any);
    });
    expect(arrowHandled).toBe(true);

    let enterHandled = false;
    await act(async () => {
      enterHandled = renderer.onKeyDown({ event: { key: "Enter" } as KeyboardEvent } as any);
    });
    expect(enterHandled).toBe(true);
    expect(commandInvoked).toEqual(SLASH_COMMANDS[1]);
  });
});
