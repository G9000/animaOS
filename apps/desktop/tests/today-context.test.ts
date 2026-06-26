import { describe, expect, test } from "bun:test";

import {
  loadTodayContext,
  normalizeTodayContext,
  saveTodayContext,
  suggestTodayContextFromMessage,
  todayIso,
  type TodayContextStorage,
} from "../src/lib/today-context";

function makeStorage(): TodayContextStorage & { data: Map<string, string> } {
  const data = new Map<string, string>();
  return {
    data,
    getItem: (key: string) => data.get(key) ?? null,
    setItem: (key: string, value: string) => {
      data.set(key, value);
    },
    removeItem: (key: string) => {
      data.delete(key);
    },
  };
}

describe("today context helpers", () => {
  test("normalizes trimmed mood energy and note for a date", () => {
    expect(
      normalizeTodayContext(
        {
          mood: " tired ",
          energy: " low ",
          note: " keep replies direct ",
        },
        "2026-05-30",
      ),
    ).toEqual({
      date: "2026-05-30",
      mood: "tired",
      energy: "low",
      note: "keep replies direct",
    });
  });

  test("drops empty context drafts", () => {
    expect(
      normalizeTodayContext({ mood: " ", energy: "", note: undefined }, "2026-05-30"),
    ).toBeNull();
  });

  test("loads only context for the current local date", () => {
    const storage = makeStorage();
    saveTodayContext(
      {
        date: "2026-05-29",
        mood: "tired",
      },
      storage,
    );

    expect(loadTodayContext(storage, "2026-05-30")).toBeNull();
    expect(storage.data.size).toBe(0);
  });

  test("clears saved context", () => {
    const storage = makeStorage();
    saveTodayContext({ date: "2026-05-30", energy: "high" }, storage);
    saveTodayContext(null, storage);

    expect(loadTodayContext(storage, "2026-05-30")).toBeNull();
  });

  test("formats dates using local calendar fields", () => {
    expect(todayIso(new Date(2026, 4, 30, 23, 30))).toBe("2026-05-30");
  });

  test("suggests today context from explicit current-state wording", () => {
    expect(
      suggestTodayContextFromMessage(
        "I'm exhausted today, keep replies direct.",
        "2026-05-30",
      ),
    ).toEqual({
      date: "2026-05-30",
      mood: "exhausted",
      energy: "low",
      note: "keep replies direct",
    });
  });

  test("does not suggest today context from ordinary chat", () => {
    expect(
      suggestTodayContextFromMessage(
        "what should we build next?",
        "2026-05-30",
      ),
    ).toBeNull();
  });
});
