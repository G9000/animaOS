import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import type { Greeting } from "@anima/api-client";

import { setUnlockToken, clearUnlockToken } from "../src/lib/api";
import {
  ambientConsentAllows,
  dreamClaimExpired,
  ACK_RETRY_DELAYS_MS,
  deliverDreamReceipt,
  displayableGreeting,
  dreamFreeGreeting,
  dreamReceiptKey,
  ONESHOT_FALLBACK_TTL_MS,
  voiceableGreeting,
  getCachedGreeting,
  peekOneShotGreeting,
  purgeGreetingStorage,
  setCachedGreeting,
  stashOneShotGreeting,
  takeOneShotGreeting,
} from "../src/lib/greetingCache";

// bun:test has no DOM — a minimal sessionStorage stub is enough for these
// pure storage helpers (they already try/catch around every access).
const store = new Map<string, string>();
(globalThis as { sessionStorage?: unknown }).sessionStorage = {
  getItem: (k: string) => store.get(k) ?? null,
  setItem: (k: string, v: string) => void store.set(k, v),
  removeItem: (k: string) => void store.delete(k),
};

function greeting(overrides: Partial<Greeting> = {}): Greeting {
  return {
    message: "hello",
    llmGenerated: true,
    context: {
      currentFocus: null,
      openTaskCount: 0,
      overdueTasks: 0,
      daysSinceLastChat: 1,
      upcomingDeadlines: [],
    },
    ...overrides,
  };
}

beforeEach(() => {
  store.clear();
  setUnlockToken("unlocked");
});

describe("greeting TTL cache (IL-010 / PR #130)", () => {
  test("ordinary LLM greetings cache and replay", () => {
    setCachedGreeting(7, greeting());
    expect(getCachedGreeting(7)?.greeting.message).toBe("hello");
  });

  test("a dream-bearing greeting is NEVER cached — one-shot by contract", () => {
    setCachedGreeting(7, greeting()); // pre-existing cache entry
    setCachedGreeting(7, greeting({ ambientDream: true, message: "dreamy" }));
    // Not only is the dream greeting absent; the stale entry is cleared so
    // nothing replays over the freshly voiced dream.
    expect(getCachedGreeting(7)).toBeNull();
  });

  test("static fallbacks are not cached", () => {
    setCachedGreeting(7, greeting({ llmGenerated: false }));
    expect(getCachedGreeting(7)).toBeNull();
  });
});

describe("one-shot handoff slot (IL-010 / PR #130)", () => {
  test("a stashed greeting is taken exactly once", () => {
    stashOneShotGreeting(7, greeting({ ambientDream: true, message: "dreamy" }));
    expect(takeOneShotGreeting(7)?.message).toBe("dreamy");
    expect(takeOneShotGreeting(7)).toBeNull(); // consumed
  });

  test("a different user's mount never takes the stash", () => {
    stashOneShotGreeting(7, greeting({ ambientDream: true }));
    expect(takeOneShotGreeting(8)).toBeNull();
  });
});

describe("one-shot queue + consent (PR #130 round 3)", () => {
  test("two in-flight consumptions both survive — FIFO, nothing overwritten", () => {
    stashOneShotGreeting(7, greeting({ ambientDream: true, message: "first dream" }));
    stashOneShotGreeting(7, greeting({ ambientDream: true, message: "second dream" }));
    expect(takeOneShotGreeting(7)?.message).toBe("first dream");
    expect(takeOneShotGreeting(7)?.message).toBe("second dream");
    expect(takeOneShotGreeting(7)).toBeNull();
  });

  test("peek reports presence without consuming", () => {
    expect(peekOneShotGreeting(7)).toBe(false);
    stashOneShotGreeting(7, greeting({ ambientDream: true }));
    expect(peekOneShotGreeting(7)).toBe(true);
    expect(peekOneShotGreeting(7)).toBe(true); // still there
    expect(takeOneShotGreeting(7)).not.toBeNull();
    expect(peekOneShotGreeting(7)).toBe(false);
  });

  test("consent gate: ambient required, opt-out and master-off win", () => {
    expect(ambientConsentAllows({ enabled: true, dreamSharing: "ambient" })).toBe(true);
    expect(ambientConsentAllows({ dreamSharing: "ambient" })).toBe(true);
    expect(ambientConsentAllows({ enabled: false, dreamSharing: "ambient" })).toBe(false);
    expect(ambientConsentAllows({ enabled: true, dreamSharing: "on_ask" })).toBe(false);
    expect(ambientConsentAllows({ enabled: true, dreamSharing: "off" })).toBe(false);
  });
});

