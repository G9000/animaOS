import { describe, expect, test } from "bun:test";
import { JSDOM } from "jsdom";
import type { WindowLike } from "dompurify";

import { createDiaryHtmlSanitizer } from "../src/pages/journal/html";
import sanitizerContract from "../../server/src/anima_server/services/corefs/writing-sanitizer-v1.json";

const sanitizeDiaryHtml = createDiaryHtmlSanitizer(
  new JSDOM("").window as unknown as WindowLike,
);

describe("diary HTML sanitizer", () => {
  test("matches the shared versioned golden contract", () => {
    for (const golden of sanitizerContract.goldens) {
      expect(sanitizeDiaryHtml(golden.input)).toBe(golden.output);
    }
  });

  test("matches the shared data URI preview policy", () => {
    for (const golden of sanitizerContract.dataGoldens) {
      expect(sanitizeDiaryHtml(golden.input), golden.name).toBe(golden.desktopOutput);
    }
  });
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
