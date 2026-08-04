import { describe, expect, test } from "bun:test";
import { JSDOM } from "jsdom";
import type { WindowLike } from "dompurify";

import { createDiaryHtmlSanitizer } from "../src/features/diary/lib/sanitize";

const sanitizeDiaryHtml = createDiaryHtmlSanitizer(
  new JSDOM("").window as unknown as WindowLike,
);

describe("diary HTML sanitizer", () => {
  test("removes executable HTML from imported or API-provided diary bodies", () => {
    const clean = sanitizeDiaryHtml(
      '<p onclick="alert(1)">safe</p><script>alert(2)</script><img src="x" onerror="alert(3)"><a href="javascript:alert(4)">link</a>',
    );

    expect(clean).toContain("<p>safe</p>");
    expect(clean).toContain("<img src=\"x\">");
    expect(clean).toContain("<a>link</a>");
    expect(clean).not.toContain("<script");
    expect(clean).not.toContain("onclick");
    expect(clean).not.toContain("onerror");
    expect(clean).not.toContain("javascript:");
  });

  test("preserves supported Tiptap diary markup and inline images", () => {
    const clean = sanitizeDiaryHtml(
      '<h2>Title</h2><blockquote><p><strong>Private</strong> <em>note</em></p></blockquote><ul><li>memory</li></ul><img class="rounded-lg" src="data:image/png;base64,AAAA" alt="memory">',
    );

    expect(clean).toContain("<h2>Title</h2>");
    expect(clean).toContain("<blockquote><p><strong>Private</strong> <em>note</em></p></blockquote>");
    expect(clean).toContain("<ul><li>memory</li></ul>");
    expect(clean).toContain('class="rounded-lg"');
    expect(clean).toContain('src="data:image/png;base64,AAAA"');
    expect(clean).toContain('alt="memory"');
  });
});

describe("diary HTML sanitizer — modern block types", () => {
  test("preserves task lists, tables, toggles and highlight tones", () => {
    const clean = sanitizeDiaryHtml(
      '<ul data-type="taskList"><li data-checked="true" data-type="taskItem"><p>done</p></li></ul>' +
        '<table><colgroup><col></colgroup><tbody><tr><td colspan="2" rowspan="1"><p>a</p></td></tr></tbody></table>' +
        '<details><summary>more</summary><div data-type="detailsContent"><p>hidden</p></div></details>' +
        '<p><mark data-tone="amber">warm</mark></p>' +
        '<p><img data-attachment-id="7" alt="shot"></p>',
    );

    expect(clean).toContain('data-type="taskList"');
    expect(clean).toContain('data-checked="true"');
    expect(clean).toContain('colspan="2"');
    expect(clean).toContain("<details>");
    expect(clean).toContain('data-tone="amber"');
    expect(clean).toContain('data-attachment-id="7"');
  });

  test("never allows style or input through", () => {
    const clean = sanitizeDiaryHtml(
      '<table style="min-width: 50px;"><colgroup><col style="min-width: 25px;"></colgroup>' +
        '<tbody><tr><td><p>a</p></td></tr></tbody></table>' +
        '<ul data-type="taskList"><li data-checked="true"><label><input type="checkbox" checked></label><div><p>x</p></div></li></ul>' +
        '<p style="color: red">red</p>',
    );

    expect(clean).not.toContain("style=");
    expect(clean).not.toContain("<input");
    expect(clean).toContain('data-checked="true"');
  });

  test("strips data attributes that are not explicitly allowlisted", () => {
    const clean = sanitizeDiaryHtml('<p data-evil="1" data-type="callout">x</p>');

    expect(clean).not.toContain("data-evil");
    expect(clean).toContain('data-type="callout"');
  });
});