describe("session-bound plaintext handling (PR #130 round 8)", () => {
  test("purge clears pending handoffs and the cache", () => {
    stashOneShotGreeting(7, greeting({ ambientDream: true, message: "dreamy" }));
    setCachedGreeting(7, greeting());
    purgeGreetingStorage();
    expect(peekOneShotGreeting(7)).toBe(false);
    expect(getCachedGreeting(7)).toBeNull();
  });

  test("a late callback cannot repopulate plaintext after the session ends", () => {
    // Greeting request resolves AFTER logout/lock: the stash must refuse,
    // or decrypted autobiographical memory would sit in a locked webview's
    // sessionStorage with no TTL.
    clearUnlockToken();
    stashOneShotGreeting(7, greeting({ ambientDream: true, message: "dreamy" }));
    expect(peekOneShotGreeting(7)).toBe(false);
  });

  test("stashing works normally while the session is unlocked", () => {
    setUnlockToken("unlocked");
    stashOneShotGreeting(7, greeting({ ambientDream: true, message: "dreamy" }));
    expect(takeOneShotGreeting(7)?.message).toBe("dreamy");
  });
});

describe("origin-session binding (PR #130 round 9)", () => {
  test("a late callback cannot write into a REPLACEMENT session", () => {
    // User A's greeting is in flight; A logs out and B signs in before it
    // resolves. A bare truthiness check accepted B's token and would have
    // written A's decrypted dream into B's sessionStorage.
    setUnlockToken("token-A");
    const originToken = "token-A";
    setUnlockToken("token-B"); // A logged out, B signed in
    stashOneShotGreeting(7, greeting({ ambientDream: true }), originToken);
    expect(peekOneShotGreeting(7)).toBe(false);
  });

  test("the originating session may still stash", () => {
    setUnlockToken("token-A");
    stashOneShotGreeting(7, greeting({ ambientDream: true }), "token-A");
    expect(peekOneShotGreeting(7)).toBe(true);
  });

  test("no live session refuses regardless of origin", () => {
    clearUnlockToken();
    stashOneShotGreeting(7, greeting({ ambientDream: true }), null);
    expect(peekOneShotGreeting(7)).toBe(false);
  });
});

