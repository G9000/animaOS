import { describe, expect, test } from "bun:test";
import { JSDOM } from "jsdom";

// Real jsdom (not renderToStaticMarkup) so a real @tiptap/core Editor can
// run real commands against a real ProseMirror doc — the hazard (PR #139
// round 8, Finding 3) is about what actually happens to the document and
// editor state, which only a live Editor instance can prove one way or
// the other.
const dom = new JSDOM("<!doctype html><html><body></body></html>");
(globalThis as any).window = dom.window;
(globalThis as any).document = dom.window.document;
(globalThis as any).navigator = dom.window.navigator;
(globalThis as any).DOMParser = dom.window.DOMParser;
(globalThis as any).Node = dom.window.Node;
(globalThis as any).HTMLElement = dom.window.HTMLElement;
// @tiptap/core's focus() command schedules a delayed DOM focus via
// requestAnimationFrame, which jsdom doesn't provide — stub it so the
// commands under test (which call .focus() as part of their chain, same
// as production) don't throw in this headless environment.
(globalThis as any).requestAnimationFrame = (cb: FrameRequestCallback) => setTimeout(() => cb(0), 0);

const { Editor } = await import("@tiptap/core");
const { createDiaryExtensions } = await import("../src/features/diary/editor/extensions");
const { performTurnInto } = await import("../src/features/diary/editor/BlockDragHandle");
const { SLASH_COMMANDS } = await import("../src/features/diary/editor/slashCommands");

const extensions = createDiaryExtensions();

function findFirst(
  editor: InstanceType<typeof Editor>,
  predicate: (node: any) => boolean,
): { node: any; pos: number } | null {
  let found: { node: any; pos: number } | null = null;
  editor.state.doc.descendants((node: any, pos: number) => {
    if (found) return false;
    if (predicate(node)) {
      found = { node, pos };
      return false;
    }
    return true;
  });
  return found;
}

describe("performTurnInto over an atom (PR #139 round 8, Finding 3)", () => {
  test("turning a hovered horizontal rule into a heading touches neither the rule nor the paragraph after it", () => {
    const editor = new Editor({
      extensions,
      content: "<p>Before</p><hr><p>After</p>",
    });

    try {
      // Park the selection somewhere identifiable and unrelated to the
      // hovered node before calling performTurnInto, so any relocation of
      // it is directly attributable to this call.
      editor.commands.setTextSelection(2);
      const selectionBefore = { from: editor.state.selection.from, to: editor.state.selection.to };

      const hovered = findFirst(editor, (node) => node.type.name === "horizontalRule");
      expect(hovered).not.toBeNull();

      const h1Command = SLASH_COMMANDS.find((c) => c.id === "h1")!;
      performTurnInto(editor, hovered!, h1Command, () => true);

      // The document itself: no heading anywhere, "After" still a plain
      // paragraph, the divider still present — this is Finding 3's
      // headline claim, asserted directly on the resulting document.
      const json = editor.getJSON();
      const topLevelTypes = (json.content ?? []).map((node: any) => node.type);
      expect(topLevelTypes).toEqual(["paragraph", "horizontalRule", "paragraph"]);
      const afterNode = (json.content ?? [])[2] as any;
      expect(afterNode.type).toBe("paragraph");
      expect(afterNode.content?.[0]?.text).toBe("After");
      const beforeNode = (json.content ?? [])[0] as any;
      expect(beforeNode.content?.[0]?.text).toBe("Before");

      // BEFORE the fix: `from = pos + 1` for an atom (nodeSize 1) is not a
      // position INSIDE it — it's the position immediately after it, i.e.
      // the boundary of whatever follows. `performTurnInto` unconditionally
      // ran `setTextSelection({ from, to: from })`, which actually
      // relocated the editor's live selection onto the START of the
      // "After" paragraph — the exact hazard the finding describes, and
      // the mechanism a slightly different command/schema combination
      // would ride to actually convert that neighbor (this schema's
      // `setNode`/`clearNodes` fallback happens to bail out safely on the
      // resulting degenerate selection, but the selection itself already
      // left the block the user was pointing at and landed on the next
      // one instead — never touching "Before", never staying where it
      // was).
      //
      // AFTER the fix: the atom guard returns before ever calling
      // `editor.chain()`, so the selection is left completely alone.
      expect(editor.state.selection.from).toBe(selectionBefore.from);
      expect(editor.state.selection.to).toBe(selectionBefore.to);
    } finally {
      editor.destroy();
    }
  });

  test("turning a hovered horizontal rule into a bullet list also leaves the document and selection untouched", () => {
    // Same hazard, a list command (wrapInList/toggleList) instead of
    // setNode — guards against a fix that only special-cased setNode-style
    // commands.
    const editor = new Editor({
      extensions,
      content: "<p>Before</p><hr><p>After</p>",
    });

    try {
      editor.commands.setTextSelection(2);
      const selectionBefore = { from: editor.state.selection.from, to: editor.state.selection.to };

      const hovered = findFirst(editor, (node) => node.type.name === "horizontalRule");
      const bulletCommand = SLASH_COMMANDS.find((c) => c.id === "bullet")!;
      performTurnInto(editor, hovered!, bulletCommand, () => true);

      const json = editor.getJSON();
      const topLevelTypes = (json.content ?? []).map((node: any) => node.type);
      expect(topLevelTypes).toEqual(["paragraph", "horizontalRule", "paragraph"]);
      expect(editor.state.selection.from).toBe(selectionBefore.from);
      expect(editor.state.selection.to).toBe(selectionBefore.to);
    } finally {
      editor.destroy();
    }
  });

  test("turning a hovered diaryImage (attachment-backed inline image) into a heading is refused the same way", () => {
    // The finding names "an inline image or a horizontal rule" explicitly
    // — diaryImage (editor/nodes/AttachmentImage.tsx) is this diary's real
    // atom-image node and shares horizontalRule's exact nodeSize-1 shape.
    const editor = new Editor({
      extensions,
      content: '<p>Before</p><img data-attachment-id="7" alt="pic"><p>After</p>',
    });

    try {
      editor.commands.setTextSelection(2);
      const selectionBefore = { from: editor.state.selection.from, to: editor.state.selection.to };

      const hovered = findFirst(editor, (node) => node.type.name === "diaryImage");
      expect(hovered).not.toBeNull();

      const h1Command = SLASH_COMMANDS.find((c) => c.id === "h1")!;
      performTurnInto(editor, hovered!, h1Command, () => true);

      const json = editor.getJSON();
      const topLevelTypes = (json.content ?? []).map((node: any) => node.type);
      expect(topLevelTypes).toEqual(["paragraph", "diaryImage", "paragraph"]);
      const afterNode = (json.content ?? [])[2] as any;
      expect(afterNode.type).toBe("paragraph");
      expect(afterNode.content?.[0]?.text).toBe("After");
      expect(editor.state.selection.from).toBe(selectionBefore.from);
      expect(editor.state.selection.to).toBe(selectionBefore.to);
    } finally {
      editor.destroy();
    }
  });
});
