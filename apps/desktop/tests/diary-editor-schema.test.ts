import { describe, expect, test } from "bun:test";
import { JSDOM } from "jsdom";

const dom = new JSDOM("<!doctype html><html><body></body></html>");
(globalThis as any).window = dom.window;
(globalThis as any).document = dom.window.document;
(globalThis as any).navigator = dom.window.navigator;
(globalThis as any).DOMParser = dom.window.DOMParser;
(globalThis as any).Node = dom.window.Node;
(globalThis as any).HTMLElement = dom.window.HTMLElement;

const { generateHTML, generateJSON } = await import("@tiptap/html");
const { createDiaryExtensions } = await import("../src/features/diary/editor/extensions");

const extensions = createDiaryExtensions();
const roundTrip = (html: string) => generateHTML(generateJSON(html, extensions), extensions);

describe("diary editor schema", () => {
  test("preserves headings, marks and lists", () => {
    const out = roundTrip("<h2>Title</h2><p>hi <strong>there</strong></p><ul><li><p>a</p></li></ul>");
    expect(out).toContain("<h2>Title</h2>");
    expect(out).toContain("<strong>there</strong>");
    expect(out).toContain("<li><p>a</p></li>");
  });

  test("preserves task list checked state", () => {
    const out = roundTrip(
      '<ul data-type="taskList"><li data-checked="true" data-type="taskItem"><p>done</p></li></ul>',
    );
    expect(out).toContain('data-type="taskList"');
    expect(out).toContain('data-checked="true"');
  });

  test("preserves table structure and spans", () => {
    const out = roundTrip(
      '<table><tbody><tr><th colspan="2"><p>h</p></th></tr><tr><td><p>a</p></td><td><p>b</p></td></tr></tbody></table>',
    );
    expect(out).toContain("<table");
    expect(out).toContain('colspan="2"');
  });

  test("preserves details toggles", () => {
    const out = roundTrip(
      '<details><summary>more</summary><div data-type="detailsContent"><p>hidden</p></div></details>',
    );
    expect(out).toContain("<details");
    expect(out).toContain("<summary>more</summary>");
  });

  test("preserves legacy base64 inline images", () => {
    const out = roundTrip('<p><img src="data:image/png;base64,AAAA" alt="memory"></p>');
    expect(out).toContain('src="data:image/png;base64,AAAA"');
    expect(out).toContain('alt="memory"');
  });

  test("preserves highlight tones", () => {
    const out = roundTrip('<p><mark data-tone="amber">warm</mark></p>');
    expect(out).toContain('data-tone="amber"');
  });
});

const { createDiaryHtmlSanitizer } = await import("../src/features/diary/lib/sanitize");
const sanitize = createDiaryHtmlSanitizer(dom.window as any);

describe("diary editor + sanitizer stability", () => {
  const cases: Record<string, string> = {
    taskList: '<ul data-type="taskList"><li data-checked="true" data-type="taskItem"><p>done</p></li></ul>',
    table: "<table><tbody><tr><td><p>a</p></td><td><p>b</p></td></tr></tbody></table>",
    details: '<details><summary>more</summary><div data-type="detailsContent"><p>hidden</p></div></details>',
    heading: "<h2>Title</h2><p>hi <strong>there</strong></p>",
    legacyImage: '<p><img src="data:image/png;base64,AAAA" alt="memory"></p>',
  };

  for (const [name, input] of Object.entries(cases)) {
    test(`${name} is stable across repeated save cycles`, () => {
      const pass1 = sanitize(roundTrip(input));
      const pass2 = sanitize(roundTrip(pass1));
      expect(pass2).toBe(pass1);
    });
  }
});

describe("callout node", () => {
  test("round-trips through the schema and the sanitizer", () => {
    const input = '<div data-type="callout" data-tone="info"><p>heads up</p></div>';
    const out = roundTrip(input);
    expect(out).toContain('data-type="callout"');
    expect(out).toContain('data-tone="info"');

    const pass1 = sanitize(roundTrip(input));
    const pass2 = sanitize(roundTrip(pass1));
    expect(pass2).toBe(pass1);
    expect(pass1).toContain('data-tone="info"');
  });
});