describe("stashed dreams die with their server claim (PR #135 review)", () => {
  // Time is faked rather than slept through: these deadlines are minutes
  // long and the assertions are about ordering, not duration.
  const realNow = Date.now;
  const at = (ms: number) => {
    Date.now = () => ms;
  };
  const T0 = 1_800_000_000_000;
  const dream = (expiresAt: string | null | undefined, message = "dreamy") =>
    greeting({ ambientDream: true, ambientDreamId: 42, message, ambientDreamExpiresAt: expiresAt });

  beforeEach(() => at(T0));
  afterEach(() => {
    Date.now = realNow;
  });

  test("a stash held past the claim deadline is dropped, not replayed", () => {
    // The dream was CLAIMED, not surfaced: once the claim expires the server
    // may voice the same narrative through an initiative or a fresh
    // greeting, so replaying this copy would disclose it twice.
    stashOneShotGreeting(7, dream(new Date(T0 + 60_000).toISOString()));
    expect(peekOneShotGreeting(7)).toBe(true);

    at(T0 + 59_000);
    expect(peekOneShotGreeting(7)).toBe(true); // still inside the claim

    at(T0 + 61_000);
    expect(peekOneShotGreeting(7)).toBe(false);
    expect(takeOneShotGreeting(7)).toBeNull();
  });

  test("an expired entry is purged from storage, not merely hidden", () => {
    stashOneShotGreeting(7, dream(new Date(T0 + 60_000).toISOString()));
    at(T0 + 61_000);
    peekOneShotGreeting(7);
    expect(store.get("anima_dashboard_greeting_oneshot")).toBeUndefined();
  });

  test("only the expired entry is dropped — live siblings survive", () => {
    stashOneShotGreeting(7, dream(new Date(T0 + 60_000).toISOString(), "early"));
    stashOneShotGreeting(7, dream(new Date(T0 + 600_000).toISOString(), "late"));
    at(T0 + 61_000);
    expect(takeOneShotGreeting(7)?.message).toBe("late");
  });

  test("a response that arrives already expired is never stashed", () => {
    stashOneShotGreeting(7, dream(new Date(T0 - 1_000).toISOString()));
    expect(peekOneShotGreeting(7)).toBe(false);
  });

  test("no stated deadline falls back to a bounded lifetime", () => {
    // An older server (or a lost field) must not produce an immortal stash.
    stashOneShotGreeting(7, dream(undefined));
    expect(peekOneShotGreeting(7)).toBe(true);
    at(T0 + ONESHOT_FALLBACK_TTL_MS + 1);
    expect(peekOneShotGreeting(7)).toBe(false);
  });

  test("an unparseable deadline falls back rather than expiring instantly", () => {
    stashOneShotGreeting(7, dream("not a date"));
    expect(takeOneShotGreeting(7)?.message).toBe("dreamy");
  });

  test("dreamClaimExpired only ever fires for dream-bearing greetings", () => {
    const past = new Date(T0 - 1).toISOString();
    expect(dreamClaimExpired(dream(past))).toBe(true);
    expect(dreamClaimExpired(dream(new Date(T0 + 1).toISOString()))).toBe(false);
    // A plain greeting carries no claim, so it never expires — even if some
    // stale expiry field rode along.
    expect(dreamClaimExpired(greeting({ ambientDreamExpiresAt: past }))).toBe(false);
  });
});

describe("confirming the claim before voicing (PR #135 review round 3)", () => {
  const realNow = Date.now;
  const T0 = 1_800_000_000_000;
  const dreamy = (overrides: Partial<Greeting> = {}) =>
    greeting({
      message: "hi there. I dreamt about something recently.",
      handoffMessage: "hi there.",
      ambientDream: true,
      ambientDreamId: 42,
      ambientDreamClaimToken: "2026-08-02T06:00:00+00:00",
      ambientDreamExpiresAt: new Date(T0 + 600_000).toISOString(),
      ...overrides,
    });
  const ok = (token: string) => ({
    confirmed: true,
    claimToken: token,
    expiresAt: new Date(T0 + 900_000).toISOString(),
  });
  const refused = { confirmed: false, claimToken: null, expiresAt: null };

  beforeEach(() => {
    Date.now = () => T0;
  });
  afterEach(() => {
    Date.now = realNow;
  });

  test("a confirmed claim is voiced and carries the RENEWED token", async () => {
    const shown = await voiceableGreeting(dreamy(), async () => ok("renewed"));
    expect(shown.ambientDream).toBe(true);
    expect(shown.message).toContain("I dreamt");
    // The ack must use the renewed token, not the spent one.
    expect(shown.ambientDreamClaimToken).toBe("renewed");
    expect(shown.ambientDreamExpiresAt).toBe(new Date(T0 + 900_000).toISOString());
  });

  test("a refused claim degrades to the dream-free copy", async () => {
    // The server has re-offered this dream to another channel; voicing the
    // stored copy would disclose the same narrative twice.
    const shown = await voiceableGreeting(dreamy(), async () => refused);
    expect(shown.ambientDream).toBe(false);
    expect(shown.message).toBe("hi there.");
    expect(shown.ambientDreamId).toBeNull();
    expect(shown.ambientDreamClaimToken).toBeNull();
  });

  test("a failed confirmation degrades rather than guessing", async () => {
    const shown = await voiceableGreeting(dreamy(), async () => {
      throw new Error("offline");
    });
    expect(shown.ambientDream).toBe(false);
    expect(shown.message).toBe("hi there.");
  });

  test("a locally expired greeting never even asks", async () => {
    let asked = false;
    const shown = await voiceableGreeting(
      dreamy({ ambientDreamExpiresAt: new Date(T0 - 1).toISOString() }),
      async () => {
        asked = true;
        return ok("renewed");
      },
    );
    expect(asked).toBe(false);
    expect(shown.ambientDream).toBe(false);
  });

  test("a dream with no claim token cannot be voiced", async () => {
    const shown = await voiceableGreeting(
      dreamy({ ambientDreamClaimToken: null }),
      async () => ok("renewed"),
    );
    expect(shown.ambientDream).toBe(false);
  });

  test("an ordinary greeting is passed through untouched and unasked", async () => {
    let asked = false;
    const plain = greeting({ message: "morning" });
    const shown = await voiceableGreeting(plain, async () => {
      asked = true;
      return ok("renewed");
    });
    expect(asked).toBe(false);
    expect(shown).toEqual(plain);
  });

  test("a missing dream-free copy blanks the greeting rather than leaking it", async () => {
    // The server always sends handoffMessage alongside a dream, so its
    // absence is a bug — and the safe failure is an empty greeting, never
    // the dream sentence we just decided not to voice.
    const shown = dreamFreeGreeting(dreamy({ handoffMessage: null }));
    expect(shown.message).toBe("");
    expect(shown.ambientDream).toBe(false);
  });
});

