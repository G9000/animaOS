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