// Task 11 / carried-forward Task 10 review hazard: a page whose only
// content is a pasted image (or an empty table, divider, callout, details,
// task item) must never be deleted by the untitled-page cleanup, even
// though its *text* content strips to "". This exercises the real Tiptap
// schema (via generateJSON, the same technique the round-trip tests above
// use) rather than a hand-written node-name list, so it proves the actual
// node type name Tiptap assigns to an image ("image") is what
// NON_TEXT_NODE_TYPES expects — not just that the pure predicate is
// internally consistent.
const { hasNonTextNode, isDiscardablePage } = await import(
  "../src/features/diary/lib/pageLifecycle"
);

// Mirrors what `editor.state.doc.descendants(...)` collects in
// DiaryWorkspace.tsx's editorHasNonTextContent: every node type name
// anywhere in the document, gathered by walking the same JSONContent tree
// generateJSON produces.
function collectNodeTypeNames(node: { type?: string; content?: unknown[] }): Set<string> {
  const names = new Set<string>();
  const walk = (n: { type?: string; content?: unknown[] }) => {
    if (n.type) names.add(n.type);
    if (Array.isArray(n.content)) {
      for (const child of n.content) walk(child as { type?: string; content?: unknown[] });
    }
  };
  walk(node);
  return names;
}

describe("image-only page is not discardable (Task 10 review hazard)", () => {
  test("an image-only document has empty plain text but a non-text node", () => {
    const doc = generateJSON('<p><img src="data:image/png;base64,AAAA" alt="memory"></p>', extensions);
    const nodeTypeNames = collectNodeTypeNames(doc);

    // The hazard: stripping tags / editor.getText() gives "" for this doc.
    const bodyPlainText = generateHTML(doc, extensions).replace(/<[^>]+>/g, "");
    expect(bodyPlainText.trim()).toBe("");

    // But the schema really does name the node "image", so the structural
    // scan catches what the text strip misses.
    expect(nodeTypeNames.has("image")).toBe(true);
    expect(hasNonTextNode(nodeTypeNames)).toBe(true);
  });

  test("isDiscardablePage keeps an image-only page (hasNonTextContent true overrides empty text)", () => {
    const doc = generateJSON('<p><img src="data:image/png;base64,AAAA" alt="memory"></p>', extensions);
    const nodeTypeNames = collectNodeTypeNames(doc);

    const discardable = isDiscardablePage({
      title: null,
      bodyPlainText: "", // what editor.getText() would report
      attachmentCount: 0, // inline images are not upload attachments
      coverAttachmentId: null,
      hasNonTextContent: hasNonTextNode(nodeTypeNames),
    });

    expect(discardable).toBe(false);
  });

  test("a genuinely empty paragraph-only document IS discardable", () => {
    const doc = generateJSON("<p></p>", extensions);
    const nodeTypeNames = collectNodeTypeNames(doc);

    expect(hasNonTextNode(nodeTypeNames)).toBe(false);
    expect(
      isDiscardablePage({
        title: null,
        bodyPlainText: "",
        attachmentCount: 0,
        coverAttachmentId: null,
        hasNonTextContent: hasNonTextNode(nodeTypeNames),
      }),
    ).toBe(true);
  });

  test("an empty (untyped-into) table is not discardable either", () => {
    const doc = generateJSON(
      "<table><tbody><tr><td><p></p></td><td><p></p></td></tr></tbody></table>",
      extensions,
    );
    const nodeTypeNames = collectNodeTypeNames(doc);

    expect(nodeTypeNames.has("table")).toBe(true);
    expect(
      isDiscardablePage({
        title: null,
        bodyPlainText: "",
        attachmentCount: 0,
        coverAttachmentId: null,
        hasNonTextContent: hasNonTextNode(nodeTypeNames),
      }),
    ).toBe(false);
  });
});
