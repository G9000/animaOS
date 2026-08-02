import { describe, expect, test } from "bun:test";

import { collectLegacyDiaryDrafts } from "../src/pages/journal/draft-migration";

describe("legacy diary draft staging", () => {
  test("collects the old schema without deleting plaintext recovery state", () => {
    const values = new Map([
      [
        "anima:diary:draft:7:edit-42",
        JSON.stringify({
          title: "Unsaved",
          html: "<p>private</p>",
          mood: "calm",
          entryDate: "2026-08-02",
        }),
      ],
    ]);
    const storage = {
      get length() { return values.size; },
      key(index: number) { return [...values.keys()][index] ?? null; },
      getItem(key: string) { return values.get(key) ?? null; },
    };

    expect(collectLegacyDiaryDrafts(storage, 7, () => new Date("2026-08-02T00:00:00Z")))
      .toEqual([
        {
          storageKey: "anima:diary:draft:7:edit-42",
          targetEntryId: 42,
          title: "Unsaved",
          html: "<p>private</p>",
          mood: "calm",
          entryDate: "2026-08-02",
          updatedAt: "2026-08-02T00:00:00.000Z",
        },
      ]);
    expect(values.has("anima:diary:draft:7:edit-42")).toBe(true);
  });
});