describe("dream receipts are owed per claim generation (PR #135 review)", () => {
  test("a dream-bearing greeting owes a receipt keyed by its claim", () => {
    expect(
      dreamReceiptKey(
        greeting({
          ambientDream: true,
          ambientDreamId: 42,
          ambientDreamClaimToken: "2026-08-02T06:00:00+00:00",
        }),
      ),
    ).toBe("42:2026-08-02T06:00:00+00:00");
  });

  test("re-claiming the same dream owes a NEW receipt", () => {
    // A dream that lapsed and was claimed again is a different disclosure;
    // the earlier receipt must not suppress the later one.
    const first = dreamReceiptKey(
      greeting({
        ambientDream: true,
        ambientDreamId: 42,
        ambientDreamClaimToken: "2026-08-02T06:00:00+00:00",
      }),
    );
    const second = dreamReceiptKey(
      greeting({
        ambientDream: true,
        ambientDreamId: 42,
        ambientDreamClaimToken: "2026-08-02T06:30:00+00:00",
      }),
    );
    expect(second).not.toBe(first);
  });

  test("nothing is owed for greetings that carry no voiceable dream", () => {
    expect(dreamReceiptKey(null)).toBeNull();
    expect(dreamReceiptKey(greeting())).toBeNull();
    // A dream stripped by a refused confirmation must never be acked.
    expect(
      dreamReceiptKey(
        dreamFreeGreeting(
          greeting({
            ambientDream: true,
            ambientDreamId: 42,
            handoffMessage: "hi there.",
            ambientDreamClaimToken: "2026-08-02T06:00:00+00:00",
          }),
        ),
      ),
    ).toBeNull();
    // No token: the server would reject the ack anyway.
    expect(
      dreamReceiptKey(greeting({ ambientDream: true, ambientDreamId: 42 })),
    ).toBeNull();
  });
});

