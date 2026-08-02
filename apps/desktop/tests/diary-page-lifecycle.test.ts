import { describe, expect, test } from "bun:test";
import { isDiscardablePage } from "../src/features/diary/lib/pageLifecycle";

const base = { title: null, bodyPlainText: "", attachmentCount: 0, coverAttachmentId: null };

describe("untitled page cleanup", () => {
  test("discards a page with no title, body, attachments or cover", () => {
    expect(isDiscardablePage(base)).toBe(true);
  });

  test("treats whitespace-only content as empty", () => {
    expect(isDiscardablePage({ ...base, title: "   ", bodyPlainText: "\n \t" })).toBe(true);
  });

  test("keeps a page with a title", () => {
    expect(isDiscardablePage({ ...base, title: "Monday" })).toBe(false);
  });

  test("keeps a page with body text", () => {
    expect(isDiscardablePage({ ...base, bodyPlainText: "hello" })).toBe(false);
  });

  test("keeps a page with an attachment", () => {
    expect(isDiscardablePage({ ...base, attachmentCount: 1 })).toBe(false);
  });

  test("keeps a page with a cover", () => {
    expect(isDiscardablePage({ ...base, coverAttachmentId: 4 })).toBe(false);
  });
});
