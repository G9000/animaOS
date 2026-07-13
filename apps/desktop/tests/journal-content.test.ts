import { describe, expect, test } from "bun:test";

import { canSaveDiaryEntry, resolveDiaryBody } from "../src/pages/journal/content";

describe("diary editor content", () => {
  test("preserves inline-image-only editor HTML", () => {
    const html = '<p><img src="data:image/png;base64,AAAA" alt="memory"></p>';

    expect(
      resolveDiaryBody({
        editorIsEmpty: false,
        editorHtml: html,
        plainText: "",
      }),
    ).toBe(html);
  });

  test("treats an empty editor document as no diary body", () => {
    expect(
      resolveDiaryBody({
        editorIsEmpty: true,
        editorHtml: "<p></p>",
        plainText: "",
      }),
    ).toBeNull();
  });

  test("allows a cover-image-only diary entry to be saved", () => {
    expect(
      canSaveDiaryEntry({
        editorHasContent: false,
        plainText: "",
        attachmentCount: 0,
        hasPendingCover: true,
      }),
    ).toBe(true);
  });
});