describe("dream receipts survive a dropped request (PR #135 review round 5)", () => {
  // Deterministic clock and waits: the point is the retry SCHEDULE, not the
  // wall time it would take.
  const harness = (deadlineOffset = 600_000) => {
    let clock = 1_800_000_000_000;
    const waited: number[] = [];
    return {
      waited,
      now: () => clock,
      wait: async (ms: number) => {
        waited.push(ms);
        clock += ms;
      },
      deadline: clock + deadlineOffset,
      advance: (ms: number) => {
        clock += ms;
      },
    };
  };

  test("a transient failure is retried until the receipt lands", async () => {
    const h = harness();
    let attempts = 0;
    const delivered = await deliverDreamReceipt(
      async () => {
        attempts += 1;
        if (attempts < 3) throw new Error("offline");
        return { acknowledged: true };
      },
      { deadline: h.deadline, now: h.now, wait: h.wait },
    );
    expect(delivered).toBe(true);
    expect(attempts).toBe(3);
    expect(h.waited).toEqual([ACK_RETRY_DELAYS_MS[0], ACK_RETRY_DELAYS_MS[1]]);
  });

  test("retries stop once the claim would have lapsed", async () => {
    // Past the deadline the server has already re-offered the dream, so a
    // late receipt would surface a claim that is no longer ours.
    const h = harness(1_500); // shorter than the first backoff
    let attempts = 0;
    const delivered = await deliverDreamReceipt(
      async () => {
        attempts += 1;
        throw new Error("offline");
      },
      { deadline: h.deadline, now: h.now, wait: h.wait },
    );
    expect(delivered).toBe(false);
    expect(attempts).toBe(1);
    expect(h.waited).toEqual([]);
  });

  test("nothing is attempted at all once the claim is already stale", async () => {
    const h = harness(0);
    let attempts = 0;
    const delivered = await deliverDreamReceipt(
      async () => {
        attempts += 1;
        return { acknowledged: true };
      },
      { deadline: h.deadline, now: h.now, wait: h.wait },
    );
    expect(delivered).toBe(false);
    expect(attempts).toBe(0);
  });

  test("a definitive refusal is not retried", async () => {
    // `acknowledged: false` means already surfaced or superseded — retrying
    // cannot change the answer.
    const h = harness();
    let attempts = 0;
    const delivered = await deliverDreamReceipt(
      async () => {
        attempts += 1;
        return { acknowledged: false };
      },
      { deadline: h.deadline, now: h.now, wait: h.wait },
    );
    expect(delivered).toBe(false);
    expect(attempts).toBe(1);
  });

  test("the backoff is exhausted rather than looping forever", async () => {
    const h = harness(3_600_000);
    let attempts = 0;
    const delivered = await deliverDreamReceipt(
      async () => {
        attempts += 1;
        throw new Error("offline");
      },
      { deadline: h.deadline, now: h.now, wait: h.wait },
    );
    expect(delivered).toBe(false);
    expect(attempts).toBe(ACK_RETRY_DELAYS_MS.length + 1);
    expect(h.waited).toEqual(ACK_RETRY_DELAYS_MS);
  });
});

describe("a hidden window never holds a voiceable dream (PR #135 review round 6)", () => {
  const realNow = Date.now;
  const T0 = 1_800_000_000_000;
  const live = () =>
    greeting({
      message: "hi there. I dreamt about something recently.",
      handoffMessage: "hi there.",
      ambientDream: true,
      ambientDreamId: 42,
      ambientDreamClaimToken: "2026-08-02T06:00:00+00:00",
      ambientDreamExpiresAt: new Date(T0 + 600_000).toISOString(),
    });

  beforeEach(() => {
    Date.now = () => T0;
  });
  afterEach(() => {
    Date.now = realNow;
  });

  test("the dream is withheld while the page is hidden", () => {
    // Nothing dream-bearing is painted into a window the user cannot see,
    // so there is no stale frame to expose on reveal.
    const shown = displayableGreeting(live(), false);
    expect(shown?.ambientDream).toBe(false);
    expect(shown?.message).toBe("hi there.");
  });

  test("a lapsed claim is withheld even while visible", () => {
    const stale = { ...live(), ambientDreamExpiresAt: new Date(T0 - 1).toISOString() };
    expect(displayableGreeting(stale, true)?.ambientDream).toBe(false);
  });

  test("a live claim on a visible page is shown untouched", () => {
    const g = live();
    expect(displayableGreeting(g, true)).toBe(g);
  });

  test("ordinary greetings are unaffected by visibility", () => {
    const plain = greeting({ message: "morning" });
    expect(displayableGreeting(plain, false)).toBe(plain);
    expect(displayableGreeting(null, true)).toBeNull();
  });

  test("a withheld dream owes no receipt", () => {
    // The ack path reads the DISPLAYED greeting, so a dream held back for
    // being hidden or stale can never be marked surfaced.
    expect(dreamReceiptKey(displayableGreeting(live(), false))).toBeNull();
    expect(dreamReceiptKey(displayableGreeting(live(), true))).toBe(
      "42:2026-08-02T06:00:00+00:00",
    );
  });
});
