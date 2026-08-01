import { describe, expect, test } from "bun:test";

import {
  classifySeedCloseAbandon,
  classifySeedNavigation,
  initiativeReplyState,
  mergeSeedContexts,
} from "../src/lib/initiativeReply";

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

describe("classifySeedNavigation (PR #131 review)", () => {
  const base = {
    handledKey: "k0",
    key: "k1",
    seedThread: true,
    contextCount: 1,
    streaming: false,
  };

  test("applies a fresh seed navigation on a mounted Chat", () => {
    expect(classifySeedNavigation(base)).toBe("apply");
  });

  test("ignores the mount navigation (owned by the useRef init path)", () => {
    expect(classifySeedNavigation({ ...base, key: "k0" })).toBe("ignore");
  });

  test("ignores non-seed navigations and empty context", () => {
    expect(classifySeedNavigation({ ...base, seedThread: false })).toBe("ignore");
    expect(classifySeedNavigation({ ...base, contextCount: 0 })).toBe("ignore");
  });

  test("defers instead of dropping when a stream is active", () => {
    // Dropping would re-create the original bug (acked, text lost); applying
    // would swap the thread under the live stream.
    expect(classifySeedNavigation({ ...base, streaming: true })).toBe("defer");
  });
});

describe("mergeSeedContexts (PR #131 round 2)", () => {
  const msg = (content: string) =>
    ({ role: "assistant", content, source: "initiative" }) as const;

  test("a second Reply adds its text instead of overwriting the first", () => {
    const first = mergeSeedContexts(null, [msg("first initiative")]);
    const both = mergeSeedContexts(first, [msg("second initiative")]);
    expect(both.map((m) => m.content)).toEqual([
      "first initiative",
      "second initiative",
    ]);
  });

  test("null existing behaves as an empty queue", () => {
    expect(mergeSeedContexts(null, [msg("only")])).toEqual([msg("only")]);
  });

  test("does not mutate the existing queue", () => {
    const existing = [msg("a")];
    const merged = mergeSeedContexts(existing, [msg("b")]);
    expect(existing).toHaveLength(1);
    expect(merged).toHaveLength(2);
  });
});

describe("classifySeedCloseAbandon (PR #131 rounds 8/10)", () => {
  test("reuses an in-flight close for the SAME thread", () => {
    expect(
      classifySeedCloseAbandon({ pendingThreadId: 7, inFlightThreadId: 7 }),
    ).toBe("await-inflight");
  });

  test("never lets another thread's request settle this one (round 10)", () => {
    // T1's close is in flight while T2 is the pending close: awaiting T1
    // would skip T2's close entirely, or let T1's failure retry against T2.
    expect(
      classifySeedCloseAbandon({ pendingThreadId: 2, inFlightThreadId: 1 }),
    ).toBe("close");
  });

  test("fires one best-effort close when nothing is in flight", () => {
    expect(
      classifySeedCloseAbandon({ pendingThreadId: 7, inFlightThreadId: null }),
    ).toBe("close");
    expect(classifySeedCloseAbandon({ pendingThreadId: 7 })).toBe("close");
  });

  test("never closes the thread the user is re-opening", () => {
    expect(
      classifySeedCloseAbandon({
        pendingThreadId: 7,
        keepThreadId: 7,
        inFlightThreadId: null,
      }),
    ).toBe("none");
    expect(
      classifySeedCloseAbandon({
        pendingThreadId: 7,
        keepThreadId: 7,
        inFlightThreadId: 7,
      }),
    ).toBe("none");
  });

  test("nothing owed means nothing to do", () => {
    expect(
      classifySeedCloseAbandon({ pendingThreadId: null, inFlightThreadId: 7 }),
    ).toBe("none");
  });
});
