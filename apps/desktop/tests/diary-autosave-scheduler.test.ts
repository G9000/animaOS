import { describe, expect, test, mock } from "bun:test";
import { createAutosaveScheduler } from "../src/features/diary/lib/autosaveScheduler";

const tick = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

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
});
