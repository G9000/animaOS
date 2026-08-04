import { describe, expect, test } from "bun:test";
import { filterDiaryEntries } from "../src/features/diary/lib/entryFilter";
import type { DiaryEntryData } from "@anima/api-client";

function makeEntry(overrides: Partial<DiaryEntryData> & { id: number }): DiaryEntryData {
  return {
    userId: 1,
    entryDate: "2026-01-01",
    title: null,
    body: "",
    mood: null,
    source: "app",
    coverAttachmentId: null,
    folderId: null,
    attachments: [],
    createdAt: null,
    updatedAt: null,
    ...overrides,
  };
}

const baseCriteria = { query: "", activeFolderId: null, moodFilter: "", dateFrom: "", dateTo: "" };

describe("filterDiaryEntries", () => {
  test("returns every entry when no criteria are active", () => {
    const entries = [makeEntry({ id: 1 }), makeEntry({ id: 2 })];
    expect(filterDiaryEntries(entries, baseCriteria)).toHaveLength(2);
  });

  test("filters by active folder", () => {
    const entries = [
      makeEntry({ id: 1, folderId: 10 }),
      makeEntry({ id: 2, folderId: 20 }),
    ];
    const result = filterDiaryEntries(entries, { ...baseCriteria, activeFolderId: 10 });
    expect(result.map((e) => e.id)).toEqual([1]);
  });

  test("filters by exact mood", () => {
    const entries = [makeEntry({ id: 1, mood: "calm" }), makeEntry({ id: 2, mood: "anxious" })];
    const result = filterDiaryEntries(entries, { ...baseCriteria, moodFilter: "calm" });
    expect(result.map((e) => e.id)).toEqual([1]);
  });

  test("filters by inclusive date range", () => {
    const entries = [
      makeEntry({ id: 1, entryDate: "2026-01-05" }),
      makeEntry({ id: 2, entryDate: "2026-01-15" }),
      makeEntry({ id: 3, entryDate: "2026-01-25" }),
    ];
    const result = filterDiaryEntries(entries, {
      ...baseCriteria,
      dateFrom: "2026-01-10",
      dateTo: "2026-01-20",
    });
    expect(result.map((e) => e.id)).toEqual([2]);
  });

  test("matches search query against title, body text, and mood, case-insensitively", () => {
    const entries = [
      makeEntry({ id: 1, title: "Morning Pages" }),
      makeEntry({ id: 2, body: "<p>A quiet walk by the river</p>" }),
      makeEntry({ id: 3, mood: "Grateful" }),
      makeEntry({ id: 4, title: "Unrelated" }),
    ];
    expect(filterDiaryEntries(entries, { ...baseCriteria, query: "morning" }).map((e) => e.id)).toEqual([1]);
    expect(filterDiaryEntries(entries, { ...baseCriteria, query: "RIVER" }).map((e) => e.id)).toEqual([2]);
    expect(filterDiaryEntries(entries, { ...baseCriteria, query: "grateful" }).map((e) => e.id)).toEqual([3]);
  });

  test("strips HTML tags before matching the search query against body text", () => {
    const entries = [makeEntry({ id: 1, body: "<p>Nested <strong>bold</strong> text</p>" })];
    expect(filterDiaryEntries(entries, { ...baseCriteria, query: "bold text" }).map((e) => e.id)).toEqual([1]);
  });

  test("combines folder, mood, date range, and search as a logical AND", () => {
    const entries = [
      makeEntry({ id: 1, folderId: 1, mood: "calm", entryDate: "2026-01-10", title: "Beach day" }),
      makeEntry({ id: 2, folderId: 1, mood: "calm", entryDate: "2026-01-10", title: "Office day" }),
      makeEntry({ id: 3, folderId: 2, mood: "calm", entryDate: "2026-01-10", title: "Beach day" }),
    ];
    const result = filterDiaryEntries(entries, {
      query: "beach",
      activeFolderId: 1,
      moodFilter: "calm",
      dateFrom: "2026-01-01",
      dateTo: "2026-01-31",
    });
    expect(result.map((e) => e.id)).toEqual([1]);
  });
});
