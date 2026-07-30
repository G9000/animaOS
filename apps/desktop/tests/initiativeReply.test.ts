import { describe, expect, test } from "bun:test";

import { initiativeReplyState } from "../src/lib/initiativeReply";

describe("initiativeReplyState (IL-009)", () => {
  test("builds a seeded-thread state carrying the initiative text verbatim", () => {
    const state = initiativeReplyState({
      text: "the gallery opening you were nervous about is tomorrow",
    });
    expect(state.seedThread).toBe(true);
    expect(state.contextMessages).toHaveLength(1);
    expect(state.contextMessages[0]).toEqual({
      role: "assistant",
      content: "the gallery opening you were nervous about is tomorrow",
      source: "initiative",
    });
  });

  test("never paraphrases or trims — the seed is exactly what was shown", () => {
    const text = "  spacing and punctuation preserved!?  ";
    const state = initiativeReplyState({ text });
    expect(state.contextMessages[0].content).toBe(text);
  });

  test("matches the chat page's ChatLocationState seeded-thread contract", () => {
    // Chat.tsx reads `seedThread === true` and `contextMessages` from router
    // state; role must be "assistant" for contextToSeedMessages to render it
    // as the companion's opening message.
    const state = initiativeReplyState({ text: "hello" });
    const asLocationState: { seedThread?: boolean; contextMessages?: unknown[] } =
      state;
    expect(asLocationState.seedThread).toBe(true);
    expect(Array.isArray(asLocationState.contextMessages)).toBe(true);
    expect(state.contextMessages[0].role).toBe("assistant");
  });
});
