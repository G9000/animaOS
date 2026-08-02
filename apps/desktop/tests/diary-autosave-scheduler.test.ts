import { describe, expect, test, mock } from "bun:test";
import { createAutosaveScheduler } from "../src/features/diary/lib/autosaveScheduler";

const tick = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

function deferred<T = void>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe("autosave scheduler", () => {
  test("debounces rapid edits into a single save with the latest payload", async () => {
    const save = mock(async (_: string) => {});
    const s = createAutosaveScheduler<string>({ save, delayMs: 20 });

    s.schedule("a");
    s.schedule("b");
    s.schedule("c");
    await tick(50);

    expect(save).toHaveBeenCalledTimes(1);
    expect(save.mock.calls[0][0]).toBe("c");
    s.dispose();
  });

  test("never runs two saves concurrently and sends the newest queued payload", async () => {
    let inFlight = 0;
    let maxInFlight = 0;
    const seen: string[] = [];
    const save = mock(async (payload: string) => {
      inFlight += 1;
      maxInFlight = Math.max(maxInFlight, inFlight);
      seen.push(payload);
      await tick(30);
      inFlight -= 1;
    });
    const s = createAutosaveScheduler<string>({ save, delayMs: 10 });

    s.schedule("first");
    await tick(20);
    s.schedule("second");
    s.schedule("third");
    await tick(100);

    expect(maxInFlight).toBe(1);
    expect(seen).toEqual(["first", "third"]);
    s.dispose();
  });

  test("flush saves immediately without waiting for the debounce", async () => {
    const save = mock(async (_: string) => {});
    const s = createAutosaveScheduler<string>({ save, delayMs: 10_000 });

    s.schedule("urgent");
    await s.flush();

    expect(save).toHaveBeenCalledTimes(1);
    expect(save.mock.calls[0][0]).toBe("urgent");
    s.dispose();
  });

  test("flush is a no-op when there is nothing pending", async () => {
    const save = mock(async (_: string) => {});
    const s = createAutosaveScheduler<string>({ save, delayMs: 10 });

    await s.flush();

    expect(save).toHaveBeenCalledTimes(0);
    s.dispose();
  });

  test("reports error status on failure and retry re-sends the same payload", async () => {
    const statuses: string[] = [];
    let attempt = 0;
    const save = mock(async (_: string) => {
      attempt += 1;
      if (attempt === 1) throw new Error("network");
    });
    const s = createAutosaveScheduler<string>({
      save,
      delayMs: 10,
      onStatusChange: (status) => statuses.push(status),
    });

    s.schedule("keep-me");
    await tick(40);
    expect(s.status()).toBe("error");

    await s.retry();
    expect(s.status()).toBe("saved");
    expect(save.mock.calls[1][0]).toBe("keep-me");
    expect(statuses).toContain("saving");
    expect(statuses).toContain("error");
    s.dispose();
  });

  test("dispose cancels a pending save", async () => {
    const save = mock(async (_: string) => {});
    const s = createAutosaveScheduler<string>({ save, delayMs: 20 });

    s.schedule("dropped");
    s.dispose();
    await tick(50);

    expect(save).toHaveBeenCalledTimes(0);
  });

  test("flush is a real barrier: it does not resolve until the newest mid-flight edit has finished saving", async () => {
    const gate1 = deferred<void>();
    const gate2 = deferred<void>();
    const calls: string[] = [];
    const finished: string[] = [];
    const save = mock(async (payload: string) => {
      calls.push(payload);
      if (payload === "X") await gate1.promise;
      else await gate2.promise;
      finished.push(payload);
    });
    const s = createAutosaveScheduler<string>({ save, delayMs: 5 });

    s.schedule("X");
    await tick(15); // debounce fires, X starts saving and blocks on gate1
    expect(calls).toEqual(["X"]);

    s.schedule("Y"); // queued while X is still in flight

    let flushSettled = false;
    const flushPromise = s.flush().then(() => {
      flushSettled = true;
    });

    await tick(10);
    expect(flushSettled).toBe(false); // X hasn't even resolved yet

    gate1.resolve(); // let X finish; the loop should pick up Y next
    await tick(10);
    expect(calls).toEqual(["X", "Y"]); // Y has started
    expect(finished).toEqual(["X"]); // ...but not finished
    expect(flushSettled).toBe(false); // flush must still be waiting on Y

    gate2.resolve(); // let Y finish
    await flushPromise;

    expect(flushSettled).toBe(true);
    expect(finished).toEqual(["X", "Y"]);
    s.dispose();
  });

  test("retry never runs concurrently with an already in-flight scheduled save", async () => {
    let inFlightCount = 0;
    let maxInFlight = 0;
    const seen: string[] = [];
    const gate = deferred<void>();
    let attempt = 0;
    const save = mock(async (payload: string) => {
      attempt += 1;
      inFlightCount += 1;
      maxInFlight = Math.max(maxInFlight, inFlightCount);
      seen.push(payload);
      if (payload === "A" && attempt === 1) {
        inFlightCount -= 1;
        throw new Error("network");
      }
      if (payload === "B") {
        await gate.promise;
      }
      inFlightCount -= 1;
    });
    const s = createAutosaveScheduler<string>({ save, delayMs: 5 });

    s.schedule("A");
    await tick(15); // A's debounce fires and fails
    expect(s.status()).toBe("error");

    s.schedule("B");
    await tick(15); // B's debounce fires, B starts saving and blocks on `gate`
    expect(seen).toEqual(["A", "B"]);

    const retryPromise = s.retry(); // retry() called while B is in flight

    await tick(10);
    expect(maxInFlight).toBe(1); // retry must not start A concurrently with B

    gate.resolve(); // let B finish
    await retryPromise;

    expect(maxInFlight).toBe(1);
    expect(seen).toEqual(["A", "B", "A"]); // A retried only after B finished
    s.dispose();
  });

  test("dispose during an in-flight save prevents further onStatusChange calls", async () => {
    const gate = deferred<void>();
    const statuses: string[] = [];
    const save = mock(async (_: string) => {
      await gate.promise;
    });
    const s = createAutosaveScheduler<string>({
      save,
      delayMs: 5,
      onStatusChange: (status) => statuses.push(status),
    });

    s.schedule("x");
    await tick(15); // debounce fires, save starts ("saving"), blocks on gate
    expect(statuses).toEqual(["saving"]);

    s.dispose();
    const countAtDispose = statuses.length;

    gate.resolve(); // let the in-flight save settle after dispose
    await tick(10);

    expect(statuses.length).toBe(countAtDispose); // no callbacks after dispose
    expect(statuses).toEqual(["saving"]);
  });

  test("scheduler survives a no-op flush", async () => {
    const save = mock(async (_: string) => {});
    const s = createAutosaveScheduler<string>({ save, delayMs: 10 });

    // Nothing has ever been scheduled — flush() must be a true no-op and
    // must not leave the internal loop in a state that blocks future work.
    await s.flush();
    expect(save).toHaveBeenCalledTimes(0);

    s.schedule("two");
    await s.flush();

    expect(save).toHaveBeenCalledTimes(1);
    expect(save.mock.calls[0][0]).toBe("two");
    s.dispose();
  });

  test("scheduler survives a debounce timer firing after the loop already drained its payload", async () => {
    const save = mock(async (_: string) => {});
    const s = createAutosaveScheduler<string>({ save, delayMs: 15 });

    s.schedule("first");
    await tick(5); // debounce has not fired yet; "first" still pending

    // Call flush() (it will drain whatever is pending), and — before it
    // resolves — schedule a second edit. This arms a fresh debounce timer
    // for "second" while flush()'s loop is still running/starting;
    // nobody ever clears that timer once the loop finishes draining.
    // (Whether "first" ends up sent as its own save call or coalesced
    // into "second" is incidental to this test — either is consistent
    // with the "newest payload wins" design — so we only assert on the
    // latest call, not the exact count.)
    const flushPromise = s.flush();
    s.schedule("second");
    await flushPromise;

    const callsAfterFlush = save.mock.calls.length;
    expect(callsAfterFlush).toBeGreaterThanOrEqual(1);
    expect(save.mock.calls[callsAfterFlush - 1][0]).toBe("second");

    // That dangling timer now fires with nothing pending (everything was
    // already drained above) — the exact trigger for the loop-poisoning
    // bug.
    await tick(25);

    // The scheduler must still work afterward: exactly one more save,
    // for "third".
    s.schedule("third");
    await s.flush();

    expect(save).toHaveBeenCalledTimes(callsAfterFlush + 1);
    expect(save.mock.calls[callsAfterFlush][0]).toBe("third");
    s.dispose();
  });

  test("status is never \"saved\" while an edit is pending", async () => {
    const save = mock(async (_: string) => {});
    const s = createAutosaveScheduler<string>({ save, delayMs: 20 });

    s.schedule("a");
    await tick(30); // let it save and settle
    expect(s.status()).toBe("saved");

    s.schedule("b"); // a new, unsent edit arrives
    expect(s.status()).not.toBe("saved"); // must not claim "Saved" now

    await s.flush();
    expect(s.status()).toBe("saved");
    s.dispose();
  });
});
