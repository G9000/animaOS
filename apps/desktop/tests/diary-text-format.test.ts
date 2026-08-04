import { describe, expect, test } from "bun:test";
import {
  countWords,
  entryExcerpt,
  formatFileSize,
  isHtmlBody,
  isPreviewableAttachment,
  moodPillClass,
  plainTextOfBody,
} from "../src/features/diary/lib/textFormat";
import type { DiaryEntryData } from "@anima/api-client";

describe("isHtmlBody / plainTextOfBody", () => {
  test("treats a body starting with a tag as HTML", () => {
    expect(isHtmlBody("<p>hello</p>")).toBe(true);
    expect(isHtmlBody("hello")).toBe(false);
  });

  test("strips tags and decodes entities from an HTML body", () => {
    expect(plainTextOfBody("<p>Tom &amp; Jerry &lt;3</p>")).toBe("Tom & Jerry <3 ");
  });

  test("returns plain (non-HTML) bodies unchanged", () => {
    expect(plainTextOfBody("just text")).toBe("just text");
  });
});

describe("entryExcerpt", () => {
  function makeEntry(body: string): DiaryEntryData {
    return {
      id: 1,
      userId: 1,
      entryDate: "2026-01-01",
      title: null,
      body,
      mood: null,
      source: "app",
      coverAttachmentId: null,
      folderId: null,
      attachments: [],
      createdAt: null,
      updatedAt: null,
    };
  }

  test("collapses whitespace and trims", () => {
    expect(entryExcerpt(makeEntry("<p>Line one</p><p>Line   two</p>"))).toBe("Line one Line two");
  });

  test("truncates long text with an ellipsis at 90 characters", () => {
    const longText = "a".repeat(120);
    const excerpt = entryExcerpt(makeEntry(longText));
    expect(excerpt.endsWith("…")).toBe(true);
    expect(excerpt.length).toBe(91);
  });
});

describe("countWords", () => {
  test("counts whitespace-separated words", () => {
    expect(countWords("hello there friend")).toBe(3);
  });

  test("treats whitespace-only text as zero words", () => {
    expect(countWords("   \n\t ")).toBe(0);
    expect(countWords("")).toBe(0);
  });
});

describe("moodPillClass", () => {
  test("is deterministic for the same mood string", () => {
    expect(moodPillClass("calm")).toBe(moodPillClass("calm"));
  });

  test("returns one of the fixed pill classes", () => {
    const cls = moodPillClass("anything");
    expect(typeof cls).toBe("string");
    expect(cls.length).toBeGreaterThan(0);
  });
});

describe("formatFileSize", () => {
  test("formats bytes, kilobytes, and megabytes", () => {
    expect(formatFileSize(500)).toBe("500 B");
    expect(formatFileSize(2048)).toBe("2.0 KB");
    expect(formatFileSize(5 * 1024 * 1024)).toBe("5.0 MB");
  });
});

describe("isPreviewableAttachment", () => {
  test("image, audio, and video are previewable", () => {
    expect(isPreviewableAttachment("image")).toBe(true);
    expect(isPreviewableAttachment("audio")).toBe(true);
    expect(isPreviewableAttachment("video")).toBe(true);
  });

  test("plain files are not previewable", () => {
    expect(isPreviewableAttachment("file")).toBe(false);
  });
});
